"""The run ledger — every Grok call this deployment makes, per account.

A run is one spend of somebody's xAI credits: a chat, a stream or an image.
It is LIVE while the upstream call is in flight, and DONE or ERROR the moment
it lands, with the duration and token usage beside it. The console's board —
ALL · LIVE · DONE · ERROR — is a straight read of this file.

Runs hang off the same address a key and a bot do, one JSON file per account
under ~/.mod/grokbot/runs, 0600, capped at the newest KEEP rows. The prompt is
truncated to a head — this is a ledger, not a transcript — and the key never
appears here at all.
"""

import json
import os
import threading
import time
import uuid

STATE = os.path.expanduser(os.environ.get('GROKBOT_DIR', '~/.mod/grokbot'))
DIR = os.path.join(STATE, 'runs')
KEEP = 200
PROMPT_HEAD = 140
# A run still LIVE after this long belongs to a process that died mid-call —
# comfortably past the 300s chat timeout.
STALE_AFTER = 360

LIVE, DONE, ERROR = 'live', 'done', 'error'
_LOCK = threading.Lock()


def _path(address):
    safe = ''.join(c for c in (address or '').lower()
                   if c.isalnum() or c in '-_.')
    return os.path.join(DIR, f'{safe}.json') if safe else None


def _load(path):
    try:
        with open(path) as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _save(path, rows):
    os.makedirs(DIR, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(rows[-KEEP:], f, indent=1)
    os.chmod(path, 0o600)


def _reap(rows):
    """A LIVE row older than the longest possible call is a dead process."""
    now = time.time()
    changed = False
    for row in rows:
        if row.get('status') == LIVE and now - row.get('ts', now) > STALE_AFTER:
            row['status'] = ERROR
            row['error'] = 'lost — the server went away mid-call'
            changed = True
    return changed


def start(address, kind, model=None, bot=None, prompt=None, search=False):
    """Open a LIVE run. Returns its id, or None when there is no account."""
    path = _path(address)
    if not path:
        return None
    row = {'id': uuid.uuid4().hex[:8], 'ts': int(time.time()), 'status': LIVE,
           'kind': kind, 'model': model, 'bot': bot or None,
           'prompt': (str(prompt)[:PROMPT_HEAD] if prompt else None),
           'search': bool(search)}
    with _LOCK:
        rows = _load(path)
        _reap(rows)
        rows.append(row)
        _save(path, rows)
    return row['id']


def finish(address, run_id, status, ms=None, tokens=None, error=None):
    """Close a run as DONE or ERROR. Silently a no-op if it was never opened."""
    path = _path(address)
    if not path or not run_id:
        return
    with _LOCK:
        rows = _load(path)
        for row in rows:
            if row.get('id') == run_id:
                row['status'] = DONE if status == DONE else ERROR
                if ms is not None:
                    row['ms'] = int(ms)
                if tokens is not None:
                    row['tokens'] = tokens
                if error:
                    row['error'] = str(error)[:300]
                break
        else:
            return
        _save(path, rows)


def list_runs(address, status=None, limit=60):
    """Newest first, with the counts the board's pills show."""
    path = _path(address)
    with _LOCK:
        rows = _load(path) if path else []
        if _reap(rows):
            _save(path, rows)
    rows = list(reversed(rows))
    counts = {'all': len(rows),
              LIVE: sum(1 for r in rows if r.get('status') == LIVE),
              DONE: sum(1 for r in rows if r.get('status') == DONE),
              ERROR: sum(1 for r in rows if r.get('status') == ERROR)}
    if status in (LIVE, DONE, ERROR):
        rows = [r for r in rows if r.get('status') == status]
    return {'runs': rows[:max(1, min(int(limit or 60), KEEP))],
            'counts': counts, 'address': address}


def clear(address):
    path = _path(address)
    if path:
        with _LOCK:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
    return {'cleared': True, 'address': address}
