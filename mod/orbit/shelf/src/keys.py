"""
Keys — reading the shared store without becoming a second copy of it.

`~/.mod/store` is the fleet's actual database. wasmland keeps its artifacts,
listings, runs and ledger there; the arena reads the same `blobs/` prefix; more
will follow, because the point of a shared store is that records are legible to
somebody other than the process that wrote them. Legible in principle, anyway —
until now nothing could list a prefix, and wasmland's own `rebuild_index()`
reaches around the store API into raw `os.listdir` because the primitive it
needed did not exist.

So this is a reader, and only a reader. It holds no index, no cache and no
database: every answer is computed from the directory at the moment it is
asked. That costs a walk per call and buys the property that matters for an
operator tool — it cannot be stale, and it cannot disagree with the disk. When
this module says a key is not there, it is not there.

A key is its path under the root with `.json` dropped, which is the same string
`core/store` hands back from `shorten_item_path`. Same names on both sides of
the fence, so a key read here can be pasted into `m store/get` and work.
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

from . import redact

STORE = os.path.expanduser('~/.mod/store')
MOD_HOME = os.path.expanduser('~/.mod')


def _resolve(root: Optional[str]) -> str:
    """Turn a root argument into an absolute path, accepting a module name.

    `root='store'` and `root='~/.mod/store'` and no root at all all mean the
    same directory, because typing the long form every time is how a tool stops
    getting used.
    """
    if not root:
        return STORE
    root = os.path.expanduser(str(root))
    if os.path.isabs(root):
        return root
    return os.path.join(MOD_HOME, root)


def _contained(root: str, path: str) -> bool:
    """Is `path` really inside `root`?

    Keys arrive from an HTTP handler, so `../../.ssh/id_rsa` is a key somebody
    will eventually try. `realpath` on both sides makes the check survive
    symlinks as well as dots, and every read goes through it.
    """
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def key2path(root: str, key: str) -> Optional[str]:
    """The file a key names, or None if it escapes the root or is absent."""
    root = _resolve(root)
    candidate = os.path.normpath(os.path.join(root, str(key).lstrip('/')))
    if not _contained(root, candidate):
        return None
    for path in (candidate, candidate + '.json'):
        if os.path.isfile(path):
            return path
    return None


def path2key(root: str, path: str) -> str:
    key = os.path.relpath(path, _resolve(root))
    return key[:-5] if key.endswith('.json') else key


def roots() -> List[Dict[str, Any]]:
    """Directories worth pointing this at: the store, then everything else.

    The store is first because it is the shared one — the only directory here
    that more than one module is supposed to read.
    """
    out = []
    for name in sorted(os.listdir(MOD_HOME)) if os.path.isdir(MOD_HOME) else []:
        path = os.path.join(MOD_HOME, name)
        if not os.path.isdir(path):
            continue
        try:
            n = sum(1 for _ in os.scandir(path))
        except OSError:
            n = 0
        out.append({'name': name, 'path': path, 'entries': n,
                    'shared': name == 'store'})
    out.sort(key=lambda r: (not r['shared'], r['name']))
    return out


def prefixes(root: Optional[str] = None) -> Dict[str, Any]:
    """Top-level namespaces in a root, with what each holds.

    In the store these are module names — `wasmland/`, `arena/` — plus the
    shared `blobs/`, which belongs to nobody by design: bytes filed under their
    own hash are not any one module's records.
    """
    root = _resolve(root)
    if not os.path.isdir(root):
        return {'root': root, 'exists': False, 'prefixes': []}

    rows = []
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        if entry.is_dir(follow_symlinks=False):
            files = 0
            size = 0
            newest = 0.0
            for dirpath, _dirs, names in os.walk(entry.path):
                for name in names:
                    try:
                        st = os.stat(os.path.join(dirpath, name))
                    except OSError:
                        continue
                    files += 1
                    size += st.st_size
                    newest = max(newest, st.st_mtime)
            rows.append({'prefix': entry.name, 'keys': files, 'bytes': size,
                         'newest': newest, 'shared': entry.name == 'blobs'})
        else:
            try:
                st = entry.stat()
            except OSError:
                continue
            rows.append({'prefix': path2key(root, entry.path), 'keys': 1,
                         'bytes': st.st_size, 'newest': st.st_mtime,
                         'loose': True, 'shared': False})
    rows.sort(key=lambda r: r['keys'], reverse=True)
    return {'root': root, 'exists': True, 'prefixes': rows,
            'keys': sum(r['keys'] for r in rows),
            'bytes': sum(r['bytes'] for r in rows)}


def keys(root: Optional[str] = None, prefix: str = '', search: str = '',
         limit: int = 200, offset: int = 0, newest_first: bool = True) -> Dict[str, Any]:
    """List keys under a prefix. Metadata only — no value is read here."""
    root = _resolve(root)
    start = os.path.join(root, prefix) if prefix else root
    if not _contained(root, start) or not os.path.exists(start):
        return {'root': root, 'prefix': prefix, 'keys': [], 'total': 0}

    found = []
    for dirpath, _dirs, names in os.walk(start):
        for name in names:
            path = os.path.join(dirpath, name)
            key = path2key(root, path)
            if search and search.lower() not in key.lower():
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            found.append({'key': key, 'bytes': st.st_size, 'mtime': st.st_mtime,
                          'secret': redact.sensitive_file(path)})

    found.sort(key=lambda r: r['mtime'], reverse=newest_first)
    now = time.time()
    page = found[offset:offset + limit]
    for row in page:
        row['age_days'] = round((now - row['mtime']) / 86400, 2)
    return {'root': root, 'prefix': prefix, 'total': len(found),
            'offset': offset, 'limit': limit, 'keys': page}


def read(key: str, root: Optional[str] = None, raw: bool = False) -> Dict[str, Any]:
    """One key's value, redacted.

    `raw=True` is honoured only for values that are not secret; there is no
    argument that turns redaction off, because an argument that turns redaction
    off is the argument an attacker sends.
    """
    root = _resolve(root)
    path = key2path(root, key)
    if not path:
        return {'key': key, 'found': False}

    st = os.stat(path)
    out: Dict[str, Any] = {
        'key': path2key(root, path), 'found': True, 'bytes': st.st_size,
        'mtime': st.st_mtime, 'age_days': round((time.time() - st.st_mtime) / 86400, 2),
        'path': path.replace(os.path.expanduser('~'), '~'),
    }

    if redact.sensitive_file(path):
        # Never opened. A fingerprint would require reading it, and the promise
        # here is stronger than redaction: these bytes are not touched.
        out.update({'secret': True, 'value': redact.REDACTED,
                    'note': 'secret file — not read'})
        return out

    if st.st_size > 2_000_000:
        out.update({'value': None, 'note': f'{st.st_size} bytes — too large to render',
                    'oversized': True})
        return out

    try:
        text = open(path, 'r', encoding='utf-8', errors='replace').read()
    except OSError as exc:
        out.update({'value': None, 'error': str(exc)})
        return out

    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        out.update({'json': False, 'value': redact.scrub_text(text)})
        return out

    out['json'] = True
    out['value'] = doc if raw and not _has_secrets(doc) else redact.document(out['key'], doc)
    out['redacted'] = out['value'] != doc
    return out


def _has_secrets(doc: Any, _depth: int = 0) -> bool:
    if _depth > 12:
        return True
    if isinstance(doc, dict):
        return any(redact.sensitive_name(k) or _has_secrets(v, _depth + 1)
                   for k, v in doc.items())
    if isinstance(doc, list):
        return any(_has_secrets(v, _depth + 1) for v in doc)
    return False


def grep(text: str, root: Optional[str] = None, prefix: str = '',
         limit: int = 50, max_bytes: int = 1_000_000) -> Dict[str, Any]:
    """Find keys whose contents mention something.

    This is the query an operator actually types — "which record refers to this
    address" — and the store has no index that could answer it, so it is a
    scan. Files that are secret are skipped rather than searched: a hit-or-miss
    on a secret file is itself a disclosure, one bit at a time.
    """
    root = _resolve(root)
    start = os.path.join(root, prefix) if prefix else root
    if not _contained(root, start) or not os.path.exists(start):
        return {'root': root, 'query': text, 'hits': []}

    needle = str(text).lower()
    hits, scanned, skipped = [], 0, 0
    for dirpath, _dirs, names in os.walk(start):
        for name in names:
            path = os.path.join(dirpath, name)
            if redact.sensitive_file(path):
                skipped += 1
                continue
            try:
                if os.path.getsize(path) > max_bytes:
                    skipped += 1
                    continue
                body = open(path, 'r', encoding='utf-8', errors='replace').read()
            except OSError:
                continue
            scanned += 1
            low = body.lower()
            if needle not in low:
                continue
            at = low.index(needle)
            hits.append({
                'key': path2key(root, path),
                'count': low.count(needle),
                'context': redact.scrub_text(body[max(0, at - 60):at + 120], limit=200),
            })
            if len(hits) >= limit:
                return {'root': root, 'query': text, 'hits': hits,
                        'scanned': scanned, 'skipped': skipped, 'truncated': True}
    return {'root': root, 'query': text, 'hits': hits, 'scanned': scanned,
            'skipped': skipped, 'truncated': False}
