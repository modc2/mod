"""
Storage — everything wasmland knows, kept in the store mod.

This module has no database of its own. Artifacts, listings, runs, receipts
and the ledger all live in `m.mod('store')`, under the `wasmland/` prefix, and
that is on purpose: a marketplace whose records sit in a private file beside
its own process is a marketplace you have to take on faith. The store already
answers the questions this one would otherwise re-answer badly — where bytes
live, who may read them, what a thing is addressed by — so it answers them.

    blobs/<sha256>.json                  the bytes somebody uploaded, base64
    wasmland/artifacts/<sha256>.json     what reading those bytes says they are
    wasmland/listings/<id>.json          one thing for sale
    wasmland/runs/<id>.json              one execution and its receipt
    wasmland/ledger/<address>.json       one account's credits and earnings
    wasmland/index.json                  ids, so a listing page is one read

Artifacts are addressed by the SHA-256 of their bytes — the same id in the
browser, on the server, and in any other mod that stores the same file. Where
localfs is reachable, the bytes are also pinned there and the CID is recorded
alongside, so an artifact can be handed to anything in the fleet that speaks
CIDs without being copied again.

`blobs/` deliberately sits *outside* the wasmland prefix. Bytes under their own
hash belong to nobody: the arena reads the same key for a game published here,
rather than being handed a second copy of a file the store already holds.
Records — which are opinions about those bytes — stay namespaced.

The index exists because listing every key means a directory walk per page
load. It is a cache of ids and never the truth: `rebuild_index()` throws it
away and reads the store back.
"""
import hashlib
import json
import threading
import time
from typing import Any, Dict, List, Optional

PREFIX = 'wasmland'
BLOBS = 'blobs'                # shared with any mod that speaks sha256
_LOCK = threading.RLock()      # the index is read-modify-written
_STORE = None
_LOCALFS = None


def protocol():
    """`import mod` — the protocol package, not this module's own mod.py.

    Every mod in the fleet ships a `mod.py`, so whichever directory happens to
    be first on sys.path decides what `import mod` means. Run from inside this
    module (pytest, `python3 src/api/api.py`, a cwd of the module root) it
    resolves to the wrong one and fails as "module 'mod' has no attribute
    'mod'". So the import is done once, with this module's own directory taken
    off the path for the duration.
    """
    import importlib
    import sys
    from pathlib import Path
    got = sys.modules.get('mod')
    if got is not None and hasattr(got, 'mod'):
        return got
    mine = {str(Path(__file__).resolve().parent),
            str(Path(__file__).resolve().parent.parent)}
    saved = list(sys.path)
    sys.modules.pop('mod', None)
    try:
        sys.path = [p for p in sys.path
                    if p and str(Path(p).resolve()) not in mine]
        return importlib.import_module('mod')
    finally:
        sys.path = saved


def store():
    """The store mod, resolved once. This is the only persistence there is."""
    global _STORE
    if _STORE is None:
        _STORE = protocol().mod('store')()
    return _STORE


def localfs():
    """The CID pinner, if this box has one. Optional by design."""
    global _LOCALFS
    if _LOCALFS is None:
        try:
            _LOCALFS = protocol().mod('localfs')()
        except Exception:
            _LOCALFS = False
    return _LOCALFS or None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj: Any) -> str:
    """The one JSON encoding this module hashes and signs.

    Sorted keys, no spaces. Two processes that disagree about how to serialise
    a receipt disagree about whether a run verified, so there is exactly one
    encoding and everything uses it.
    """
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)


# ── raw keys ─────────────────────────────────────────────────────────

def put_json(key: str, value: Any) -> Any:
    return store().put_json(f'{PREFIX}/{key}', value)


def get_json(key: str, default: Any = None) -> Any:
    try:
        got = store().get(f'{PREFIX}/{key}')
    except Exception:
        return default
    return default if got is None else got


def exists(key: str) -> bool:
    return bool(store().exists(f'{PREFIX}/{key}'))


def delete(key: str) -> bool:
    try:
        store().rm(f'{PREFIX}/{key}')
        return True
    except Exception:
        return False


# ── blobs: bytes under their own hash, shared across mods ────────────

def blob_key(artifact_id: str) -> str:
    """The store key any mod can read these bytes from."""
    return f'{BLOBS}/{artifact_id}'


def put_blob(data: bytes) -> str:
    """Store bytes under their SHA-256 and return the id.

    Records and blobs live in separate folders because the store appends
    `.json` to a key: `artifacts/<id>` and `artifacts/<id>.json` are the same
    file, and the second write silently ate the first.
    """
    import base64
    artifact_id = sha256(data)
    store().put_json(blob_key(artifact_id), {'b64': base64.b64encode(data).decode()})
    return artifact_id


