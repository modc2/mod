"""
bt.trades — every alpha trade on every subnet, read from the chain's own events.

The trader index (bt.traders) infers trades from position deltas, and only
for coldkeys someone chose to track. This is the other half: a block-by-block
indexer that records every StakeAdded / StakeRemoved event the chain emits, so
any subnet page can list who bought and who sold, by how much, at what price —
no third-party explorer, no API key.

What counts as a trade
  StakeAdded   (coldkey, hotkey, tao, alpha, netuid, fee)  -> buy  alpha with TAO
  StakeRemoved (coldkey, hotkey, tao, alpha, netuid, fee)  -> sell alpha for TAO
  Within one extrinsic, a Removed + Added pair on the SAME subnet with the same
  tao and alpha is a hotkey move or a coldkey transfer — stake changed hands
  but nothing went through the pool — so both legs are dropped. A swap between
  subnets keeps both legs (a sell on one pool, a buy on the other) and is
  marked kind='swap'. Root (netuid 0) has no AMM and is skipped. Events not
  inside an extrinsic (emission, auto-stake) are not trades.

Store: ~/.mod/bt/trades.db
  blocks(block, ts, trades)   every block indexed — gaps are what is missing
  trades(block, ev, ...)      one row per leg, keyed by the event's position

The loop follows the finalized head and backfills BT_TRADES_BACKFILL_DAYS
newest-first, healing any gap (a node offline for a day catches up on its
own). Blocks older than the lite node keeps state for come from the archive
node. Rows older than BT_TRADES_KEEP_DAYS are pruned.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Dict, Iterable, List, Optional

ENDPOINT = os.environ.get('BT_TRADES_ENDPOINT', 'wss://entrypoint-finney.opentensor.ai:443')
ARCHIVE = os.environ.get('BT_ARCHIVE_ENDPOINT', 'wss://archive.chain.opentensor.ai:443')
BACKFILL_DAYS = float(os.environ.get('BT_TRADES_BACKFILL_DAYS', '7'))
KEEP_DAYS = float(os.environ.get('BT_TRADES_KEEP_DAYS', '30'))
BLOCK_SEC = 12
LITE_DEPTH = 200                  # blocks behind head the lite node still has state for
PASS_SEC = 10.0                   # time budget per indexing pass
YIELD_SEC = 0.02                  # breath between backfill blocks (decode is GIL-bound)
RAO = 1e9

COLS = ('block', 'ev', 'ext', 'ts', 'netuid', 'side', 'kind', 'coldkey',
        'hotkey', 'tao', 'alpha', 'price', 'fee')

_thread: Optional[threading.Thread] = None
_state: Dict = {'last_pass': None, 'last_error': None, 'head': None,
                'indexed_last_pass': 0, 'backlog': None}


# ------------------------------------------------------------- store

def _path() -> str:
    from .history import data_dir
    return os.path.join(data_dir(), 'trades.db')


_schema_done: set = set()


def _db() -> sqlite3.Connection:
    p = _path()
    conn = sqlite3.connect(p, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    if p not in _schema_done:
        conn.execute('''CREATE TABLE IF NOT EXISTS blocks (
            block INTEGER PRIMARY KEY, ts INTEGER, trades INTEGER)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS trades (
            block INTEGER NOT NULL, ev INTEGER NOT NULL, ext INTEGER,
            ts INTEGER, netuid INTEGER, side TEXT, kind TEXT,
            coldkey TEXT, hotkey TEXT, tao REAL, alpha REAL, price REAL, fee REAL,
            PRIMARY KEY (block, ev))''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_net ON trades(netuid, block)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_cold ON trades(coldkey, block)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts)')
        conn.commit()
        _schema_done.add(p)
    return conn


# ------------------------------------------------------------- classify

def _attrs(a) -> list:
    if isinstance(a, dict):
        return list(a.values())
    return list(a or [])


def _event(e: Dict):
    """(module, name, attributes, extrinsic_idx) from either decoded shape."""
    inner = e.get('event') or {}
    mod = e.get('module_id') or inner.get('module_id')
    name = e.get('event_id') or inner.get('event_id')
    attrs = e.get('attributes', inner.get('attributes'))
    ext = e.get('extrinsic_idx') if e.get('phase', 'ApplyExtrinsic') == 'ApplyExtrinsic' else None
    return mod, name, attrs, ext


def classify(events: Iterable[Dict], block: int, ts: int) -> List[Dict]:
    """Decoded block events -> trade rows (pure; the tests feed it fixtures)."""
    legs: Dict[int, List[Dict]] = {}
    swaps: set = set()
    for i, e in enumerate(events):
        mod, name, attrs, ext = _event(e)
        if mod != 'SubtensorModule' or ext is None:
            continue
        if name == 'StakeSwapped':
            swaps.add(ext)
            continue
        if name not in ('StakeAdded', 'StakeRemoved'):
            continue
        a = _attrs(attrs)
        if len(a) < 5:
            continue
        coldkey, hotkey, tao, alpha, netuid = a[:5]
        fee = a[5] if len(a) > 5 else 0
        netuid, tao, alpha = int(netuid), int(tao), int(alpha)
        if netuid == 0 or tao <= 0 or alpha <= 0:
            continue
        legs.setdefault(ext, []).append({
            'block': block, 'ev': i, 'ext': ext, 'ts': ts, 'netuid': netuid,
            'side': 'buy' if name == 'StakeAdded' else 'sell',
            'coldkey': str(coldkey), 'hotkey': str(hotkey),
            'tao': tao / RAO, 'alpha': alpha / RAO, 'price': tao / alpha,
            'fee': int(fee or 0) / RAO, '_key': (netuid, tao, alpha)})
    out: List[Dict] = []
    for ext, rows in legs.items():
        # same-pool Removed+Added pairs are moves/transfers, not trades
        buys = [r for r in rows if r['side'] == 'buy']
        for s in [r for r in rows if r['side'] == 'sell']:
            twin = next((b for b in buys if b['_key'] == s['_key']), None)
            if twin is not None:
                buys.remove(twin)
                rows.remove(twin)
                rows.remove(s)
        kind = 'swap' if ext in swaps else 'stake'
        for r in rows:
            r.pop('_key')
            r['kind'] = kind
            out.append(r)
    out.sort(key=lambda r: r['ev'])
    return out


# ------------------------------------------------------------- the chain

class Chain:
    """Lite node for the head, archive node for anything older."""

    def __init__(self):
        self._subs: Dict[str, object] = {}

    def sub(self, url: str):
        if url not in self._subs:
            from async_substrate_interface.sync_substrate import SubstrateInterface
            self._subs[url] = SubstrateInterface(url, ss58_format=42)
        return self._subs[url]

    def head(self) -> int:
        s = self.sub(ENDPOINT)
        return int(s.get_block_number(s.get_chain_finalised_head()))

    def block(self, n: int, head: int):
        s = self.sub(ENDPOINT if head - n < LITE_DEPTH else ARCHIVE)
        h = s.get_block_hash(n)
        ts = int(s.query('Timestamp', 'Now', block_hash=h).value) // 1000
        return ts, s.get_events(h)

    def close(self):
        for s in self._subs.values():
            try:
                s.close()
            except Exception:
                pass
        self._subs.clear()


def index_block(conn: sqlite3.Connection, chain, n: int, head: int) -> int:
    ts, events = chain.block(n, head)
    if not events:                # every real block emits events; none = pruned state
        raise RuntimeError(f'block {n}: no events (node has pruned its state)')
    rows = classify(events, n, ts)
    conn.executemany(
        f'INSERT OR REPLACE INTO trades ({",".join(COLS)}) VALUES ({",".join("?" * len(COLS))})',
        [tuple(r[c] for c in COLS) for r in rows])
    conn.execute('INSERT OR REPLACE INTO blocks VALUES (?,?,?)', (n, ts, len(rows)))
    conn.commit()
    return len(rows)


def missing(conn: sqlite3.Connection, head: int, floor: int) -> List[int]:
    """Blocks in [floor, head] not indexed yet, newest first."""
    have = {b for (b,) in conn.execute('SELECT block FROM blocks WHERE block >= ?', (floor,))}
    return [n for n in range(head, floor - 1, -1) if n not in have]


def run_once(chain=None, budget: float = PASS_SEC) -> Dict:
    own = chain is None
    chain = chain or Chain()
    conn = _db()
    done = 0
    try:
        head = chain.head()
        _state['head'] = head
        floor = head - int(BACKFILL_DAYS * 86400 / BLOCK_SEC)
        todo = missing(conn, head, floor)
        t0 = time.time()
        for n in todo:
            if time.time() - t0 > budget:
                break
            index_block(conn, chain, n, head)
            done += 1
            if head - n > 2:
                time.sleep(YIELD_SEC)
        _state['backlog'] = len(todo) - done
        _prune(conn, head)
        return {'head': head, 'indexed': done, 'backlog': len(todo) - done}
    finally:
        _state['indexed_last_pass'] = done
        conn.close()
        if own:
            chain.close()


_last_prune = [0.0]


def _prune(conn: sqlite3.Connection, head: int) -> None:
    if time.time() - _last_prune[0] < 3600:
        return
    _last_prune[0] = time.time()
    cut = head - int(KEEP_DAYS * 86400 / BLOCK_SEC)
    conn.execute('DELETE FROM trades WHERE block < ?', (cut,))
    conn.execute('DELETE FROM blocks WHERE block < ?', (cut,))
    conn.commit()


def _loop() -> None:
    time.sleep(20)
    chain = Chain()
    while True:
        try:
            r = run_once(chain)
            _state['last_pass'] = int(time.time())
            _state['last_error'] = None
            time.sleep(1 if r['backlog'] else BLOCK_SEC)
        except Exception as e:
            _state['last_error'] = f'{type(e).__name__}: {e}'
            print(f'[bt.trades] pass failed: {_state["last_error"]}', flush=True)
            chain.close()                 # a dead socket reconnects on the next pass
            time.sleep(30)


def start() -> None:
    """Background trade indexer (idempotent; BT_NO_SNAPSHOT=1 disables)."""
    global _thread
    if os.environ.get('BT_NO_SNAPSHOT') == '1' or os.environ.get('BT_NO_TRADES') == '1':
        return
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name='bt-trades', daemon=True)
    _thread.start()


# ------------------------------------------------------------- reads

def _coverage(conn: sqlite3.Connection, since_ts: int) -> Dict:
    lo, hi, n, lo_ts, hi_ts = conn.execute(
        'SELECT MIN(block), MAX(block), COUNT(*), MIN(ts), MAX(ts) FROM blocks WHERE ts >= ?',
        (since_ts,)).fetchone()
    span = (hi - lo + 1) if hi is not None else 0
    return {'from_block': lo, 'to_block': hi, 'from_ts': lo_ts, 'to_ts': hi_ts,
            'blocks': n, 'complete': bool(span) and n == span,
            'gaps': span - n if span else 0}


def trades(netuid: Optional[int] = None, coldkey: Optional[str] = None,
           side: Optional[str] = None, hours: float = 24, limit: int = 100,
           before_block: Optional[int] = None, min_tao: float = 0) -> Dict:
    """Trades newest-first plus a summary of the whole window (not just the page)."""
    since = int(time.time() - hours * 3600) if hours else 0
    where, args = ['ts >= ?'], [since]
    if netuid is not None:
        where.append('netuid = ?'); args.append(int(netuid))
    if coldkey:
        where.append('coldkey = ?'); args.append(coldkey)
    if min_tao:
        where.append('tao >= ?'); args.append(float(min_tao))
    w = ' AND '.join(where)
    conn = _db()
    try:
        s = conn.execute(
            'SELECT COUNT(*), '
            "SUM(side='buy'), SUM(side='sell'), "
            "COALESCE(SUM(CASE WHEN side='buy' THEN tao END),0), "
            "COALESCE(SUM(CASE WHEN side='sell' THEN tao END),0), "
            'COUNT(DISTINCT coldkey) '
            f'FROM trades WHERE {w}', args).fetchone()
        top = conn.execute(
            "SELECT coldkey, SUM(CASE WHEN side='buy' THEN tao ELSE -tao END) AS net, "
            'SUM(tao) AS gross, COUNT(*) '
            f'FROM trades WHERE {w} GROUP BY coldkey ORDER BY gross DESC LIMIT 8',
            args).fetchall()
        pw, pa = list(where), list(args)
        if side in ('buy', 'sell'):
            pw.append('side = ?'); pa.append(side)
        if before_block:
            pw.append('block < ?'); pa.append(int(before_block))
        rows = conn.execute(
            f'SELECT {",".join(COLS)} FROM trades WHERE {" AND ".join(pw)} '
            'ORDER BY block DESC, ev DESC LIMIT ?', pa + [int(limit)]).fetchall()
        cov = _coverage(conn, since)
    finally:
        conn.close()
    items = [dict(zip(COLS, r)) for r in rows]
    return {
        'netuid': netuid, 'hours': hours, 'count': len(items),
        'more': len(items) == int(limit),
        'next_before_block': items[-1]['block'] if items else None,
        'summary': {'trades': s[0], 'buys': s[1] or 0, 'sells': s[2] or 0,
                    'buy_tao': s[3], 'sell_tao': s[4], 'net_tao': s[3] - s[4],
                    'traders': s[5]},
        'top': [{'coldkey': c, 'net_tao': n, 'gross_tao': g, 'trades': k}
                for c, n, g, k in top],
        'coverage': cov,
        'source': 'chain events (SubtensorModule StakeAdded/StakeRemoved), indexed locally',
        'trades': items,
    }


def flows(hours: float = 24) -> Dict:
    """Per-subnet TAO flow board over one window — who the market is rotating
    into and out of, straight from the chain-event index. One SQL pass.

    net_tao > 0 means more TAO was staked into the subnet's pool than left it.
    Swap legs count on each side they touch (a swap IS a sell on one pool and
    a buy on the other); hotkey moves and coldkey transfers were never indexed
    as trades, so they can't fake a flow.
    """
    since = int(time.time() - hours * 3600) if hours else 0
    conn = _db()
    try:
        rows = conn.execute(
            'SELECT netuid, COUNT(*), '
            "COALESCE(SUM(side='buy'),0), COALESCE(SUM(side='sell'),0), "
            "COALESCE(SUM(CASE WHEN side='buy' THEN tao END),0), "
            "COALESCE(SUM(CASE WHEN side='sell' THEN tao END),0), "
            'COUNT(DISTINCT coldkey), '
            "COUNT(DISTINCT CASE WHEN side='buy' THEN coldkey END), "
            "COUNT(DISTINCT CASE WHEN side='sell' THEN coldkey END), "
            'MAX(tao) '
            'FROM trades WHERE ts >= ? GROUP BY netuid', (since,)).fetchall()
        cov = _coverage(conn, since)
    finally:
        conn.close()
    out = [{'netuid': n, 'trades': t, 'buys': b, 'sells': s,
            'buy_tao': bt, 'sell_tao': st, 'net_tao': bt - st,
            'traders': c, 'buyers': cb, 'sellers': cs, 'biggest_tao': mx}
           for n, t, b, s, bt, st, c, cb, cs, mx in rows]
    out.sort(key=lambda r: r['net_tao'], reverse=True)
    return {
        'hours': hours, 'subnets': len(out),
        'total_buy_tao': sum(r['buy_tao'] for r in out),
        'total_sell_tao': sum(r['sell_tao'] for r in out),
        'total_net_tao': sum(r['net_tao'] for r in out),
        'coverage': cov,
        'source': 'chain events (SubtensorModule StakeAdded/StakeRemoved), indexed locally',
        'rows': out,
    }


def status() -> Dict:
    conn = _db()
    try:
        n, lo, hi = conn.execute('SELECT COUNT(*), MIN(block), MAX(block) FROM blocks').fetchone()
        t = conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
    finally:
        conn.close()
    return {'blocks': n, 'from_block': lo, 'to_block': hi, 'trades': t,
            'backfill_days': BACKFILL_DAYS, 'keep_days': KEEP_DAYS,
            'endpoint': ENDPOINT, 'archive': ARCHIVE,
            'running': bool(_thread and _thread.is_alive()), **_state}
