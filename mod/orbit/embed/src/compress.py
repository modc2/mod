"""Compressing a model file, and saying honestly what it cost.

Two rewrites, both of which leave a valid ONNX file that any runtime can load:

`float16`  each float32 initializer is stored as float16 and a `Cast` node puts
           it back to float32 before the op that consumes it. Half the bytes,
           and an error small enough that it rarely shows up downstream.

`int8`     each float32 initializer is stored as int8 with a scale, and a
           `DequantizeLinear` node — an ONNX operator, not an invention of this
           module — turns it back into floats at load. A quarter of the bytes.
           Per-channel (a scale per output column) costs a few hundred extra
           floats and recovers most of the accuracy that per-tensor loses.

Both are **weight-only**: the file is smaller, the arithmetic still happens in
float. That is the honest description, and it is worth being clear about,
because "int8 model" is usually said about something else — a model whose matrix
multiplies also run in integers, which is faster but a much larger change. This
module does the first and does not claim the second.

Small tensors are left alone. A 64-element bias quantized saves 192 bytes and
buys real error; the threshold is in `MIN_ELEMENTS` and it is a judgement call,
not a law.

What is *not* here: retraining. Every method in this file is post-training, so
the ceiling is whatever the float model already knew.
"""
from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from . import onnxfile, quantize
from .onnxfile import Attribute, Model, Node, Tensor

MIN_ELEMENTS = 1024          # below this, quantizing a tensor is not worth the error
METHODS = ('float32', 'float16', 'int8', 'int8-per-channel', 'int4-sim')
BITS = {'int8': 8, 'int8-per-channel': 8, 'int4-sim': 4}


def compress(model: Model, method: str = 'int8-per-channel',
             min_elements: int = MIN_ELEMENTS) -> Dict[str, Any]:
    """Rewrite every big float initializer. Returns the new model and a report."""
    if method not in METHODS:
        raise ValueError(f'unknown method {method!r} — one of {METHODS}')
    graph = model.graph
    consumers = _consumers(graph)
    kept: List[Tensor] = []
    inserted: Dict[str, List[Node]] = {}
    rows: List[Dict[str, Any]] = []

    for tensor in graph.initializers:
        w = tensor.array
        skip = (w.dtype != np.float32 or w.size < min_elements or method == 'float32')
        if skip:
            kept.append(tensor)
            rows.append({'tensor': tensor.name, 'shape': list(w.shape),
                         'action': 'left as float32',
                         'bytes_before': tensor.nbytes, 'bytes_after': tensor.nbytes})
            continue

        internal = f'{tensor.name}_compressed'
        rows_extra: Dict[str, Any] = {}
        if method == 'float16':
            small = w.astype(np.float16)
            kept.append(Tensor(internal, small))
            node = Node('Cast', [internal], [tensor.name],
                        name=f'{tensor.name}/cast',
                        attributes=[Attribute.i('to', 1)])
            after = int(small.nbytes)
            restored = small.astype(np.float32)
        else:
            bits = BITS[method]
            axis = _channel_axis(w, tensor.name, consumers) \
                if method == 'int8-per-channel' else None
            q, scale, zero = quantize.quantize_int8(w, axis=axis, bits=bits)
            scale = np.atleast_1d(np.asarray(scale, dtype=np.float32)) if axis is not None \
                else np.asarray(scale, dtype=np.float32)
            zero_t = np.atleast_1d(np.asarray(zero, dtype=np.int8)) if axis is not None \
                else np.asarray(zero, dtype=np.int8)
            kept += [Tensor(internal, q),
                     Tensor(f'{internal}_scale', scale),
                     Tensor(f'{internal}_zero', zero_t)]
            attributes = [Attribute.i('axis', axis)] if axis is not None else []
            node = Node('DequantizeLinear',
                        [internal, f'{internal}_scale', f'{internal}_zero'],
                        [tensor.name], name=f'{tensor.name}/dequant',
                        attributes=attributes)
            after = int(q.nbytes + scale.nbytes + zero_t.nbytes)
            if bits < 8:
                # The file keeps int8 bytes; this is what packing would weigh.
                after_packed = int(np.ceil(q.size * bits / 8) + scale.nbytes
                                   + zero_t.nbytes)
                rows_extra = {'bytes_if_packed': after_packed, 'bits': bits}
            restored = quantize.dequantize(q, scale, zero_t, axis)

        inserted.setdefault(_first_consumer(tensor.name, graph), []).append(node)
        rows.append({
            'tensor': tensor.name, 'shape': list(w.shape),
            'action': method + (f' (axis {_channel_axis(w, tensor.name, consumers)})'
                                if method == 'int8-per-channel' else ''),
            'bytes_before': int(w.nbytes), 'bytes_after': after,
            'error': quantize.error(w, restored), **rows_extra,
        })

    nodes: List[Node] = []
    for node in graph.nodes:                          # dequant goes before its consumer
        nodes.extend(inserted.pop(node.name, []))
        nodes.append(node)
    for leftovers in inserted.values():               # unconsumed weights, if any
        nodes = leftovers + nodes

    smaller = Model(onnxfile.Graph(graph.name, nodes, kept, graph.inputs, graph.outputs),
                    opset=max(model.opset, 13), ir_version=model.ir_version,
                    producer='mod/embed')
    before = sum(r['bytes_before'] for r in rows)
    after = sum(r['bytes_after'] for r in rows)
    return {
        'method': method,
        'tensors': rows,
        'weight_bytes_before': before,
        'weight_bytes_after': after,
        'ratio': round(before / after, 2) if after else 1.0,
        'model': smaller,
    }


