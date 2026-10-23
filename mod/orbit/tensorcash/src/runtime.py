"""tcq1 — the interpreter, and the trace that makes one step checkable.

A model's graph is a straight line of steps over named buffers. Running it
is unremarkable; what matters here is that running it also produces a
TRACE, and that the trace is committed:

    step 0        the input buffer, exactly as it was fed in
    step i        the output buffer of graph step i
    leaf(i)       SHA3('tcq1-step', u32 i, op, u32 len, bytes)
    trace_root    Merkle root over the leaves (model.py's tree shape)

The trace root is in the receipt. That one hash is a commitment to every
intermediate activation, which buys two things no output hash can:

    1. A verifier can check ONE step. Open the step's input buffers (they
       are earlier leaves), the layer's weights (model.py openings) and the
       claimed output leaf, recompute that single op, compare. Cost is one
       layer, not the whole network, and it does not grow with depth.

    2. Two workers who disagree can be adjudicated in log2(steps)
       questions. Both committed to a trace; compare leaves, find the first
       index where they differ, and check that one step. Exactly one of
       them is wrong there, and recomputing a single layer says which.
       That is challenge.py.

Buffers carry a dtype (i8, i32 or idx) because the digest is over BYTES and
the same integers must not encode two ways. Encoding is fixed here:
int8 one byte two's complement, int32 four bytes little-endian signed,
indices four bytes little-endian unsigned.
"""

from __future__ import annotations

import struct

import model as M
import tensors as T

ENGINE = 'tcq1'
MAX_STEPS = 4096
MAX_LANES = 1 << 20


class RunError(ValueError):
    def __init__(self, message, code='bad_run', status=400):
        super().__init__(message)
        self.code, self.status = code, status


# ── buffers ───────────────────────────────────────────────────────


def encode(buf: dict) -> bytes:
    dt, vals = buf['dtype'], buf['values']
    if dt == 'i8':
        return T.to_i8(vals)
    if dt == 'i32':
        return T.to_i32(vals)
    if dt == 'idx':
        return b''.join(struct.pack('<I', int(v)) for v in vals)
    raise RunError(f'unknown buffer dtype {dt!r}')


def decode(dt: str, raw: bytes) -> dict:
    if dt == 'i8':
        return {'dtype': dt, 'values': T.i8(raw)}
    if dt == 'i32':
        return {'dtype': dt, 'values': T.i32(raw)}
    if dt == 'idx':
        if len(raw) % 4:
            raise RunError('idx buffer length is not a multiple of 4')
        return {'dtype': dt, 'values': [
            struct.unpack('<I', raw[i:i + 4])[0]
            for i in range(0, len(raw), 4)]}
    raise RunError(f'unknown buffer dtype {dt!r}')


def step_leaf(i: int, op: str, raw: bytes) -> bytes:
    return M.sha3(b'tcq1-step', struct.pack('<I', i), op.encode(),
                  struct.pack('<I', len(raw)), raw)


# ── one step, in isolation ────────────────────────────────────────
#
# apply_step is THE op semantics, and it is deliberately the only place
# they exist: the full run below calls it in a loop, and the single-step
# challenge verifier calls it once. A verifier that re-implemented the op
# could disagree with the runtime, and then "the proof failed" would mean
# nothing.

OUT_DTYPE = {'matvec': 'i32', 'bias': None, 'requant': 'i8', 'relu': None,
             'add': 'i32', 'embed': 'i32', 'clamp8': 'i8', 'argmax': 'idx',
             'topk': 'idx'}

OPS = tuple(OUT_DTYPE)


def step_inputs(step: dict) -> list[str]:
    """Which buffers this step reads, in a fixed order."""
    op = step.get('op')
    if op in ('matvec', 'bias', 'requant', 'relu', 'clamp8', 'argmax',
              'topk'):
        return [step['x']]
    if op == 'add':
        return [step['x'], step['y']]
    if op == 'embed':
        return [step['ids']]
    raise RunError(f'unknown op {op!r} — {", ".join(OPS)}', 'bad_op')


