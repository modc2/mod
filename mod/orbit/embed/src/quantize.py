"""Making numbers smaller, and measuring what that cost.

Quantization is one idea: a weight tensor does not use the range float32 offers,
so store each number as a small integer plus a shared scale.

    scale = max|w| / 127            q = round(w / scale)            w' = q * scale

That is the whole of symmetric int8. Everything else in this file is either the
asymmetric version (a zero point, for tensors that sit off-centre), the
per-channel version (one scale per column instead of one for the tensor, which
is where most of the accuracy comes back), or a measurement of the error.

The measurement is the point. A compressor that reports only the new file size
is telling you the easy half; `error()` is the other half, and every function
here that changes numbers returns it alongside them.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np

INT8 = np.iinfo(np.int8)


# ── the arithmetic ───────────────────────────────────────────────────

def _over(w: np.ndarray, axis: Optional[int]) -> Optional[Tuple[int, ...]]:
    """Which axes to reduce so that one scale survives per index along `axis`.

    ONNX's `axis` names the axis the scales are *indexed by* — a per-column
    scale for a (rows, columns) weight is `axis=1` and has `columns` entries —
    while numpy's `axis=` names the axis to reduce *over*. They are opposites,
    and mixing them up produces a broadcast error at best and silently wrong
    scales at worst.
    """
    if axis is None:
        return None
    return tuple(i for i in range(w.ndim) if i != axis)


def levels(bits: int) -> int:
    """The largest magnitude `bits` signed bits can hold: 127 at 8, 7 at 4."""
    return (1 << (bits - 1)) - 1


def scale_symmetric(w: np.ndarray, axis: Optional[int] = None,
                    bits: int = 8) -> np.ndarray:
    """One scale (or one per channel) that maps the largest weight onto the top level."""
    over = _over(w, axis)
    peak = np.abs(w).max(axis=over, keepdims=True) if over is not None \
        else np.abs(w).max()
    peak = np.asarray(peak, dtype=np.float32)
    return np.where(peak > 0, peak / levels(bits), np.float32(1.0)).astype(np.float32)


def quantize_int8(w: np.ndarray, axis: Optional[int] = None, symmetric: bool = True,
                  bits: int = 8) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """float32 → int8 + scale + zero point. `axis` picks the per-channel axis.

    Symmetric keeps zero at zero, which matters for weights that are pruned or
    padded. Asymmetric fits the range tighter when the weights are lopsided, and
    costs one extra small array.

    `bits` below 8 still comes back in an int8 container — the values use fewer
    levels, the bytes do not shrink. That is the shape needed to answer "how bad
    would 4-bit be here?" without also needing somewhere to put 4-bit numbers.
    """
    w = np.asarray(w, dtype=np.float32)
    over = _over(w, axis)
    top = levels(bits)
    if symmetric:
        scale = scale_symmetric(w, axis, bits)
        zero = np.zeros_like(scale, dtype=np.int8)
    else:
        lo = w.min(axis=over, keepdims=True) if over is not None else w.min()
        hi = w.max(axis=over, keepdims=True) if over is not None else w.max()
        lo, hi = np.minimum(lo, 0.0), np.maximum(hi, 0.0)
        scale = np.asarray((hi - lo) / (2 * top + 1), dtype=np.float32)
        scale = np.where(scale > 0, scale, np.float32(1.0)).astype(np.float32)
        zero = np.clip(np.rint(-top - 1 - lo / scale), -top - 1, top).astype(np.int8)
    q = np.clip(np.rint(w / scale) + zero.astype(np.float32), -top - 1, top)
    return q.astype(np.int8), np.squeeze(scale), np.squeeze(zero)


def dequantize(q: np.ndarray, scale: np.ndarray, zero: np.ndarray,
               axis: Optional[int] = None) -> np.ndarray:
    """int8 + scale → float32. The lossy step already happened; this is exact."""
    s, z = np.asarray(scale, dtype=np.float32), np.asarray(zero, dtype=np.float32)
    if s.size > 1 and axis is not None:
        shape = [1] * q.ndim
        shape[axis] = -1
        s, z = s.reshape(shape), z.reshape(shape)
    return (q.astype(np.float32) - z) * s


def pack_int4(w: np.ndarray) -> Tuple[np.ndarray, np.ndarray, Tuple[int, ...]]:
    """Two weights per byte: sixteen levels, and half of int8's bytes again.

    ONNX has no 4-bit tensor before opset 21, so this is not written into the
    models here — it is in this file because it is the next thing anyone asks
    about after int8, and because packing is easier to believe once you have
    seen the shifts.
    """
    w = np.asarray(w, dtype=np.float32)
    scale = np.float32(np.abs(w).max() / 7.0 or 1.0)
    q = np.clip(np.rint(w.ravel() / scale), -8, 7).astype(np.int8)
    if q.size % 2:
        q = np.append(q, np.int8(0))
    nibbles = (q & 0x0F).astype(np.uint8)
    packed = (nibbles[0::2] | (nibbles[1::2] << 4)).astype(np.uint8)
    return packed, scale, w.shape


def unpack_int4(packed: np.ndarray, scale: np.ndarray,
                shape: Tuple[int, ...]) -> np.ndarray:
    low = (packed & 0x0F).astype(np.int8)
    high = (packed >> 4).astype(np.int8)
    q = np.empty(low.size + high.size, dtype=np.int8)
    q[0::2], q[1::2] = low, high
    q = np.where(q > 7, q - 16, q)                 # sign-extend the nibble
    return (q[:int(np.prod(shape))].astype(np.float32) * scale).reshape(shape)


# ── the measurement ──────────────────────────────────────────────────

def error(original: np.ndarray, restored: np.ndarray) -> Dict[str, float]:
    """How far the round trip moved the numbers, four ways.

    `rel_rmse` is the one to watch across tensors of different magnitudes; a
    weight matrix with an rel_rmse near 0.5% is usually invisible downstream,
    and one near 5% usually is not.
    """
    a = np.asarray(original, dtype=np.float64).ravel()
    b = np.asarray(restored, dtype=np.float64).ravel()
    diff = a - b
    scale = float(np.sqrt((a ** 2).mean())) or 1.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return {
        'max_abs': float(np.abs(diff).max()) if diff.size else 0.0,
        'rmse': float(np.sqrt((diff ** 2).mean())) if diff.size else 0.0,
        'rel_rmse': float(np.sqrt((diff ** 2).mean()) / scale) if diff.size else 0.0,
        'cosine': float(a @ b / denom) if denom else 1.0,
    }


def round_trip(w: np.ndarray, method: str = 'int8', axis: Optional[int] = None,
               symmetric: bool = True) -> Dict[str, Any]:
    """Compress one tensor and hand back the numbers, the error and the bytes."""
    w = np.asarray(w, dtype=np.float32)
    if method == 'float16':
        small = w.astype(np.float16)
        restored, stored = small.astype(np.float32), int(small.nbytes)
    elif method == 'int8':
        q, scale, zero = quantize_int8(w, axis=axis, symmetric=symmetric)
        restored = dequantize(q, scale, zero, axis)
        stored = int(q.nbytes + np.asarray(scale).nbytes + np.asarray(zero).nbytes)
    elif method == 'int4':
        packed, scale, shape = pack_int4(w)
        restored = unpack_int4(packed, scale, shape)
        stored = int(packed.nbytes + 4)
    elif method == 'float32':
        restored, stored = w.copy(), int(w.nbytes)
    else:
        raise ValueError(f'unknown method {method!r} — float32, float16, int8, int4')
    return {
        'method': method,
        'per_channel': axis is not None and method == 'int8',
        'original_bytes': int(w.nbytes),
        'stored_bytes': stored,
        'ratio': round(w.nbytes / stored, 2) if stored else 0.0,
        'error': error(w, restored),
        'restored': restored,
    }


def compare(w: np.ndarray, axis: Optional[int] = 1) -> Dict[str, Any]:
    """Every method on one tensor, side by side. This is the teaching call."""
    rows = []
    for method, kwargs in (('float32', {}), ('float16', {}), ('int8', {}),
                           ('int8', {'axis': axis}), ('int4', {})):
        result = round_trip(w, method, **kwargs)
        result.pop('restored')
        rows.append(result)
    return {'shape': list(np.shape(w)), 'parameters': int(np.size(w)), 'methods': rows}
