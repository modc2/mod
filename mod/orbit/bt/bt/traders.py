"""
bt.traders — the open trader index: watch any coldkey, keep its history.

`bt.history` snapshots every subnet's pool state; this does the same for
accounts. Track an ss58 coldkey and a background thread records what it
holds — free TAO plus every alpha position, valued at the price of the
moment. From that one table everything else falls out with no chain
round-trip: equity curves, N-day PnL, and a trade tape inferred from the
deltas between consecutive snapshots.

It is also the time machine other modules borrow. `positions_at(ss58, ts)`
and `prices_at(ts)` answer "what did this account hold, and what was it
worth?" from local SQLite — the questions that otherwise need an archive
node.

Store: ~/.mod/bt/traders.db  (override dir with BT_DATA_DIR)
  traders(ss58, label, network, added_ts, last_ts)
  trader_snaps(ss58, ts, free_tao, staked_tao, total_tao, positions)
  trader_flows(ts, ss58, netuid, side, alpha, price, tao_value)

The refresher runs on its OWN Bt connection (the substrate websocket is
not thread-safe), so API calls never wait behind a snapshot.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from . import history

REFRESH_SEC = int(os.environ.get('BT_TRADER_REFRESH_SEC', '900'))
WINDOWS = {'change_24h': 86400, 'change_7d': 7 * 86400}

# An alpha position drifts upward on its own — validators accrue emission
# every tempo. A delta only counts as a trade once it clears both a share
# of the position and an absolute TAO floor.
FLOW_MIN_FRACTION = 0.02      # 2% of the previous position
FLOW_MIN_TAO = 0.05           # and at least this much TAO moved

_db_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(history.data_dir(), 'traders.db'),
                           timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS traders (
        ss58 TEXT PRIMARY KEY, label TEXT, network TEXT,
        added_ts INTEGER, last_ts INTEGER)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS trader_snaps (
        ss58 TEXT NOT NULL, ts INTEGER NOT NULL,
        free_tao REAL, staked_tao REAL, total_tao REAL, positions TEXT,
        PRIMARY KEY (ss58, ts))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS trader_flows (
        ts INTEGER NOT NULL, ss58 TEXT NOT NULL, netuid INTEGER NOT NULL,
        side TEXT, alpha REAL, price REAL, tao_value REAL,
        PRIMARY KEY (ss58, ts, netuid))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_flows_ts ON trader_flows(ts)')
    return conn


def _valid_ss58(address: str) -> bool:
    try:
        from scalecodec.utils.ss58 import ss58_decode
        ss58_decode(address)
        return True
    except Exception:
        return False


# ------------------------------------------------------------- watchlist

def track(address: str, label: Optional[str] = None,
          network: str = 'finney') -> Dict:
    """Add a coldkey to the trader index and snapshot it immediately."""
    if not _valid_ss58(address):
        raise ValueError(f'invalid ss58 address: {address}')
    now = int(time.time())
    with _db_lock:
        conn = _db()
        try:
            row = conn.execute('SELECT added_ts FROM traders WHERE ss58 = ?',
                               (address,)).fetchone()
            if row is None:
                conn.execute('INSERT INTO traders (ss58, label, network, added_ts) '
                             'VALUES (?,?,?,?)', (address, label, network, now))
            elif label is not None:
                conn.execute('UPDATE traders SET label = ? WHERE ss58 = ?',
                             (label, address))
            conn.commit()
        finally:
            conn.close()
    out = {'ss58': address, 'label': label, 'tracked': True,
           'added_ts': row[0] if row else now, 'new': row is None}
    try:
        snap = snapshot(address)
        out['snapshot'] = {'ts': snap['ts'], 'total_tao': snap['total_tao'],
                           'positions': len(snap['positions'])}
    except Exception as e:
        out['snapshot_error'] = f'{type(e).__name__}: {e}'
    return out


def untrack(address: str, purge: bool = False) -> Dict:
    """Stop tracking a coldkey. History is kept unless purge=True."""
    with _db_lock:
        conn = _db()
        try:
            n = conn.execute('DELETE FROM traders WHERE ss58 = ?',
                             (address,)).rowcount
            if purge:
                conn.execute('DELETE FROM trader_snaps WHERE ss58 = ?', (address,))
                conn.execute('DELETE FROM trader_flows WHERE ss58 = ?', (address,))
            conn.commit()
        finally:
            conn.close()
    return {'ss58': address, 'tracked': False, 'removed': bool(n),
            'purged': bool(purge)}


def watchlist() -> List[Dict]:
    with _db_lock:
        conn = _db()
        try:
            rows = conn.execute(
                'SELECT ss58, label, network, added_ts, last_ts FROM traders '
                'ORDER BY added_ts').fetchall()
        finally:
            conn.close()
    return [{'ss58': r[0], 'label': r[1], 'network': r[2],
             'added_ts': r[3], 'last_ts': r[4]} for r in rows]


# ------------------------------------------------------------- snapshots

def _prices_now() -> Dict[int, float]:
    return {r['netuid']: r.get('price') or 0.0
            for r in history.screener(sparks=False).get('rows', [])}


def _read_positions(bt, address: str, prices: Dict[int, float]) -> Dict:
    """One account's live state: free TAO + alpha positions, valued now."""
    free = float(bt.balance(address) or 0.0)
    infos = bt.subtensor.get_stake_info_for_coldkey(address) or []
    positions, staked = [], 0.0
    for si in infos:
        alpha = float(getattr(si, 'stake', 0) or 0)
        if alpha <= 0:
            continue
        netuid = getattr(si, 'netuid', None)
        price = prices.get(netuid, 0.0)
        value = alpha * price
        staked += value
        positions.append({'netuid': netuid,
                          'hotkey': getattr(si, 'hotkey_ss58', None),
                          'alpha': alpha, 'price': price, 'value_tao': value})
    positions.sort(key=lambda p: p['value_tao'], reverse=True)
    return {'free_tao': free, 'staked_tao': staked,
            'total_tao': free + staked, 'positions': positions}


