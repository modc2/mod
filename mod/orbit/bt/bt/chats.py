"""
bt.chats — the conversation store behind the chat surface.

The Claude CLI already keeps its own transcript (that is what ``--resume``
replays), but it keeps it per-process on disk in a form nothing else can
read. This module keeps the part the *console* needs: the list of
conversations, their messages, the tools each answer played and what the
run cost — so a chat survives a reload, a restart, and can be read back by
any client of the agent protocol.

    ~/.mod/bt/chats.db      (BT_DATA_DIR overrides, same as the indexes)

    chats     id, title, created_ts, updated_ts, session, model,
              turns, cost_usd, msgs
    messages  id, chat_id, ts, role, text, tools (json), meta (json)

``session`` is the Claude CLI session id for this conversation; it is
re-read before every turn and rewritten after, because the CLI is free to
hand back a new id (a compaction, a fork) and the next turn has to follow
it or the conversation silently starts over.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Dict, List, Optional

from . import history

_lock = threading.Lock()

MAX_TITLE = 70


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(history.data_dir(), 'chats.db'),
                           timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY, title TEXT, created_ts INTEGER,
        updated_ts INTEGER, session TEXT, model TEXT,
        turns INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0, msgs INTEGER DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
        ts INTEGER NOT NULL, role TEXT NOT NULL, text TEXT,
        tools TEXT, meta TEXT)''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id, id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_updated ON chats(updated_ts)')
    return conn


def _title_of(text: str) -> str:
    t = ' '.join(str(text or '').split())
    return (t[:MAX_TITLE - 1] + '…') if len(t) > MAX_TITLE else (t or 'New chat')


def create(title: str = '', model: Optional[str] = None) -> str:
    """Open a conversation and return its id."""
    cid = uuid.uuid4().hex[:12]
    now = int(time.time())
    with _lock:
        conn = _db()
        try:
            conn.execute(
                'INSERT INTO chats (id, title, created_ts, updated_ts, model) '
                'VALUES (?,?,?,?,?)',
                (cid, _title_of(title), now, now, model))
            conn.commit()
        finally:
            conn.close()
    return cid


def append(chat_id: str, role: str, text: str,
           tools: Optional[List[Dict]] = None,
           meta: Optional[Dict] = None) -> int:
    """Add one message; returns its row id."""
    now = int(time.time())
    with _lock:
        conn = _db()
        try:
            cur = conn.execute(
                'INSERT INTO messages (chat_id, ts, role, text, tools, meta) '
                'VALUES (?,?,?,?,?,?)',
                (chat_id, now, role, text or '',
                 json.dumps(tools or [], default=str),
                 json.dumps(meta or {}, default=str)))
            conn.execute(
                'UPDATE chats SET updated_ts = ?, msgs = msgs + 1 WHERE id = ?',
                (now, chat_id))
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()


def finish_turn(chat_id: str, session: Optional[str] = None,
                model: Optional[str] = None, turns: int = 0,
                cost_usd: float = 0.0) -> None:
    """Fold one completed run's session/cost/turn count into the chat."""
    with _lock:
        conn = _db()
        try:
            row = conn.execute(
                'SELECT session, model FROM chats WHERE id = ?',
                (chat_id,)).fetchone()
            if row is None:
                return
            conn.execute(
                'UPDATE chats SET session = ?, model = ?, updated_ts = ?, '
                'turns = turns + ?, cost_usd = cost_usd + ? WHERE id = ?',
                (session or row[0], model or row[1], int(time.time()),
                 int(turns or 0), float(cost_usd or 0.0), chat_id))
            conn.commit()
        finally:
            conn.close()


def session_of(chat_id: str) -> Optional[str]:
    """The Claude session to resume for this conversation, if any."""
    with _lock:
        conn = _db()
        try:
            row = conn.execute('SELECT session FROM chats WHERE id = ?',
                               (chat_id,)).fetchone()
        finally:
            conn.close()
    return row[0] if row and row[0] else None


def exists(chat_id: str) -> bool:
    with _lock:
        conn = _db()
        try:
            return conn.execute('SELECT 1 FROM chats WHERE id = ?',
                                (chat_id,)).fetchone() is not None
        finally:
            conn.close()


def rename(chat_id: str, title: str) -> Dict:
    with _lock:
        conn = _db()
        try:
            conn.execute('UPDATE chats SET title = ? WHERE id = ?',
                         (_title_of(title), chat_id))
            conn.commit()
        finally:
            conn.close()
    return {'ok': True, 'id': chat_id, 'title': _title_of(title)}


def delete(chat_id: str) -> Dict:
    with _lock:
        conn = _db()
        try:
            conn.execute('DELETE FROM messages WHERE chat_id = ?', (chat_id,))
            n = conn.execute('DELETE FROM chats WHERE id = ?', (chat_id,)).rowcount
            conn.commit()
        finally:
            conn.close()
    return {'ok': bool(n), 'id': chat_id}


def _chat_row(r) -> Dict:
    return {'id': r[0], 'title': r[1], 'created_ts': r[2], 'updated_ts': r[3],
            'session': r[4], 'model': r[5], 'turns': r[6],
            'cost_usd': round(r[7] or 0.0, 6), 'msgs': r[8]}


def list_chats(limit: int = 50) -> List[Dict]:
    with _lock:
        conn = _db()
        try:
            rows = conn.execute(
                'SELECT id, title, created_ts, updated_ts, session, model, '
                'turns, cost_usd, msgs FROM chats ORDER BY updated_ts DESC '
                'LIMIT ?', (max(1, int(limit or 50)),)).fetchall()
        finally:
            conn.close()
    return [_chat_row(r) for r in rows]


def get(chat_id: str) -> Optional[Dict]:
    """One conversation with its full message list."""
    with _lock:
        conn = _db()
        try:
            head = conn.execute(
                'SELECT id, title, created_ts, updated_ts, session, model, '
                'turns, cost_usd, msgs FROM chats WHERE id = ?',
                (chat_id,)).fetchone()
            if head is None:
                return None
            msgs = conn.execute(
                'SELECT id, ts, role, text, tools, meta FROM messages '
                'WHERE chat_id = ? ORDER BY id', (chat_id,)).fetchall()
        finally:
            conn.close()
    out = _chat_row(head)
    out['messages'] = [{
        'id': m[0], 'ts': m[1], 'role': m[2], 'text': m[3],
        'tools': json.loads(m[4] or '[]'), 'meta': json.loads(m[5] or '{}'),
    } for m in msgs]
    return out


def stats() -> Dict:
    with _lock:
        conn = _db()
        try:
            chats, last = conn.execute(
                'SELECT COUNT(*), MAX(updated_ts) FROM chats').fetchone()
            msgs = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
            cost = conn.execute('SELECT SUM(cost_usd) FROM chats').fetchone()[0]
        finally:
            conn.close()
    return {'chats': chats, 'messages': msgs, 'last_ts': last,
            'cost_usd': round(cost or 0.0, 4)}
