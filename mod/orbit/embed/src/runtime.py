"""Running an ONNX graph with nothing but numpy.

A graph is a list of nodes in topological order, each naming its inputs and
outputs. Running it is a dictionary and a for-loop: seed the dictionary with the
initializers and the caller's feeds, then walk the nodes, and each one reads
names out of the dictionary and writes names back in.

    from src import onnxfile, runtime
    model = onnxfile.load('model.onnx')
    out = runtime.run(model, {'input_ids': ids})

Every operator this module's models use is implemented below, and nothing else
is — the point is that you can read all of them in a couple of minutes. When a
model needs an op that is not here, `run` says which one by name rather than
failing somewhere deep in a library.

`onnxruntime` is not required, and is not used here even when installed. That
is deliberate: `check.py` runs the same model through both and compares, which
is only worth anything if the two implementations share no code.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from . import onnxfile

OPS: Dict[str, Callable[..., Any]] = {}


def op(name: str) -> Callable:
    def register(fn: Callable) -> Callable:
        OPS[name] = fn
        return fn
    return register


# ── arithmetic ───────────────────────────────────────────────────────

@op('Add')
def _add(node, x, y): return x + y


@op('Sub')
def _sub(node, x, y): return x - y


@op('Mul')
def _mul(node, x, y): return x * y


@op('Div')
def _div(node, x, y): return x / y


@op('MatMul')
def _matmul(node, a, b): return a @ b


@op('Gemm')
def _gemm(node, a, b, c=None):
    alpha, beta = node.attr('alpha', 1.0), node.attr('beta', 1.0)
    if node.attr('transA', 0):
        a = a.T
    if node.attr('transB', 0):
        b = b.T
    out = alpha * (a @ b)
    return out + beta * c if c is not None else out


@op('Sqrt')
def _sqrt(node, x): return np.sqrt(x)


@op('Erf')
def _erf(node, x):
    # Abramowitz & Stegun 7.1.26 — enough for an activation, not for a library.
    s, z = np.sign(x), np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741
                + t * (-1.453152027 + t * 1.061405429))))
    return (s * (1.0 - poly * np.exp(-z * z))).astype(x.dtype)


# ── activations ──────────────────────────────────────────────────────

@op('Relu')
def _relu(node, x): return np.maximum(x, 0)


@op('Sigmoid')
def _sigmoid(node, x): return (1.0 / (1.0 + np.exp(-x))).astype(x.dtype)


@op('Tanh')
def _tanh(node, x): return np.tanh(x)


@op('Softmax')
def _softmax(node, x):
    axis = int(node.attr('axis', -1))
    shifted = x - x.max(axis=axis, keepdims=True)     # subtract the max, or exp overflows
    e = np.exp(shifted)
    return (e / e.sum(axis=axis, keepdims=True)).astype(x.dtype)


# ── shape and indexing ───────────────────────────────────────────────

@op('Gather')
def _gather(node, data, indices):
    return np.take(data, indices.astype(np.int64), axis=int(node.attr('axis', 0)))


@op('Transpose')
def _transpose(node, x):
    perm = node.attr('perm')
    return np.transpose(x, perm if perm else None)


@op('Reshape')
def _reshape(node, x, shape):
    return x.reshape([int(d) for d in np.asarray(shape).ravel()])


@op('Identity')
def _identity(node, x): return x


@op('Cast')
def _cast(node, x):
    return x.astype(onnxfile.DTYPES[int(node.attr('to', 1))])


@op('Concat')
def _concat(node, *xs):
    return np.concatenate(xs, axis=int(node.attr('axis', 0)))


def _axes(node, x):
    axes = node.attr('axes')
    return tuple(int(a) for a in axes) if axes else tuple(range(x.ndim))


@op('ReduceMean')
def _reduce_mean(node, x, axes=None):
    keep = bool(node.attr('keepdims', 1))
    return x.mean(axis=_axes(node, x), keepdims=keep).astype(x.dtype)


@op('ReduceSum')
def _reduce_sum(node, x, axes=None):
    keep = bool(node.attr('keepdims', 1))
    return x.sum(axis=_axes(node, x), keepdims=keep).astype(x.dtype)


@op('ReduceL2')
def _reduce_l2(node, x, axes=None):
    keep = bool(node.attr('keepdims', 1))
    return np.sqrt((x.astype(np.float64) ** 2).sum(axis=_axes(node, x),
                                                   keepdims=keep)).astype(x.dtype)


@op('ReduceMax')
def _reduce_max(node, x, axes=None):
    keep = bool(node.attr('keepdims', 1))
    return x.max(axis=_axes(node, x), keepdims=keep)


# ── quantization ─────────────────────────────────────────────────────
# These two are why a compressed model is still a valid ONNX model: the int8
# weights live in the file, and the graph says in the open how to get floats
# back out of them.

@op('DequantizeLinear')
def _dequantize(node, x, scale, zero_point=None):
    axis = node.attr('axis')
    zero = zero_point if zero_point is not None else np.zeros_like(scale, dtype=x.dtype)
    if np.ndim(scale) > 0 and np.size(scale) > 1:     # per-channel: line the axis up
        shape = [1] * x.ndim
        shape[int(axis if axis is not None else 1)] = -1
        scale, zero = np.asarray(scale).reshape(shape), np.asarray(zero).reshape(shape)
    return (x.astype(np.float32) - np.asarray(zero).astype(np.float32)) \
        * np.asarray(scale).astype(np.float32)


@op('QuantizeLinear')
def _quantize(node, x, scale, zero_point=None):
    zero = 0 if zero_point is None else zero_point
    dtype = np.asarray(zero).dtype if zero_point is not None else np.int8
    info = np.iinfo(dtype)
    axis = node.attr('axis')
    s, z = np.asarray(scale), np.asarray(zero)
    if s.size > 1:
        shape = [1] * x.ndim
        shape[int(axis if axis is not None else 1)] = -1
        s, z = s.reshape(shape), z.reshape(shape)
    return np.clip(np.rint(x / s) + z, info.min, info.max).astype(dtype)


# ── the loop ─────────────────────────────────────────────────────────

class Missing(NotImplementedError):
    """An op the graph needs and this runtime does not have."""


def run(model: onnxfile.Model, feeds: Dict[str, Any],
        trace: bool = False) -> Dict[str, np.ndarray]:
    """Run every node once, in file order, and return the graph's outputs."""
    values: Dict[str, Any] = dict(model.tensors())
    for name, value in feeds.items():
        values[name] = np.asarray(value)
    steps: List[Dict[str, Any]] = []

    for node in model.graph.nodes:
        fn = OPS.get(node.op_type)
        if fn is None:
            raise Missing(f'{node.op_type} — implemented ops: {sorted(OPS)}')
        args = [values[i] if i else None for i in node.inputs]
        for i in node.inputs:
            if i and i not in values:
                raise KeyError(f'{node.op_type} wants "{i}", which nothing has produced')
        started = time.perf_counter()
        out = fn(node, *args)
        elapsed = (time.perf_counter() - started) * 1000
        outs = out if isinstance(out, tuple) else (out,)
        for name, value in zip(node.outputs, outs):
            values[name] = value
        if trace:
            steps.append({'op': node.op_type, 'name': node.name, 'ms': round(elapsed, 3),
                          'out_shape': list(np.shape(outs[0])),
                          'out_dtype': str(np.asarray(outs[0]).dtype)})

    result = {v.name: values[v.name] for v in model.graph.outputs}
    if trace:
        result['__trace__'] = steps            # type: ignore[assignment]
    return result


def outputs(model: onnxfile.Model, feeds: Dict[str, Any]) -> np.ndarray:
    """The single output of a single-output graph, which most of these are."""
    result = run(model, feeds)
    return result[model.graph.outputs[0].name]


def implemented() -> List[str]:
    return sorted(OPS)


def unsupported(model: onnxfile.Model) -> List[str]:
    """Which of this model's ops are missing here — the answer before you run."""
    return sorted({n.op_type for n in model.graph.nodes if n.op_type not in OPS})