def _by_netuid(positions: List[Dict]) -> Dict[int, Dict]:
    """Collapse per-hotkey positions into one row per subnet."""
    out: Dict[int, Dict] = {}
    for p in positions:
        uid = p['netuid']
        row = out.setdefault(uid, {'netuid': uid, 'alpha': 0.0,
                                   'price': p.get('price') or 0.0,
                                   'value_tao': 0.0, 'hotkeys': []})
        row['alpha'] += p.get('alpha') or 0.0
        row['value_tao'] += p.get('value_tao') or 0.0
        row['price'] = p.get('price') or row['price']
        if p.get('hotkey'):
            row['hotkeys'].append(p['hotkey'])
    return out


def _record(address: str, ts: int, state: Dict) -> List[Dict]:
    """Persist a snapshot and derive the flows since the previous one."""
    with _db_lock:
        conn = _db()
        try:
            prev = conn.execute(
                'SELECT ts, positions FROM trader_snaps WHERE ss58 = ? '
                'ORDER BY ts DESC LIMIT 1', (address,)).fetchone()
            conn.execute(
                'INSERT OR REPLACE INTO trader_snaps VALUES (?,?,?,?,?,?)',
                (address, ts, state['free_tao'], state['staked_tao'],
                 state['total_tao'], json.dumps(state['positions'])))
            flows = _diff(json.loads(prev[1]) if prev else None,
                          state['positions']) if prev else []
            if flows:
                conn.executemany(
                    'INSERT OR REPLACE INTO trader_flows VALUES (?,?,?,?,?,?,?)',
                    [(ts, address, f['netuid'], f['side'], f['alpha'],
                      f['price'], f['tao_value']) for f in flows])
            conn.execute('UPDATE traders SET last_ts = ? WHERE ss58 = ?',
                         (ts, address))
            conn.commit()
        finally:
            conn.close()
    return flows