def get_blob(artifact_id: str) -> Optional[bytes]:
    """Bytes back, checked against the id they are filed under."""
    import base64
    try:
        blob = store().get(blob_key(artifact_id))
    except Exception:
        return None
    if not blob or 'b64' not in blob:
        return None
    data = base64.b64decode(blob['b64'])
    # The id *is* the hash, so a mismatch means the bytes changed underneath
    # us — the one corruption a content-addressed store must never serve.
    if sha256(data) != artifact_id:
        raise ValueError(f'blob {artifact_id[:12]} does not hash to its own id — '
                         'the stored bytes have been altered')
    return data


# ── artifacts ────────────────────────────────────────────────────────

def put_artifact(data: bytes, engine: str, manifest: Dict[str, Any],
                 filename: str = '') -> Dict[str, Any]:
    """Store bytes under their own hash. Idempotent — same bytes, same id.

    Uploading something already here is not an error and does not copy it
    again; the answer is the record that was already true.
    """
    artifact_id = sha256(data)
    record = get_json(f'artifacts/{artifact_id}.json')
    if record:
        return record

    put_blob(data)

    cid = None
    pinner = localfs()
    if pinner:
        try:
            cid = pinner.put(data)
        except Exception:
            cid = None      # a missing CID is a missing convenience, not a failure

    record = {
        'id': artifact_id,
        'engine': engine,
        'bytes': len(data),
        'filename': filename or '',
        'cid': cid,
        'manifest': manifest,
        'created': time.time(),
    }
    put_json(f'artifacts/{artifact_id}.json', record)
    _index_add('artifacts', artifact_id)
    return record


def get_artifact(artifact_id: str) -> Optional[bytes]:
    return get_blob(artifact_id)


def artifact_record(artifact_id: str) -> Optional[Dict[str, Any]]:
    return get_json(f'artifacts/{artifact_id}.json')


# ── records ──────────────────────────────────────────────────────────

def put_record(kind: str, record_id: str, value: Dict[str, Any]) -> Dict[str, Any]:
    put_json(f'{kind}/{record_id}.json', value)
    _index_add(kind, record_id)
    return value


def get_record(kind: str, record_id: str) -> Optional[Dict[str, Any]]:
    return get_json(f'{kind}/{record_id}.json')


def drop_record(kind: str, record_id: str) -> bool:
    ok = delete(f'{kind}/{record_id}.json')
    _index_remove(kind, record_id)
    return ok


def records(kind: str, limit: int = 200, newest_first: bool = True) -> List[Dict[str, Any]]:
    ids = index().get(kind, [])
    out = []
    for record_id in ids:
        record = get_record(kind, record_id)
        if record:
            out.append(record)
    out.sort(key=lambda r: r.get('created', 0), reverse=newest_first)
    return out[:limit]


# ── the index ────────────────────────────────────────────────────────

def index() -> Dict[str, List[str]]:
    return get_json('index.json', default={}) or {}


def _index_add(kind: str, record_id: str):
    with _LOCK:
        idx = index()
        ids = idx.setdefault(kind, [])
        if record_id not in ids:
            ids.append(record_id)
            put_json('index.json', idx)


def _index_remove(kind: str, record_id: str):
    with _LOCK:
        idx = index()
        ids = idx.get(kind) or []
        if record_id in ids:
            ids.remove(record_id)
            put_json('index.json', idx)


def rebuild_index() -> Dict[str, List[str]]:
    """Read the store back and replace the index with what is actually there.

    The index is a cache. This is the function that proves it: after it runs,
    anything the store holds is listed and anything it doesn't isn't.
    """
    import os
    root = os.path.join(str(getattr(store(), 'path', '')), PREFIX)
    found: Dict[str, List[str]] = {}
    for kind in ('artifacts', 'listings', 'runs', 'games'):
        folder = os.path.join(root, kind)
        if not os.path.isdir(folder):
            continue
        found[kind] = [name[:-len('.json')] for name in sorted(os.listdir(folder))
                       if name.endswith('.json')]
    with _LOCK:
        put_json('index.json', found)
    return found


def stats() -> Dict[str, Any]:
    idx = index()
    return {
        'backend': 'store mod',
        'prefix': PREFIX,
        'path': str(getattr(store(), 'path', '')),
        'cids': bool(localfs()),
        'counts': {kind: len(ids) for kind, ids in idx.items()},
    }
