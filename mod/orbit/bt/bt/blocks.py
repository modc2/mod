"""
bt.blocks — the daily block ledger + daily candles.

Once a day (checked hourly) the node fetches the chain block that opened each
UTC day — number, hash and the chain's own timestamp — and rolls that day's
5-minute subnet snapshots into one candle per subnet. Both land in
~/.mod/bt/blocks.db and never change once a day is closed, so:

  * "what block was it on 2026-09-01" is a local read, not an archive query;
  * daily OHLC / volume for every subnet survives even if the raw snapshot
    table is ever pruned;
  * a node that was offline for a week backfills the missing days on its
    next pass (block anchors come from an archive node, candles from
    whatever snapshots exist — `snaps` says how many backed each candle).

The block that opened a day is found by bisection over Timestamp.Now on an
archive node (BT_ARCHIVE_ENDPOINT) — ~20 reads per day, done once.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

ARCHIVE = os.environ.get('BT_ARCHIVE_ENDPOINT', 'wss://archive.chain.opentensor.ai:443')
CHECK_SEC = int(os.environ.get('BT_BLOCKS_CHECK_SEC', '3600'))
BACKFILL_DAYS = int(os.environ.get('BT_BLOCKS_BACKFILL_DAYS', '120'))
PER_PASS = 40                     # block anchors resolved per pass (keeps a pass < ~5 min)
BLOCK_SEC = 12.0
DAY = 86400

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_state: Dict = {'last_pass': None, 'last_error': None, 'running': False}


def _path() -> str:
    from .history import data_dir
    return os.path.join(data_dir(), 'blocks.db')


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS days (
        day TEXT PRIMARY KEY,            -- YYYY-MM-DD (UTC)
        block INTEGER, block_hash TEXT,  -- first block at/after 00:00 UTC
        block_ts INTEGER,                -- that block's chain timestamp (s)
        fetched_ts INTEGER)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS candles (
        day TEXT NOT NULL, netuid INTEGER NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        mcap REAL, tao_in REAL, vol_tao REAL, emission REAL,
        snaps INTEGER, first_ts INTEGER, last_ts INTEGER,
        block_open INTEGER, block_close INTEGER,
        PRIMARY KEY (day, netuid))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_candles_netuid ON candles(netuid, day)')
    return conn


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%d')


def day_start(day: str) -> int:
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp())


def days_between(first: str, last: str) -> List[str]:
    out, t = [], day_start(first)
    end = day_start(last)
    while t <= end:
        out.append(day_of(t))
        t += DAY
    return out


# ------------------------------------------------------------- block anchors

class Chain:
    """Just enough of an archive node: block number, hash, timestamp."""

    def __init__(self, url: str = ARCHIVE):
        from async_substrate_interface.sync_substrate import SubstrateInterface
        self.sub = SubstrateInterface(url, ss58_format=42)
        self._ts: Dict[int, int] = {}

    def head(self) -> int:
        return int(self.sub.get_block_number(None))

    def hash(self, n: int) -> str:
        return self.sub.get_block_hash(n)

    def ts(self, n: int) -> int:
        if n not in self._ts:
            v = self.sub.query('Timestamp', 'Now', block_hash=self.hash(n)).value
            self._ts[n] = int(v) // 1000
        return self._ts[n]

    def close(self):
        try:
            self.sub.close()
        except Exception:
            pass


def block_at(chain, target_ts: int, head: Optional[int] = None) -> int:
    """First block whose timestamp is >= target_ts (bisection, ~20 reads)."""
    head = head if head is not None else chain.head()
    head_ts = chain.ts(head)
    if target_ts > head_ts:
        raise ValueError('target is in the future')
    guess = max(1, head - int((head_ts - target_ts) / BLOCK_SEC))
    # bracket [lo, hi] with ts(lo) < target <= ts(hi), widening from the guess
    step = 600
    lo, hi = max(1, guess - step), min(head, guess + step)
    while lo > 1 and chain.ts(lo) >= target_ts:
        step *= 2
        lo = max(1, lo - step)
    while hi < head and chain.ts(hi) < target_ts:
        step *= 2
        hi = min(head, hi + step)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if chain.ts(mid) >= target_ts:
            hi = mid
        else:
            lo = mid
    return hi if chain.ts(hi) >= target_ts else lo


def anchor_days(chain=None, days: Optional[List[str]] = None,
                limit: int = PER_PASS) -> List[Dict]:
    """Resolve + store the opening block of each day still missing one."""
    today = day_of(time.time())
    if days is None:
        days = days_between(day_of(time.time() - BACKFILL_DAYS * DAY), today)
    conn = _db()
    try:
        have = {r[0] for r in conn.execute('SELECT day FROM days WHERE block IS NOT NULL')}
    finally:
        conn.close()
    todo = [d for d in reversed(days) if d not in have][:limit]   # newest first
    if not todo:
        return []
    own = chain is None
    chain = chain or Chain()
    out = []
    try:
        head = chain.head()
        for d in todo:
            n = block_at(chain, day_start(d), head)
            row = {'day': d, 'block': n, 'block_hash': chain.hash(n),
                   'block_ts': chain.ts(n), 'fetched_ts': int(time.time())}
            with _lock:
                conn = _db()
                try:
                    conn.execute('INSERT OR REPLACE INTO days VALUES (?,?,?,?,?)',
                                 (row['day'], row['block'], row['block_hash'],
                                  row['block_ts'], row['fetched_ts']))
                    conn.commit()
                finally:
                    conn.close()
            out.append(row)
    finally:
        if own:
            chain.close()
    return out


# ------------------------------------------------------------- daily candles

def _snaps_conn() -> sqlite3.Connection:
    from .history import data_dir
    return sqlite3.connect(os.path.join(data_dir(), 'history.db'), timeout=30)


def rollup(day: str, snaps_conn: Optional[sqlite3.Connection] = None) -> int:
    """Fold one UTC day of subnet snapshots into candles. Idempotent."""
    t0 = day_start(day)
    own = snaps_conn is None
    sc = snaps_conn or _snaps_conn()
    try:
        rows = sc.execute(
            'SELECT netuid, ts, price, mcap, tao_in, volume, emission, block FROM snaps '
            'WHERE ts >= ? AND ts < ? ORDER BY ts', (t0, t0 + DAY)).fetchall()
        # cumulative volume as the previous day closed — 24h volume is the delta
        prev = dict(sc.execute(
            'SELECT netuid, volume FROM snaps WHERE ts = '
            '(SELECT MAX(ts) FROM snaps WHERE ts < ?)', (t0,)).fetchall())
    finally:
        if own:
            sc.close()
    by: Dict[int, List] = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    out = []
    for netuid, rs in by.items():
        prices = [r[2] for r in rs if r[2] is not None]
        if not prices:
            continue
        first, last = rs[0], rs[-1]
        v0 = prev.get(netuid, first[5])
        vol = (last[5] - v0) if (last[5] is not None and v0 is not None) else None
        out.append((day, netuid, prices[0], max(prices), min(prices), prices[-1],
                    last[3], last[4], max(vol, 0.0) if vol is not None else None,
                    last[6], len(rs), first[1], last[1], first[7], last[7]))
    with _lock:
        conn = _db()
        try:
            conn.execute('DELETE FROM candles WHERE day = ?', (day,))
            conn.executemany(f'INSERT INTO candles VALUES ({",".join("?" * 15)})', out)
            conn.commit()
        finally:
            conn.close()
    return len(out)


def rollup_missing() -> List[str]:
    """Roll every day that has snapshots but no candles, plus today/yesterday."""
    sc = _snaps_conn()
    try:
        lo, hi = sc.execute('SELECT MIN(ts), MAX(ts) FROM snaps').fetchone()
        if lo is None:
            return []
        conn = _db()
        try:
            done = {r[0] for r in conn.execute('SELECT DISTINCT day FROM candles')}
        finally:
            conn.close()
        today = day_of(time.time())
        recent = {today, day_of(time.time() - DAY)}   # still filling / just closed
        todo = [d for d in days_between(day_of(lo), day_of(hi))
                if d not in done or d in recent]
        for d in todo:
            rollup(d, sc)
        return todo
    finally:
        sc.close()


# ------------------------------------------------------------- reads

def days(limit: int = 60) -> Dict:
    """The block ledger: the block that opened each UTC day, newest first."""
    conn = _db()
    try:
        rows = conn.execute(
            'SELECT d.day, d.block, d.block_hash, d.block_ts, d.fetched_ts, '
            '(SELECT COUNT(*) FROM candles c WHERE c.day = d.day) '
            'FROM days d ORDER BY d.day DESC LIMIT ?', (int(limit),)).fetchall()
        n_days, n_candles = conn.execute(
            'SELECT (SELECT COUNT(*) FROM days), (SELECT COUNT(*) FROM candles)').fetchone()
    finally:
        conn.close()
    out = []
    for i, r in enumerate(rows):
        row = {'day': r[0], 'block': r[1], 'block_hash': r[2], 'block_ts': r[3],
               'fetched_ts': r[4], 'subnets': r[5]}
        # blocks produced during the day = next day's opening block - this one's
        if i > 0 and rows[i - 1][1] and r[1]:
            row['blocks'] = rows[i - 1][1] - r[1]
        out.append(row)
    return {'days': out, 'count': n_days, 'candles': n_candles, **status()}


def daily(netuid: Optional[int] = None, day: Optional[str] = None,
          days_back: int = 90) -> Dict:
    """Daily candles: one subnet over time, or every subnet on one day."""
    conn = _db()
    try:
        cols = ('day', 'netuid', 'open', 'high', 'low', 'close', 'mcap', 'tao_in',
                'vol_tao', 'emission', 'snaps', 'first_ts', 'last_ts',
                'block_open', 'block_close')
        if netuid is not None:
            rows = conn.execute(
                f'SELECT {",".join(cols)} FROM candles WHERE netuid = ? AND day >= ? ORDER BY day',
                (int(netuid), day_of(time.time() - days_back * DAY))).fetchall()
        else:
            day = day or day_of(time.time() - DAY)
            rows = conn.execute(
                f'SELECT {",".join(cols)} FROM candles WHERE day = ? ORDER BY mcap DESC',
                (day,)).fetchall()
        anchors = dict(conn.execute('SELECT day, block FROM days').fetchall())
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d['change_pct'] = ((d['close'] / d['open'] - 1) * 100) if d['open'] else None
        d['day_block'] = anchors.get(d['day'])
        out.append(d)
    return {'netuid': netuid, 'day': None if netuid is not None else day,
            'count': len(out), 'candles': out}


def status() -> Dict:
    return {'archive': ARCHIVE, 'check_sec': CHECK_SEC,
            'last_pass': _state['last_pass'], 'last_error': _state['last_error'],
            'running': _state['running']}


# ------------------------------------------------------------- the loop

def run_once() -> Dict:
    _state['running'] = True
    try:
        rolled = rollup_missing()
        anchored = anchor_days()
        _state['last_pass'] = int(time.time())
        _state['last_error'] = None
        return {'rolled': rolled, 'anchored': [a['day'] for a in anchored]}
    except Exception as e:
        _state['last_error'] = f'{type(e).__name__}: {e}'
        raise
    finally:
        _state['running'] = False


def _loop() -> None:
    time.sleep(30)                 # let the server and the first snapshot settle
    while True:
        try:
            r = run_once()
            if r['rolled'] or r['anchored']:
                print(f"[bt.blocks] rolled {len(r['rolled'])} day(s), "
                      f"anchored {len(r['anchored'])} block(s)", flush=True)
        except Exception as e:
            print(f'[bt.blocks] pass failed: {type(e).__name__}: {e}', flush=True)
        time.sleep(CHECK_SEC)


def start() -> None:
    """Background daily ledger (idempotent; BT_NO_SNAPSHOT=1 disables)."""
    global _thread
    if os.environ.get('BT_NO_SNAPSHOT') == '1':
        return
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name='bt-blocks', daemon=True)
    _thread.start()