def step_weights(step: dict) -> list[str]:
    op = step.get('op')
    if op == 'matvec':
        return [step['w']]
    if op == 'bias':
        return [step['b']]
    if op == 'embed':
        return [step['table']]
    return []


def apply_step(step: dict, ins: list[dict], weights: dict) -> dict:
    """ins = the step's input buffers in step_inputs() order.
    weights = {tensor name: (values, shape, dtype)}."""
    op = step.get('op')
    x = ins[0] if ins else None
    if op == 'matvec':
        vals, shape, _dt = weights[step['w']]
        if len(shape) != 2:
            raise RunError(f'matvec weight {step["w"]!r} has shape {shape}, '
                           'want [out_dim, in_dim]', 'shape')
        out_dim, in_dim = int(shape[0]), int(shape[1])
        return {'dtype': 'i32',
                'values': T.matvec(vals, x['values'], out_dim, in_dim)}
    if op == 'bias':
        vals, _shape, _dt = weights[step['b']]
        return {'dtype': x['dtype'], 'values': T.bias(x['values'], vals)}
    if op == 'requant':
        return {'dtype': 'i8',
                'values': T.requant(x['values'], int(step['mult']),
                                    int(step['shift']))}
    if op == 'relu':
        return {'dtype': x['dtype'], 'values': T.relu(x['values'])}
    if op == 'clamp8':
        return {'dtype': 'i8', 'values': T.clamp8(x['values'])}
    if op == 'add':
        return {'dtype': 'i32',
                'values': T.add(x['values'], ins[1]['values'])}
    if op == 'embed':
        vals, shape, _dt = weights[step['table']]
        rows, dim = int(shape[0]), int(shape[1])
        return {'dtype': 'i32',
                'values': T.embed(vals, x['values'], dim, rows,
                                  step.get('pool') or 'mean')}
    if op == 'argmax':
        return {'dtype': 'idx', 'values': [T.argmax(x['values'])]}
    if op == 'topk':
        return {'dtype': 'idx',
                'values': T.topk(x['values'], int(step.get('k') or 1))}
    raise RunError(f'unknown op {op!r} — {", ".join(OPS)}', 'bad_op')


def macs(step: dict, weights: dict) -> int:
    """Multiply-accumulates in one step — the honest unit of work."""
    op = step.get('op')
    if op == 'matvec':
        _v, shape, _d = weights[step['w']]
        return int(shape[0]) * int(shape[1])
    if op == 'embed':
        _v, shape, _d = weights[step['table']]
        return int(shape[1])
    return 0


# ── the run ───────────────────────────────────────────────────────


def parse_input(m: dict, raw: bytes | None = None, values=None) -> dict:
    """The input buffer from bytes or ints, per the model's io.input."""
    spec = (m['io'] or {}).get('input') or {}
    dt = spec.get('dtype') or 'i8'
    want = spec.get('len')
    if raw is not None:
        buf = decode(dt, raw)
    elif values is not None:
        buf = {'dtype': dt, 'values': [int(v) for v in values]}
    else:
        raise RunError('an inference needs an input', 'missing_arg')
    n = len(buf['values'])
    if want is not None and n != int(want):
        raise RunError(f'this model takes {want} {dt} input(s), got {n}',
                       'shape')
    if n > MAX_LANES:
        raise RunError(f'input has {n} lanes, cap {MAX_LANES}', 'too_big')
    if dt == 'i8':
        T.to_i8(buf['values'])          # range check, now not later
    return buf


