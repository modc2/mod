"""
Where everything lives: the store mod, under the `0xprof/` prefix.

No database of its own, on purpose. A market whose records sit in a private
file next to its own process is a market you have to take on faith, and this
one is in the business of not being taken on faith. The store already answers
where bytes live and what they are addressed by, so it answers it here too.

    blobs/<sha256>.json          bytes under their own hash — shared, unprefixed
    0xprof/proofs/<id>.json      one proof, its statement, and every verdict on it
    0xprof/bounties/<id>.json    one request for a proof, and its escrow
    0xprof/keys/<id>.json        a verification key or circuit artifact
    0xprof/ledger/<address>.json one account's credits, escrow and entitlements
    0xprof/index.json            ids, so a list page is one read

Proof ids are the SHA-256 of the canonical (system, statement, proof) triple.
That is what makes the same proof the same id everywhere: two people who
upload the same bytes get one record with both their names on it rather than
two listings racing to sell the identical thing.
"""
import hashlib
import json
import threading
import time
from typing import Any, Dict, List, Optional

PREFIX = '0xprof'
BLOBS = 'blobs'
_LOCK = threading.RLock()
_STORE = None
_LOCALFS = None


def protocol():
    """`import mod` — the protocol package, not this module's own mod.py.

    Every mod ships a `mod.py`, so whichever directory is first on sys.path
    decides what `import mod` means. Run from inside this module it resolves to
    the wrong one and fails as "module 'mod' has no attribute 'mod'". So the
    import is done once, with this module's directories off the path.
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

    Two processes that disagree about how to serialise a proof disagree about
    what its id is, so there is exactly one encoding and everything uses it.
    """
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)


def digest(obj: Any) -> str:
    return sha256(canonical(obj).encode())


# ── raw keys ─────────────────────────────────────────────────────────

def put_json(key: str, value: Any) -> Any:
    return store().put_json(f'{PREFIX}/{key}', value)


def get_json(key: str, default: Any = None) -> Any:
    try:
        got = store().get(f'{PREFIX}/{key}')
    except Exception:
        return default
    return default if got is None else got


def delete(key: str) -> bool:
    try:
        store().rm(f'{PREFIX}/{key}')
        return True
    except Exception:
        return False


# ── blobs: bytes under their own hash, shared across mods ────────────

def put_blob(data: bytes) -> str:
    """Store bytes under their SHA-256 and return the id.

    Records and blobs live in separate folders because the store appends
    `.json` to a key: `keys/<id>` and `keys/<id>.json` would be the same file.
    """
    import base64
    blob_id = sha256(data)
    store().put_json(f'{BLOBS}/{blob_id}', {'b64': base64.b64encode(data).decode()})
    return blob_id


def get_blob(blob_id: str) -> Optional[bytes]:
    """Bytes back, checked against the id they are filed under."""
    import base64
    try:
        blob = store().get(f'{BLOBS}/{blob_id}')
    except Exception:
        return None
    if not blob or 'b64' not in blob:
        return None
    data = base64.b64decode(blob['b64'])
    if sha256(data) != blob_id:
        raise ValueError(f'blob {blob_id[:12]} does not hash to its own id — '
                         'the stored bytes have been altered')
    return data


def pin(data: bytes) -> Optional[str]:
    """A CID, if this box pins. A missing CID is a missing convenience."""
    pinner = localfs()
    if not pinner:
        return None
    try:
        return pinner.put(data)
    except Exception:
        return None


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


def records(kind: str, limit: int = 500, newest_first: bool = True) -> List[Dict[str, Any]]:
    out = []
    for record_id in index().get(kind, []):
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

    The index is a cache. This is the function that proves it.
    """
    import os
    root = os.path.join(str(getattr(store(), 'path', '')), PREFIX)
    found: Dict[str, List[str]] = {}
    for kind in ('proofs', 'bounties', 'keys'):
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
