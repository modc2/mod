"""An .onnx file, read and written from scratch.

An ONNX model is a protobuf message. Protobuf is a small enough format that a
reader and a writer for the parts a model actually uses fit in one file, and
having them here means you can open a model, see every byte accounted for, and
put a changed one back — without installing anything but numpy.

    from src import onnxfile
    model = onnxfile.load('model.onnx')
    model.tensors()                      # {name: numpy array}
    model.graph.nodes                    # the ops, in order
    onnxfile.save(model, 'smaller.onnx')

The wire format, in three sentences: every field is a varint tag followed by a
payload, the tag is `(field_number << 3) | wire_type`, and wire type 0 is a
varint, 2 is a length-prefixed blob, 5 is four bytes. Repeated numeric fields
are usually *packed* — one blob holding all the values — so a parser has to
accept both shapes for the same field, which is the one place this file gets
fiddly.

Everything else is bookkeeping: which field number means what in which message.
Those numbers come from onnx.proto and are written down in the constants below
so you can check them against it.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

# ── onnx.proto field numbers ─────────────────────────────────────────
# ModelProto
M_IR_VERSION, M_OPSET, M_PRODUCER, M_GRAPH = 1, 8, 2, 7
# GraphProto
G_NODE, G_NAME, G_INITIALIZER, G_INPUT, G_OUTPUT, G_VALUE_INFO = 1, 2, 5, 11, 12, 13
# NodeProto
N_INPUT, N_OUTPUT, N_NAME, N_OP_TYPE, N_ATTRIBUTE, N_DOMAIN = 1, 2, 3, 4, 5, 7
# AttributeProto
A_NAME, A_F, A_I, A_S, A_T, A_FLOATS, A_INTS, A_STRINGS, A_TYPE = 1, 2, 3, 4, 5, 7, 8, 9, 20
# TensorProto
T_DIMS, T_DATA_TYPE, T_FLOAT, T_INT32, T_STRING, T_INT64, T_NAME, T_RAW, T_DOUBLE, T_UINT64 = (
    1, 2, 4, 5, 6, 7, 8, 9, 10, 11)

# TensorProto.DataType → numpy dtype. The numbers are ONNX's, not numpy's.
DTYPES: Dict[int, Any] = {
    1: np.float32, 2: np.uint8, 3: np.int8, 4: np.uint16, 5: np.int16,
    6: np.int32, 7: np.int64, 9: np.bool_, 10: np.float16, 11: np.float64,
    12: np.uint32, 13: np.uint64,
}
ONNX_DTYPE: Dict[Any, int] = {np.dtype(v): k for k, v in DTYPES.items()}

# AttributeProto.AttributeType
AT_FLOAT, AT_INT, AT_STRING, AT_TENSOR, AT_FLOATS, AT_INTS, AT_STRINGS = 1, 2, 3, 4, 6, 7, 8


# ── protobuf, the whole of it ────────────────────────────────────────

def _varint(buf: bytes, pos: int) -> Tuple[int, int]:
    """Read one base-128 varint. Seven bits per byte, high bit = continue."""
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _put_varint(value: int) -> bytes:
    if value < 0:                     # two's complement, 64-bit, as protobuf does it
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def parse(buf: bytes) -> Dict[int, List[Any]]:
    """A protobuf message → {field number: [payloads]}.

    Payloads are bytes for wire type 2 and ints for the numeric wire types.
    No schema is consulted, which is why the same function reads every message
    in the file.
    """
    fields: Dict[int, List[Any]] = {}
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 2:
            length, pos = _varint(buf, pos)
            value, pos = buf[pos:pos + length], pos + length
        elif wire == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        elif wire == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        else:
            raise ValueError(f'unsupported protobuf wire type {wire}')
        fields.setdefault(number, []).append(value)
    return fields


def _field(number: int, payload: bytes) -> bytes:
    return _put_varint(number << 3 | 2) + _put_varint(len(payload)) + payload


def _varint_field(number: int, value: int) -> bytes:
    return _put_varint(number << 3) + _put_varint(int(value))


def _packed_ints(values: Iterable[int]) -> bytes:
    return b''.join(_put_varint(int(v)) for v in values)


def _read_packed_varints(payloads: List[Any]) -> List[int]:
    """Repeated int64, packed or not — protobuf allows either on the wire."""
    out: List[int] = []
    for payload in payloads:
        if isinstance(payload, int):
            # Negative int64s go on the wire as their unsigned 64-bit form, so
            # anything past 2^63 is a negative number wearing a large hat.
            out.append(payload if payload < (1 << 63) else payload - (1 << 64))
            continue
        pos = 0
        while pos < len(payload):
            value, pos = _varint(payload, pos)
            out.append(value if value < (1 << 63) else value - (1 << 64))
    return out


def _text(payload: Any) -> str:
    return payload.decode('utf-8') if isinstance(payload, bytes) else str(payload)


# ── the model ────────────────────────────────────────────────────────

class Tensor:
    """A named array with an ONNX dtype. Weights and constants are these."""

    def __init__(self, name: str, array: np.ndarray):
        self.name = name
        self.array = array

    @property
    def nbytes(self) -> int:
        return int(self.array.nbytes)

    def encode(self) -> bytes:
        # reshape after ascontiguousarray: it promotes a 0-d scalar to shape (1,),
        # which would write `dims: 1` onto a rank-0 tensor. Real models are full of
        # scalar Constants feeding Concat and Unsqueeze, and a rank that changes
        # under a round trip breaks shape inference in a graph nobody here wrote.
        array = np.ascontiguousarray(self.array).reshape(self.array.shape)
        dtype = ONNX_DTYPE.get(array.dtype)
        if dtype is None:
            raise ValueError(f'no ONNX dtype for {array.dtype}')
        body = b''.join(_varint_field(T_DIMS, d) for d in array.shape)
        body += _varint_field(T_DATA_TYPE, dtype)
        if self.name:                               # an empty name is not a name
            body += _field(T_NAME, self.name.encode())
        body += _field(T_RAW, array.tobytes())      # raw_data: always, for every dtype
        return body

    @staticmethod
    def decode(payload: bytes) -> 'Tensor':
        f = parse(payload)
        dims = _read_packed_varints(f.get(T_DIMS, []))
        dtype = DTYPES[f[T_DATA_TYPE][0]] if T_DATA_TYPE in f else np.float32
        name = _text(f[T_NAME][0]) if T_NAME in f else ''
        if T_RAW in f and f[T_RAW][0]:
            array = np.frombuffer(f[T_RAW][0], dtype=dtype).reshape(dims)
        else:                                        # the typed-field spelling
            array = _decode_typed(f, dtype, dims)
        return Tensor(name, np.array(array))         # copy: frombuffer is read-only

    def __repr__(self) -> str:
        return f'<Tensor {self.name} {self.array.dtype} {tuple(self.array.shape)}>'


def _decode_typed(f: Dict[int, List[Any]], dtype: Any, dims: List[int]) -> np.ndarray:
    """Tensors that spell their values out in float_data / int64_data / ..."""
    if T_FLOAT in f:
        flat = np.array([struct.unpack('<f', p)[0] for p in f[T_FLOAT]], dtype=np.float32) \
            if isinstance(f[T_FLOAT][0], bytes) and len(f[T_FLOAT][0]) == 4 \
            else np.frombuffer(b''.join(f[T_FLOAT]), dtype=np.float32)
    elif T_INT64 in f:
        flat = np.array(_read_packed_varints(f[T_INT64]), dtype=np.int64)
    elif T_INT32 in f:
        flat = np.array(_read_packed_varints(f[T_INT32]), dtype=np.int32)
    elif T_DOUBLE in f:
        flat = np.frombuffer(b''.join(f[T_DOUBLE]), dtype=np.float64)
    else:
        flat = np.zeros(int(np.prod(dims)) if dims else 0, dtype=dtype)
    return flat.astype(dtype).reshape(dims) if dims else flat.astype(dtype)


class Attribute:
    def __init__(self, name: str, value: Any, kind: int):
        self.name, self.value, self.kind = name, value, kind

    @staticmethod
    def ints(name: str, values: Iterable[int]) -> 'Attribute':
        return Attribute(name, [int(v) for v in values], AT_INTS)

    @staticmethod
    def i(name: str, value: int) -> 'Attribute':
        return Attribute(name, int(value), AT_INT)

    @staticmethod
    def f(name: str, value: float) -> 'Attribute':
        return Attribute(name, float(value), AT_FLOAT)

    @staticmethod
    def s(name: str, value: str) -> 'Attribute':
        return Attribute(name, value, AT_STRING)

    def encode(self) -> bytes:
        body = _field(A_NAME, self.name.encode()) + _varint_field(A_TYPE, self.kind)
        if self.kind == AT_INT:
            body += _varint_field(A_I, self.value)
        elif self.kind == AT_FLOAT:
            body += _put_varint(A_F << 3 | 5) + struct.pack('<f', self.value)
        elif self.kind == AT_STRING:
            body += _field(A_S, self.value.encode())
        elif self.kind == AT_INTS:
            body += _field(A_INTS, _packed_ints(self.value))
        elif self.kind == AT_FLOATS:
            body += _field(A_FLOATS, b''.join(struct.pack('<f', v) for v in self.value))
        elif self.kind == AT_TENSOR:
            body += _field(A_T, self.value.encode())
        else:
            raise ValueError(f'attribute type {self.kind} not written by this file')
        return body

    @staticmethod
    def decode(payload: bytes) -> 'Attribute':
        f = parse(payload)
        name = _text(f[A_NAME][0]) if A_NAME in f else ''
        kind = f[A_TYPE][0] if A_TYPE in f else 0
        if kind == AT_INT:
            value: Any = _read_packed_varints(f.get(A_I, [0]))[0]
        elif kind == AT_FLOAT:
            value = struct.unpack('<f', f[A_F][0])[0] if A_F in f else 0.0
        elif kind == AT_STRING:
            value = _text(f[A_S][0]) if A_S in f else ''
        elif kind == AT_INTS:
            value = _read_packed_varints(f.get(A_INTS, []))
        elif kind == AT_FLOATS:
            value = list(np.frombuffer(b''.join(f.get(A_FLOATS, [b''])), dtype=np.float32))
        elif kind == AT_TENSOR:
            value = Tensor.decode(f[A_T][0])
        else:
            value = None
        return Attribute(name, value, kind)


class Node:
    def __init__(self, op_type: str, inputs: List[str], outputs: List[str],
                 name: str = '', attributes: Optional[List[Attribute]] = None):
        self.op_type, self.inputs, self.outputs = op_type, list(inputs), list(outputs)
        self.name = name or (outputs[0] if outputs else op_type)
        self.attributes = list(attributes or [])

    def attr(self, name: str, default: Any = None) -> Any:
        for a in self.attributes:
            if a.name == name:
                return a.value
        return default

    def encode(self) -> bytes:
        body = b''.join(_field(N_INPUT, i.encode()) for i in self.inputs)
        body += b''.join(_field(N_OUTPUT, o.encode()) for o in self.outputs)
        body += _field(N_NAME, self.name.encode())
        body += _field(N_OP_TYPE, self.op_type.encode())
        body += b''.join(_field(N_ATTRIBUTE, a.encode()) for a in self.attributes)
        return body

    @staticmethod
    def decode(payload: bytes) -> 'Node':
        f = parse(payload)
        return Node(
            op_type=_text(f[N_OP_TYPE][0]) if N_OP_TYPE in f else '',
            inputs=[_text(p) for p in f.get(N_INPUT, [])],
            outputs=[_text(p) for p in f.get(N_OUTPUT, [])],
            name=_text(f[N_NAME][0]) if N_NAME in f else '',
            attributes=[Attribute.decode(p) for p in f.get(N_ATTRIBUTE, [])],
        )

    def __repr__(self) -> str:
        return f'<{self.op_type} {self.inputs} -> {self.outputs}>'


class ValueInfo:
    """A graph input or output: a name, a dtype, and a shape that may be symbolic."""

    def __init__(self, name: str, dtype: int = 1, shape: Optional[List[Any]] = None):
        self.name, self.dtype, self.shape = name, dtype, list(shape or [])

    def encode(self) -> bytes:
        dims = b''
        for dim in self.shape:
            dims += _field(1, _varint_field(1, dim) if isinstance(dim, int)
                           else _field(2, str(dim).encode()))
        tensor_type = _varint_field(1, self.dtype) + _field(2, dims)
        return _field(1, self.name.encode()) + _field(2, _field(1, tensor_type))

    @staticmethod
    def decode(payload: bytes) -> 'ValueInfo':
        f = parse(payload)
        name = _text(f[1][0]) if 1 in f else ''
        dtype, shape = 1, []
        if 2 in f:
            tp = parse(f[2][0])
            if 1 in tp:
                tt = parse(tp[1][0])
                dtype = tt[1][0] if 1 in tt else 1
                if 2 in tt:
                    for dim in parse(tt[2][0]).get(1, []):
                        d = parse(dim)
                        shape.append(_read_packed_varints(d[1])[0] if 1 in d
                                     else (_text(d[2][0]) if 2 in d else None))
        return ValueInfo(name, dtype, shape)


class Graph:
    def __init__(self, name: str = 'graph', nodes: Optional[List[Node]] = None,
                 initializers: Optional[List[Tensor]] = None,
                 inputs: Optional[List[ValueInfo]] = None,
                 outputs: Optional[List[ValueInfo]] = None):
        self.name = name
        self.nodes = list(nodes or [])
        self.initializers = list(initializers or [])
        self.inputs = list(inputs or [])
        self.outputs = list(outputs or [])

    def encode(self) -> bytes:
        body = b''.join(_field(G_NODE, n.encode()) for n in self.nodes)
        body += _field(G_NAME, self.name.encode())
        body += b''.join(_field(G_INITIALIZER, t.encode()) for t in self.initializers)
        body += b''.join(_field(G_INPUT, v.encode()) for v in self.inputs)
        body += b''.join(_field(G_OUTPUT, v.encode()) for v in self.outputs)
        return body

    @staticmethod
    def decode(payload: bytes) -> 'Graph':
        f = parse(payload)
        return Graph(
            name=_text(f[G_NAME][0]) if G_NAME in f else 'graph',
            nodes=[Node.decode(p) for p in f.get(G_NODE, [])],
            initializers=[Tensor.decode(p) for p in f.get(G_INITIALIZER, [])],
            inputs=[ValueInfo.decode(p) for p in f.get(G_INPUT, [])],
            outputs=[ValueInfo.decode(p) for p in f.get(G_OUTPUT, [])],
        )


class Model:
    def __init__(self, graph: Graph, opset: int = 17, ir_version: int = 8,
                 producer: str = 'mod/embed'):
        self.graph, self.opset, self.ir_version, self.producer = (
            graph, opset, ir_version, producer)

    def tensors(self) -> Dict[str, np.ndarray]:
        return {t.name: t.array for t in self.graph.initializers}

    def tensor(self, name: str) -> Optional[Tensor]:
        return next((t for t in self.graph.initializers if t.name == name), None)

    def weight_bytes(self) -> int:
        return sum(t.nbytes for t in self.graph.initializers)

    def encode(self) -> bytes:
        body = _varint_field(M_IR_VERSION, self.ir_version)
        body += _field(M_PRODUCER, self.producer.encode())
        body += _field(M_GRAPH, self.graph.encode())
        body += _field(M_OPSET, _field(1, b'') + _varint_field(2, self.opset))
        return body

    @staticmethod
    def decode(buf: bytes) -> 'Model':
        f = parse(buf)
        opset = 17
        for payload in f.get(M_OPSET, []):
            entry = parse(payload)
            if not entry.get(1, [b''])[0]:            # the default (ai.onnx) domain
                opset = _read_packed_varints(entry.get(2, [17]))[0]
        return Model(
            graph=Graph.decode(f[M_GRAPH][0]),
            opset=opset,
            ir_version=_read_packed_varints(f.get(M_IR_VERSION, [8]))[0],
            producer=_text(f[M_PRODUCER][0]) if M_PRODUCER in f else '',
        )

    def summary(self) -> Dict[str, Any]:
        ops: Dict[str, int] = {}
        for node in self.graph.nodes:
            ops[node.op_type] = ops.get(node.op_type, 0) + 1
        by_dtype: Dict[str, Dict[str, int]] = {}
        for t in self.graph.initializers:
            slot = by_dtype.setdefault(str(t.array.dtype), {'tensors': 0, 'bytes': 0})
            slot['tensors'] += 1
            slot['bytes'] += t.nbytes
        return {
            'graph': self.graph.name,
            'opset': self.opset,
            'inputs': [{'name': v.name, 'shape': v.shape} for v in self.graph.inputs],
            'outputs': [{'name': v.name, 'shape': v.shape} for v in self.graph.outputs],
            'ops': ops,
            'parameters': int(sum(t.array.size for t in self.graph.initializers)),
            'weight_bytes': self.weight_bytes(),
            'weights_by_dtype': by_dtype,
        }


def load(path: str | Path) -> Model:
    return Model.decode(Path(path).read_bytes())


def save(model: Model, path: str | Path) -> int:
    data = model.encode()
    Path(path).write_bytes(data)
    return len(data)
