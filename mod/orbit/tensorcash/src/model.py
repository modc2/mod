"""tcm1 — a model as one content-addressed blob with an openable commitment.

    'TCM1' | u32 header_len | header JSON | tensor bytes ...

The header names the tensors (dtype, shape, offset, length) and the graph
that consumes them; the payload is the raw weight bytes, nothing else. One
blob, one hash, no directory of files that can drift apart.

WHY A MERKLE TREE AND NOT JUST THE HASH
    SHA3-256 over the whole blob already identifies the model, and that is
    what the chain anchors. But a verifier who wants to check ONE layer of
    a hundred should not have to download the other ninety-nine. So the
    payload is also committed as a Merkle tree over 1 KiB chunks:

        leaf(i, j)   = SHA3('tcm1-leaf', u32 tensor_i, u32 chunk_j, bytes)
        node(l, r)   = SHA3('tcm1-node', l, r)
        weights_root = root over every chunk of every tensor, in order
        model_root   = SHA3('tcm1-model', header_sha3, weights_root)

    An odd level promotes its last node unchanged rather than hashing it
    with itself: hashing a node with itself makes a tree where a leaf and
    its duplicate are indistinguishable, which is the classic second
    preimage bug in Merkle tree design.

    With that, `open_tensor` hands over one layer's weights plus ~log2(n)
    hashes, and `check_chunks` proves those bytes are the committed model's
    without ever seeing the rest of it. That is what makes the single-step
    challenge in challenge.py cheap for a deep network.

model_root is what a receipt commits to and what the chain pins, and it is
derived from the header too — so changing the graph while keeping the
weights (or the reverse) is a different model, as it should be.
"""

from __future__ import annotations

import hashlib
import json
import struct

import tensors as T

MAGIC = b'TCM1'
CHUNK = 1024
DTYPES = {'i8': 1, 'i32': 4}
MAX_BLOB = 64 << 20


