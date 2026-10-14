"""
gemscan.http — JSON over HTTP with a SQLite cache of raw bodies keyed by URL.

Raw bodies, not parsed items: a parser fix applies to everything already
cached, and a source that is down still answers from its last good copy.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

HOME = Path(os.environ.get('GEMFINDER_HOME', Path.home() / '.mod' / 'gemfinder'))
UA = 'Mozilla/5.0 (X11; Linux x86_64) gemfinder/0.1'
MAX_ROWS = 3000
_lock = threading.Lock()
_db: Optional[sqlite3.Connection] = None


def db() -> sqlite3.Connection:
    global _db
    if _db is None:
        HOME.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(HOME / 'cache.db'), check_same_thread=False)
        _db.execute('CREATE TABLE IF NOT EXISTS http (url TEXT PRIMARY KEY, body BLOB, t REAL)')
    return _db


def _get(url: str, ttl: Optional[int]) -> Optional[bytes]:
    with _lock:
        row = db().execute('SELECT body, t FROM http WHERE url=?', (url,)).fetchone()
    if row and (ttl is None or time.time() - row[1] < ttl):
        return row[0]
    return None


def _put(url: str, body: bytes) -> None:
    with _lock:
        c = db()
        c.execute('INSERT OR REPLACE INTO http VALUES (?,?,?)', (url, body, time.time()))
        c.execute('DELETE FROM http WHERE url IN (SELECT url FROM http ORDER BY t DESC LIMIT -1 OFFSET ?)',
                  (MAX_ROWS,))
        c.commit()


def _fetch(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt:
                raise
            time.sleep(2.5)  # one polite retry on rate limit (CoinGecko keyless tier)
    raise RuntimeError('unreachable')


def get_json(url: str, ttl: int = 600, timeout: float = 12):
    """Fresh cache hit, else fetch; on fetch failure fall back to any cached copy."""
    body = _get(url, ttl)
    if body is None:
        try:
            body = _fetch(url, timeout)
            json.loads(body)  # only cache what parses
            _put(url, body)
        except Exception:
            body = _get(url, None)
            if body is None:
                raise
    return json.loads(body)


def stats() -> dict:
    with _lock:
        n, size = db().execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(body)),0) FROM http').fetchone()
    return {'path': str(HOME / 'cache.db'), 'entries': n, 'bytes': size}


def clear() -> dict:
    with _lock:
        n = db().execute('DELETE FROM http').rowcount
        db().commit()
    return {'cleared': n}
