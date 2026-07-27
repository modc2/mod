"""
bt.history — the open indexer behind the bt explorer.

Taostats and tao.app feel instant because a closed indexer sits behind
them. This is ours, in ~150 lines, on SQLite: a background thread
snapshots every subnet's pool state on an interval, and every screener /
chart / change-% / volume query is served from the local store in
microseconds — no chain round-trip, no third-party API, no key.

Store: ~/.mod/bt/history.db  (override dir with BT_DATA_DIR)
  snaps(ts, netuid, price, mcap, tao_in, volume, emission)
  subnet_meta(netuid, name, symbol, github, url, discord, logo,
              description, registered_at, updated_ts, tempo, owner)

The refresher uses its OWN Bt connection (the substrate websocket is not
thread-safe), so API calls never wait behind a snapshot.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

REFRESH_SEC = int(os.environ.get('BT_REFRESH_SEC', '300'))
HORIZONS = {'change_1h': 3600, 'change_24h': 86400, 'change_7d': 7 * 86400}
SPARK_HOURS = 24
SPARK_POINTS = 48

_db_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {'rows': None, 'ts': None}
_thread: Optional[threading.Thread] = None


def data_dir() -> str:
    d = os.environ.get('BT_DATA_DIR') or os.path.expanduser('~/.mod/bt')
    os.makedirs(d, exist_ok=True)
    return d


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(data_dir(), 'history.db'), timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS snaps (
        ts INTEGER NOT NULL, netuid INTEGER NOT NULL,
        price REAL, mcap REAL, tao_in REAL, volume REAL, emission REAL,
        PRIMARY KEY (netuid, ts))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS subnet_meta (
        netuid INTEGER PRIMARY KEY, name TEXT, symbol TEXT,
        github TEXT, url TEXT, discord TEXT, logo TEXT,
        description TEXT, registered_at INTEGER, updated_ts INTEGER)''')
    # columns added after the first release — widen in place
    have = {r[1] for r in conn.execute('PRAGMA table_info(subnet_meta)')}
    for col, decl in (('tempo', 'INTEGER'), ('owner', 'TEXT')):
        if col not in have:
            conn.execute(f'ALTER TABLE subnet_meta ADD COLUMN {col} {decl}')
    return conn


def row_from_subnet(s: Dict) -> Dict:
    """Normalize one engine subnet dict into a screener row."""
    price = s.get('price') or 0.0
    alpha_total = (s.get('alpha_in') or 0.0) + (s.get('alpha_out') or 0.0)
    ident = s.get('subnet_identity') or {}
    return {
        'netuid': s.get('netuid'),
        'name': s.get('subnet_name') or s.get('name') or f"subnet {s.get('netuid')}",
        'symbol': s.get('symbol'),
        'price': price,
        'market_cap': price * alpha_total,
        'tao_in': s.get('tao_in') or 0.0,
        'alpha_in': s.get('alpha_in') or 0.0,
        'alpha_out': s.get('alpha_out') or 0.0,
        'volume': s.get('subnet_volume') or 0.0,
        'emission': s.get('emission') or 0.0,
        'registered_at': s.get('network_registered_at'),
        'tempo': s.get('tempo'),
        'owner': s.get('owner_coldkey'),
        'github': ident.get('github_repo') or None,
        'url': ident.get('subnet_url') or None,
        'discord': ident.get('discord') or None,
        'logo': ident.get('logo_url') or None,
        'description': ident.get('description') or None,
    }


def record(rows: List[Dict], ts: Optional[int] = None) -> None:
    """Persist one snapshot of all subnets and refresh the warm cache."""
    ts = int(ts if ts is not None else time.time())
    with _db_lock:
        conn = _db()
        try:
            conn.executemany(
                'INSERT OR REPLACE INTO snaps VALUES (?,?,?,?,?,?,?)',
                [(ts, r['netuid'], r['price'], r['market_cap'], r['tao_in'],
                  r['volume'], r['emission']) for r in rows])
            conn.executemany(
                'INSERT OR REPLACE INTO subnet_meta (netuid, name, symbol, github, '
                'url, discord, logo, description, registered_at, updated_ts, '
                'tempo, owner) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                [(r['netuid'], r['name'], r['symbol'], r['github'], r['url'],
                  r['discord'], r['logo'], r['description'],
                  r['registered_at'], ts, r.get('tempo'), r.get('owner'))
                 for r in rows])
            conn.commit()
        finally:
            conn.close()
    with _cache_lock:
        _cache['rows'] = rows
        _cache['ts'] = ts


def _load_last_snapshot() -> None:
    """Cold-start: serve the most recent stored snapshot until live data lands."""
    with _db_lock:
        conn = _db()
        try:
            last = conn.execute('SELECT MAX(ts) FROM snaps').fetchone()[0]
            if last is None:
                return
            rows = conn.execute(
                '''SELECT s.netuid, m.name, m.symbol, s.price, s.mcap, s.tao_in,
                          s.volume, s.emission, m.github, m.url, m.discord,
                          m.logo, m.description, m.registered_at, m.tempo,
                          m.owner
                   FROM snaps s LEFT JOIN subnet_meta m ON m.netuid = s.netuid
                   WHERE s.ts = ?''', (last,)).fetchall()
        finally:
            conn.close()
    cols = ('netuid', 'name', 'symbol', 'price', 'market_cap', 'tao_in',
            'volume', 'emission', 'github', 'url', 'discord', 'logo',
            'description', 'registered_at', 'tempo', 'owner')
    with _cache_lock:
        if _cache['rows'] is None:
            _cache['rows'] = [dict(zip(cols, r)) for r in rows]
            _cache['ts'] = last


def _prices_at(ago_sec: int, now: int, tolerance: float = 0.5) -> Dict[int, float]:
    """price per netuid at the snapshot closest to (now - ago_sec).

    Ignored if the nearest snapshot misses the target by more than
    tolerance × horizon (not enough history yet for that column).
    """
    target = now - ago_sec
    with _db_lock:
        conn = _db()
        try:
            row = conn.execute(
                'SELECT ts FROM snaps ORDER BY ABS(ts - ?) LIMIT 1',
                (target,)).fetchone()
            if not row or abs(row[0] - target) > tolerance * ago_sec:
                return {}
            return dict(conn.execute(
                'SELECT netuid, price FROM snaps WHERE ts = ?', (row[0],)))
        finally:
            conn.close()


def _sparks(now: int) -> Dict[int, List[float]]:
    """Per-netuid downsampled price series over the last SPARK_HOURS."""
    since = now - SPARK_HOURS * 3600
    with _db_lock:
        conn = _db()
        try:
            rows = conn.execute(
                'SELECT netuid, ts, price FROM snaps WHERE ts >= ? ORDER BY ts',
                (since,)).fetchall()
        finally:
            conn.close()
    series: Dict[int, List[float]] = {}
    for netuid, _ts, price in rows:
        series.setdefault(netuid, []).append(price)
    out = {}
    for netuid, prices in series.items():
        if len(prices) > SPARK_POINTS:
            step = len(prices) / SPARK_POINTS
            prices = [prices[int(i * step)] for i in range(SPARK_POINTS - 1)] + [prices[-1]]
        out[netuid] = [round(p, 9) for p in prices]
    return out


def screener(sort_by: str = 'market_cap', limit: int = 0,
             search: Optional[str] = None, sparks: bool = True) -> Dict:
    """The instant market table: cached rows + change % + 24h volume + sparklines."""
    with _cache_lock:
        rows, ts = _cache['rows'], _cache['ts']
    if rows is None:
        _load_last_snapshot()
        with _cache_lock:
            rows, ts = _cache['rows'], _cache['ts']
    if rows is None:
        return {'warming': True, 'updated_at': None, 'rows': [],
                'note': 'first chain snapshot in progress — retry in ~1 min'}
    now = int(time.time())
    past = {k: _prices_at(h, now) for k, h in HORIZONS.items()}
    vol_past = _volumes_at(86400, now)
    spark_map = _sparks(now) if sparks else {}
    out = []
    for r in rows:
        row = dict(r)
        uid = row['netuid']
        for k in HORIZONS:
            p0 = past[k].get(uid)
            row[k] = (row['price'] / p0 - 1.0) * 100.0 if p0 else None
        v0 = vol_past.get(uid)
        row['vol_24h'] = max(row['volume'] - v0, 0.0) if v0 is not None else None
        if sparks:
            row['spark'] = spark_map.get(uid) or []
        out.append(row)
    if search:
        q = search.lower()
        out = [r for r in out if q in str(r.get('name') or '').lower()
               or q in str(r.get('symbol') or '').lower()
               or q == str(r.get('netuid'))]
    if sort_by:
        out.sort(key=lambda r: r.get(sort_by) or 0.0, reverse=True)
    if limit:
        out = out[:limit]
    return {'updated_at': ts, 'age_sec': now - ts, 'count': len(out), 'rows': out}


def _volumes_at(ago_sec: int, now: int) -> Dict[int, float]:
    target = now - ago_sec
    with _db_lock:
        conn = _db()
        try:
            row = conn.execute(
                'SELECT ts FROM snaps ORDER BY ABS(ts - ?) LIMIT 1',
                (target,)).fetchone()
            if not row or abs(row[0] - target) > 0.5 * ago_sec:
                return {}
            return dict(conn.execute(
                'SELECT netuid, volume FROM snaps WHERE ts = ?', (row[0],)))
        finally:
            conn.close()


def series(netuid: int, hours: int = 24, points: int = 300) -> List[Dict]:
    """Price/mcap/volume history for one subnet, downsampled to <= points."""
    since = int(time.time()) - hours * 3600
    with _db_lock:
        conn = _db()
        try:
            rows = conn.execute(
                '''SELECT ts, price, mcap, volume FROM snaps
                   WHERE netuid = ? AND ts >= ? ORDER BY ts''',
                (netuid, since)).fetchall()
        finally:
            conn.close()
    if len(rows) > points:
        step = len(rows) / points
        rows = [rows[int(i * step)] for i in range(points - 1)] + [rows[-1]]
    return [{'t': t, 'price': p, 'mcap': m, 'volume': v} for t, p, m, v in rows]


def stats() -> Dict:
    """Network-wide totals from the cached table (root netuid 0 excluded)."""
    scr = screener(sort_by='market_cap', sparks=False)
    rows = [r for r in scr.get('rows', []) if r.get('netuid') != 0]
    vol = [r['vol_24h'] for r in rows if r.get('vol_24h') is not None]
    return {
        'subnets': len(rows),
        'total_market_cap_tao': sum(r['market_cap'] for r in rows),
        'total_tao_in_pools': sum(r['tao_in'] for r in rows),
        'volume_24h_tao': sum(vol) if vol else None,
        'updated_at': scr.get('updated_at'),
        'warming': scr.get('warming', False),
    }


# ------------------------------------------------------------- refresher

def refresh_once(bt=None) -> int:
    """One snapshot: pull all subnets from chain, normalize, persist."""
    if bt is None:
        from .bt import Bt
        from .tools import DEFAULT_NETWORK
        bt = Bt(network=DEFAULT_NETWORK)
    subs = bt.subnets(n=10000)
    rows = [row_from_subnet(s) for s in subs]
    if rows:
        record(rows)
    return len(rows)


def _loop() -> None:
    bt = None
    while True:
        try:
            if bt is None:
                from .bt import Bt
                from .tools import DEFAULT_NETWORK
                bt = Bt(network=DEFAULT_NETWORK)
            n = refresh_once(bt)
            print(f'[bt.history] snapshot: {n} subnets', flush=True)
        except Exception as e:
            print(f'[bt.history] snapshot failed: {type(e).__name__}: {e}', flush=True)
            bt = None  # reconnect next round
        time.sleep(REFRESH_SEC)


def start() -> None:
    """Start the background snapshotter (idempotent; BT_NO_SNAPSHOT=1 disables)."""
    global _thread
    if os.environ.get('BT_NO_SNAPSHOT') == '1':
        return
    if _thread is not None and _thread.is_alive():
        return
    _load_last_snapshot()
    _thread = threading.Thread(target=_loop, name='bt-history', daemon=True)
    _thread.start()
