"""
newsagg.cache — one SQLite file of raw HTTP bodies keyed by URL.

Raw bodies, not parsed items: a parser fix applies to everything already
cached, and a source that is down still answers from its last good copy.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

HOME = Path(os.environ.get('NEWS_HOME', Path.home() / '.mod' / 'news'))
_lock = threading.Lock()
_db: Optional[sqlite3.Connection] = None
MAX_ROWS = 5000


def db() -> sqlite3.Connection:
    global _db
    if _db is None:
        HOME.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(HOME / 'cache.db'), check_same_thread=False)
        _db.execute('CREATE TABLE IF NOT EXISTS http (url TEXT PRIMARY KEY, body BLOB, t REAL)')
    return _db


def get(url: str, ttl: Optional[int]) -> Optional[bytes]:
    """Body if cached and younger than ttl seconds (ttl=None: any age)."""
    with _lock:
        row = db().execute('SELECT body, t FROM http WHERE url=?', (url,)).fetchone()
    if row and (ttl is None or time.time() - row[1] < ttl):
        return row[0]
    return None


def put(url: str, body: bytes) -> None:
    with _lock:
        c = db()
        c.execute('INSERT OR REPLACE INTO http VALUES (?,?,?)', (url, body, time.time()))
        c.execute('DELETE FROM http WHERE url IN (SELECT url FROM http ORDER BY t DESC LIMIT -1 OFFSET ?)',
                  (MAX_ROWS,))
        c.commit()


def stats() -> dict:
    with _lock:
        n, size = db().execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(body)),0) FROM http').fetchone()
    return {'path': str(HOME / 'cache.db'), 'entries': n, 'bytes': size}


def clear() -> dict:
    with _lock:
        n = db().execute('DELETE FROM http').rowcount
        db().commit()
    return {'cleared': n}
