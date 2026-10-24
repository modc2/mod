"""The kernel: tensor arithmetic with no floating point anywhere.

A proof of inference is impossible for most of the models people run, and
the reason is not cryptography — it is that the arithmetic is not a
function. IEEE-754 addition is not associative, so the same network, the
same weights and the same input give different answers on a different GPU,
a different batch size, a different kernel-fusion decision or a different
cuDNN version. "I ran this model on this input and got this" is then an
unfalsifiable claim: the output you cannot reproduce is not evidence of
cheating.

So tensorcash does not try to prove floating point. It defines the model as
an INTEGER function and proves that instead:

    weights      int8, symmetric, one scale per tensor
    accumulators int32, exactly — overflow is a hard error, never a wrap
    requantise   (acc * mult + round) >> shift, integer multiply and shift
    activations  int8 (or int32 for the final logits)

Every operation below is a pure function from integers to integers, written
so that a faithful reimplementation in Rust, C or wasm lands on identical
bytes. There is nothing to round differently, no reduction order to get
wrong (we fix it: ascending input index), no fused kernel to disagree with.
That makes the forward pass REPLAYABLE, and replay is what the receipt in
receipt.py is worth.

The cost of this honesty: these are quantised models, and quantisation
costs accuracy. The digits model in the zoo loses ~1 point against its
float twin. The alternative is not a more accurate proof, it is no proof.

INT32 OVERFLOW is raised, not wrapped, on purpose. A wrap is a place where
two implementations can differ (does your C compiler wrap signed overflow?
it is undefined), so a model that overflows is refused rather than
silently given a platform-dependent answer.
"""

from __future__ import annotations

INT8_MIN, INT8_MAX = -128, 127
INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1


class TensorError(ValueError):
    def __init__(self, message, code='bad_tensor'):
        super().__init__(message)
        self.code = code


def i8(b: bytes) -> list[int]:
    """int8 bytes -> ints. Two's complement, one byte each."""
    return [x - 256 if x > 127 else x for x in b]


def to_i8(xs) -> bytes:
    for x in xs:
        if not INT8_MIN <= x <= INT8_MAX:
            raise TensorError(f'{x} does not fit in int8', 'overflow')
    return bytes((x + 256) & 0xFF for x in xs)


def i32(b: bytes) -> list[int]:
    """int32 little-endian bytes -> ints."""
    if len(b) % 4:
        raise TensorError('int32 tensor length is not a multiple of 4')
    return [int.from_bytes(b[i:i + 4], 'little', signed=True)
            for i in range(0, len(b), 4)]


def to_i32(xs) -> bytes:
    out = bytearray()
    for x in xs:
        _guard(x)
        out += int(x).to_bytes(4, 'little', signed=True)
    return bytes(out)


def _guard(x: int):
    if not INT32_MIN <= x <= INT32_MAX:
        raise TensorError(
            f'accumulator {x} leaves int32 — this model cannot be evaluated '
            'reproducibly (overflow is refused, never wrapped, because two '
            'implementations would wrap differently)', 'overflow')


# ── the ops ───────────────────────────────────────────────────────
#
# Each op takes and returns plain lists of python ints and is named in a
# model's graph by its key in OPS at the bottom of this file. The reduction
# order is part of the specification: ascending index, one accumulator,
# no pairwise tree, no fused multiply-add with a wider intermediate.


def matvec(w: list[int], x: list[int], out_dim: int, in_dim: int) -> list[int]:
    """Row-major int8 weights x int8 activations -> int32 accumulators.

    w is out_dim rows of in_dim weights; row r holds the weights of output
    r. Accumulation is int32 and checked on every add, so a model whose
    activations could overflow is refused here rather than on someone
    else's machine."""
    if len(w) != out_dim * in_dim:
        raise TensorError(f'weight tensor is {len(w)}, expected '
                          f'{out_dim}x{in_dim}={out_dim * in_dim}', 'shape')
    if len(x) != in_dim:
        raise TensorError(f'input is {len(x)}, expected {in_dim}', 'shape')
    out = []
    for r in range(out_dim):
        base = r * in_dim
        acc = 0
        for c in range(in_dim):
            acc += w[base + c] * x[c]
            _guard(acc)
        out.append(acc)
    return out


def bias(acc: list[int], b: list[int]) -> list[int]:
    if len(acc) != len(b):
        raise TensorError(f'bias is {len(b)}, expected {len(acc)}', 'shape')
    out = []
    for a, bb in zip(acc, b):
        v = a + bb
        _guard(v)
        out.append(v)
    return out


def requant(acc: list[int], mult: int, shift: int) -> list[int]:
    """int32 accumulators -> int8, by integer multiply and rounding shift.

        y = clamp(-128, 127, (acc * mult + 2^(shift-1)) >> shift)

    mult/2^shift is the fixed-point stand-in for the float rescale
    (in_scale * weight_scale / out_scale) that a float pipeline would do.
    Rounding is round-half-UP on the number line (arithmetic shift of a
    pre-biased value), which is one fixed rule rather than the four that
    float libraries pick between. Saturation, not wrap, on both ends."""
    if mult < 0 or not 0 <= shift <= 62:
        raise TensorError('requant needs mult >= 0 and 0 <= shift <= 62',
                          'bad_requant')
    half = 1 << (shift - 1) if shift else 0
    out = []
    for a in acc:
        _guard(a)
        v = (a * mult + half) >> shift
        out.append(INT8_MIN if v < INT8_MIN else
                   INT8_MAX if v > INT8_MAX else v)
    return out


def relu(x: list[int]) -> list[int]:
    return [0 if v < 0 else v for v in x]


def clamp8(x: list[int]) -> list[int]:
    return [INT8_MIN if v < INT8_MIN else INT8_MAX if v > INT8_MAX else v
            for v in x]


def add(a: list[int], b: list[int]) -> list[int]:
    """Residual add on int32 lanes."""
    if len(a) != len(b):
        raise TensorError(f'cannot add {len(a)} and {len(b)} lanes', 'shape')
    out = []
    for x, y in zip(a, b):
        v = x + y
        _guard(v)
        out.append(v)
    return out


def embed(table: list[int], ids: list[int], dim: int, rows: int,
          pool: str = 'mean') -> list[int]:
    """Look up int8 rows by id and pool them. `mean` divides by the number
    of ids with floor division towards negative infinity (python's //),
    which is stated here because C's / truncates towards zero and the two
    disagree on negatives."""
    if len(table) != rows * dim:
        raise TensorError(f'embedding table is {len(table)}, expected '
                          f'{rows}x{dim}', 'shape')
    if not ids:
        return [0] * dim
    acc = [0] * dim
    for i in ids:
        if not 0 <= i < rows:
            raise TensorError(f'token id {i} outside 0..{rows - 1}', 'range')
        base = i * dim
        for d in range(dim):
            acc[d] += table[base + d]
            _guard(acc[d])
    if pool == 'sum':
        return acc
    if pool == 'mean':
        n = len(ids)
        return [a // n for a in acc]
    raise TensorError(f'unknown pool {pool!r} (sum, mean)', 'bad_op')


def argmax(x: list[int]) -> int:
    """Index of the largest lane; ties go to the LOWEST index. Stated
    because a tie broken differently is a different answer, and a model
    with a tie must still have exactly one output."""
    best, at = None, -1
    for i, v in enumerate(x):
        if best is None or v > best:
            best, at = v, i
    return at


def topk(x: list[int], k: int) -> list[int]:
    """Indices of the k largest lanes, ties by lowest index."""
    order = sorted(range(len(x)), key=lambda i: (-x[i], i))
    return order[:max(0, min(k, len(x)))]