def _diff(before: Optional[List[Dict]], after: List[Dict]) -> List[Dict]:
    """Infer buys/sells from the change in alpha held per subnet."""
    if before is None:
        return []
    old, new = _by_netuid(before), _by_netuid(after)
    flows = []
    for uid in set(old) | set(new):
        a0 = old.get(uid, {}).get('alpha', 0.0)
        a1 = new.get(uid, {}).get('alpha', 0.0)
        delta = a1 - a0
        if delta == 0:
            continue
        price = (new.get(uid) or old.get(uid) or {}).get('price') or 0.0
        tao = abs(delta) * price
        if abs(delta) < FLOW_MIN_FRACTION * max(a0, a1) or tao < FLOW_MIN_TAO:
            continue  # emission drift / dust, not a trade
        flows.append({'netuid': uid, 'side': 'buy' if delta > 0 else 'sell',
                      'alpha': abs(delta), 'price': price, 'tao_value': tao})
    flows.sort(key=lambda f: f['tao_value'], reverse=True)
    return flows


def snapshot(address: str, bt=None) -> Dict:
    """Snapshot one account now (chain read), persist, return the state."""
    if bt is None:
        from .tools import get_bt
        bt = get_bt()
    ts = int(time.time())
    state = _read_positions(bt, address, _prices_now())
    flows = _record(address, ts, state)
    return {'ss58': address, 'ts': ts, **state, 'flows': flows}


def snapshot_all(bt=None) -> Dict:
    """Snapshot every tracked account. Errors are per-account, not fatal."""
    ok, errors = [], {}
    for t in watchlist():
        try:
            snap = snapshot(t['ss58'], bt=bt)
            ok.append({'ss58': t['ss58'], 'total_tao': snap['total_tao'],
                       'flows': len(snap['flows'])})
        except Exception as e:
            errors[t['ss58']] = f'{type(e).__name__}: {e}'
    return {'snapped': len(ok), 'accounts': ok, 'errors': errors,
            'ts': int(time.time())}


# ------------------------------------------------------------- queries

def _snap_at(conn, address: str, ts: int, tolerance: float = 0.5,
             horizon: Optional[int] = None) -> Optional[Dict]:
    """The stored snapshot closest to ts, or None if none is close enough."""
    row = conn.execute(
        'SELECT ts, free_tao, staked_tao, total_tao, positions FROM trader_snaps '
        'WHERE ss58 = ? ORDER BY ABS(ts - ?) LIMIT 1', (address, ts)).fetchone()
    if not row:
        return None
    if horizon and abs(row[0] - ts) > tolerance * horizon:
        return None
    return {'ts': row[0], 'free_tao': row[1], 'staked_tao': row[2],
            'total_tao': row[3], 'positions': json.loads(row[4])}


def positions_at(address: str, ts: Optional[int] = None,
                 tolerance_sec: int = 0) -> Dict:
    """What this account held at a moment in time, from the local index.

    The archive-node replacement: any module needing historical positions
    reads them here instead of paying a deep-block state query.
    """
    ts = int(ts if ts is not None else time.time())
    with _db_lock:
        conn = _db()
        try:
            snap = _snap_at(conn, address, ts,
                            horizon=tolerance_sec or None, tolerance=1.0)
        finally:
            conn.close()
    if snap is None:
        return {'ss58': address, 'ts': ts, 'found': False, 'positions': [],
                'note': 'no snapshot in range — track this address first'}
    return {'ss58': address, 'requested_ts': ts, 'found': True,
            'age_sec': abs(snap['ts'] - ts), **snap}


def prices_at(ts: Optional[int] = None) -> Dict:
    """Every subnet's alpha price at a moment in time (from bt.history)."""
    now = int(time.time())
    ts = int(ts if ts is not None else now)
    ago = max(now - ts, 0)
    prices = _prices_now() if ago < 60 else history._prices_at(
        ago, now, tolerance=1.0)
    return {'ts': ts, 'count': len(prices),
            'prices': {str(k): v for k, v in prices.items()}}