def run(m: dict, inp: dict, trace: bool = True) -> dict:
    """Execute a parsed model on a parsed input buffer.

    Returns the output, the trace leaves, trace_root and the work done.
    Pure: same model bytes + same input bytes -> same everything."""
    graph = m['graph']
    if not graph:
        raise RunError(f'model {m["name"]!r} has an empty graph', 'bad_model')
    if len(graph) > MAX_STEPS:
        raise RunError(f'{len(graph)} steps, cap {MAX_STEPS}', 'too_big')
    weights = {}
    for i, t in enumerate(m['table']):
        raw = m['payload'][t['offset']:t['offset'] + t['bytes']]
        vals = T.i8(raw) if t['dtype'] == 'i8' else T.i32(raw)
        weights[t['name']] = (vals, t['shape'], t['dtype'])

    in_name = (m['io'] or {}).get('input', {}).get('buffer') or 'in'
    bufs = {in_name: inp}
    raw0 = encode(inp)
    leaves = [step_leaf(0, 'input', raw0)]
    steps_out, total = [], 0
    for i, step in enumerate(graph, start=1):
        op = step.get('op')
        if op not in OUT_DTYPE:
            raise RunError(f'step {i} has unknown op {op!r} — '
                           f'{", ".join(OPS)}', 'bad_op')
        names = step_inputs(step)
        ins = []
        for nm in names:
            if nm not in bufs:
                raise RunError(f'step {i} ({op}) reads buffer {nm!r} before '
                               'anything writes it', 'bad_model')
            ins.append(bufs[nm])
        for wn in step_weights(step):
            if wn not in weights:
                raise RunError(f'step {i} ({op}) wants tensor {wn!r}, which '
                               'the model does not carry', 'no_tensor')
        out = apply_step(step, ins, weights)
        dest = step.get('out')
        if not dest:
            raise RunError(f'step {i} ({op}) has no out= buffer',
                           'bad_model')
        bufs[dest] = out
        raw = encode(out)
        leaves.append(step_leaf(i, op, raw))
        total += macs(step, weights)
        steps_out.append({'i': i, 'op': op, 'out': dest,
                          'dtype': out['dtype'], 'lanes': len(out['values']),
                          'bytes': len(raw)})

    io = m['io'] or {}
    out_name = (io.get('output') or {}).get('buffer')
    if out_name and out_name not in bufs:
        raise RunError(f'the model names {out_name!r} as its output and no '
                       'step writes it', 'bad_model')
    out_buf = bufs[out_name] if out_name else bufs[graph[-1]['out']]
    levels = M._levels(leaves)
    res = {
        'model': m['name'], 'model_root': m['model_root'],
        'engine': ENGINE, 'steps': len(graph),
        'input': raw0.hex(), 'input_sha3': M.cid_of(raw0),
        'output': encode(out_buf).hex(),
        'output_dtype': out_buf['dtype'],
        'output_values': list(out_buf['values']),
        'output_sha3': M.cid_of(encode(out_buf)),
        'trace_root': levels[-1][0].hex(),
        'macs': total, 'step_info': steps_out,
    }
    cls = io.get('class')
    if cls and cls in bufs and bufs[cls]['values']:
        k = bufs[cls]['values'][0]
        res['class'] = k
        labels = io.get('labels') or []
        if 0 <= k < len(labels):
            res['label'] = labels[k]
    if trace:
        res['_leaves'] = leaves
        res['_levels'] = levels
        res['_buffers'] = bufs
    return res


def trace_path(levels, index: int) -> list[dict]:
    return M._path(levels, index)


def step_of_buffer(m: dict, name: str) -> int:
    """Which trace leaf holds this buffer's current value: the LAST step
    that writes it (0 = the input)."""
    in_name = (m['io'] or {}).get('input', {}).get('buffer') or 'in'
    at = 0 if name == in_name else None
    for i, step in enumerate(m['graph'], start=1):
        if step.get('out') == name:
            at = i
    if at is None:
        raise RunError(f'no step writes buffer {name!r}', 'bad_model')
    return at


def buffer_dtype_at(m: dict, name: str, upto: int) -> str:
    """The dtype of a buffer as of the step that last wrote it before
    `upto`. Static: it follows from the graph, no execution needed."""
    in_name = (m['io'] or {}).get('input', {}).get('buffer') or 'in'
    dt = (m['io'] or {}).get('input', {}).get('dtype', 'i8') \
        if name == in_name else None
    for i, step in enumerate(m['graph'], start=1):
        if i >= upto:
            break
        if step.get('out') != name:
            continue
        fixed = OUT_DTYPE[step['op']]
        if fixed:
            dt = fixed
        else:                                   # bias/relu pass dtype on
            dt = buffer_dtype_at(m, step_inputs(step)[0], i)
    if dt is None:
        raise RunError(f'buffer {name!r} has no dtype before step {upto}',
                       'bad_model')
    return dt
