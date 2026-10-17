"""The local economy — a simulated market agents can live in.

Three parts, all local, no chain, no network needed:

  * a **price feed** that ticks every few seconds: geometric Brownian
    motion per asset, anchored on the live price when prefi can see one
    and on a default when it can't, nudged by the agents' own order
    flow (every buy pushes the price up by notional / liquidity);
  * an **exchange** — a PAPER-denominated cash + holdings ledger per
    account, market orders at the feed price plus a spread, so agents
    can trade as well as predict;
  * a **one-minute paper pool** — the same hash-chained, wasm-settled
    paper pool prefi already runs (paper.py), opened at
    ``<store>/economy/paper`` with ``interval = 60`` so an agent can call
    a close every minute and be scored sixty seconds later. The feed is
    that pool's oracle: resolution reads the stored tick at the close,
    so replay never asks the world anything.

Money is PAPER everywhere and worth nothing; the record is the product.
"""

import json
import math
import os
import random
import threading
import time as time_mod
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import identity
    import paper as paper_mod
except ImportError:                                   # imported as a package
    from . import identity
    from . import paper as paper_mod

DEFAULT_CONFIG = {
    'tick': 5,                 # seconds between feed ticks
    'interval': 60,            # a prediction round a minute
    'entry_cutoff': 5,         # calls close this long before the minute ends
    'vol': 0.0025,             # per-minute volatility (≈ 0.25%)
    'drift': 0.0,              # per-minute drift
    'liquidity': 250_000.0,    # PAPER notional that moves a price 100%
    'spread_bps': 10,          # half-spread paid on every fill
    'faucet_grant': 1000.0,    # exchange cash per grant
    'faucet_interval': 3600,   # one grant an hour per account
    'history': 2880,           # ticks kept per asset (4h at 5s)
    'feed': 'sim',             # sim | live  (live = anchor every tick on prefi's oracle)
    'seed': None,              # fixed seed = reproducible economy
}

PAPER_CONFIG = {
    'interval': 60, 'entry_cutoff': 5,
    'faucet_grant': 1000 * paper_mod.MICRO, 'faucet_interval': 3600,
    'per_round': 20, 'min_stake': paper_mod.MICRO, 'max_stake': 0,
    'tol_ppm': 10_000,        # a 1% miss scores zero on a one-minute call
    'open': True,
}

DEFAULT_ASSETS = {'BTC': 110_000.0, 'ETH': 4_000.0, 'SOL': 200.0,
                  'HYPE': 45.0, 'TAO': 450.0}
MAX_TRADES = 2000


class EconomyError(ValueError):
    pass