def compress_file(source: str | Path, target: str | Path,
                  method: str = 'int8-per-channel',
                  min_elements: int = MIN_ELEMENTS) -> Dict[str, Any]:
    """The file version: bytes on disk before and after, gzip included."""
    source, target = Path(source), Path(target)
    model = onnxfile.load(source)
    result = compress(model, method, min_elements)
    written = onnxfile.save(result.pop('model'), target)
    return {
        **result,
        'source': str(source), 'target': str(target),
        'file_bytes_before': source.stat().st_size,
        'file_bytes_after': written,
        'file_ratio': round(source.stat().st_size / written, 2) if written else 1.0,
        'gzip_bytes_before': gzipped(source.read_bytes()),
        'gzip_bytes_after': gzipped(target.read_bytes()),
    }


def gzipped(payload: bytes) -> int:
    """What the file would weigh over the wire. Worth knowing before quantizing:
    on a float32 model gzip saves almost nothing, because the low bits of a
    float are noise and noise does not compress."""
    return len(gzip.compress(payload, 6))


# ── graph reading ────────────────────────────────────────────────────

def _consumers(graph) -> Dict[str, List[Node]]:
    out: Dict[str, List[Node]] = {}
    for node in graph.nodes:
        for name in node.inputs:
            out.setdefault(name, []).append(node)
    return out


def _first_consumer(name: str, graph) -> str:
    for node in graph.nodes:
        if name in node.inputs:
            return node.name
    return graph.nodes[0].name if graph.nodes else ''


def _channel_axis(w: np.ndarray, name: str, consumers: Dict[str, List[Node]]
                  ) -> Optional[int]:
    """Which axis gets its own scale.

    For a MatMul weight of shape (in, out) the output columns are what a later
    op sees separately, so the scale goes per column — axis 1. For an embedding
    table the rows are the separable thing — axis 0. Anything else gets one
    scale for the whole tensor, because guessing wrong is worse than not
    guessing.
    """
    if w.ndim != 2:
        return None
    for node in consumers.get(name, []):
        if node.op_type in ('MatMul', 'Gemm'):
            return 1 if node.inputs.index(name) == 1 else 0
        if node.op_type == 'Gather':
            return 0
    return None
