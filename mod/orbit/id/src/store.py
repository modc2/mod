"""Where an identity lives on disk: an append-only log, and caches of it.

The log is the identity. `ids/<id>.jsonl` holds one signed event per line in the
order they happened, and everything else in this directory — the index that maps
an address to an identity, the alias map that keeps a merged-away identity
resolvable — is a cache that `rebuild()` can regenerate from the logs alone.
That split is the point: an index can be corrupted, and if it is, nothing is
lost, because every claim this module makes is a replay of signatures that are
still sitting in the log and can still be re-checked by hand.

Nothing secret is stored. Public keys, addresses, signatures and statements are
all things the holder published on purpose. There is no private key here and no
place to put one.

State lives under ~/.mod/id, never in the repo — as with every module in this
fleet, the checkout stays free of anything belonging to whoever is running it.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Iterable, List, Optional

HOME = Path(os.environ.get('ID_DIR') or (Path.home() / '.mod' / 'id'))
LOGS = HOME / 'ids'
INDEX = HOME / 'index.json'
CHALLENGES = HOME / 'challenges.json'

_LOCK = threading.RLock()


def ensure() -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    return HOME


@contextlib.contextmanager
def sandbox(path: Optional[str] = None) -> Iterable[Path]:
    """Point the whole store somewhere else for a moment — used by the tests and
    by `m id/demo`, so that showing how it works never touches real identities."""
    global HOME, LOGS, INDEX, CHALLENGES
    keep = (HOME, LOGS, INDEX, CHALLENGES)
    temporary = tempfile.TemporaryDirectory(prefix='mod-id-') if path is None else None
    HOME = Path(path or temporary.name)
    LOGS, INDEX, CHALLENGES = HOME / 'ids', HOME / 'index.json', HOME / 'challenges.json'
    try:
        ensure()
        yield HOME
    finally:
        HOME, LOGS, INDEX, CHALLENGES = keep
        if temporary is not None:
            temporary.cleanup()


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(handle, 'w') as out:
            json.dump(payload, out, indent=2, sort_keys=True)
        os.replace(temp, path)
    except BaseException:
        os.path.exists(temp) and os.unlink(temp)
        raise


# ── the log ──────────────────────────────────────────────────────────────

def log_path(id: str) -> Path:
    if not id or '/' in id or '.' in id or not id.startswith('id_'):
        raise ValueError(f'not an identity name: {id!r}')
    return LOGS / f'{id}.jsonl'


def exists(id: str) -> bool:
    return log_path(id).exists()


def append(id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """One event, one line, never rewritten."""
    with _LOCK:
        ensure()
        path = log_path(id)
        event = dict(event)
        event['seq'] = sum(1 for _ in read(id))
        with path.open('a') as out:
            out.write(json.dumps(event, sort_keys=True) + '\n')
        return event


def read(id: str) -> Iterator[Dict[str, Any]]:
    path = log_path(id)
    if not path.exists():
        return iter(())

    def walk() -> Iterator[Dict[str, Any]]:
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)
    return walk()


def events(id: str) -> List[Dict[str, Any]]:
    return list(read(id))


def ids() -> List[str]:
    ensure()
    return sorted(p.stem for p in LOGS.glob('id_*.jsonl'))


# ── the index (a cache — `rebuild()` regenerates it) ─────────────────────

def _blank() -> Dict[str, Any]:
    return {'accounts': {}, 'aliases': {}, 'names': {}}


def index() -> Dict[str, Any]:
    with _LOCK:
        if not INDEX.exists():
            return _blank()
        try:
            data = json.loads(INDEX.read_text())
        except (json.JSONDecodeError, OSError):
            return _blank()
        base = _blank()
        base.update({k: v for k, v in data.items() if k in base})
        return base


def save_index(data: Dict[str, Any]) -> None:
    with _LOCK:
        ensure()
        _write_atomic(INDEX, data)


def resolve(account: str) -> Optional[str]:
    """address → identity, following merges."""
    data = index()
    found = data['accounts'].get(account)
    return follow(found, data) if found else None


def follow(id: Optional[str], data: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """An identity that was merged away still resolves — to the survivor."""
    if not id:
        return None
    data = data or index()
    seen = set()
    while id in data['aliases'] and id not in seen:
        seen.add(id)
        id = data['aliases'][id]
    return id


# ── challenges: issued once, consumed once ───────────────────────────────

def challenges() -> Dict[str, Any]:
    with _LOCK:
        if not CHALLENGES.exists():
            return {}
        try:
            return json.loads(CHALLENGES.read_text())
        except (json.JSONDecodeError, OSError):
            return {}


def put_challenge(fields: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        ensure()
        pending = challenges()
        pending[fields['nonce']] = fields
        _sweep(pending, fields['issued_at'])
        _write_atomic(CHALLENGES, pending)
        return fields


def take_challenge(nonce: str) -> Optional[Dict[str, Any]]:
    """Read and burn. A nonce that comes back a second time is not there."""
    with _LOCK:
        pending = challenges()
        found = pending.pop(nonce, None)
        if found is not None:
            _write_atomic(CHALLENGES, pending)
        return found


def peek_challenge(nonce: str) -> Optional[Dict[str, Any]]:
    return challenges().get(nonce)


def _sweep(pending: Dict[str, Any], at: float) -> None:
    for key in [k for k, v in pending.items() if float(v.get('expires_at', 0)) < at - 3600]:
        pending.pop(key, None)


def blob(name: str) -> Dict[str, Any]:
    """A small JSON file under the state directory — sessions, pending merges."""
    path = HOME / f'{name}.json'
    with _LOCK:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}


def save_blob(name: str, data: Dict[str, Any]) -> None:
    with _LOCK:
        ensure()
        _write_atomic(HOME / f'{name}.json', data)


def spent(id: str, nonce: str) -> bool:
    """Was this nonce already written into a log? The replay backstop."""
    for event in read(id):
        for proof in event.get('proofs', []):
            if proof.get('nonce') == nonce:
                return True
    return False


def stats() -> Dict[str, Any]:
    ensure()
    data = index()
    return {'home': str(HOME), 'identities': len(ids()),
            'accounts': len(data['accounts']), 'merged_away': len(data['aliases']),
            'pending_challenges': len(challenges())}