class Economy:

    def __init__(self, store_dir, markets: Callable = None,
                 price_now: Callable = None, price_at: Callable = None,
                 config: Dict = None, assets: Dict[str, float] = None):
        self.dir = Path(store_dir) / 'economy'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'economy.json'
        self.lock_path = self.dir / 'lock'
        self.live_markets = markets or (lambda: [])
        self.live_price_now = price_now
        self.live_price_at = price_at
        self._config_override = dict(config or {})
        self._assets_override = dict(assets or {})
        self._state: Optional[Dict] = None
        self._rlock = threading.RLock()
        self._lock_depth = 0
        self._paper: Optional[paper_mod.Paper] = None

    # ── Storage ──────────────────────────────────────────────────────

    @contextmanager
    def _lock(self):
        # Re-entrant: the RLock serialises threads, the depth counter keeps
        # a nested call from opening a second fd — flock would block on it.
        with self._rlock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            self._lock_depth = 1
            try:
                try:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except (ImportError, OSError):        # pragma: no cover
                    pass
                yield
            finally:
                self._lock_depth = 0
                os.close(fd)

    def _fresh(self) -> Dict:
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(self._config_override)
        now = int(time_mod.time())
        assets = {}
        seeds = self._assets_override or self._seed_assets()
        for sym, px in seeds.items():
            assets[sym] = {'price': float(px), 'anchor': float(px),
                           'history': [[now, float(px)]]}
        return {'v': 1, 'config': cfg, 'created': now, 'ticks': 0,
                'last_tick': now, 'assets': assets, 'ledger': {},
                'trades': [], 'rng': cfg['seed'] if cfg['seed'] is not None
                else random.randrange(1 << 30)}

    def _seed_assets(self) -> Dict[str, float]:
        """Listed prefi markets that quote a price, else the defaults."""
        found = {}
        try:
            for m in self.live_markets() or []:
                sym = m.get('symbol')
                if not sym or sym in found:
                    continue
                px = None
                if self.live_price_now:
                    try:
                        px = self.live_price_now(sym)
                    except Exception:
                        px = None
                if px and px > 0:
                    found[sym] = float(px)
                if len(found) >= 8:
                    break
        except Exception:
            found = {}
        return found or dict(DEFAULT_ASSETS)

    def state(self) -> Dict:
        if self._state is not None:
            return self._state
        with self._lock():
            if self._state is not None:
                return self._state
            if self.path.exists():
                try:
                    self._state = json.loads(self.path.read_text())
                    cfg = dict(DEFAULT_CONFIG)
                    cfg.update(self._state.get('config') or {})
                    self._state['config'] = cfg      # backfill new keys
                    for key in ('ledger', 'trades', 'assets'):
                        self._state.setdefault(key, {} if key != 'trades' else [])
                    return self._state
                except (json.JSONDecodeError, OSError):
                    pass
            self._state = self._fresh()
            self._save()
            return self._state

    def _save(self):
        tmp = self.path.with_suffix('.json.tmp')
        with open(tmp, 'w') as fh:
            json.dump(self._state, fh)
        os.replace(tmp, self.path)

    def reset(self, assets: Dict[str, float] = None, **config) -> Dict:
        """Wipe the economy — ledger, trades, feed and the minute pool."""
        import shutil
        with self._lock():
            self._state = None
            self._paper = None
            if config:
                self._config_override.update(config)
            if assets:
                self._assets_override = dict(assets)
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir.mkdir(parents=True, exist_ok=True)
            self.state()
        return self.status()

    # ── The minute pool ──────────────────────────────────────────────

    @property
    def paper(self) -> paper_mod.Paper:
        if self._paper is None:
            cfg = self.state()['config']
            pcfg = dict(PAPER_CONFIG, interval=int(cfg['interval']),
                        entry_cutoff=int(cfg['entry_cutoff']))
            self._paper = paper_mod.Paper(
                self.dir, price_at=self.price_at, price_now=self.price_now,
                markets=self.markets, config=pcfg)
        return self._paper

    def markets(self) -> List[Dict]:
        return [{'symbol': sym, 'source': 'economy', 'price': a['price']}
                for sym, a in self.state()['assets'].items()]

    # ── Feed ─────────────────────────────────────────────────────────

    def _rng(self) -> random.Random:
        st = self.state()
        r = random.Random(st['rng'])
        st['rng'] = (st['rng'] * 6364136223846793005 + 1442695040888963407) \
            % (1 << 63)
        return r

    def tick(self, now: float = None) -> Dict:
        """Advance the feed one step and settle whatever minute is due."""
        with self._lock():
            st = self.state()
            cfg = st['config']
            now = int(now or time_mod.time())
            if now <= st['last_tick']:
                # a clock behind the last tick (skew, a replayed step)
                # must not write history out of order
                return {'tick': st['ticks'], 'time': st['last_tick'],
                        'prices': self.prices(), 'settled': None}
            dt_min = (now - st['last_tick']) / 60.0
            rng = self._rng()
            vol, drift = float(cfg['vol']), float(cfg['drift'])
            live = {}
            if cfg['feed'] == 'live' and self.live_price_now:
                for sym in st['assets']:
                    try:
                        live[sym] = self.live_price_now(sym)
                    except Exception:
                        pass
            for sym, a in st['assets'].items():
                px = a['price']
                if live.get(sym):
                    px = float(live[sym])
                elif dt_min > 0:
                    z = rng.gauss(0.0, 1.0)
                    px *= math.exp((drift - vol * vol / 2) * dt_min
                                   + vol * math.sqrt(dt_min) * z)
                px = max(px, 1e-9)
                a['price'] = px
                a['history'].append([now, px])
                keep = int(cfg['history'])
                if len(a['history']) > keep:
                    del a['history'][:len(a['history']) - keep]
            st['ticks'] += 1
            st['last_tick'] = now
            self._save()
        settled = None
        try:
            settled = self.paper.resolve(now=now)
        except Exception as err:                      # pragma: no cover
            settled = {'error': str(err)}
        return {'tick': st['ticks'], 'time': now, 'prices': self.prices(),
                'settled': settled}

    def price_now(self, asset: str, *_a, **_k) -> Optional[float]:
        a = self.state()['assets'].get(self._sym(asset))
        return a['price'] if a else None

    def price_at(self, asset: str, ts: float, *_a, **_k) -> Dict:
        """The last stored tick at or before `ts` — what a minute settles on."""
        a = self.state()['assets'].get(self._sym(asset))
        if not a:
            return {}
        best = None
        for t, px in a['history']:
            if t <= ts:
                best = px
            else:
                break
        if best is None:
            return {'price': a['history'][0][1], 'mode': 'first-tick'}
        return {'price': best, 'mode': 'tick'}

    def _sym(self, asset: str) -> str:
        want = str(asset or '').strip().upper()
        for sym in self.state()['assets']:
            if sym.upper() == want:
                return sym
        return want

    def prices(self) -> Dict[str, Dict]:
        out = {}
        now = time_mod.time()
        for sym, a in self.state()['assets'].items():
            hist = a['history']
            px = a['price']
            out[sym] = {'price': px,
                        'change_1m': self._change(hist, now - 60, px),
                        'change_1h': self._change(hist, now - 3600, px),
                        'anchor': a['anchor']}
        return out

    @staticmethod
    def _change(hist: List, since: float, px: float) -> Optional[float]:
        ref = None
        for t, p in hist:
            if t <= since:
                ref = p
            else:
                break
        if ref is None:
            ref = hist[0][1] if hist else None
        return (px / ref - 1.0) if ref else None

    def history(self, asset: str, limit: int = 120) -> List[List[float]]:
        a = self.state()['assets'].get(self._sym(asset))
        if not a:
            return []
        return a['history'][-int(limit):]

    def add_asset(self, symbol: str, price: float = None) -> Dict:
        sym = str(symbol or '').strip().upper()
        if not sym:
            raise EconomyError('symbol required')
        with self._lock():
            st = self.state()
            if sym in st['assets']:
                return {'symbol': sym, 'price': st['assets'][sym]['price']}
            px = price
            if (not px or px <= 0) and self.live_price_now:
                try:
                    px = self.live_price_now(sym)
                except Exception:
                    px = None
            if not px or px <= 0:
                px = DEFAULT_ASSETS.get(sym)
            if not px or px <= 0:
                raise EconomyError(f'no price for {sym} — pass price=')
            now = int(time_mod.time())
            st['assets'][sym] = {'price': float(px), 'anchor': float(px),
                                 'history': [[now, float(px)]]}
            self._save()
        return {'symbol': sym, 'price': float(px)}

    # ── Exchange ─────────────────────────────────────────────────────

    def _acct(self, st: Dict, address: str) -> Dict:
        acct = st['ledger'].get(address)
        if acct is None:
            acct = {'cash': 0.0, 'holdings': {}, 'cost': {}, 'realized': 0.0,
                    'fees': 0.0, 'trades': 0, 'last_faucet': 0,
                    'created': int(time_mod.time())}
            st['ledger'][address] = acct
        return acct

    def faucet(self, address: str) -> Dict:
        """Exchange cash AND minute-pool PAPER in one grant."""
        address = identity.normalize(address)
        with self._lock():
            st = self.state()
            cfg = st['config']
            acct = self._acct(st, address)
            now = int(time_mod.time())
            granted = 0.0
            if now - acct['last_faucet'] >= int(cfg['faucet_interval']):
                acct['cash'] += float(cfg['faucet_grant'])
                acct['last_faucet'] = now
                granted = float(cfg['faucet_grant'])
            self._save()
        pool = self.paper.faucet(address)
        out = self.account(address)
        out['granted'] = granted
        out['pool_granted'] = pool.get('granted', 0.0) \
            if 'error' not in pool else 0.0
        if 'error' in pool and not granted:
            out['error'] = pool['error']
        return out

    def trade(self, address: str, asset: str, side: str,
              notional: float = None, qty: float = None) -> Dict:
        address = identity.normalize(address)
        side = str(side or '').lower()
        if side not in ('buy', 'sell'):
            raise EconomyError("side must be 'buy' or 'sell'")
        with self._lock():
            st = self.state()
            cfg = st['config']
            sym = self._sym(asset)
            a = st['assets'].get(sym)
            if not a:
                raise EconomyError(f'unknown asset {asset!r}')
            acct = self._acct(st, address)
            mid = a['price']
            half = int(cfg['spread_bps']) / 10_000.0
            fill = mid * (1 + half) if side == 'buy' else mid * (1 - half)
            if qty is not None:
                qty = float(qty)
                notional = qty * fill
            else:
                notional = float(notional or 0)
                qty = notional / fill
            if notional <= 0 or qty <= 0:
                raise EconomyError('notional must be positive')
            held = acct['holdings'].get(sym, 0.0)
            if side == 'buy':
                if acct['cash'] + 1e-9 < notional:
                    raise EconomyError(f'insufficient cash ({acct["cash"]:.2f} '
                                       f'PAPER, need {notional:.2f})')
                acct['cash'] -= notional
                prev_cost = acct['cost'].get(sym, 0.0)
                acct['holdings'][sym] = held + qty
                acct['cost'][sym] = prev_cost + notional
            else:
                if held + 1e-12 < qty:
                    raise EconomyError(f'insufficient {sym} (hold {held:.6f})')
                avg = acct['cost'].get(sym, 0.0) / held if held else fill
                acct['cash'] += notional
                acct['realized'] += (fill - avg) * qty
                acct['holdings'][sym] = held - qty
                acct['cost'][sym] = max(0.0, acct['cost'].get(sym, 0.0)
                                        - avg * qty)
                if acct['holdings'][sym] <= 1e-12:
                    acct['holdings'].pop(sym, None)
                    acct['cost'].pop(sym, None)
            fee = notional * half
            acct['fees'] += fee
            acct['trades'] += 1
            # order flow moves the market
            impact = notional / float(cfg['liquidity'])
            a['price'] = max(1e-9, mid * (1 + impact if side == 'buy'
                                          else 1 - impact))
            row = {'time': int(time_mod.time()), 'address': address,
                   'asset': sym, 'side': side, 'qty': qty,
                   'price': fill, 'notional': notional, 'fee': fee,
                   'impact': impact if side == 'buy' else -impact}
            st['trades'].append(row)
            if len(st['trades']) > MAX_TRADES:
                del st['trades'][:len(st['trades']) - MAX_TRADES]
            self._save()
        return dict(row, cash=acct['cash'],
                    holdings=dict(acct['holdings']))

    def predict(self, address: str, asset: str, price: float,
                stake: float, signature: str = None,
                now: float = None) -> Dict:
        return self.paper.predict(address, self._sym(asset), price, stake,
                                  signature=signature, now=now)

    # ── Reads ────────────────────────────────────────────────────────

    def account(self, address: str) -> Dict:
        address = identity.normalize(address)
        st = self.state()
        acct = st['ledger'].get(address) or {
            'cash': 0.0, 'holdings': {}, 'cost': {}, 'realized': 0.0,
            'fees': 0.0, 'trades': 0, 'last_faucet': 0}
        holdings = {}
        value = 0.0
        for sym, qty in acct['holdings'].items():
            px = st['assets'].get(sym, {}).get('price', 0.0)
            cost = acct['cost'].get(sym, 0.0)
            holdings[sym] = {'qty': qty, 'price': px, 'value': qty * px,
                             'avg': cost / qty if qty else 0.0,
                             'unrealized': qty * px - cost}
            value += qty * px
        pool = self.paper.account(address)
        equity = acct['cash'] + value
        return {
            'address': address, 'cash': acct['cash'], 'holdings': holdings,
            'holdings_value': value, 'equity': equity,
            'realized': acct['realized'], 'fees': acct['fees'],
            'trades': acct['trades'],
            'next_faucet': acct['last_faucet']
            + int(st['config']['faucet_interval']),
            'pool': pool,
            'net_worth': equity + float(pool.get('balance') or 0.0)
            + float(pool.get('staked') or 0.0),
        }

    def leaderboard(self, limit: int = 50, names: Dict[str, str] = None) \
            -> List[Dict]:
        st = self.state()
        pool_rows = {r['address']: r for r in self.paper.leaderboard(10_000)}
        addrs = set(st['ledger']) | set(pool_rows)
        rows = []
        for addr in addrs:
            acct = self.account(addr)
            pool = acct['pool']
            rows.append({'address': addr,
                         'name': (names or {}).get(addr) or pool.get('name') or '',
                         'net_worth': acct['net_worth'],
                         'cash': acct['cash'],
                         'holdings_value': acct['holdings_value'],
                         'realized': acct['realized'],
                         'trades': acct['trades'],
                         'pool_profit': pool.get('profit', 0.0),
                         'accuracy': pool.get('accuracy'),
                         'entries': pool.get('entries', 0),
                         'resolved': pool.get('resolved', 0)})
        rows.sort(key=lambda r: -r['net_worth'])
        return rows[:int(limit)]

    def trades(self, address: str = None, limit: int = 100) -> List[Dict]:
        rows = self.state()['trades']
        if address:
            address = identity.normalize(address)
            rows = [r for r in rows if r['address'] == address]
        return list(reversed(rows[-int(limit):]))

    def status(self) -> Dict:
        st = self.state()
        cfg = st['config']
        now = time_mod.time()
        pool = self.paper.status()
        return {
            'created': st['created'], 'ticks': st['ticks'],
            'last_tick': st['last_tick'], 'tick_age': now - st['last_tick'],
            'assets': len(st['assets']), 'accounts': len(st['ledger']),
            'trades': len(st['trades']),
            'volume': sum(r['notional'] for r in st['trades']),
            'config': cfg, 'feed': cfg['feed'],
            'round': pool['round'], 'round_closes': pool['round_closes'],
            'pool': pool, 'prices': self.prices(),
        }

    def config(self) -> Dict:
        return dict(self.state()['config'])

    def set_config(self, **patch) -> Dict:
        """Retune the feed/exchange. `interval`/`entry_cutoff` only apply
        on reset — the minute pool's rounds are time // interval."""
        allowed = set(DEFAULT_CONFIG)
        unknown = set(patch) - allowed
        if unknown:
            raise EconomyError(f'unknown config keys: {sorted(unknown)}')
        with self._lock():
            cfg = dict(self.state()['config'])     # validate on a copy
            for k, v in patch.items():
                if k == 'feed':
                    if v not in ('sim', 'live'):
                        raise EconomyError("feed must be 'sim' or 'live'")
                    cfg[k] = v
                elif k == 'seed':
                    cfg[k] = None if v in (None, '', 'none') else int(v)
                elif k in ('tick', 'interval', 'entry_cutoff', 'spread_bps',
                           'faucet_interval', 'history'):
                    cfg[k] = int(v)
                else:
                    cfg[k] = float(v)
            if cfg['tick'] < 1:
                raise EconomyError('tick must be at least 1s')
            if cfg['liquidity'] <= 0:
                raise EconomyError('liquidity must be positive')
            self.state()['config'] = cfg
            self._save()
        return dict(cfg)