def _perf(conn, address: str, total_now: float,
          prices: Dict[int, float]) -> Dict:
    """Window PnL: realized value change, plus price-only PnL on holdings."""
    now = int(time.time())
    out: Dict[str, Any] = {}
    for key, horizon in WINDOWS.items():
        past = _snap_at(conn, address, now - horizon, horizon=horizon)
        if not past or not past['total_tao']:
            out[key] = None
            out[key.replace('change', 'pnl')] = None
            continue
        out[key] = (total_now / past['total_tao'] - 1.0) * 100.0
        out[key.replace('change', 'pnl')] = total_now - past['total_tao']
        if key == 'change_24h':
            # What the earlier allocation earned on price alone — free of
            # deposits, withdrawals and rebalances.
            hold = 0.0
            for uid, row in _by_netuid(past['positions']).items():
                p_now = prices.get(uid)
                if p_now is None or not row['price']:
                    continue
                hold += row['alpha'] * (p_now - row['price'])
            out['hold_pnl_24h'] = hold
    return out


def _spark(conn, address: str, hours: int = 24, points: int = 32) -> List[float]:
    rows = conn.execute(
        'SELECT total_tao FROM trader_snaps WHERE ss58 = ? AND ts >= ? ORDER BY ts',
        (address, int(time.time()) - hours * 3600)).fetchall()
    vals = [r[0] for r in rows]
    if len(vals) > points:
        step = len(vals) / points
        vals = [vals[int(i * step)] for i in range(points - 1)] + [vals[-1]]
    return [round(v, 6) for v in vals]


def traders(sort_by: str = 'total_tao', limit: int = 0,
            sparks: bool = True) -> Dict:
    """The tracked-trader table: value, allocation, PnL — served locally."""
    prices = _prices_now()
    rows = []
    with _db_lock:
        conn = _db()
        try:
            for t in [dict(zip(('ss58', 'label', 'network', 'added_ts', 'last_ts'), r))
                      for r in conn.execute(
                          'SELECT ss58, label, network, added_ts, last_ts FROM traders'
                      ).fetchall()]:
                snap = _snap_at(conn, t['ss58'], int(time.time()))
                row = dict(t)
                if snap is None:
                    row.update({'total_tao': None, 'free_tao': None,
                                'staked_tao': None, 'subnets': 0,
                                'positions': [], 'warming': True})
                    rows.append(row)
                    continue
                by_uid = _by_netuid(snap['positions'])
                # revalue at current prices so the table never shows a stale mark
                staked = sum((prices.get(uid, r['price']) or 0.0) * r['alpha']
                             for uid, r in by_uid.items())
                total = snap['free_tao'] + staked
                row.update({
                    'snapshot_ts': snap['ts'], 'free_tao': snap['free_tao'],
                    'staked_tao': staked, 'total_tao': total,
                    'subnets': len(by_uid),
                    'top_subnets': sorted(
                        [{'netuid': uid,
                          'value_tao': (prices.get(uid, r['price']) or 0.0) * r['alpha'],
                          'alpha': r['alpha']} for uid, r in by_uid.items()],
                        key=lambda x: x['value_tao'], reverse=True)[:3],
                    'snapshots': conn.execute(
                        'SELECT COUNT(*) FROM trader_snaps WHERE ss58 = ?',
                        (t['ss58'],)).fetchone()[0],
                    'flows_24h': conn.execute(
                        'SELECT COUNT(*) FROM trader_flows WHERE ss58 = ? AND ts >= ?',
                        (t['ss58'], int(time.time()) - 86400)).fetchone()[0],
                })
                row.update(_perf(conn, t['ss58'], total, prices))
                if sparks:
                    row['spark'] = _spark(conn, t['ss58'])
                rows.append(row)
        finally:
            conn.close()
    rows.sort(key=lambda r: r.get(sort_by) or 0.0, reverse=True)
    if limit:
        rows = rows[:limit]
    return {'count': len(rows), 'updated_at': int(time.time()), 'rows': rows}


BOARD_SORTS = ('market_pct', 'market_pnl_tao', 'pnl_pct', 'pnl_tao',
               'total_stake_tao', 'num_subnets')


