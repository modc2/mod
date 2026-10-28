"""
bt.indexes — saved baskets of traders: an index you build off the board.

A trader index is a named list of coldkeys with weights. The members are
(auto-)tracked in the trader index (`bt.traders`), so everything an index
reports comes from the same local store the leaderboard is ranked from:

  * per-member stats reuse ``traders._board_row`` — the flow-normalized
    market/pnl split, so a member who merely wired TAO in reads ~0%;
  * index-level returns are the weight-averaged member percentages,
    renormalized over the members that actually have a baseline;
  * the blended curve is each member's equity rebased to 1.0 at the start
    of the window, weight-summed, and rebased to 100.

Store: ~/.mod/bt/indexes.db  (same BT_DATA_DIR override as everything else)
  indexes(id, name UNIQUE, note, members JSON, created_ts, updated_ts)

Members are passed as one string — ``ss58, ss58:2, ss58:1.5`` — because the
tool registry speaks scalar params only. Weights are relative and
normalized to sum 1; omitted weights default to equal.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from . import history, traders

MAX_MEMBERS = int(os.environ.get('BT_INDEX_MAX_MEMBERS', '50'))
CURVE_PTS = 64
DUST_TAO = 0.5                 # a member below this never shapes the curve

_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(history.data_dir(), 'indexes.db'),
                           timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('''CREATE TABLE IF NOT EXISTS indexes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        note TEXT,
        members TEXT NOT NULL,
        created_ts INTEGER, updated_ts INTEGER)''')
    return conn


# ------------------------------------------------------------------ members

def parse_members(members: str) -> List[Dict]:
    """``ss58, ss58:2.5`` → [{'ss58', 'weight'}], weights normalized to 1."""
    entries: List[Dict] = []
    seen = set()
    for part in re.split(r'[,\s]+', (members or '').strip()):
        if not part:
            continue
        ss58, _, w = part.partition(':')
        if ss58 in seen:
            continue
        if not traders._valid_ss58(ss58):
            raise ValueError(f'invalid ss58 address: {ss58}')
        try:
            weight = float(w) if w else 1.0
        except ValueError:
            raise ValueError(f'bad weight {w!r} for {ss58}')
        if weight <= 0:
            raise ValueError(f'weight must be positive: {ss58}:{w}')
        seen.add(ss58)
        entries.append({'ss58': ss58, 'weight': weight})
    if not entries:
        raise ValueError('an index needs at least one member')
    if len(entries) > MAX_MEMBERS:
        raise ValueError(f'too many members ({len(entries)} > {MAX_MEMBERS})')
    total = sum(e['weight'] for e in entries)
    for e in entries:
        e['weight'] /= total
    return entries


def _row(r) -> Dict:
    return {'id': r[0], 'name': r[1], 'note': r[2],
            'members': json.loads(r[3]), 'created_ts': r[4],
            'updated_ts': r[5]}


def _resolve(conn, index) -> Dict:
    """Find one index by numeric id or by name (case-insensitive)."""
    key = str(index).strip()
    sql = 'SELECT id, name, note, members, created_ts, updated_ts FROM indexes '
    r = (conn.execute(sql + 'WHERE id = ?', (int(key),)).fetchone()
         if key.isdigit() else
         conn.execute(sql + 'WHERE name = ? COLLATE NOCASE', (key,)).fetchone())
    if r is None:
        raise ValueError(f'no index matching {index!r}')
    return _row(r)


def _track_missing(entries: List[Dict]) -> List[Dict]:
    """bt_track every member not already in the trader index."""
    have = {t['ss58'] for t in traders.watchlist()}
    out = []
    for e in entries:
        if e['ss58'] in have:
            continue
        try:
            traders.track(e['ss58'])
            out.append({'ss58': e['ss58'], 'tracked': True})
        except Exception as exc:    # the index is still created; say why
            out.append({'ss58': e['ss58'], 'tracked': False,
                        'error': str(exc)})
    return out


# -------------------------------------------------------------------- CRUD

def create(name: str, members: str, note: Optional[str] = None,
           track: bool = True) -> Dict:
    name = (name or '').strip()
    if not name:
        raise ValueError('index needs a name')
    entries = parse_members(members)
    tracked = _track_missing(entries) if track else []
    now = int(time.time())
    with _lock:
        conn = _db()
        try:
            try:
                cur = conn.execute(
                    'INSERT INTO indexes (name, note, members, created_ts, '
                    'updated_ts) VALUES (?, ?, ?, ?, ?)',
                    (name, note, json.dumps(entries), now, now))
            except sqlite3.IntegrityError:
                raise ValueError(f'an index named {name!r} already exists')
            conn.commit()
            idx_id = cur.lastrowid
        finally:
            conn.close()
    out = get(idx_id)
    if tracked:
        out['tracking'] = tracked
    return out


def update(index, name: Optional[str] = None, members: Optional[str] = None,
           note: Optional[str] = None, track: bool = True) -> Dict:
    with _lock:
        conn = _db()
        try:
            cur = _resolve(conn, index)
            entries = parse_members(members) if members is not None \
                else cur['members']
            conn.execute(
                'UPDATE indexes SET name = ?, note = ?, members = ?, '
                'updated_ts = ? WHERE id = ?',
                (name.strip() if name else cur['name'],
                 note if note is not None else cur['note'],
                 json.dumps(entries), int(time.time()), cur['id']))
            conn.commit()
        finally:
            conn.close()
    if members is not None and track:
        _track_missing(entries)
    return get(cur['id'])


def delete(index) -> Dict:
    """Remove an index. Its members stay tracked — history is never dropped."""
    with _lock:
        conn = _db()
        try:
            cur = _resolve(conn, index)
            conn.execute('DELETE FROM indexes WHERE id = ?', (cur['id'],))
            conn.commit()
        finally:
            conn.close()
    return {'removed': True, 'id': cur['id'], 'name': cur['name']}


# ------------------------------------------------------------- performance

def _member_rows(entries: List[Dict], days: int) -> List[Dict]:
    """Each member ranked exactly like the leaderboard ranks it."""
    prices = traders._prices_now()
    names = {r['netuid']: r.get('name')
             for r in history.screener(sparks=False).get('rows', [])}
    conn = traders._reader()
    try:
        labels = dict(conn.execute('SELECT ss58, label FROM traders'))
        rows = []
        for e in entries:
            t = {'ss58': e['ss58'], 'label': labels.get(e['ss58'])}
            row = traders._board_row(conn, t, max(1, int(days)) * 86400,
                                     prices, False, names)
            row['weight'] = e['weight']
            row['tracked'] = e['ss58'] in labels
            rows.append(row)
        return rows
    finally:
        conn.close()


def _blend(rows: List[Dict]) -> Dict:
    """Weight-average the percentages over the members that have a baseline."""
    priced = [r for r in rows if r['baseline']]
    w = sum(r['weight'] for r in priced)
    out: Dict[str, Any] = {
        'priced': len(priced), 'member_count': len(rows),
        'book_tao': sum((r['total_stake_tao'] or 0) + (r['free_tao'] or 0)
                        for r in rows),
        'market_pnl_tao': sum(r['market_pnl_tao'] for r in rows),
        'flow_tao': sum(r['flow_tao'] for r in rows),
    }
    if not priced or w <= 0:
        out.update({'market_pct': None, 'pnl_pct': None})
        return out
    out['market_pct'] = sum(r['market_pct'] * r['weight'] for r in priced) / w
    out['pnl_pct'] = sum(r['pnl_pct'] * r['weight'] for r in priced) / w
    return out


def _curve(entries: List[Dict], days: int, points: int = CURVE_PTS) -> List[Dict]:
    """The blended equity curve, rebased to 100 at the start of the window.

    Each member's total_tao series is carried forward onto a common grid and
    divided by its first value in the window; a member with no history yet
    (or a dust book) holds at 1.0 so it never shapes the line. Deposits do
    move a member's equity — the headline numbers come from the normalized
    board math, the curve is the honest raw blend.
    """
    now = int(time.time())
    start = now - max(1, int(days)) * 86400
    grid = [start + i * (now - start) / (points - 1) for i in range(points)]
    legs: List[Dict] = []           # {'series': [ratio]*points, 'weight': w}
    conn = traders._reader()
    try:
        for e in entries:
            rows = conn.execute(
                'SELECT ts, total_tao FROM trader_snaps WHERE ss58 = ? '
                'AND ts >= ? ORDER BY ts', (e['ss58'], start)).fetchall()
            rows = [(t, v) for t, v in rows if v is not None]
            if not rows or rows[0][1] < DUST_TAO:
                continue                      # no history / dust: holds at 1.0
            base = rows[0][1]
            series, j, last = [], 0, None
            for g in grid:
                while j < len(rows) and rows[j][0] <= g:
                    last = rows[j][1]
                    j += 1
                series.append((last / base) if last is not None else 1.0)
            legs.append({'series': series, 'weight': e['weight']})
    finally:
        conn.close()
    # weighted mean over the legs that have history; renormalize their
    # weights so a member still warming dilutes nothing.
    total_w = sum(leg['weight'] for leg in legs)
    out = []
    for i, g in enumerate(grid):
        v = (sum(leg['series'][i] * leg['weight'] for leg in legs) / total_w
             if total_w > 0 else 1.0)
        out.append({'t': int(g), 'v': round(v * 100.0, 4)})
    return out


def get(index, days: int = 7, curve: bool = True) -> Dict:
    conn = _db()
    try:
        meta = _resolve(conn, index)
    finally:
        conn.close()
    rows = _member_rows(meta['members'], days)
    out = {**meta, 'days': days, **_blend(rows),
           'rows': sorted(rows, key=lambda r: r['weight'], reverse=True)}
    if curve:
        out['curve'] = _curve(meta['members'], days)
    return out


def indexes(days: int = 7, sparks: bool = True) -> Dict:
    """Every saved index with its blended window performance."""
    conn = _db()
    try:
        metas = [_row(r) for r in conn.execute(
            'SELECT id, name, note, members, created_ts, updated_ts '
            'FROM indexes ORDER BY created_ts DESC').fetchall()]
    finally:
        conn.close()
    out = []
    for m in metas:
        rows = _member_rows(m['members'], days)
        entry = {**m, 'days': days, **_blend(rows)}
        if sparks:
            entry['spark'] = [p['v'] for p in
                              _curve(m['members'], days, points=32)]
        out.append(entry)
    return {'days': days, 'count': len(out), 'indexes': out,
            'updated_at': int(time.time())}