class ModelError(ValueError):
    def __init__(self, message, code='bad_model', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def sha3(*parts: bytes) -> bytes:
    h = hashlib.sha3_256()
    for p in parts:
        h.update(p)
    return h.digest()


def cid_of(b: bytes) -> str:
    return hashlib.sha3_256(b).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()


# ── the tree ──────────────────────────────────────────────────────


def leaf_hash(ti: int, ci: int, data: bytes) -> bytes:
    return sha3(b'tcm1-leaf', struct.pack('<II', ti, ci), data)


def node_hash(left: bytes, right: bytes) -> bytes:
    return sha3(b'tcm1-node', left, right)


def _levels(leaves: list[bytes]) -> list[list[bytes]]:
    """Every level of the tree, leaves first. A lone node at the end of an
    odd level is PROMOTED, not paired with itself."""
    if not leaves:
        return [[sha3(b'tcm1-empty')]]
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        cur, nxt = levels[-1], []
        for i in range(0, len(cur) - 1, 2):
            nxt.append(node_hash(cur[i], cur[i + 1]))
        if len(cur) % 2:
            nxt.append(cur[-1])
        levels.append(nxt)
    return levels


def _path(levels: list[list[bytes]], index: int) -> list[dict]:
    """Sibling hashes from a leaf to the root, as {side, hash}."""
    path, i = [], index
    for level in levels[:-1]:
        if i % 2:
            path.append({'side': 'left', 'hash': level[i - 1].hex()})
        elif i + 1 < len(level):
            path.append({'side': 'right', 'hash': level[i + 1].hex()})
        else:
            i //= 2            # promoted: no sibling, no hash to carry
            continue
        i //= 2
    return path


def root_from_path(leaf: bytes, path: list[dict]) -> bytes:
    h = leaf
    for step in path:
        sib = bytes.fromhex(step['hash'])
        h = node_hash(sib, h) if step.get('side') == 'left' \
            else node_hash(h, sib)
    return h


# ── the blob ──────────────────────────────────────────────────────


def build(name: str, tensors: list[dict], graph: list[dict], io: dict,
          meta: dict | None = None) -> bytes:
    """tensors = [{name, dtype, shape, data: bytes}] in graph order."""
    payload, table = bytearray(), []
    for t in tensors:
        data = bytes(t['data'])
        dt = t.get('dtype', 'i8')
        if dt not in DTYPES:
            raise ModelError(f'unknown dtype {dt!r} — {", ".join(DTYPES)}')
        n = 1
        for d in t['shape']:
            n *= int(d)
        if len(data) != n * DTYPES[dt]:
            raise ModelError(f'tensor {t["name"]!r} is {len(data)} bytes, '
                             f'shape {t["shape"]} dtype {dt} needs '
                             f'{n * DTYPES[dt]}', 'shape')
        table.append({'name': t['name'], 'dtype': dt,
                      'shape': [int(d) for d in t['shape']],
                      'offset': len(payload), 'bytes': len(data)})
        payload += data
    header = {'v': 'tcm1', 'name': name, 'tensors': table, 'graph': graph,
              'io': io, 'chunk': CHUNK, 'engine': 'tcq1',
              'meta': meta or {}}
    hb = canonical(header)
    return MAGIC + struct.pack('<I', len(hb)) + hb + bytes(payload)


def parse(blob: bytes) -> dict:
    """Open a blob: {header, payload, model_root, weights_root, ...}."""
    if len(blob) > MAX_BLOB:
        raise ModelError(f'model is {len(blob)} bytes, cap {MAX_BLOB}',
                         'too_big', 413)
    if len(blob) < 8 or blob[:4] != MAGIC:
        raise ModelError('not a tcm1 model (bad magic)')
    (hlen,) = struct.unpack('<I', blob[4:8])
    if hlen > len(blob) - 8:
        raise ModelError('header length runs past the end of the blob')
    try:
        header = json.loads(blob[8:8 + hlen].decode())
    except (ValueError, UnicodeDecodeError):
        raise ModelError('header is not JSON') from None
    if header.get('v') != 'tcm1':
        raise ModelError(f'header version {header.get("v")!r}, want tcm1')
    payload = blob[8 + hlen:]
    table = header.get('tensors') or []
    seen, end = set(), 0
    for i, t in enumerate(table):
        for k in ('name', 'dtype', 'shape', 'offset', 'bytes'):
            if k not in t:
                raise ModelError(f'tensor {i} is missing {k!r}')
        if t['name'] in seen:
            raise ModelError(f'two tensors named {t["name"]!r}')
        seen.add(t['name'])
        if t['dtype'] not in DTYPES:
            raise ModelError(f'tensor {t["name"]!r} has dtype '
                             f'{t["dtype"]!r}')
        if t['offset'] != end:
            raise ModelError(f'tensor {t["name"]!r} starts at {t["offset"]}, '
                             f'expected {end} — the payload must be packed '
                             'in table order with no gaps')
        end += int(t['bytes'])
    if end != len(payload):
        raise ModelError(f'payload is {len(payload)} bytes, the table '
                         f'accounts for {end}')
    chunk = int(header.get('chunk') or CHUNK)
    if chunk <= 0 or chunk > 1 << 20:
        raise ModelError('chunk size must be 1..1048576')
    leaves, index = [], []
    for ti, t in enumerate(table):
        data = payload[t['offset']:t['offset'] + t['bytes']]
        n = max(1, (len(data) + chunk - 1) // chunk)
        for ci in range(n):
            leaves.append(leaf_hash(ti, ci, data[ci * chunk:(ci + 1) * chunk]))
            index.append((ti, ci))
    levels = _levels(leaves)
    weights_root = levels[-1][0]
    header_sha3 = hashlib.sha3_256(blob[8:8 + hlen]).digest()
    return {
        'header': header, 'payload': payload, 'table': table,
        'chunk': chunk, 'levels': levels, 'leaf_index': index,
        'weights_root': weights_root.hex(),
        'header_sha3': header_sha3.hex(),
        'model_root': sha3(b'tcm1-model', header_sha3,
                           weights_root).hex(),
        'cid': cid_of(blob), 'bytes': len(blob),
        'name': header.get('name') or '',
        'graph': header.get('graph') or [], 'io': header.get('io') or {},
        'meta': header.get('meta') or {},
    }


def model_root(header_sha3: str, weights_root: str) -> str:
    return sha3(b'tcm1-model', bytes.fromhex(header_sha3),
                bytes.fromhex(weights_root)).hex()


def tensor_index(m: dict, name: str) -> int:
    for i, t in enumerate(m['table']):
        if t['name'] == name:
            return i
    raise ModelError(f'no tensor named {name!r} in model {m["name"]!r}',
                     'no_tensor', 404)


def tensor_bytes(m: dict, name: str) -> bytes:
    t = m['table'][tensor_index(m, name)]
    return m['payload'][t['offset']:t['offset'] + t['bytes']]


def tensor_values(m: dict, name: str) -> list[int]:
    t = m['table'][tensor_index(m, name)]
    raw = tensor_bytes(m, name)
    return T.i8(raw) if t['dtype'] == 'i8' else T.i32(raw)


# ── openings ──────────────────────────────────────────────────────


def open_tensor(m: dict, name: str) -> dict:
    """Every chunk of one tensor, each with its path to weights_root. The
    verifier of a single step needs exactly this and nothing else."""
    ti = tensor_index(m, name)
    t = m['table'][ti]
    data = m['payload'][t['offset']:t['offset'] + t['bytes']]
    chunk = m['chunk']
    out = []
    for ci in range(max(1, (len(data) + chunk - 1) // chunk)):
        li = m['leaf_index'].index((ti, ci))
        out.append({'tensor': ti, 'name': name, 'chunk': ci,
                    'data': data[ci * chunk:(ci + 1) * chunk].hex(),
                    'path': _path(m['levels'], li)})
    return {'name': name, 'tensor': ti, 'dtype': t['dtype'],
            'shape': t['shape'], 'bytes': t['bytes'], 'chunks': out}


def check_chunks(opening: dict, weights_root: str) -> dict:
    """Do these chunks belong to the committed model, and do they
    reassemble the tensor? Refutation only: a bad path is a no."""
    want = bytes.fromhex(weights_root)
    data = bytearray()
    for c in opening.get('chunks') or []:
        raw = bytes.fromhex(c['data'])
        leaf = leaf_hash(int(c['tensor']), int(c['chunk']), raw)
        if root_from_path(leaf, c.get('path') or []) != want:
            return {'valid': False, 'chunk': c.get('chunk'),
                    'reason': f'chunk {c.get("chunk")} of tensor '
                              f'{c.get("name")!r} is not in the committed '
                              'model (Merkle path does not reach '
                              'weights_root)'}
        data += raw
    if len(data) != int(opening.get('bytes') or len(data)):
        return {'valid': False, 'reason': 'the chunks do not reassemble the '
                                          'tensor length the opening claims'}
    return {'valid': True, 'bytes': len(data), 'data': bytes(data)}