def _board_row(conn, t: Dict, window: int, prices: Dict[int, float],
               sparks: bool, names: Dict[int, str]) -> Dict:
    """One trader ranked over `window` seconds, from the index alone."""
    row = {'ss58': t['ss58'], 'label': t['label'],
           'pnl_tao': 0.0, 'pnl_pct': 0.0, 'market_pnl_tao': 0.0,
           'market_pct': 0.0, 'flow_tao': 0.0, 'num_subnets': 0,
           'top_subnet': None, 'top_subnet_name': None, 'top_subnet_pnl': 0.0,
           'baseline': False, 'window_days': 0.0, 'total_stake_tao': 0.0,
           'free_tao': None}

    now = int(time.time())
    end = conn.execute(
        'SELECT ts, free_tao, positions FROM trader_snaps WHERE ss58 = ? '
        'ORDER BY ts DESC LIMIT 1', (t['ss58'],)).fetchone()
    if end is None:
        return row  # tracked, never snapshotted yet

    after = _by_netuid(json.loads(end[2]))
    row['free_tao'] = end[1]
    row['num_subnets'] = len(after)
    # Mark the book at live prices, like the trader table does, so the board
    # never ranks on a stale snapshot price.
    row['total_stake_tao'] = sum((prices.get(uid) or r['price'] or 0.0) * r['alpha']
                                 for uid, r in after.items())
    if sparks:
        row['spark'] = _spark(conn, t['ss58'], hours=max(1, window // 3600))

    # The oldest snapshot inside the window is the baseline. A trader tracked
    # for less than the horizon is ranked over the history that exists, and
    # window_days says so — a 30d column must never quietly show 3 days for
    # one trader and 30 for the next.
    start = conn.execute(
        'SELECT ts, positions FROM trader_snaps WHERE ss58 = ? AND ts >= ? '
        'ORDER BY ts LIMIT 1', (t['ss58'], now - window)).fetchone()
    if start is None or start[0] >= end[0]:
        return row

    before = _by_netuid(json.loads(start[1]))
    start_value = market = flow = 0.0
    top_uid, top_pnl = None, 0.0

    for uid in set(before) | set(after):
        b, a = before.get(uid), after.get(uid)
        alpha_b = b['alpha'] if b else 0.0
        alpha_a = a['alpha'] if a else 0.0
        price_b = (b['price'] if b else 0.0) or 0.0
        # A position that left the book has no live mark of its own; value it
        # at the price it had, so the exit reads as a withdrawal not a wipeout.
        price_a = prices.get(uid) or (a['price'] if a else 0.0) or price_b
        price_b = price_b or price_a

        start_value += alpha_b * price_b
        market += alpha_b * (price_a - price_b)
        flow += (alpha_a - alpha_b) * price_a

        sn_pnl = alpha_a * price_a - alpha_b * price_b
        if sn_pnl > top_pnl:
            top_uid, top_pnl = uid, sn_pnl

    # market + flow is exactly end_value − start_value, by construction.
    pnl = market + flow
    row.update({
        'baseline': True,
        'window_days': round((end[0] - start[0]) / 86400, 2),
        'start_value_tao': start_value,
        'pnl_tao': pnl,
        'pnl_pct': (pnl / start_value * 100) if start_value > 0 else 0.0,
        'market_pnl_tao': market,
        'market_pct': (market / start_value * 100) if start_value > 0 else 0.0,
        'flow_tao': flow,
        'top_subnet': top_uid,
        'top_subnet_name': names.get(top_uid) if top_uid is not None else None,
        'top_subnet_pnl': top_pnl,
    })
    return row


def board(days: int = 7, top: int = 0, min_subnets: int = 0,
          sort_by: str = 'market_pct', sparks: bool = False) -> Dict:
    """Rank every tracked trader by what they actually earned over `days`.

    One pass over the index — no chain round-trip, no archive node. For each
    trader the oldest snapshot inside the window is the baseline and the
    newest is the mark, and the change between them splits exactly into

        market = Σ alpha_start · (price_end − price_start)   what the book did
        flow   = Σ (alpha_end − alpha_start) · price_end     what was staked

    Both are reported because ranking on the raw percentage puts every fresh
    deposit above every real trader: a coldkey that merely wired stake in
    shows a huge pnl_pct and a market_pct of nothing. `market_pct` is the
    column that measures trading, so it is the default sort.
    """
    window = max(1, int(days)) * 86400
    prices = _prices_now()
    names = {r['netuid']: r.get('name')
             for r in history.screener(sparks=False).get('rows', [])}
    with _db_lock:
        conn = _db()
        try:
            tracked = [dict(zip(('ss58', 'label'), r)) for r in conn.execute(
                'SELECT ss58, label FROM traders').fetchall()]
            rows = [_board_row(conn, t, window, prices, sparks, names)
                    for t in tracked]
        finally:
            conn.close()

    rows = [r for r in rows if r['num_subnets'] >= min_subnets]
    key = sort_by if sort_by in BOARD_SORTS else 'market_pct'
    # Unpriced rows all tie at 0; size breaks it so the table stays stable.
    rows.sort(key=lambda r: (r.get(key) or 0.0, r['total_stake_tao']),
              reverse=True)
    if top:
        rows = rows[:top]
    return {'days': days, 'count': len(rows), 'sort_by': key,
            'priced': sum(1 for r in rows if r['baseline']),
            'updated_at': int(time.time()), 'rows': rows}


def trader(address: str, hours: int = 168, flows_limit: int = 50) -> Dict:
    """One trader in full: positions now, equity curve, inferred trade tape."""
    prices = _prices_now()
    names = {r['netuid']: r.get('name')
             for r in history.screener(sparks=False).get('rows', [])}
    with _db_lock:
        conn = _db()
        try:
            meta = conn.execute(
                'SELECT label, network, added_ts, last_ts FROM traders WHERE ss58 = ?',
                (address,)).fetchone()
            snap = _snap_at(conn, address, int(time.time()))
            out: Dict[str, Any] = {
                'ss58': address, 'tracked': meta is not None,
                'label': meta[0] if meta else None,
                'added_ts': meta[2] if meta else None,
                'last_ts': meta[3] if meta else None,
            }
            if snap is None:
                out.update({'warming': True, 'positions': [], 'series': [],
                            'flows': [], 'total_tao': None,
                            'note': 'no snapshot yet — call bt_track, then bt_trader_snapshot'})
                return out
            by_uid = _by_netuid(snap['positions'])
            positions = []
            for uid, r in sorted(by_uid.items(),
                                 key=lambda kv: kv[1]['value_tao'], reverse=True):
                price = prices.get(uid, r['price']) or 0.0
                positions.append({
                    'netuid': uid, 'name': names.get(uid) or f'subnet {uid}',
                    'alpha': r['alpha'], 'price': price,
                    'value_tao': price * r['alpha'],
                    'hotkeys': r['hotkeys'],
                })
            staked = sum(p['value_tao'] for p in positions)
            total = snap['free_tao'] + staked
            for p in positions:
                p['pct_of_total'] = (p['value_tao'] / total * 100.0) if total else 0.0
            out.update({
                'snapshot_ts': snap['ts'], 'free_tao': snap['free_tao'],
                'staked_tao': staked, 'total_tao': total,
                'subnets': len(positions), 'positions': positions,
                'series': _series(conn, address, hours),
                'flows': _flows(conn, address, hours=hours * 4,
                                limit=flows_limit),
                'snapshots': conn.execute(
                    'SELECT COUNT(*) FROM trader_snaps WHERE ss58 = ?',
                    (address,)).fetchone()[0],
            })
            out.update(_perf(conn, address, total, prices))
            return out
        finally:
            conn.close()


def _series(conn, address: str, hours: int, points: int = 300) -> List[Dict]:
    rows = conn.execute(
        'SELECT ts, free_tao, staked_tao, total_tao FROM trader_snaps '
        'WHERE ss58 = ? AND ts >= ? ORDER BY ts',
        (address, int(time.time()) - hours * 3600)).fetchall()
    if len(rows) > points:
        step = len(rows) / points
        rows = [rows[int(i * step)] for i in range(points - 1)] + [rows[-1]]
    return [{'t': t, 'free_tao': f, 'staked_tao': s, 'total_tao': v}
            for t, f, s, v in rows]


def series(address: str, hours: int = 168, points: int = 300) -> Dict:
    with _db_lock:
        conn = _db()
        try:
            return {'ss58': address, 'hours': hours,
                    'series': _series(conn, address, hours, points)}
        finally:
            conn.close()


def _flows(conn, address: Optional[str], hours: int, limit: int) -> List[Dict]:
    since = int(time.time()) - hours * 3600
    if address:
        rows = conn.execute(
            'SELECT ts, ss58, netuid, side, alpha, price, tao_value FROM trader_flows '
            'WHERE ss58 = ? AND ts >= ? ORDER BY ts DESC LIMIT ?',
            (address, since, limit)).fetchall()
    else:
        rows = conn.execute(
            'SELECT ts, ss58, netuid, side, alpha, price, tao_value FROM trader_flows '
            'WHERE ts >= ? ORDER BY ts DESC LIMIT ?', (since, limit)).fetchall()
    labels = dict(conn.execute('SELECT ss58, label FROM traders').fetchall())
    return [{'ts': t, 'ss58': s, 'label': labels.get(s), 'netuid': u,
             'side': side, 'alpha': a, 'price': p, 'tao_value': v,
             'inferred': True}
            for t, s, u, side, a, p, v in rows]


def flows(address: Optional[str] = None, hours: int = 168,
          limit: int = 100) -> Dict:
    """The inferred trade tape — position changes between snapshots."""
    with _db_lock:
        conn = _db()
        try:
            rows = _flows(conn, address, hours, limit)
        finally:
            conn.close()
    names = {r['netuid']: r.get('name')
             for r in history.screener(sparks=False).get('rows', [])}
    for r in rows:
        r['name'] = names.get(r['netuid']) or f"subnet {r['netuid']}"
    return {'count': len(rows), 'hours': hours, 'ss58': address,
            'note': 'inferred from snapshot deltas, not extrinsics: a move '
                    f'under {int(FLOW_MIN_FRACTION * 100)}% of the position '
                    f'or under {FLOW_MIN_TAO} TAO is treated as emission '
                    'drift and dropped, and a large dividend payout can '
                    'still read as a small buy',
            'flows': rows}


def stats() -> Dict:
    with _db_lock:
        conn = _db()
        try:
            n = conn.execute('SELECT COUNT(*) FROM traders').fetchone()[0]
            snaps = conn.execute('SELECT COUNT(*) FROM trader_snaps').fetchone()[0]
            fl = conn.execute('SELECT COUNT(*) FROM trader_flows').fetchone()[0]
            last = conn.execute('SELECT MAX(ts) FROM trader_snaps').fetchone()[0]
        finally:
            conn.close()
    return {'tracked': n, 'snapshots': snaps, 'flows': fl,
            'last_snapshot_ts': last, 'refresh_sec': REFRESH_SEC}


# ------------------------------------------------------------- refresher

def _loop() -> None:
    bt = None
    while True:
        try:
            if watchlist():
                if bt is None:
                    from .bt import Bt
                    from .tools import DEFAULT_NETWORK
                    bt = Bt(network=DEFAULT_NETWORK)
                out = snapshot_all(bt=bt)
                print(f"[bt.traders] snapshot: {out['snapped']} traders, "
                      f"{len(out['errors'])} errors", flush=True)
                if out['errors']:
                    bt = None  # a dead socket shows up as per-account errors
        except Exception as e:
            print(f'[bt.traders] snapshot failed: {type(e).__name__}: {e}',
                  flush=True)
            bt = None
        time.sleep(REFRESH_SEC)


def start() -> None:
    """Start the background trader snapshotter (idempotent)."""
    global _thread
    if os.environ.get('BT_NO_SNAPSHOT') == '1':
        return
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name='bt-traders', daemon=True)
    _thread.start()
