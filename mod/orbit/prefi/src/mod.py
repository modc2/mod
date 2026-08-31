"""PreFi - Trading Protocol

Trade assets through Uniswap V3 on Base. Profit goes to treasury,
trader receives 1 PREFI per $1 profit. Lock PREFI for staketime
to claim weekly treasury distributions. Burn PREFI to call tomorrow's
price — accuracy is scored on normalized dollar error and pays back.
"""

import json
import os
import time
import subprocess
import signal
import requests
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
from urllib.parse import quote_plus

try:
    import scoring
    import curves
    import pool as pool_mod
    import hyperevm
except ImportError:  # imported as a package (`src.mod`) rather than from src/
    from . import scoring
    from . import curves
    from . import pool as pool_mod
    from . import hyperevm


class Mod:
    description = """PreFi - Trading protocol on Base.
    Trade via Uniswap V3, profit → treasury, earn PREFI tokens.
    Lock PREFI for staketime → claim weekly treasury earnings."""

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent.parent
        # Callers that hand us nothing (the `m` CLI does) still get the real
        # ports and network — config.json is the module's own source of truth.
        if not config:
            try:
                config = json.loads((self.module_dir / 'config.json').read_text())
            except Exception:
                config = {}
        self.config = config
        self.store_dir = Path(os.environ.get('PREFI_DIR', os.path.expanduser('~/.mod/prefi')))
        self.store_dir.mkdir(parents=True, exist_ok=True)

        # Storage paths
        self.positions_path = self.store_dir / 'positions.json'
        self.stakes_path = self.store_dir / 'stakes.json'
        self.treasury_path = self.store_dir / 'treasury.json'
        self.markets_path = self.store_dir / 'markets.json'
        self.predictions_path = self.store_dir / 'predictions.json'
        self.scoring_path = self.store_dir / 'scoring.json'
        self.functions_path = self.store_dir / 'functions.json'

        # Network config
        self.network = self.config.get('network', 'baseSepolia')
        self.contracts = self.config.get('contracts', {})

        # Ports
        self.api_port = self.config.get('port', self.config.get('api_port', 50410))
        self.app_port = self.config.get('app_port', 50411)
        urls = self.config.get('urls', {})
        if urls.get('api'):
            try:
                self.api_port = int(urls['api'].split(':')[-1])
            except (ValueError, IndexError):
                pass
        if urls.get('app'):
            try:
                self.app_port = int(urls['app'].split(':')[-1])
            except (ValueError, IndexError):
                pass

        # Price cache (5-min TTL)
        self._price_cache = {}
        self._price_cache_ttl = 300
        # Which door the hyperliquid module answered on last, and where the
        # last successful fetch actually came from (None = served from cache)
        self._hl_base = None
        self._hl_last = None
        # Same two-door memory for the bt module (Bittensor subnets).
        self._bt_base = None
        self._bt_last = None
        # Where the last DEX read (DexScreener / GeckoTerminal) came from.
        self._dex_last = None
        self._pool = None

        self._load_deployment()

    # ── Storage helpers ──────────────────────────────────────────────

    def _load_json(self, path, default=None):
        if path.exists():
            with open(path, 'r') as f:
                return json.load(f)
        return default if default is not None else {}

    def _save_json(self, path, data):
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def _load_deployment(self):
        deploy_dir = self.module_dir / 'deployments'
        deploy_file = deploy_dir / f'{self.network}-latest.json'
        if deploy_file.exists():
            data = self._load_json(deploy_file)
            if data and 'contracts' in data:
                self.contracts = data['contracts']

    def _init_treasury(self):
        """Get or initialize treasury state"""
        treasury = self._load_json(self.treasury_path, {})
        treasury.setdefault('balance', 0.0)
        treasury.setdefault('total_captured', 0.0)
        treasury.setdefault('total_distributed', 0.0)
        treasury.setdefault('total_prefi_minted', 0.0)
        treasury.setdefault('epochs', [])
        if 'genesis_time' not in treasury:
            treasury['genesis_time'] = time.time()
            self._save_json(self.treasury_path, treasury)
        return treasury

    # ── Markets ──────────────────────────────────────────────────────

    def add_market(self, token: str, symbol: str, fee_tier: int = 3000,
                   source: str = 'coingecko', hl_key: str = None,
                   hl_kind: str = None, bt_netuid: int = None,
                   bt_name: str = None, bt_symbol: str = None,
                   chain: str = None, dex_pair: str = None, dex_token: str = None,
                   dex_id: str = None, dex_name: str = None,
                   liquidity_usd: float = None) -> Dict:
        """Add a supported asset market

        source picks where the price comes from: 'coingecko' (Base tokens with a
        Uniswap pool, keyed by CG_IDS), 'hyperliquid' (any pair in the HL
        universe — see add_hl_market, which fills token/source/hl_key for you),
        'bittensor' (a subnet's alpha token, priced in TAO through the local
        `bt` module — see add_bt_market, which fills netuid/name for you) or
        'dex' (a token with a pool on Solana or Base, priced per pool by
        DexScreener — see add_dex_market, which verifies the pool clears the
        owner's liquidity floor and fills chain/pair/token for you).

        hl_key is the name Hyperliquid itself answers to, which is not always
        the symbol: spot pairs are listed here as 'HYPE/USDC' and quoted there
        as '@107'. Recording it at listing time is what keeps every later price
        and settlement lookup off the universe.
        """
        if source not in self.PRICE_SOURCES:
            return {'error': f'unknown source {source} — have {list(self.PRICE_SOURCES)}'}
        if not symbol:
            return {'error': 'symbol required'}

        markets = self._load_json(self.markets_path, [])

        for m in markets:
            if m['token'].lower() == token.lower():
                return {'error': f'{symbol} already listed', 'market': m}
            # Positions resolve markets by symbol, so two markets can never
            # share one — even across price sources.
            if m['symbol'].upper() == symbol.upper():
                return {'error': f'{symbol} already listed', 'market': m}

        market = {
            'token': token,
            'symbol': symbol,
            'source': source,
            **({'hl_key': hl_key} if hl_key else {}),
            **({'hl_kind': hl_kind} if hl_kind else {}),
            **({'bt_netuid': int(bt_netuid)} if bt_netuid is not None else {}),
            **({'bt_name': bt_name} if bt_name else {}),
            **({'bt_symbol': bt_symbol} if bt_symbol else {}),
            **({'chain': chain} if chain else {}),
            **({'dex_pair': dex_pair} if dex_pair else {}),
            **({'dex_token': dex_token} if dex_token else {}),
            **({'dex_id': dex_id} if dex_id else {}),
            **({'dex_name': dex_name} if dex_name else {}),
            **({'liquidity_usd': float(liquidity_usd)} if liquidity_usd is not None else {}),
            # What the price is denominated in. Subnet alpha is quoted in TAO
            # by the chain itself; everything else here is dollars.
            'quote': 'TAO' if source == 'bittensor' else 'USD',
            'fee_tier': fee_tier,
            'active': True,
            'added_at': datetime.now().isoformat(),
            'total_volume': 0.0,
            'total_positions': 0,
            'total_profit': 0.0,
            'win_count': 0,
            'loss_count': 0,
        }
        markets.append(market)
        self._save_json(self.markets_path, markets)
        return {'status': 'added', 'market': market}

    def add_hl_market(self, coin: str) -> Dict:
        """List a Hyperliquid pair as a market — a perp ('SOL'), a spot pair
        ('HYPE/USDC' or just 'HYPE'), or the raw '@index' key HL quotes an
        unnamed spot pair under.

        The pair must be in the live universe. That check is the whole point:
        it keeps typos out of the market list, where they would become
        positions nothing can price and nothing can settle.
        """
        want = (coin or '').strip()
        if not want:
            return {'error': 'coin required'}

        entry = self._hl_find(want)
        if not entry:
            # A miss is either a typo or a cache that predates the listing —
            # refetch once before telling someone their coin does not exist.
            if not self._hl_universe(force=True):
                return {'error': 'Hyperliquid unreachable — no asset list to verify against'}
            entry = self._hl_find(want)
        if not entry:
            return {'error': f'{want} is not a Hyperliquid pair'}

        # fee_tier is a Uniswap concept; HL markets carry 0 and the UI hides it.
        return self.add_market(f"hl:{entry['key']}", entry['coin'], 0,
                               source='hyperliquid', hl_key=entry['key'],
                               hl_kind=entry['kind'])

    # Canonical Base assets with a Uniswap V3 pool against USDC. Without at
    # least one market open_position() has nothing to trade, so a fresh install
    # starts here.
    DEFAULT_MARKETS = [
        ('0x4200000000000000000000000000000000000006', 'WETH', 500),
        ('0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf', 'cbBTC', 3000),
        ('0x940181a94A35A4569E4529A3CDfB74e38FD98631', 'AERO', 3000),
    ]

    def seed(self) -> Dict:
        """List the default Base markets (idempotent)"""
        added = [self.add_market(t, s, f) for t, s, f in self.DEFAULT_MARKETS]
        return {
            'added': [r['market']['symbol'] for r in added if r.get('status') == 'added'],
            'existing': [r['market']['symbol'] for r in added if r.get('error')],
        }

    def seed_hl(self, limit: int = 20, kind: str = 'all',
                min_volume: float = 0) -> Dict:
        """List the busiest Hyperliquid pairs in one call (idempotent).

        The universe is ~880 pairs and the picker adds them one at a time,
        which is the right shape for choosing a market and the wrong one for
        standing a pool up. Ranked by 24h volume, because a pot on a book with
        no volume settles against a price nobody traded at.

        `limit` is the top of that ranking, not a count of new listings — so
        seeding twice lists nothing the second time.
        """
        limit = max(1, int(limit or 1))
        try:
            floor = float(min_volume or 0)
        except (TypeError, ValueError):
            floor = 0.0

        assets = [a for a in self.hl_assets(kind=kind, limit=0)
                  if (a.get('volume_24h') or 0) >= floor]
        if not assets:
            return {'error': 'Hyperliquid unreachable — no asset list to seed from',
                    'added': [], 'existing': []}

        added, existing = [], []
        for a in assets[:limit]:
            if a['listed']:
                existing.append(a['coin'])
                continue
            result = self.add_hl_market(a['key'])
            (added if result.get('status') == 'added' else existing).append(a['coin'])
        return {'added': added, 'existing': existing,
                'markets': len(self._load_json(self.markets_path, []))}

    def add_bt_market(self, subnet) -> Dict:
        """List a Bittensor subnet's alpha token as a market — by netuid
        ('64'), by 'SN64', by name ('lium.io') or by its alpha glyph.

        The subnet must be in the live screener: the price the pool settles
        against comes from the `bt` module's indexer, so a netuid it does not
        snapshot could never be settled honestly.
        """
        want = str(subnet if subnet is not None else '').strip()
        if not want:
            return {'error': 'subnet required'}

        entry = self._bt_find(want)
        if not entry:
            if not self._bt_universe(force=True):
                return {'error': 'Bittensor unreachable — the bt module has no '
                                 'subnet list to verify against'}
            entry = self._bt_find(want)
        if not entry:
            return {'error': f'{want} is not a Bittensor subnet'}

        return self.add_market(f"bt:{entry['netuid']}", entry['coin'], 0,
                               source='bittensor', bt_netuid=entry['netuid'],
                               bt_name=entry.get('name'),
                               bt_symbol=entry.get('symbol'))

    def seed_bt(self, limit: int = 20, min_volume: float = 0) -> Dict:
        """List the busiest Bittensor subnets in one call (idempotent).

        Ranked by 24h alpha volume in TAO, like seed_hl: a pot on a subnet
        nobody trades settles against a price nobody paid. `limit` is the top
        of the ranking, not a count of new listings.
        """
        limit = max(1, int(limit or 1))
        try:
            floor = float(min_volume or 0)
        except (TypeError, ValueError):
            floor = 0.0

        assets = [a for a in self.bt_assets(limit=0)
                  if (a.get('volume_24h') or 0) >= floor]
        if not assets:
            return {'error': 'Bittensor unreachable — no subnet list to seed from',
                    'added': [], 'existing': []}

        added, existing = [], []
        for a in assets[:limit]:
            if a['listed']:
                existing.append(a['coin'])
                continue
            result = self.add_bt_market(a['netuid'])
            (added if result.get('status') == 'added' else existing).append(a['coin'])
        return {'added': added, 'existing': existing,
                'markets': len(self._load_json(self.markets_path, []))}

    def list_markets(self) -> List[Dict]:
        """Get all supported asset markets with prices and stats"""
        markets = self._load_json(self.markets_path, [])
        # TAO in dollars, so a subnet row can show a USD figure beside its
        # native TAO price. Read once, only when a subnet is listed, and never
        # required — the pool settles in TAO regardless.
        tao_usd = None
        if any(m.get('source') == 'bittensor' for m in markets):
            try:
                tao_usd = self._hl_mids().get('TAO')
            except Exception:
                tao_usd = None
        # One DexScreener read prices every listed DEX token and reports the
        # dollars in each pool — the floor the owner set is shown against it.
        has_dex = any(m.get('source') == 'dex' for m in markets)
        dex_quotes = self._dex_prices() if has_dex else {}
        dex_floor = self.dex_min_liquidity() if has_dex else 0.0
        for m in markets:
            m.setdefault('source', 'coingecko')
            m.setdefault('quote', 'TAO' if m['source'] == 'bittensor' else 'USD')
            price = self._get_token_price(m['symbol'], m.get('source'))
            if price:
                m['price'] = price
                if m['quote'] == 'USD':
                    m['price_usd'] = price
                elif tao_usd:
                    m['price_usd'] = price * tao_usd
            if m['source'] == 'dex':
                q = dex_quotes.get(m['symbol'].upper())
                if q:
                    m['liquidity_usd'] = q.get('liquidity_usd')
                    m['volume_24h'] = q.get('volume_24h')
                    m['change_24h'] = q.get('change_24h')
                m['min_liquidity_usd'] = dex_floor
                # Only a live reading decides eligibility — the figure stored
                # at listing is what the pool held then, not now. No reading
                # means "unknown", and the pool refuses stakes on unknown.
                m['eligible'] = (q['liquidity_usd'] or 0) >= dex_floor if q else None
            total = m.get('win_count', 0) + m.get('loss_count', 0)
            m['win_rate'] = round(m.get('win_count', 0) / total * 100, 1) if total > 0 else 0
        return markets

    def _market(self, asset: str) -> Optional[Dict]:
        """Find a market by symbol or token address"""
        for m in self._load_json(self.markets_path, []):
            if m['symbol'].upper() == asset.upper() or m['token'].lower() == asset.lower():
                return m
        return None

    # ── Prices ───────────────────────────────────────────────────────
    # Two sources, one interface. Everything downstream asks for a symbol and
    # gets dollars; only these helpers know where the dollars came from.

    PRICE_SOURCES = ('coingecko', 'hyperliquid', 'bittensor', 'dex')
    HL_INFO_URL = 'https://api.hyperliquid.xyz/info'
    # Bittensor subnets come from the `bt` module on this host — its indexer
    # snapshots every subnet's alpha price every five minutes into SQLite, which
    # is what makes a historical mark at a round's close possible without an
    # archive node. There is no public fallback: the module IS the feed.
    BT_MOD_URL = os.environ.get('PREFI_BT_API', 'http://localhost:50280')
    BT_WAKE_URL = os.environ.get('PREFI_BT_WAKE', 'http://localhost:9000/api/bt')
    BT_UNIVERSE_TTL = 900
    # Hyperliquid rate-limits per IP, and the `hyperliquid` module on this host
    # already holds a client against it. Ask that module first and fall back to
    # the public endpoint — one HL client per box, not one per module.
    HL_MOD_URL = os.environ.get('PREFI_HL_API', 'http://localhost:8919')
    # The second door to that module. It is scale-to-zero behind the activator,
    # and only a call routed through :9000 wakes it — a refused call on its own
    # port means "asleep", not "no feed", so we knock on the activator before
    # falling through to the public endpoint.
    HL_WAKE_URL = os.environ.get('PREFI_HL_WAKE', 'http://localhost:9000/api/hyperliquid')
    # The pair list moves when HL lists a coin, not by the second. Fifteen
    # minutes of cache is what keeps a market-picker keystroke off the feed.
    HL_UNIVERSE_TTL = 900

    # Symbols that share a CoinGecko id share a cache entry — pricing WETH
    # prices ETH too, which keeps the free tier out of rate-limit territory.
    CG_IDS = {
        'WETH': 'ethereum', 'ETH': 'ethereum', 'BTC': 'bitcoin',
        'CBBTC': 'bitcoin', 'USDC': 'usd-coin', 'LINK': 'chainlink',
        'UNI': 'uniswap', 'AAVE': 'aave', 'SOL': 'solana',
        'ARB': 'arbitrum', 'OP': 'optimism', 'AERO': 'aerodrome-finance',
    }

    def _hl_mod_get(self, path: str, timeout: int = 10):
        """GET from the local hyperliquid module. None if it isn't reachable.

        Tries its own port and then the activator in front of it, and remembers
        which door answered — the activator hop is what wakes a slept module,
        so it is worth the extra attempt before we go to the public API.
        """
        bases, seen = [], set()
        for base in (self._hl_base, self.HL_MOD_URL, self.HL_WAKE_URL):
            if base and base not in seen:
                seen.add(base)
                bases.append(base)
        for base in bases:
            try:
                # Waking a slept module through the activator costs a few
                # seconds of boot before it answers — a short read timeout
                # there reports the feed as dead when it is merely starting.
                wait = max(timeout, 30) if base == self.HL_WAKE_URL else timeout
                resp = requests.get(f'{base}{path}', timeout=wait)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue
            self._hl_base = self._hl_last = base
            return data
        return None

    def _hl_post(self, body: Dict, timeout: int = 20, attempts: int = 4):
        """POST to the public Hyperliquid info endpoint — no key needed, but it
        429s a busy host readily, so back off and retry rather than report the
        universe as empty."""
        delay = 0.5
        for attempt in range(attempts):
            resp = requests.post(self.HL_INFO_URL, json=body, timeout=timeout)
            if resp.status_code == 429 and attempt < attempts - 1:
                time.sleep(delay)
                delay = min(delay * 2, 4.0)
                continue
            resp.raise_for_status()
            self._hl_last = self.HL_INFO_URL
            return resp.json()

    @staticmethod
    def _hl_named(raw: Dict) -> Dict[str, float]:
        """Every key Hyperliquid quotes a price for: named perps ('BTC', '0G')
        and spot pairs, which it quotes under an '@index' key rather than a
        name. Prediction legs ('#12000') are dropped — they are event odds, not
        a pair a price call can be settled against."""
        if not isinstance(raw, dict):
            return {}
        out = {}
        for k, v in raw.items():
            if k[:1] == '#':
                continue
            try:
                price = float(v)
            except (TypeError, ValueError):
                continue
            if price > 0:
                out[k] = price
        return out

    def _hl_mids(self) -> Dict[str, float]:
        """Every HL mid price in one call, cached 60s. One request prices the
        whole market list, so per-symbol lookups are free after the first."""
        cached = self._price_cache.get('_hl_mids')
        if cached and (time.time() - cached['ts']) < 60:
            return cached['mids']

        raw = self._hl_mod_get('/mids')
        if raw is None:
            try:
                raw = self._hl_post({'type': 'allMids'})
            except Exception:
                return cached['mids'] if cached else {}

        mids = self._hl_named(raw)
        if mids:
            self._price_cache['_hl_mids'] = {'mids': mids, 'ts': time.time()}
        return mids or (cached['mids'] if cached else {})

    # ── The Hyperliquid pair universe ────────────────────────────────
    #
    # HL quotes two kinds of pair and names them differently. Perps carry their
    # own name ('BTC'); spot pairs are quoted under an '@index' key and only
    # spotMeta says which token that index is. Everything below normalises the
    # two into one row shape — `key` is the name HL speaks (allMids, candles),
    # `coin` is the name a human types — so the rest of the module never has to
    # know which side of the exchange a market came from.

    @property
    def hl_universe_path(self):
        return self.store_dir / 'hl_universe.json'

    def _hl_perp_meta(self) -> Optional[Dict]:
        """Perp universe + per-asset context (24h volume, previous close)."""
        data = self._hl_mod_get('/market/meta')
        if data is None:
            try:
                data = self._hl_post({'type': 'metaAndAssetCtxs'})
            except Exception:
                return None
        return self._split_meta(data)

    def _hl_spot_meta(self) -> Optional[Dict]:
        """Spot universe + context. The hyperliquid module has no spot route
        today, so this is normally the public endpoint — asked once per cache
        window, not once per keystroke."""
        data = self._hl_mod_get('/market/spot_meta')
        if data is None:
            try:
                data = self._hl_post({'type': 'spotMetaAndAssetCtxs'})
            except Exception:
                return None
        return self._split_meta(data)

    @staticmethod
    def _split_meta(data) -> Optional[Dict]:
        """`metaAndAssetCtxs` answers as [meta, ctxs]; the plain `meta` types
        answer as the meta alone. Accept either and return one dict."""
        if isinstance(data, list):
            meta = data[0] if data else {}
            ctxs = data[1] if len(data) > 1 else []
        elif isinstance(data, dict):
            meta, ctxs = data, []
        else:
            return None
        if not isinstance(meta, dict) or 'universe' not in meta:
            return None
        return {'universe': meta.get('universe') or [],
                'tokens': meta.get('tokens') or [],
                'ctxs': ctxs if isinstance(ctxs, list) else []}

    @staticmethod
    def _ctx_stats(ctx) -> Dict:
        """24h volume and change, when the exchange reports them."""
        if not isinstance(ctx, dict):
            return {}
        out = {}
        try:
            out['volume_24h'] = float(ctx.get('dayNtlVlm'))
        except (TypeError, ValueError):
            pass
        try:
            prev, mark = float(ctx.get('prevDayPx')), float(ctx.get('markPx') or ctx.get('midPx'))
            if prev > 0:
                out['change_24h'] = round((mark - prev) / prev * 100, 2)
        except (TypeError, ValueError):
            pass
        return out

    def _hl_build_universe(self) -> List[Dict]:
        """One row per tradeable pair, perps first then spot, each sorted by
        24h volume so the liquid end of a 900-pair list is what you see first.

        A pair has to be in allMids to make the list: a name in the meta with
        no quote behind it cannot be priced now or settled later, and listing
        it would only produce a market that fails at settlement.
        """
        mids = self._hl_mids()
        if not mids:
            return []

        perps, spot = [], []

        meta = self._hl_perp_meta()
        for i, u in enumerate((meta or {}).get('universe', [])):
            name = u.get('name') or ''
            if not name or u.get('isDelisted') or name not in mids:
                continue
            ctxs = (meta or {}).get('ctxs') or []
            perps.append({
                'coin': name, 'key': name, 'kind': 'perp',
                'price': mids[name],
                'max_leverage': u.get('maxLeverage'),
                'sz_decimals': u.get('szDecimals'),
                **self._ctx_stats(ctxs[i] if i < len(ctxs) else None),
            })

        smeta = self._hl_spot_meta()
        tokens = {t.get('index'): t.get('name')
                  for t in (smeta or {}).get('tokens', []) if isinstance(t, dict)}
        named, ctx_by_coin = {}, {}
        for c in (smeta or {}).get('ctxs', []):
            if isinstance(c, dict) and c.get('coin'):
                ctx_by_coin[c['coin']] = c
        for u in (smeta or {}).get('universe', []):
            key = u.get('name')
            if not key:
                continue
            pair = u.get('tokens') or []
            base = tokens.get(pair[0]) if len(pair) > 0 else None
            quote = tokens.get(pair[1]) if len(pair) > 1 else None
            named[key] = {'base': base, 'quote': quote, 'index': u.get('index')}

        for key, price in mids.items():
            if not key.startswith('@') and key not in named:
                continue          # a named perp, already listed above
            info = named.get(key, {})
            base, quote = info.get('base'), info.get('quote')
            # HL quotes ~700 spot pairs but only names ~330 of them in spotMeta.
            # The unnamed ones stay addressable under the raw '@index' key
            # rather than dropping off the list — they price and settle the same.
            coin = f'{base}/{quote}' if base and quote else key
            spot.append({
                'coin': coin, 'key': key, 'kind': 'spot', 'price': price,
                'base': base, 'quote': quote, 'named': bool(base and quote),
                **self._ctx_stats(ctx_by_coin.get(key)),
            })

        by_volume = lambda a: (-(a.get('volume_24h') or 0), a['coin'])
        perps.sort(key=by_volume)
        spot.sort(key=by_volume)
        return perps + spot

    def _hl_universe(self, force: bool = False) -> List[Dict]:
        """The pair universe, cached in memory for 15 minutes and on disk under
        the store. The feed 429s a busy host, and a stale pair list is a far
        better answer than an empty one — an empty one reads as "Hyperliquid
        has no markets", which is never true.
        """
        cached = self._price_cache.get('_hl_universe')
        if not force and cached and (time.time() - cached['ts']) < self.HL_UNIVERSE_TTL:
            return cached['assets']

        if not force and not cached:
            disk = self._load_json(self.hl_universe_path, {})
            if disk.get('assets') and (time.time() - disk.get('ts', 0)) < self.HL_UNIVERSE_TTL:
                self._price_cache['_hl_universe'] = disk
                return disk['assets']

        assets = self._hl_build_universe()
        if assets:
            entry = {'assets': assets, 'ts': time.time()}
            self._price_cache['_hl_universe'] = entry
            try:
                self._save_json(self.hl_universe_path, entry)
            except Exception:
                pass
            return assets

        if cached:
            return cached['assets']
        disk = self._load_json(self.hl_universe_path, {})
        return disk.get('assets', [])

    def _hl_index(self) -> Dict[str, Dict]:
        """Lookup table over the universe: every name a caller might type — the
        pair name, the HL key, and a bare base token — points at one row."""
        assets = self._hl_universe()
        stamp = self._price_cache.get('_hl_universe', {}).get('ts')
        cached = self._price_cache.get('_hl_index')
        if cached and cached.get('stamp') == stamp:
            return cached['index']

        index = {}
        for a in assets:
            index.setdefault(a['coin'].upper(), a)
            index.setdefault(a['key'].upper(), a)
        for a in assets:                       # bare token: 'HYPE' → HYPE/USDC
            if a['kind'] == 'spot' and a.get('base'):
                index.setdefault(a['base'].upper(), a)
        self._price_cache['_hl_index'] = {'index': index, 'stamp': stamp}
        return index

    def _hl_find(self, name: str) -> Optional[Dict]:
        """Resolve anything a caller might type to one universe row."""
        want = (name or '').strip().upper()
        return self._hl_index().get(want) if want else None

    def _hl_key(self, symbol: str) -> str:
        """The name Hyperliquid answers to for one of our market symbols.

        Perps are themselves; a spot pair listed here as 'HYPE/USDC' is '@107'
        to allMids and to candleSnapshot. Markets record the key at listing, so
        the common path is a dict hit and never touches the feed.
        """
        sym = (symbol or '').strip()
        if not sym:
            return ''
        cached = self._price_cache.get('_hl_keys')
        if not cached or (time.time() - cached['ts']) > 5:
            cached = {'keys': {m['symbol'].upper(): m['hl_key']
                               for m in self._load_json(self.markets_path, [])
                               if m.get('hl_key')},
                      'ts': time.time()}
            self._price_cache['_hl_keys'] = cached
        if sym.upper() in cached['keys']:
            return cached['keys'][sym.upper()]
        entry = self._hl_find(sym)
        return entry['key'] if entry else sym.upper()

    def hl_assets(self, search: str = '', limit: int = 50,
                  kind: str = 'all') -> List[Dict]:
        """Browse the whole Hyperliquid universe — every perp and every spot
        pair — which is what add_hl_market draws from.

        `kind` narrows to 'perp' or 'spot'; `limit=0` returns everything.
        Rows come back liquid end first, and carry `listed` so a picker can
        show what this pool already trades.
        """
        assets = self._hl_universe()
        markets = self._load_json(self.markets_path, [])
        listed = {m['symbol'].upper() for m in markets}
        listed |= {m['hl_key'].upper() for m in markets if m.get('hl_key')}

        want_kind = (kind or 'all').strip().lower()
        q = (search or '').strip().upper()
        out = []
        for a in assets:
            if want_kind in ('perp', 'spot') and a['kind'] != want_kind:
                continue
            if q and q not in a['coin'].upper() and q not in a['key'].upper():
                continue
            out.append({**a, 'listed': a['coin'].upper() in listed
                                       or a['key'].upper() in listed})
        limit = int(limit or 0)
        return out[:limit] if limit > 0 else out

    def hl_stats(self) -> Dict:
        """What the pair universe looks like right now — how many pairs of each
        kind are quoted, how many this pool lists, and how old the snapshot is.
        A picker showing 24 of 900 rows needs to say so."""
        assets = self._hl_universe()
        cached = self._price_cache.get('_hl_universe', {})
        listed = self.hl_assets(kind='all', limit=0)
        age = time.time() - cached['ts'] if cached.get('ts') else None
        return {
            'pairs': len(assets),
            'perps': sum(1 for a in assets if a['kind'] == 'perp'),
            'spot': sum(1 for a in assets if a['kind'] == 'spot'),
            'listed': sum(1 for a in listed if a['listed']),
            # Where these rows came from — naming an endpoint we never
            # called would make a cached list look live.
            'source': self._hl_last or 'cache',
            'age_seconds': round(age) if age is not None else None,
            'reachable': bool(assets),
        }

    # ── The Bittensor subnet universe ────────────────────────────────
    #
    # Every subnet has an alpha token priced in TAO by its own liquidity pool.
    # The `bt` module's screener is the whole list in one call, served from its
    # indexer, so the picker never touches the chain. Rows take the same shape
    # as the Hyperliquid universe — `coin` is what a human types ('SN64'),
    # `key` is the netuid the feed speaks — so the rest of the module does not
    # care which exchange a market came from.

    @property
    def bt_universe_path(self):
        return self.store_dir / 'bt_universe.json'

    def _bt_call(self, tool: str, args: Dict = None, timeout: int = 15):
        """Call one bt tool through the module's JSON API. None if the module
        is unreachable. Its own port first, then the activator — bt can be
        scale-to-zero, and a refused call means asleep, not dead."""
        bases, seen = [], set()
        for base in (self._bt_base, self.BT_MOD_URL, self.BT_WAKE_URL):
            if base and base not in seen:
                seen.add(base)
                bases.append(base)
        for base in bases:
            try:
                wait = max(timeout, 30) if base == self.BT_WAKE_URL else timeout
                resp = requests.post(f'{base}/api/call',
                                     json={'tool': tool, 'args': args or {}},
                                     timeout=wait)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue
            if not isinstance(data, dict) or not data.get('ok'):
                # The module answered but the tool failed — that is a real
                # answer, not a closed door; do not go knocking elsewhere.
                self._bt_base = base
                return None
            self._bt_base = self._bt_last = base
            return data.get('result')
        return None

    def _bt_prices(self) -> Dict[str, float]:
        """Every subnet's alpha price in TAO, keyed by netuid string, cached
        60s. One call prices the whole market list."""
        cached = self._price_cache.get('_bt_prices')
        if cached and (time.time() - cached['ts']) < 60:
            return cached['prices']
        data = self._bt_call('bt_prices_at', {})
        prices = {}
        for k, v in ((data or {}).get('prices') or {}).items():
            try:
                price = float(v)
            except (TypeError, ValueError):
                continue
            if price > 0:
                prices[str(k)] = price
        if prices:
            self._price_cache['_bt_prices'] = {'prices': prices, 'ts': time.time()}
        return prices or (cached['prices'] if cached else {})

    def _bt_build_universe(self) -> List[Dict]:
        """One row per subnet from the bt screener, busiest first. Root
        (netuid 0) is dropped: its price is 1 TAO by definition, and a call on
        a constant is not a prediction."""
        data = self._bt_call('bt_screener', {}, timeout=30)
        rows = (data or {}).get('rows') if isinstance(data, dict) else None
        if not rows:
            return []
        out = []
        for r in rows:
            try:
                netuid = int(r.get('netuid'))
                price = float(r.get('price') or 0)
            except (TypeError, ValueError):
                continue
            if netuid == 0 or price <= 0:
                continue
            out.append({
                'coin': f'SN{netuid}', 'key': str(netuid), 'netuid': netuid,
                'kind': 'subnet', 'name': r.get('name') or '',
                'symbol': r.get('symbol') or '',
                'price': price, 'quote': 'TAO',
                'market_cap': r.get('market_cap'),
                'tao_in': r.get('tao_in'),
                'volume_24h': r.get('vol_24h'),
                'change_24h': r.get('change_24h'),
                'change_7d': r.get('change_7d'),
                'logo': r.get('logo'), 'url': r.get('url'),
            })
        out.sort(key=lambda a: (-(a.get('volume_24h') or 0), a['netuid']))
        return out

    def _bt_universe(self, force: bool = False) -> List[Dict]:
        """The subnet list, cached 15 minutes in memory and on disk — same
        reasoning as the HL universe: stale beats empty."""
        cached = self._price_cache.get('_bt_universe')
        if not force and cached and (time.time() - cached['ts']) < self.BT_UNIVERSE_TTL:
            return cached['assets']

        if not force and not cached:
            disk = self._load_json(self.bt_universe_path, {})
            if disk.get('assets') and (time.time() - disk.get('ts', 0)) < self.BT_UNIVERSE_TTL:
                self._price_cache['_bt_universe'] = disk
                return disk['assets']

        assets = self._bt_build_universe()
        if assets:
            entry = {'assets': assets, 'ts': time.time()}
            self._price_cache['_bt_universe'] = entry
            try:
                self._save_json(self.bt_universe_path, entry)
            except Exception:
                pass
            return assets

        if cached:
            return cached['assets']
        disk = self._load_json(self.bt_universe_path, {})
        return disk.get('assets', [])

    def _bt_index(self) -> Dict[str, Dict]:
        """Every name a caller might type — 'SN64', '64', 'lium.io', the
        alpha glyph — points at one row. Busiest wins a shared glyph."""
        assets = self._bt_universe()
        stamp = self._price_cache.get('_bt_universe', {}).get('ts')
        cached = self._price_cache.get('_bt_index')
        if cached and cached.get('stamp') == stamp:
            return cached['index']
        index = {}
        for a in assets:
            index.setdefault(a['coin'].upper(), a)
            index.setdefault(a['key'], a)
        for a in assets:
            if a.get('name'):
                index.setdefault(a['name'].strip().upper(), a)
            if a.get('symbol'):
                index.setdefault(a['symbol'].strip().upper(), a)
        self._price_cache['_bt_index'] = {'index': index, 'stamp': stamp}
        return index

    def _bt_find(self, name) -> Optional[Dict]:
        want = str(name if name is not None else '').strip().upper()
        if want.startswith('BT:'):
            want = want[3:]
        return self._bt_index().get(want) if want else None

    def _bt_netuid(self, symbol: str) -> Optional[int]:
        """The netuid behind one of our market symbols. Markets record it at
        listing, so the common path never touches the feed."""
        sym = (symbol or '').strip()
        if not sym:
            return None
        cached = self._price_cache.get('_bt_netuids')
        if not cached or (time.time() - cached['ts']) > 5:
            cached = {'ids': {m['symbol'].upper(): int(m['bt_netuid'])
                              for m in self._load_json(self.markets_path, [])
                              if m.get('bt_netuid') is not None},
                      'ts': time.time()}
            self._price_cache['_bt_netuids'] = cached
        if sym.upper() in cached['ids']:
            return cached['ids'][sym.upper()]
        entry = self._bt_find(sym)
        return entry['netuid'] if entry else None

    def bt_assets(self, search: str = '', limit: int = 50) -> List[Dict]:
        """Browse every Bittensor subnet — what add_bt_market draws from.
        `limit=0` returns everything; rows carry `listed`."""
        assets = self._bt_universe()
        markets = self._load_json(self.markets_path, [])
        listed = {int(m['bt_netuid']) for m in markets if m.get('bt_netuid') is not None}
        q = (search or '').strip().upper()
        out = []
        for a in assets:
            hay = (a['coin'], a['key'], a.get('name') or '', a.get('symbol') or '')
            if q and not any(q in h.upper() for h in hay):
                continue
            out.append({**a, 'listed': a['netuid'] in listed})
        limit = int(limit or 0)
        return out[:limit] if limit > 0 else out

    def bt_stats(self) -> Dict:
        """How many subnets the bt indexer quotes, how many are listed here,
        and how old the snapshot is."""
        assets = self._bt_universe()
        cached = self._price_cache.get('_bt_universe', {})
        listed = self.bt_assets(limit=0)
        age = time.time() - cached['ts'] if cached.get('ts') else None
        return {
            'subnets': len(assets),
            'listed': sum(1 for a in listed if a['listed']),
            'volume_24h_tao': round(sum(a.get('volume_24h') or 0 for a in assets), 3),
            'source': self._bt_last or 'cache',
            'age_seconds': round(age) if age is not None else None,
            'reachable': bool(assets),
        }


    # ── DEX tokens on Solana and Base ────────────────────────────────
    #
    # Anything with a pool on Solana or Base is listable, which is a different
    # shape of universe from Hyperliquid's ~880 pairs or Bittensor's ~130
    # subnets: it is unbounded, and most of it is worthless. Two things keep
    # it honest. A market is one *pool* — the pair address is recorded at
    # listing and every price and settlement read is for that pool, so a
    # token can never be quietly re-pointed at a thinner one. And the pool
    # owner sets a dollar floor on liquidity (`min_liquidity_usd`) that a
    # token has to clear to be listed and to take a stake: a pot on a $900
    # pool settles against a price one trade can move.
    #
    # Spot comes from DexScreener (no key, per-pair liquidity and volume);
    # history from GeckoTerminal's hourly OHLCV for the same pool, and from
    # our own snapshots — taken every time a price is read — when it can't.

    DEX_CHAINS = {'solana': 'Solana', 'base': 'Base'}
    DEX_API = os.environ.get('PREFI_DEX_API', 'https://api.dexscreener.com')
    GECKO_API = os.environ.get('PREFI_GECKO_API', 'https://api.geckoterminal.com/api/v2')
    DEX_UNIVERSE_TTL = 900       # the ranked list moves slowly
    DEX_SEARCH_TTL = 120         # a search is a keystroke — but not a feed hit each time
    DEX_PRICE_TTL = 60
    DEX_SNAPSHOT_GAP = 240       # seconds between two stored history points
    DEX_HISTORY_DAYS = 30
    DEX_UNIVERSE_PAGES = 3       # GeckoTerminal pages a chain's default list is built from

    @property
    def dex_history_path(self):
        return self.store_dir / 'dex_history.json'

    def dex_universe_path(self, chain: str):
        return self.store_dir / f'dex_universe_{chain}.json'

    def _dex_chain(self, chain) -> Optional[str]:
        want = str(chain or '').strip().lower()
        aliases = {'sol': 'solana', 'solana': 'solana', 'base': 'base'}
        return aliases.get(want)

    def _dex_get(self, path: str, params: Dict = None, timeout: int = 10):
        """GET from DexScreener. None if it isn't reachable — never an
        exception, a browser keystroke must not 500."""
        try:
            resp = requests.get(f'{self.DEX_API}{path}', params=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        self._dex_last = 'dexscreener'
        return data

    def _gecko_get(self, path: str, params: Dict = None, timeout: int = 15):
        """GET from GeckoTerminal — the candle feed. Same contract: None
        when it can't answer."""
        try:
            resp = requests.get(f'{self.GECKO_API}{path}', params=params, timeout=timeout,
                                headers={'accept': 'application/json'})
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        self._dex_last = 'geckoterminal'
        return data

    @staticmethod
    def _dex_symbol(raw) -> str:
        """A DexScreener symbol, cleaned: '$WIF' is WIF. Anything that is not
        a letter, digit, dot or dash goes — symbols end up in URLs and
        signed messages."""
        sym = ''.join(ch for ch in str(raw or '').strip().lstrip('$')
                      if ch.isalnum() or ch in '.-')
        # Tickers are upper-case everywhere else in the market list, and a
        # symbol lookup ('wif') should not depend on how a deployer typed it.
        return sym[:24].upper()

    @staticmethod
    def _num(value) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if out == out else None      # NaN guard

    def _dex_row(self, pair: Dict) -> Optional[Dict]:
        """One DexScreener pair → the row shape everything else reads."""
        if not isinstance(pair, dict):
            return None
        chain = self._dex_chain(pair.get('chainId'))
        base = pair.get('baseToken') or {}
        if not chain or not pair.get('pairAddress') or not base.get('address'):
            return None
        price = self._num(pair.get('priceUsd'))
        if not price or price <= 0:
            return None
        return {
            'chain': chain,
            'key': pair['pairAddress'],
            'coin': self._dex_symbol(base.get('symbol')) or base['address'][:6],
            'name': (base.get('name') or '')[:48] or None,
            'token': base['address'],
            'dex': pair.get('dexId'),
            'quote_symbol': self._dex_symbol((pair.get('quoteToken') or {}).get('symbol')),
            'price': price,
            'liquidity_usd': self._num((pair.get('liquidity') or {}).get('usd')) or 0.0,
            'volume_24h': self._num((pair.get('volume') or {}).get('h24')) or 0.0,
            'change_24h': self._num((pair.get('priceChange') or {}).get('h24')),
            'url': pair.get('url'),
        }

    def _gecko_row(self, pool: Dict, chain: str) -> Optional[Dict]:
        """One GeckoTerminal pool → the same row shape."""
        try:
            attrs = pool['attributes']
            rel = pool['relationships']
            token_id = rel['base_token']['data']['id']          # 'solana_<addr>'
            token = token_id.split('_', 1)[1]
            name = attrs.get('name') or ''
            coin = self._dex_symbol(name.split(' / ')[0]) or token[:6]
            price = self._num(attrs.get('base_token_price_usd'))
            if not price or price <= 0 or not attrs.get('address'):
                return None
            return {
                'chain': chain,
                'key': attrs['address'],
                'coin': coin,
                'name': None,
                'token': token,
                'dex': (rel.get('dex') or {}).get('data', {}).get('id'),
                'quote_symbol': self._dex_symbol(name.split(' / ')[1].split(' ')[0])
                                if ' / ' in name else None,
                'price': price,
                'liquidity_usd': self._num(attrs.get('reserve_in_usd')) or 0.0,
                'volume_24h': self._num((attrs.get('volume_usd') or {}).get('h24')) or 0.0,
                'change_24h': self._num((attrs.get('price_change_percentage') or {}).get('h24')),
                'url': None,
            }
        except (KeyError, TypeError, AttributeError, IndexError):
            return None

    @staticmethod
    def _dex_best(rows: List[Dict]) -> List[Dict]:
        """One row per token — its deepest pool. The same token trades in a
        dozen pools; the price that matters is the one with money behind it,
        and it is the only one a market gets pinned to."""
        best: Dict[str, Dict] = {}
        for r in rows:
            if not r:
                continue
            k = f"{r['chain']}:{r['token'].lower()}"
            if k not in best or (r['liquidity_usd'] or 0) > (best[k]['liquidity_usd'] or 0):
                best[k] = r
        return list(best.values())

    def _dex_build_universe(self, chain: str) -> List[Dict]:
        """A chain's default list: its busiest pools by 24h volume, from
        GeckoTerminal, deduped to one pool per token."""
        rows = []
        for page in range(1, self.DEX_UNIVERSE_PAGES + 1):
            data = self._gecko_get(f'/networks/{chain}/pools', {'page': page})
            if not data:
                break
            got = [self._gecko_row(p, chain) for p in (data.get('data') or [])]
            rows.extend(r for r in got if r)
            if len(got) < 20:
                break
        rows = self._dex_best(rows)
        # Stables and wrapped gas are in every top-pools list and are not a
        # price call anyone would make.
        boring = {'USDC', 'USDT', 'USDBC', 'DAI', 'USDE', 'USDS', 'WETH', 'WSOL', 'SOL', 'ETH',
                  'CBBTC', 'WBTC', 'STETH', 'WSTETH', 'CBETH', 'WEETH', 'USD1', 'PYUSD', 'FDUSD'}
        # A pool with no reserve reading is not one to show, whatever it traded.
        rows = [r for r in rows if r['coin'].upper() not in boring and (r['liquidity_usd'] or 0) > 0]
        rows.sort(key=lambda r: -(r['volume_24h'] or 0))
        return rows

    def _dex_universe(self, chain: str, force: bool = False) -> List[Dict]:
        """The default list per chain, cached 15 minutes in memory and on disk
        — a stale list beats an empty one, same as the HL universe."""
        chain = self._dex_chain(chain)
        if not chain:
            return []
        ckey = f'_dex_universe_{chain}'
        cached = self._price_cache.get(ckey)
        if not force:
            if cached and (time.time() - cached['ts']) < self.DEX_UNIVERSE_TTL:
                return cached['assets']
            disk = self._load_json(self.dex_universe_path(chain), {})
            if disk.get('assets') and (time.time() - disk.get('ts', 0)) < self.DEX_UNIVERSE_TTL:
                self._price_cache[ckey] = disk
                return disk['assets']

        assets = self._dex_build_universe(chain)
        if assets:
            entry = {'assets': assets, 'ts': time.time()}
            self._price_cache[ckey] = entry
            try:
                self._save_json(self.dex_universe_path(chain), entry)
            except OSError:
                pass
            return assets
        if cached:
            return cached['assets']
        disk = self._load_json(self.dex_universe_path(chain), {})
        return disk.get('assets') or []

    def dex_search(self, chain: str, query: str) -> List[Dict]:
        """Search one chain for a token — by symbol, name or address.

        Symbols go to GeckoTerminal: its search ranks the real `$WIF` first,
        where DexScreener's returns thirty pump.fun namesakes and a "WIF"
        with $35k behind it. Addresses go to DexScreener, which answers a
        pool or a token address exactly. One row per token (its deepest
        pool); exact ticker matches first, then by 24h volume — volume is
        the one number a fake pool can't fabricate by parking tokens in it.
        """
        chain = self._dex_chain(chain)
        q = (query or '').strip()
        if not chain or not q:
            return []
        ckey = f'_dex_search_{chain}_{q.lower()}'
        cached = self._price_cache.get(ckey)
        if cached and (time.time() - cached['ts']) < self.DEX_SEARCH_TTL:
            return cached['rows']

        rows: List[Dict] = []
        if self._looks_like_address(q):
            hit = self._dex_lookup_address(chain, q)
            rows = [hit] if hit else []
        else:
            data = self._gecko_get('/search/pools', {'query': q, 'network': chain, 'page': 1})
            if data is not None:
                rows = [self._gecko_row(p, chain) for p in (data.get('data') or [])]
            else:
                data = self._dex_get('/latest/dex/search', {'q': q}, timeout=12)
                if data is None:
                    return cached['rows'] if cached else []
                rows = [self._dex_row(p) for p in (data.get('pairs') or [])]
            rows = self._dex_best([r for r in rows if r and r['chain'] == chain])
            want = q.upper()
            rows.sort(key=lambda r: (r['coin'].upper() != want, -(r['volume_24h'] or 0),
                                     -(r['liquidity_usd'] or 0)))
        self._price_cache[ckey] = {'rows': rows, 'ts': time.time()}
        return rows

    @staticmethod
    def _looks_like_address(text: str) -> bool:
        t = (text or '').strip()
        if t.startswith('0x'):
            return len(t) == 42 and all(c in '0123456789abcdefABCDEF' for c in t[2:])
        return 32 <= len(t) <= 44 and t.isalnum()          # base58 pubkey

    def _dex_lookup_address(self, chain: str, address: str) -> Optional[Dict]:
        """A pool address answers directly; a token address answers with its
        deepest pool. Both through DexScreener, which is exact on addresses."""
        data = self._dex_get(f'/latest/dex/pairs/{chain}/{address}')
        rows = [self._dex_row(p) for p in ((data or {}).get('pairs') or [])]
        rows = [r for r in rows if r and r['chain'] == chain]
        if rows:
            return rows[0]
        data = self._dex_get(f'/token-pairs/v1/{chain}/{address}')
        rows = [self._dex_row(p) for p in (data if isinstance(data, list) else [])]
        rows = self._dex_best([r for r in rows if r and r['chain'] == chain])
        return max(rows, key=lambda r: r['liquidity_usd'] or 0) if rows else None

    def _dex_lookup(self, chain: str, address: str) -> Optional[Dict]:
        """Resolve what a caller typed to one pool on one chain.

        An address is exact. A plain symbol takes the busiest pool whose
        ticker matches exactly — the response says which pool was picked,
        and the console never sends a symbol, only the row's pool address.
        """
        chain = self._dex_chain(chain)
        want = (address or '').strip()
        if not chain or not want:
            return None
        if self._looks_like_address(want):
            return self._dex_lookup_address(chain, want)
        rows = self.dex_search(chain, want)
        exact = [r for r in rows if r['coin'].upper() == want.upper()]
        return (exact or [None])[0]

    def dex_min_liquidity(self) -> float:
        """The owner's floor, in dollars. Lives in the pool config because the
        pool owner is the one whose money settles on these prices."""
        try:
            return float(self.pool.state()['config'].get('min_liquidity_usd') or 0)
        except Exception:
            return float(pool_mod.DEFAULT_CONFIG['min_liquidity_usd'])

    def _dex_listed(self) -> Dict[str, Dict]:
        return {f"{m.get('chain')}:{m.get('dex_pair', '').lower()}": m
                for m in self._load_json(self.markets_path, [])
                if m.get('source') == 'dex' and m.get('dex_pair')}

    def _dex_market_symbol(self, row: Dict, markets: List[Dict]) -> str:
        """'WIF.sol', 'BRETT.base' — the chain is in the name because the same
        ticker is on Hyperliquid, on Solana and on Base at once and the pot
        for each is a different thing. A second token with the same ticker
        on the same chain gets the first four characters of its address."""
        suffix = {'solana': 'sol', 'base': 'base'}[row['chain']]
        taken = {m['symbol'].upper() for m in markets}
        sym = f"{row['coin']}.{suffix}"
        if sym.upper() in taken:
            sym = f"{row['coin']}.{suffix}.{row['token'][:4]}"
        return sym

    def add_dex_market(self, chain: str, address: str) -> Dict:
        """List a Solana or Base token as a market, by pool address, token
        address or symbol.

        Two checks, both refusable. The pool has to exist on DexScreener —
        that is what makes it priceable — and it has to hold at least
        `min_liquidity_usd` (the pool owner's number) right now. The market
        records the pool, not the token: every later price and settlement
        reads that one pool.
        """
        chain_id = self._dex_chain(chain)
        if not chain_id:
            return {'error': f"chain must be one of {sorted(self.DEX_CHAINS)} — got '{chain}'"}
        want = (address or '').strip()
        if not want:
            return {'error': 'address required — a pool address, a token address or a symbol'}

        row = self._dex_lookup(chain_id, want)
        if row is None:
            if self._dex_last is None and self._dex_get('/latest/dex/search', {'q': 'SOL'}) is None:
                return {'error': 'DexScreener unreachable — nothing to verify the token against'}
            return {'error': f"{want} is not a token with a pool on {self.DEX_CHAINS[chain_id]}"}

        floor = self.dex_min_liquidity()
        if row['liquidity_usd'] < floor:
            return {'error': f"{row['coin']} on {self.DEX_CHAINS[chain_id]} has "
                             f"${row['liquidity_usd']:,.0f} in its deepest pool ({row['dex']}) — "
                             f"under the ${floor:,.0f} liquidity floor the pool owner set",
                    'liquidity_usd': row['liquidity_usd'], 'min_liquidity_usd': floor,
                    'pair': row['key']}

        markets = self._load_json(self.markets_path, [])
        listed = self._dex_listed().get(f"{chain_id}:{row['key'].lower()}")
        if listed:
            return {'error': f"{listed['symbol']} already listed", 'market': listed}
        symbol = self._dex_market_symbol(row, markets)
        out = self.add_market(f"dex:{chain_id}:{row['key']}", symbol, 0,
                              source='dex', chain=chain_id, dex_pair=row['key'],
                              dex_token=row['token'], dex_id=row['dex'],
                              dex_name=row.get('name'), liquidity_usd=row['liquidity_usd'])
        if out.get('status') == 'added':
            out['pool'] = row
            # First history point — a pot could open on this before anyone
            # reads a price again.
            self._dex_snapshot({symbol.upper(): {'price': row['price']}})
        return out

    def seed_dex(self, chain: str, limit: int = 20, min_volume: float = 0) -> Dict:
        """List a chain's busiest tokens that clear the liquidity floor, in
        one call (idempotent — `limit` is the top of the ranking)."""
        chain_id = self._dex_chain(chain)
        if not chain_id:
            return {'error': f"chain must be one of {sorted(self.DEX_CHAINS)} — got '{chain}'"}
        limit = max(1, int(limit or 1))
        try:
            vol_floor = float(min_volume or 0)
        except (TypeError, ValueError):
            vol_floor = 0.0
        assets = [a for a in self.dex_assets(chain_id, limit=0)
                  if a['eligible'] and (a.get('volume_24h') or 0) >= vol_floor]
        if not assets:
            return {'error': f'no {self.DEX_CHAINS[chain_id]} token clears the floor — or the '
                             'feed is unreachable', 'added': [], 'existing': []}
        added, existing = [], []
        for a in assets[:limit]:
            if a['listed']:
                existing.append(a['coin'])
                continue
            result = self.add_dex_market(chain_id, a['key'])
            (added if result.get('status') == 'added' else existing).append(a['coin'])
        return {'added': added, 'existing': existing,
                'markets': len(self._load_json(self.markets_path, []))}

    def dex_assets(self, chain: str = 'solana', search: str = '', limit: int = 50) -> List[Dict]:
        """Browse tokens on one chain — the busiest pools by default, a
        DexScreener search when `search` is given. Rows carry `listed`, and
        `eligible` against the owner's liquidity floor, so a picker can grey
        out what cannot be listed rather than let someone find out on click."""
        chain_id = self._dex_chain(chain)
        if not chain_id:
            return []
        rows = self.dex_search(chain_id, search) if (search or '').strip() \
            else self._dex_universe(chain_id)
        floor = self.dex_min_liquidity()
        listed = self._dex_listed()
        out = []
        for r in rows:
            m = listed.get(f"{chain_id}:{r['key'].lower()}")
            out.append({**r, 'listed': bool(m), 'symbol': m['symbol'] if m else None,
                        'eligible': (r['liquidity_usd'] or 0) >= floor,
                        'min_liquidity_usd': floor})
        if not (search or '').strip():
            # The busiest pools on a chain include pump.fun launches with a
            # nine-figure day and no reserve. Listable first, then busiest —
            # the top of the default list should be things you can add.
            out.sort(key=lambda r: (not r['eligible'], -(r['volume_24h'] or 0)))
        limit = int(limit or 0)
        return out[:limit] if limit > 0 else out

    def dex_stats(self, chain: str = 'solana') -> Dict:
        """How many pools the default list ranks, how many clear the floor,
        how many are listed here, and how old the list is."""
        chain_id = self._dex_chain(chain)
        if not chain_id:
            return {'error': f"chain must be one of {sorted(self.DEX_CHAINS)}"}
        assets = self.dex_assets(chain_id, limit=0)
        cached = self._price_cache.get(f'_dex_universe_{chain_id}', {})
        age = time.time() - cached['ts'] if cached.get('ts') else None
        listed = [m for m in self._dex_listed().values() if m.get('chain') == chain_id]
        return {
            'chain': chain_id,
            'label': self.DEX_CHAINS[chain_id],
            'pools': len(assets),
            'eligible': sum(1 for a in assets if a['eligible']),
            'listed': len(listed),
            'min_liquidity_usd': self.dex_min_liquidity(),
            'source': self._dex_last or 'cache',
            'age_seconds': round(age) if age is not None else None,
            'reachable': bool(assets),
        }

    def _dex_prices(self) -> Dict[str, Dict]:
        """Every listed DEX market's pool, priced in one DexScreener read per
        chain (≤20 pools a call), cached 60s. Keyed by upper-cased market
        symbol → {price, liquidity_usd, volume_24h, change_24h}."""
        cached = self._price_cache.get('_dex_prices')
        if cached and (time.time() - cached['ts']) < self.DEX_PRICE_TTL:
            return cached['quotes']

        markets = [m for m in self._load_json(self.markets_path, [])
                   if m.get('source') == 'dex' and m.get('dex_pair') and m.get('chain')]
        if not markets:
            return {}
        by_pair = {f"{m['chain']}:{m['dex_pair'].lower()}": m['symbol'].upper() for m in markets}
        quotes: Dict[str, Dict] = {}
        failed = False
        for chain in sorted({m['chain'] for m in markets}):
            pairs = sorted({m['dex_pair'] for m in markets if m['chain'] == chain})
            for i in range(0, len(pairs), 20):
                data = self._dex_get(f"/latest/dex/pairs/{chain}/{','.join(pairs[i:i + 20])}")
                if data is None:
                    failed = True
                    continue
                for p in data.get('pairs') or []:
                    row = self._dex_row(p)
                    if not row:
                        continue
                    sym = by_pair.get(f"{row['chain']}:{row['key'].lower()}")
                    if sym:
                        quotes[sym] = {'price': row['price'],
                                       'liquidity_usd': row['liquidity_usd'],
                                       'volume_24h': row['volume_24h'],
                                       'change_24h': row['change_24h']}
        if quotes:
            if cached and failed:
                # A partial read must not blank the markets it missed.
                quotes = {**cached['quotes'], **quotes}
            self._price_cache['_dex_prices'] = {'quotes': quotes, 'ts': time.time()}
            self._dex_snapshot(quotes)
            return quotes
        return cached['quotes'] if cached else {}

    def _dex_liquidity(self, symbol: str) -> Optional[float]:
        """Dollars in a listed token's pool right now — the pool checks the
        owner's floor against this before a stake goes in."""
        q = self._dex_prices().get((symbol or '').upper())
        return q.get('liquidity_usd') if q else None

    def _dex_snapshot(self, quotes: Dict[str, Dict]):
        """Append a history point per symbol, at most one per
        DEX_SNAPSHOT_GAP, trimmed to DEX_HISTORY_DAYS. This is the fallback
        oracle when GeckoTerminal can't answer for a pool at the close."""
        if not quotes:
            return
        now = time.time()
        try:
            hist = self._load_json(self.dex_history_path, {})
        except (OSError, ValueError):
            hist = {}
        changed = False
        for sym, q in quotes.items():
            price = (q or {}).get('price')
            if not price:
                continue
            points = hist.setdefault(sym, [])
            if points and now - points[-1][0] < self.DEX_SNAPSHOT_GAP:
                continue
            points.append([round(now), price])
            cutoff = now - self.DEX_HISTORY_DAYS * 86400
            if points and points[0][0] < cutoff:
                hist[sym] = [pt for pt in points if pt[0] >= cutoff]
            changed = True
        if changed:
            try:
                self._save_json(self.dex_history_path, hist)
            except OSError:
                pass

    def dex_snapshot(self) -> Dict:
        """Take a history point for every listed DEX token now. The API runs
        this on a timer so a pot can settle even if nobody read a price near
        the close and GeckoTerminal is down."""
        self._price_cache.pop('_dex_prices', None)
        quotes = self._dex_prices()
        return {'snapshotted': sorted(quotes), 'at': round(time.time())}

    def _dex_price_at(self, symbol: str, ts: float) -> Optional[Dict]:
        """The pool's price at `ts`: GeckoTerminal's hourly candle first
        (the candle opening nearest ts — its open *is* the price at that
        boundary, at most 30 minutes off), then our own snapshots within 30
        minutes. None means the caller falls through to spot, and says so."""
        m = self._market(symbol)
        if not m or m.get('source') != 'dex' or not m.get('dex_pair'):
            return None
        want = int(ts)
        data = self._gecko_get(
            f"/networks/{m['chain']}/pools/{m['dex_pair']}/ohlcv/hour",
            {'before_timestamp': want + 3600, 'limit': 3, 'currency': 'usd'})
        candles = (((data or {}).get('data') or {}).get('attributes') or {}).get('ohlcv_list') or []
        best = None
        for c in candles:
            try:
                t, o = int(c[0]), float(c[1])
            except (TypeError, ValueError, IndexError):
                continue
            if o > 0 and (best is None or abs(t - want) < abs(best[0] - want)):
                best = (t, o)
        if best and abs(best[0] - want) <= 1800:
            return {'price': best[1], 'mode': 'historical'}

        points = self._load_json(self.dex_history_path, {}).get(symbol.upper()) or []
        near = [p for p in points if abs(p[0] - want) <= 1800]
        if near:
            t, price = min(near, key=lambda p: abs(p[0] - want))
            return {'price': float(price), 'mode': 'historical', 'via': 'snapshot'}
        return None

    def _get_token_price(self, symbol: str, source: str = None) -> Optional[float]:
        """Current USD price for a symbol. `source` defaults to the one recorded
        on the market — callers that already hold the market should pass it and
        skip the lookup."""
        if source is None:
            market = self._market(symbol)
            source = market.get('source', 'coingecko') if market else 'coingecko'

        if source == 'hyperliquid':
            return self._hl_mids().get(self._hl_key(symbol))
        if source == 'bittensor':
            netuid = self._bt_netuid(symbol)
            return self._bt_prices().get(str(netuid)) if netuid is not None else None
        if source == 'dex':
            quote = self._dex_prices().get(symbol.upper())
            return quote['price'] if quote else None

        cg_id = self.CG_IDS.get(symbol.upper())
        if not cg_id:
            return None

        cached = self._price_cache.get(cg_id)
        if cached and (time.time() - cached['ts']) < self._price_cache_ttl:
            return cached['price']
        try:
            resp = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={'ids': cg_id, 'vs_currencies': 'usd'},
                timeout=10,
            )
            resp.raise_for_status()
            price = resp.json().get(cg_id, {}).get('usd')
            if price:
                self._price_cache[cg_id] = {'price': price, 'ts': time.time()}
            return price
        except Exception:
            return cached['price'] if cached else None

    def _price_at(self, symbol: str, ts: float, source: str = None) -> Dict:
        """Price at a past moment, for settling a prediction honestly.

        Resolution runs whenever someone next asks — minutes or days after a
        prediction came due — so scoring against the spot price would score the
        wrong moment. Both sources can answer historically; if neither does we
        fall back to spot and say so in `mode`.
        """
        if source is None:
            market = self._market(symbol)
            source = market.get('source', 'coingecko') if market else 'coingecko'

        try:
            if source == 'hyperliquid':
                start = int(ts // 3600 * 3600) * 1000
                # Candles are keyed the way HL names the pair, not the way we
                # list it — '@107', not 'HYPE/USDC'. The key can carry a slash
                # ('PURR/USDC'), so it is quoted before it goes in a path.
                coin = self._hl_key(symbol)
                # The module only takes a lookback in hours, so ask for enough
                # to cover ts and pick the candle that contains it.
                hours = int((time.time() - ts) // 3600) + 2
                candles = self._hl_mod_get(
                    f'/candles/{quote_plus(coin)}?interval=1h&hours={hours}', timeout=15)
                if candles is None:
                    candles = self._hl_post({
                        'type': 'candleSnapshot',
                        'req': {'coin': coin, 'interval': '1h',
                                'startTime': start, 'endTime': start + 3600_000},
                    })
                match = [c for c in (candles or []) if c.get('t') == start]
                if match:
                    return {'price': float(match[0]['c']), 'mode': 'historical'}
            elif source == 'bittensor':
                # The bt indexer answers "every alpha price at ts" from its own
                # snapshots — the nearest one, five minutes apart at worst.
                netuid = self._bt_netuid(symbol)
                if netuid is not None:
                    data = self._bt_call('bt_prices_at', {'ts': int(ts)}, timeout=20)
                    price = ((data or {}).get('prices') or {}).get(str(netuid))
                    if price:
                        return {'price': float(price), 'mode': 'historical'}
            elif source == 'dex':
                hit = self._dex_price_at(symbol, ts)
                if hit:
                    return hit
            else:
                cg_id = self.CG_IDS.get(symbol.upper())
                if cg_id:
                    resp = requests.get(
                        f'https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart/range',
                        params={'vs_currency': 'usd',
                                'from': int(ts - 3600), 'to': int(ts + 3600)},
                        timeout=15,
                    )
                    resp.raise_for_status()
                    points = resp.json().get('prices', [])
                    if points:
                        nearest = min(points, key=lambda p: abs(p[0] / 1000 - ts))
                        return {'price': float(nearest[1]), 'mode': 'historical'}
        except Exception:
            pass

        spot = self._get_token_price(symbol, source)
        return {'price': spot, 'mode': 'spot'} if spot else {'price': None, 'mode': 'none'}

    # ── Positions ────────────────────────────────────────────────────

    def open_position(self, asset: str, amount: float, address: str) -> Dict:
        """Open a trading position: buy asset with USDC through protocol"""
        if amount <= 0:
            return {'error': 'Amount must be positive'}
        if not address:
            return {'error': 'Address required'}

        markets = self._load_json(self.markets_path, [])
        market = None
        for m in markets:
            if m['symbol'].upper() == asset.upper() or m['token'].lower() == asset.lower():
                market = m
                break

        if not market:
            return {'error': f'Market not found for {asset}'}
        if not market.get('active'):
            return {'error': f'{asset} market not active'}

        positions = self._load_json(self.positions_path, [])
        position_id = len(positions) + 1

        price = self._get_token_price(market['symbol'])
        asset_amount = (amount / price) if price and price > 0 else 0

        position = {
            'id': position_id,
            'trader': address,
            'asset': market['symbol'],
            'token': market['token'],
            'usdc_in': amount,
            'asset_amount': asset_amount,
            'entry_price': price,
            'open_time': time.time(),
            'closed': False,
            'usdc_out': None,
            'profit': None,
            'prefi_earned': None,
        }
        positions.append(position)

        for m in markets:
            if m['token'].lower() == market['token'].lower():
                m['total_volume'] = m.get('total_volume', 0) + amount
                m['total_positions'] = m.get('total_positions', 0) + 1
        self._save_json(self.markets_path, markets)
        self._save_json(self.positions_path, positions)

        return {
            'position_id': position_id,
            'asset': market['symbol'],
            'usdc_in': amount,
            'asset_amount': round(asset_amount, 8),
            'entry_price': price,
            'status': 'open',
        }

    def close_position(self, position_id: int, address: str) -> Dict:
        """Close a position: sell asset back to USDC, capture profit to treasury"""
        positions = self._load_json(self.positions_path, [])
        treasury = self._init_treasury()

        pos = None
        pos_idx = None
        for i, p in enumerate(positions):
            if p['id'] == position_id:
                pos = p
                pos_idx = i
                break

        if not pos:
            return {'error': f'Position {position_id} not found'}
        if pos['trader'].lower() != address.lower():
            return {'error': 'Not your position'}
        if pos['closed']:
            return {'error': 'Already closed'}

        price = self._get_token_price(pos['asset'])
        if not price:
            return {'error': f'Could not get price for {pos["asset"]}'}

        usdc_out = pos['asset_amount'] * price
        profit = usdc_out - pos['usdc_in']

        pos['closed'] = True
        pos['close_time'] = time.time()
        pos['exit_price'] = price
        pos['usdc_out'] = round(usdc_out, 6)
        pos['profit'] = round(profit, 6)

        # Update market stats
        markets = self._load_json(self.markets_path, [])
        for m in markets:
            if m['token'].lower() == pos.get('token', '').lower():
                m['total_volume'] = m.get('total_volume', 0) + usdc_out
                if profit > 0:
                    m['total_profit'] = m.get('total_profit', 0) + profit
                    m['win_count'] = m.get('win_count', 0) + 1
                else:
                    m['loss_count'] = m.get('loss_count', 0) + 1

        if profit > 0:
            pos['prefi_earned'] = round(profit, 6)
            treasury['balance'] += profit
            treasury['total_captured'] += profit
            treasury['total_prefi_minted'] = treasury.get('total_prefi_minted', 0) + profit
        else:
            pos['prefi_earned'] = 0

        positions[pos_idx] = pos
        self._save_json(self.positions_path, positions)
        self._save_json(self.treasury_path, treasury)
        self._save_json(self.markets_path, markets)

        return {
            'position_id': position_id,
            'asset': pos['asset'],
            'usdc_in': pos['usdc_in'],
            'usdc_out': pos['usdc_out'],
            'profit': pos['profit'],
            'prefi_earned': pos['prefi_earned'],
            'entry_price': pos['entry_price'],
            'exit_price': price,
            'hold_time': round(pos['close_time'] - pos['open_time']),
            'return_pct': round(profit / pos['usdc_in'] * 100, 2) if pos['usdc_in'] > 0 else 0,
            'status': 'profitable' if profit > 0 else 'loss',
        }

    def get_positions(self, address: str) -> List[Dict]:
        """Get all positions for an address"""
        positions = self._load_json(self.positions_path, [])
        result = []
        for p in positions:
            if p['trader'].lower() == address.lower():
                info = {**p}
                if not p['closed']:
                    price = self._get_token_price(p['asset'])
                    if price:
                        info['current_price'] = price
                        info['unrealized_pnl'] = round(
                            p['asset_amount'] * price - p['usdc_in'], 6
                        )
                        info['return_pct'] = round(
                            (p['asset_amount'] * price - p['usdc_in']) / p['usdc_in'] * 100, 2
                        ) if p['usdc_in'] > 0 else 0
                result.append(info)
        return result

    # ── PREFI balance ────────────────────────────────────────────────

    def prefi_balance(self, address: str) -> Dict:
        """What an address actually holds.

        PREFI has no transfers — it is minted by winning trades and correct
        predictions, and it leaves by being locked or burned. So the balance is
        derivable from the ledgers, and every spend path checks it. Without this
        you could lock or burn tokens you never earned.
        """
        addr = (address or '').lower()

        from_trades = sum(
            p.get('prefi_earned') or 0
            for p in self._load_json(self.positions_path, [])
            if p['trader'].lower() == addr and p.get('closed')
        )
        predictions = [p for p in self._load_json(self.predictions_path, [])
                       if p['predictor'].lower() == addr]
        from_predictions = sum(p.get('payout') or 0 for p in predictions if p.get('resolved'))
        from_free = sum(p.get('payout') or 0 for p in predictions
                        if p.get('resolved') and p.get('free'))
        burned = sum(p.get('burn', 0) for p in predictions)
        locked = sum(s['amount'] for s in self._load_json(self.stakes_path, [])
                     if s['staker'].lower() == addr and not s.get('withdrawn'))

        minted = from_trades + from_predictions
        return {
            'address': address,
            'minted': round(minted, 6),
            'from_trades': round(from_trades, 6),
            'from_predictions': round(from_predictions, 6),
            # Free calls mint too — this is the slice earned without spending.
            'from_free': round(from_free, 6),
            'burned': round(burned, 6),
            'locked': round(locked, 6),
            'available': round(minted - burned - locked, 6),
        }

    # ── Predictions ──────────────────────────────────────────────────

    def predict(self, asset: str, predicted_price: float, burn: float = 0,
                address: str = None, horizon: int = None) -> Dict:
        """Call an asset's price one horizon from now — free, or with a burn.

        Omit `burn` (or pass 0) and the call is **free**: it costs nothing, it
        is scored by exactly the same curve, and a good one still mints PREFI —
        `free_payout` × score. Every address gets `free_per_day` of them per
        rolling 24 hours, which is the only way in for someone holding none.

        Burn PREFI instead and you are playing for size: the burn is gone the
        moment it is placed, and a perfect call returns `multiplier`× it.
        Either way the scoring params are snapshotted onto the prediction, so
        retuning them later can never re-price a bet already on the table.
        """
        if not address:
            return {'error': 'Address required'}

        params = self.get_scoring()
        horizon = int(horizon) if horizon else params['horizon']
        if not scoring.MIN_HORIZON <= horizon <= scoring.MAX_HORIZON:
            return {'error': f'Horizon must be {scoring.MIN_HORIZON}..'
                             f'{scoring.MAX_HORIZON} seconds'}

        try:
            predicted_price = float(predicted_price)
            burn = float(burn or 0)
        except (TypeError, ValueError):
            return {'error': 'predicted_price and burn must be numbers'}
        if predicted_price <= 0:
            return {'error': 'Predicted price must be positive'}
        if burn < 0:
            return {'error': 'Burn cannot be negative'}

        # A zero burn is the free tier, not a rejected one.
        free = burn == 0
        if free:
            quota = self.free_quota(address)
            if not quota['enabled']:
                return {'error': 'Free predictions are off — '
                                 f'burn at least {params["min_burn"]} PREFI'}
            if quota['remaining'] <= 0:
                mins = max(1, round(quota['seconds_until_reset'] / 60))
                return {'error': f'Out of free calls — {quota["limit"]} per 24h, '
                                 f'next one in {mins} min'}
        elif burn < params['min_burn']:
            return {'error': f'Minimum burn is {params["min_burn"]} PREFI '
                             f'(or leave it empty for a free call)'}

        market = self._market(asset)
        if not market:
            return {'error': f'Market not found for {asset}'}
        if not market.get('active'):
            return {'error': f'{asset} market not active'}

        if not free:
            balance = self.prefi_balance(address)
            if balance['available'] < burn:
                return {'error': f'Insufficient PREFI — {balance["available"]} available, '
                                 f'{burn} needed'}

        entry_price = self._get_token_price(market['symbol'], market.get('source'))
        if not entry_price:
            return {'error': f'Could not price {market["symbol"]}'}

        predictions = self._load_json(self.predictions_path, [])
        now = time.time()
        prediction = {
            'id': len(predictions) + 1,
            'predictor': address,
            'asset': market['symbol'],
            'source': market.get('source', 'coingecko'),
            'entry_price': entry_price,
            'predicted_price': predicted_price,
            'burn': burn,
            'free': free,
            'horizon': horizon,
            'created_at': now,
            'resolve_at': now + horizon,
            'params': params,
            'resolved': False,
            'actual_price': None,
            'abs_error': None,
            'normalized_error': None,
            'score': None,
            'payout': None,
            'resolved_at': None,
            'price_mode': None,
        }
        predictions.append(prediction)
        self._save_json(self.predictions_path, predictions)

        if burn:
            treasury = self._init_treasury()
            treasury['total_prefi_burned'] = treasury.get('total_prefi_burned', 0) + burn
            self._save_json(self.treasury_path, treasury)

        result = {
            'prediction_id': prediction['id'],
            'asset': prediction['asset'],
            'entry_price': entry_price,
            'predicted_price': predicted_price,
            'implied_move_pct': round((predicted_price - entry_price) / entry_price * 100, 2),
            'burned': burn,
            'free': free,
            'max_payout': (scoring.free_mint(1.0, params) if free
                           else scoring.payout(burn, 1.0, params)),
            'resolves_at': datetime.fromtimestamp(prediction['resolve_at']).isoformat(),
            'model': params['model'],
            'status': 'open',
        }
        if free:
            # Counted after the fact, so the number shown is what is left.
            result['free_remaining'] = self.free_quota(address)['remaining']
        return result

    def free_quota(self, address: str) -> Dict:
        """How many free calls this address has left, and when the next one
        lands. The allowance is a rolling 24h window rather than a calendar
        day — no midnight stampede, and a call frees up exactly a day after it
        was spent."""
        params = self.get_scoring()
        limit = params['free_per_day']
        now = time.time()
        addr = (address or '').lower()

        spent = sorted(p['created_at'] for p in self._load_json(self.predictions_path, [])
                       if p.get('free') and p['predictor'].lower() == addr
                       and p['created_at'] > now - scoring.FREE_WINDOW)
        remaining = max(0, limit - len(spent))

        # The window is freed by the oldest call still inside it.
        resets_at = spent[0] + scoring.FREE_WINDOW if spent else None
        return {
            'address': address,
            'enabled': limit > 0,
            'limit': limit,
            'used': len(spent),
            'remaining': remaining,
            'window_hours': scoring.FREE_WINDOW // 3600,
            'free_payout': params['free_payout'],
            'resets_at': datetime.fromtimestamp(resets_at).isoformat() if resets_at else None,
            'seconds_until_reset': int(resets_at - now) if resets_at else 0,
        }

    def resolve_predictions(self) -> Dict:
        """Settle every prediction whose horizon has passed.

        Called on read as well as on demand, so a prediction is never left
        hanging just because nobody ran a cron. Prices are looked up *at the
        resolve time*, not now — see _price_at.
        """
        predictions = self._load_json(self.predictions_path, [])
        now = time.time()
        resolved, minted = [], 0.0

        for p in predictions:
            if p.get('resolved') or p['resolve_at'] > now:
                continue

            quote = self._price_at(p['asset'], p['resolve_at'], p.get('source'))
            if not quote['price']:
                continue  # unpriceable right now — try again on the next pass

            params = p.get('params') or self.get_scoring()
            result = scoring.score(p['predicted_price'], quote['price'], params)
            # A free call has no burn to scale, so it mints off `free_payout`.
            payout = (scoring.free_mint(result['score'], params) if p.get('free')
                      else scoring.payout(p['burn'], result['score'], params))

            p.update({
                'resolved': True,
                'actual_price': quote['price'],
                'price_mode': quote['mode'],
                'resolved_at': now,
                'abs_error': result['abs_error'],
                'normalized_error': result['normalized_error'],
                'score': result['score'],
                'payout': payout,
                'net': round(payout - p['burn'], 6),
            })
            minted += payout
            resolved.append(p['id'])

        if resolved:
            self._save_json(self.predictions_path, predictions)
            treasury = self._init_treasury()
            treasury['total_prefi_minted'] = treasury.get('total_prefi_minted', 0) + minted
            self._save_json(self.treasury_path, treasury)

        return {
            'resolved': resolved,
            'prefi_minted': round(minted, 6),
            'pending': sum(1 for p in predictions if not p.get('resolved')),
        }

    def get_predictions(self, address: str = None, limit: int = 100) -> List[Dict]:
        """Predictions, newest first — all of them, or one address's"""
        self.resolve_predictions()
        predictions = self._load_json(self.predictions_path, [])
        if address:
            predictions = [p for p in predictions
                           if p['predictor'].lower() == address.lower()]

        now = time.time()
        for p in predictions:
            p['seconds_remaining'] = max(0, int(p['resolve_at'] - now))
            if not p.get('resolved'):
                current = self._get_token_price(p['asset'], p.get('source'))
                if current:
                    p['current_price'] = current
                    # What it would score if it settled at this instant.
                    p['projected'] = scoring.score(p['predicted_price'], current,
                                                   p.get('params'))
        predictions.sort(key=lambda p: p['created_at'], reverse=True)
        return predictions[:max(1, int(limit))]

    def prediction_board(self) -> List[Dict]:
        """Forecaster rankings — average score over resolved calls, with the
        burn/payout ledger that produced it."""
        self.resolve_predictions()
        players: Dict[str, Dict] = {}

        for p in self._load_json(self.predictions_path, []):
            addr = p['predictor']
            row = players.setdefault(addr, {
                'address': addr, 'predictions': 0, 'resolved': 0,
                'free_calls': 0, 'total_burned': 0.0, 'total_payout': 0.0,
                'score_sum': 0.0, 'best_score': 0.0,
            })
            row['predictions'] += 1
            # Accuracy is accuracy — free calls rank alongside burned ones, and
            # the count says which is which.
            row['free_calls'] += 1 if p.get('free') else 0
            row['total_burned'] += p.get('burn', 0)
            if p.get('resolved'):
                row['resolved'] += 1
                row['total_payout'] += p.get('payout') or 0
                row['score_sum'] += p.get('score') or 0
                row['best_score'] = max(row['best_score'], p.get('score') or 0)

        board = []
        for row in players.values():
            row['avg_score'] = round(row['score_sum'] / row['resolved'], 4) if row['resolved'] else 0
            row['net_prefi'] = round(row['total_payout'] - row['total_burned'], 6)
            row['total_burned'] = round(row['total_burned'], 6)
            row['total_payout'] = round(row['total_payout'], 6)
            row['best_score'] = round(row['best_score'], 4)
            row.pop('score_sum')
            board.append(row)

        # Unresolved forecasters sort last — an average of nothing isn't a rank.
        board.sort(key=lambda r: (r['resolved'] > 0, r['avg_score'], r['net_prefi']),
                   reverse=True)
        for i, row in enumerate(board):
            row['rank'] = i + 1
        return board

    # ── Scoring config ───────────────────────────────────────────────

    def get_scoring(self) -> Dict:
        """Active scoring params, defaults filled in — including `fn`, the
        resolved score function."""
        try:
            return scoring.validate(self._load_json(self.scoring_path, {}), self.fns)
        except ValueError:
            # A hand-edited file with junk in it shouldn't brick predictions.
            return scoring.validate({}, self.fns)

    def set_scoring(self, **params) -> Dict:
        """Retune the score. Only affects predictions placed after this call —
        open ones carry the params they were made under. `model` is any score
        function: a default or one from the library; `model_params` overrides
        its other parameters (JSON)."""
        given = {k: v for k, v in params.items() if v is not None}
        if not given:
            return {'error': f'nothing to set — params are {list(scoring.DEFAULT_PARAMS)}'}
        if isinstance(given.get('model_params'), str):
            try:
                given['model_params'] = json.loads(given['model_params'] or '{}')
            except json.JSONDecodeError as e:
                return {'error': f'model_params must be JSON: {e.msg}'}
        current = self.get_scoring()
        if 'model' in given and 'model_params' not in given:
            given['model_params'] = {}          # a new function, its own defaults
        try:
            merged = scoring.validate({**current, **given, 'fn': None}, self.fns)
        except ValueError as e:
            return {'error': str(e)}
        self._save_json(self.scoring_path, merged)
        return {'status': 'updated', 'scoring': merged, 'changed': list(given)}

    def scoring_models(self) -> Dict:
        """The model registry — name → what its curve does"""
        return {
            'models': scoring.describe_models(self.fns),
            'defaults': dict(scoring.DEFAULT_PARAMS),
            'active': self.get_scoring(),
        }

    def score_preview(self, predicted: float, actual: float,
                      model: str = None, tolerance: float = None,
                      burn: float = None, model_params=None) -> Dict:
        """Score a hypothetical without placing anything — the same code path
        the resolver uses, so the number shown is the number paid."""
        params = self.get_scoring()
        if model:
            params['model'] = model
            params['model_params'] = {}
            params['fn'] = None
        if tolerance:
            params['tolerance'] = float(tolerance)
        if model_params:
            if isinstance(model_params, str):
                try:
                    model_params = json.loads(model_params)
                except json.JSONDecodeError as e:
                    return {'error': f'model_params must be JSON: {e.msg}'}
            params['model_params'] = model_params
            params['fn'] = None
        try:
            params = scoring.validate(params, self.fns)
        except ValueError as e:
            return {'error': str(e)}

        result = scoring.score(float(predicted), float(actual), params)
        stake = float(burn) if burn else params['min_burn']
        result['burn'] = stake
        result['payout'] = scoring.payout(stake, result['score'], params)
        result['net'] = round(result['payout'] - stake, 6)
        # What the same call would have minted for free — nothing at risk, so
        # the free number is always the net.
        result['free_payout'] = scoring.free_mint(result['score'], params)
        return result

    # ── Staking ──────────────────────────────────────────────────────

    # ── Score functions ───────────────────────────────────────────────
    # The rule that turns a miss into a payout is a program — see curves.py.
    # These are the protocol-facing names: list, try, save (signed), share,
    # publish to the store, import from a code or a CID.

    @staticmethod
    def _fn_row(spec: Dict, sample: bool = True) -> Dict:
        row = {k: spec.get(k) for k in (
            'name', 'description', 'expr', 'params', 'author', 'owner', 'builtin',
            'origin_cid', 'cid', 'created_at', 'updated_at')}
        row['builtin'] = bool(row['builtin'])
        row['digest'] = spec.get('digest') or curves.digest(spec)
        if sample:
            row['sample'] = curves.sample(spec)
        return row

    def fn_list(self, sample: bool = True) -> Dict:
        """Every score function — the defaults and the library — with its
        curve sampled, which function each layer is using, and the language."""
        try:
            pool_model = self.pool.config().get('model')
        except Exception:
            pool_model = None
        return {
            'functions': [self._fn_row(spec, sample) for spec in self.fns.all()],
            'active': {'pool': pool_model, 'predict': self.get_scoring()['model']},
            'language': curves.language(),
        }

    def fn_get(self, name: str) -> Dict:
        """One function, its curve and its report."""
        spec = self.fns.get(name)
        if not spec:
            return {'error': f'no function named `{name}`', 'have': self.fns.names()}
        row = self._fn_row(spec)
        row['report'] = curves.report(spec)
        row['code'] = curves.to_code(spec)
        return row

    def fn_test(self, expr: str, params=None, name: str = None,
                tolerance: float = None, actual: float = 100.0,
                calls=None, stake: float = 100.0, fee_bps: int = 0) -> Dict:
        """Run a function without saving it: validate, draw the curve, and
        settle a mock pot with it so the split is visible before anyone adopts
        it. `calls` is a list of prices called against `actual` (default: on
        the nose, 0.5%, 1%, 3% and 10% off), each staked `stake`."""
        try:
            spec = curves.validate_spec({'name': name or 'draft', 'expr': expr,
                                         'params': params}, name_required=False)
            fn = curves.resolve(spec, tolerance=tolerance)
        except (curves.ExprError, ValueError) as e:
            return {'error': str(e)}
        if isinstance(calls, str):
            try:
                calls = [float(x) for x in calls.split(',') if x.strip()]
            except ValueError:
                return {'error': 'calls must be numbers, comma separated'}
        actual = float(actual)
        calls = [float(c) for c in (calls or [actual, actual * 1.005, actual * 1.01,
                                              actual * 0.97, actual * 1.10])]
        entries = [{'id': i + 1, 'address': f'caller-{i + 1}', 'amount': float(stake),
                    'predicted_price': c} for i, c in enumerate(calls)]
        pot = pool_mod.settle_asset(entries, actual, fn, int(fee_bps or 0))
        return {
            'fn': fn,
            'report': curves.report(fn),
            'pot': {
                'actual': actual, 'stake': float(stake), 'fee_bps': int(fee_bps or 0),
                'mode': pot['mode'], 'gross': pot['gross'], 'pot': pot['pot'],
                'winner': pot.get('winner'),
                'entries': [{'called': e['predicted_price'],
                             'miss_pct': round(abs(e['predicted_price'] - actual) / actual * 100, 3),
                             'accuracy': e['accuracy'], 'share': e.get('share', 0.0),
                             'payout': e['payout'], 'net': e['net']} for e in pot['entries']],
            },
            'code': curves.to_code({**spec, 'name': spec['name'] or 'draft'}),
        }

    @staticmethod
    def _fn_spec(name, expr, params, description, author=None, origin_cid=None) -> Dict:
        spec = curves.validate_spec({'name': name, 'expr': expr, 'params': params,
                                     'description': description or ''})
        if author:
            spec['author'] = str(author).lower()
        if origin_cid:
            spec['origin_cid'] = origin_cid
        return spec

    def fn_sign(self, address: str, name: str, expr: str, params=None,
                description: str = '') -> Dict:
        """The message a wallet signs to save a function: its name and a hash
        of exactly what will be stored, bound to the address's nonce."""
        try:
            spec = self._fn_spec(name, expr, params, description)
        except curves.ExprError as e:
            return {'error': str(e)}
        return {**self.pool.sign_request('fn_save', address, name=spec['name'],
                                         digest=curves.digest(spec)),
                'digest': curves.digest(spec)}

    def fn_save(self, address: str, name: str, expr: str, params=None,
                description: str = '', signature: str = None, nonce: int = None,
                origin_cid: str = None, author: str = None) -> Dict:
        """Save a function to the library under `address`. Signed, like a free
        call: it costs nothing, but a name in the library is public and only
        its owner may change it. Returns the record and its share code."""
        if not hyperevm.is_address(address):
            return {'error': 'a valid 0x address is required'}
        addr = hyperevm.normalize(address)
        try:
            spec = self._fn_spec(name, expr, params, description, author, origin_cid)
        except curves.ExprError as e:
            return {'error': str(e)}
        digest = curves.digest(spec)
        pool = self.pool
        check = pool_mod.sigauth.verify(
            'fn_save', addr, [('digest', digest), ('name', spec['name'])],
            pool.nonce(addr) if nonce is None else int(nonce), signature)
        if not check['ok']:
            return {'error': check['error'], 'sign_message': check['message'],
                    'nonce': pool.nonce(addr)}
        try:
            record = self.fns.save(spec, addr,
                                   origin={'origin_cid': origin_cid} if origin_cid else None)
        except curves.ExprError as e:
            return {'error': str(e)}
        pool._bump_nonce(addr)
        return {'status': 'saved', 'function': self._fn_row(record),
                'code': curves.to_code(record),
                'note': ('the pool owner can switch the pot to it with '
                         f"pool-set model={record['name']}; predictions with "
                         f"set-scoring model={record['name']}")}

    def fn_delete(self, address: str, name: str, signature: str = None,
                  nonce: int = None) -> Dict:
        """Remove a saved function — its owner only. Rounds and predictions
        that opened under it keep their snapshot and settle unchanged."""
        if not hyperevm.is_address(address):
            return {'error': 'a valid 0x address is required'}
        addr = hyperevm.normalize(address)
        pool = self.pool
        name = (name or '').strip().lower()
        check = pool_mod.sigauth.verify(
            'fn_delete', addr, [('name', name)],
            pool.nonce(addr) if nonce is None else int(nonce), signature)
        if not check['ok']:
            return {'error': check['error'], 'sign_message': check['message'],
                    'nonce': pool.nonce(addr)}
        # A function the pool (or the predict layer) is scoring with right now
        # stays put: the owner switches first, then it can go. Rounds already
        # open keep their snapshot either way.
        try:
            in_use = pool.state()['config'].get('model') == name
        except Exception:
            in_use = False
        if in_use:
            return {'error': f'`{name}` is the pool\'s live score function — '
                             'the pool owner must switch models first'}
        if self.get_scoring().get('model') == name:
            return {'error': f'`{name}` is the prediction layer\'s live score '
                             'function — set-scoring to another model first'}
        try:
            gone = self.fns.delete(name, addr)
        except curves.ExprError as e:
            return {'error': str(e)}
        pool._bump_nonce(addr)
        return {'status': 'deleted', 'name': name, 'was': self._fn_row(gone, sample=False)}

    def fn_share(self, name: str) -> Dict:
        """How to hand a function to someone: a share code that works
        anywhere PreFi runs, and the store CID if it has been published."""
        spec = self.fns.get(name)
        if not spec:
            return {'error': f'no function named `{name}`', 'have': self.fns.names()}
        out = {'name': spec['name'], 'code': curves.to_code(spec),
               'bundle': curves.bundle(spec), 'cid': spec.get('cid')}
        if spec.get('cid'):
            out['url'] = f"{self._store().url}/get?cid={spec['cid']}"
        out['import'] = (f"m prefi/fn_import source={out['cid'] or '<code>'} "
                         "address=0x… — or paste it into the console")
        return out

    def _store(self):
        try:
            import store_link
        except ImportError:
            from . import store_link
        return store_link.StoreLink()

    def fn_publish(self, name: str, token: str = None) -> Dict:
        """Put a function in the fleet's store as a public object; the CID is
        the share link. The upload is made with the caller's protocol token
        (Bearer) — the store's whitelist, quota and terms apply to *them*."""
        try:
            import store_link
        except ImportError:
            from . import store_link
        spec = self.fns.get(name)
        if not spec:
            return {'error': f'no function named `{name}`', 'have': self.fns.names()}
        if not token:
            try:
                token = store_link.local_token()
            except Exception as e:
                return {'error': 'a protocol token is needed to publish — sign in '
                                 f'(or the host key could not mint one: {e})'}
        payload = curves.bundle(spec)
        payload['published_at'] = time.time()
        try:
            put = self._store().put_json(token, f"prefi-fn-{spec['name']}.json",
                                         payload, public=True)
        except store_link.StoreError as e:
            return {'error': e.message, 'status': e.status}
        if not spec.get('builtin'):
            self.fns.annotate(spec['name'], cid=put['cid'], published_at=payload['published_at'])
        return {'status': 'published', 'name': spec['name'], 'cid': put['cid'],
                'url': put['url'], 'size': put['size'],
                'import': f"m prefi/fn_import source={put['cid']} address=0x…"}

    def fn_import(self, source: str, address: str = None, signature: str = None,
                  nonce: int = None, name: str = None) -> Dict:
        """Bring in a shared function from a share code or a store CID.

        Without `address` it only previews (spec, curve, report) — nothing is
        written. With one it saves, signed like `fn_save`; `name` renames it
        when the shared name is already taken here."""
        try:
            import store_link
        except ImportError:
            from . import store_link
        source = (source or '').strip()
        origin_cid = None
        try:
            if curves.is_code(source):
                spec = curves.from_code(source)
            elif source.startswith('Qm') or source.startswith('ba'):
                data = self._store().fetch_json(source)
                spec = curves.from_bundle(data)
                origin_cid = source
            elif source.startswith('{'):
                spec = curves.from_bundle(json.loads(source))
            else:
                return {'error': 'source must be a share code (prefi.fn.…), a store '
                                 'CID, or a bundle'}
        except store_link.StoreError as e:
            return {'error': e.message, 'status': e.status}
        except (curves.ExprError, ValueError) as e:
            return {'error': str(e)}
        origin_cid = origin_cid or spec.get('origin_cid')
        if name:
            spec['name'] = str(name).strip().lower()
        preview = {**self._fn_row(spec), 'report': curves.report(spec),
                   'origin_cid': origin_cid}
        if not address:
            taken = self.fns.get(spec['name'])
            return {'preview': preview,
                    'name_taken': bool(taken),
                    'next': ('save it with fn_import address=0x… (signed); pass '
                             'name= to rename' + (' — this name is taken' if taken else ''))}
        out = self.fn_save(address, spec['name'], spec['expr'], spec['params'],
                           spec.get('description', ''), signature, nonce,
                           origin_cid=origin_cid, author=spec.get('author'))
        if 'error' in out:
            out['preview'] = preview
        return out

    def lock_prefi(self, amount: float, duration: int, address: str) -> Dict:
        """Lock PREFI tokens for staketime

        Args:
            amount: PREFI amount to lock
            duration: lock duration in seconds (min 1 week, max 52 weeks)
            address: staker address
        """
        if amount <= 0:
            return {'error': 'Amount must be positive'}
        if not address:
            return {'error': 'Address required'}
        if duration < 604800:
            return {'error': 'Minimum lock duration is 1 week (604800s)'}
        if duration > 31449600:
            return {'error': 'Maximum lock duration is 52 weeks'}

        available = self.prefi_balance(address)['available']
        if available < amount:
            return {'error': f'Insufficient PREFI — {available} available, {amount} needed'}

        stakes = self._load_json(self.stakes_path, [])
        stake_id = len(stakes) + 1
        now = time.time()
        staketime = amount * duration

        stake = {
            'id': stake_id,
            'staker': address,
            'amount': amount,
            'lock_end': now + duration,
            'duration': duration,
            'staketime': staketime,
            'start_epoch': self._current_epoch(),
            'withdrawn': False,
            'created_at': datetime.now().isoformat(),
        }
        stakes.append(stake)
        self._save_json(self.stakes_path, stakes)

        return {
            'stake_id': stake_id,
            'amount': amount,
            'duration_weeks': round(duration / 604800, 1),
            'staketime': staketime,
            'lock_end': datetime.fromtimestamp(now + duration).isoformat(),
            'status': 'locked',
        }

    def extend_lock(self, stake_id: int, added_duration: int, address: str) -> Dict:
        """Extend lock duration on an existing stake"""
        if added_duration < 604800:
            return {'error': 'Minimum extension is 1 week'}

        stakes = self._load_json(self.stakes_path, [])
        for i, s in enumerate(stakes):
            if s['id'] == stake_id:
                if s['staker'].lower() != address.lower():
                    return {'error': 'Not your stake'}
                if s['withdrawn']:
                    return {'error': 'Already withdrawn'}

                new_end = s['lock_end'] + added_duration
                max_end = time.time() + 31449600  # 52 weeks from now
                if new_end > max_end:
                    return {'error': 'Would exceed 52 week maximum'}

                added_staketime = s['amount'] * added_duration
                s['lock_end'] = new_end
                s['duration'] += added_duration
                s['staketime'] += added_staketime
                stakes[i] = s
                self._save_json(self.stakes_path, stakes)
                return {
                    'stake_id': stake_id,
                    'added_weeks': round(added_duration / 604800, 1),
                    'new_staketime': s['staketime'],
                    'new_lock_end': datetime.fromtimestamp(new_end).isoformat(),
                    'status': 'extended',
                }
        return {'error': f'Stake {stake_id} not found'}

    def unlock_prefi(self, stake_id: int, address: str) -> Dict:
        """Unlock expired PREFI stake"""
        stakes = self._load_json(self.stakes_path, [])
        for i, s in enumerate(stakes):
            if s['id'] == stake_id:
                if s['staker'].lower() != address.lower():
                    return {'error': 'Not your stake'}
                if s['withdrawn']:
                    return {'error': 'Already withdrawn'}
                if time.time() < s['lock_end']:
                    remaining = s['lock_end'] - time.time()
                    return {'error': f'Still locked for {int(remaining)}s'}

                s['withdrawn'] = True
                s['withdrawn_at'] = datetime.now().isoformat()
                stakes[i] = s
                self._save_json(self.stakes_path, stakes)
                return {
                    'stake_id': stake_id,
                    'amount': s['amount'],
                    'status': 'unlocked',
                }
        return {'error': f'Stake {stake_id} not found'}

    def get_stakes(self, address: str) -> Dict:
        """Get staking info for an address"""
        stakes = self._load_json(self.stakes_path, [])
        user_stakes = [s for s in stakes if s['staker'].lower() == address.lower()]

        active = [s for s in user_stakes if not s['withdrawn']]
        total_staketime = sum(s['staketime'] for s in active)
        total_locked = sum(s['amount'] for s in active)

        now = time.time()
        for s in user_stakes:
            s['is_unlockable'] = not s['withdrawn'] and now >= s['lock_end']
            s['time_remaining'] = max(0, int(s['lock_end'] - now))

        return {
            'address': address,
            'total_locked': total_locked,
            'total_staketime': total_staketime,
            'active_stakes': len(active),
            'stakes': user_stakes,
        }

    def _current_epoch(self) -> int:
        """Get current weekly epoch number"""
        treasury = self._init_treasury()
        genesis = treasury.get('genesis_time', time.time())
        return int((time.time() - genesis) / 604800)

    # ── Treasury ─────────────────────────────────────────────────────

    def treasury(self) -> Dict:
        """Get treasury status"""
        treasury = self._init_treasury()
        stakes = self._load_json(self.stakes_path, [])
        active_stakes = [s for s in stakes if not s.get('withdrawn')]
        total_staketime = sum(s['staketime'] for s in active_stakes)
        total_staked = sum(s['amount'] for s in active_stakes)

        return {
            'balance': treasury.get('balance', 0),
            'total_captured': treasury.get('total_captured', 0),
            'total_distributed': treasury.get('total_distributed', 0),
            'total_prefi_minted': treasury.get('total_prefi_minted', 0),
            'total_prefi_burned': treasury.get('total_prefi_burned', 0),
            'prefi_supply': round(treasury.get('total_prefi_minted', 0)
                                  - treasury.get('total_prefi_burned', 0), 6),
            'current_epoch': self._current_epoch(),
            'total_staketime': total_staketime,
            'total_staked': total_staked,
            'active_stakers': len(set(s['staker'] for s in active_stakes)),
            'epoch_count': len(treasury.get('epochs', [])),
        }

    def deposit_rewards(self, amount: float = None) -> Dict:
        """Deposit USDC from treasury into current epoch for staker distribution"""
        treasury = self._init_treasury()
        stakes = self._load_json(self.stakes_path, [])

        active_stakes = [s for s in stakes if not s.get('withdrawn')]
        if not active_stakes:
            return {'error': 'No active stakers'}

        balance = treasury.get('balance', 0)
        if balance <= 0:
            return {'error': 'Treasury empty'}

        deposit = amount if amount and amount <= balance else balance
        epoch = self._current_epoch()
        total_staketime = sum(s['staketime'] for s in active_stakes)

        epoch_record = {
            'epoch': epoch,
            'amount': deposit,
            'total_staketime': total_staketime,
            'stakers': len(set(s['staker'] for s in active_stakes)),
            'timestamp': datetime.now().isoformat(),
            'claims': {},
        }

        treasury['balance'] -= deposit
        treasury['total_distributed'] = treasury.get('total_distributed', 0) + deposit
        treasury.setdefault('epochs', []).append(epoch_record)
        self._save_json(self.treasury_path, treasury)

        return {
            'epoch': epoch,
            'deposited': deposit,
            'total_staketime': total_staketime,
            'stakers': epoch_record['stakers'],
        }

    def claim_treasury(self, epoch: int, address: str) -> Dict:
        """Claim share of epoch rewards based on staketime"""
        treasury = self._init_treasury()

        epoch_rec = None
        epoch_idx = None
        for i, e in enumerate(treasury.get('epochs', [])):
            if e['epoch'] == epoch:
                epoch_rec = e
                epoch_idx = i
                break

        if not epoch_rec:
            return {'error': f'No rewards for epoch {epoch}'}

        claims = epoch_rec.get('claims', {})
        if address.lower() in claims:
            return {'error': 'Already claimed this epoch'}

        stakes = self._load_json(self.stakes_path, [])
        user_staketime = 0
        for s in stakes:
            if (s['staker'].lower() == address.lower()
                    and not s.get('withdrawn')
                    and s['start_epoch'] <= epoch):
                user_staketime += s['staketime']

        if user_staketime <= 0:
            return {'error': 'No staketime for this epoch'}

        total_st = epoch_rec['total_staketime']
        if total_st <= 0:
            return {'error': 'No staketime in epoch'}

        share = (epoch_rec['amount'] * user_staketime) / total_st

        claims[address.lower()] = {
            'amount': round(share, 6),
            'staketime': user_staketime,
            'timestamp': datetime.now().isoformat(),
        }
        epoch_rec['claims'] = claims
        treasury['epochs'][epoch_idx] = epoch_rec
        self._save_json(self.treasury_path, treasury)

        return {
            'epoch': epoch,
            'address': address,
            'share': round(share, 6),
            'staketime': user_staketime,
            'total_staketime': total_st,
            'pct_of_pool': round(user_staketime / total_st * 100, 2),
            'status': 'claimed',
        }

    def treasury_history(self) -> List[Dict]:
        """Get past epoch distribution history"""
        treasury = self._init_treasury()
        return treasury.get('epochs', [])

    # ── Leaderboard & Portfolio ──────────────────────────────────────

    def leaderboard(self) -> List[Dict]:
        """Trader leaderboard ranked by total profit captured"""
        positions = self._load_json(self.positions_path, [])
        traders = {}

        for p in positions:
            addr = p['trader']
            if addr not in traders:
                traders[addr] = {
                    'address': addr,
                    'total_volume': 0.0,
                    'total_profit': 0.0,
                    'total_loss': 0.0,
                    'prefi_earned': 0.0,
                    'positions': 0,
                    'wins': 0,
                    'losses': 0,
                }
            t = traders[addr]
            t['positions'] += 1
            t['total_volume'] += p.get('usdc_in', 0)

            if p.get('closed') and p.get('profit') is not None:
                profit = p['profit']
                if profit > 0:
                    t['total_profit'] += profit
                    t['prefi_earned'] += p.get('prefi_earned', 0)
                    t['wins'] += 1
                else:
                    t['total_loss'] += abs(profit)
                    t['losses'] += 1

        board = list(traders.values())
        for t in board:
            t['net_pnl'] = round(t['total_profit'] - t['total_loss'], 6)
            total = t['wins'] + t['losses']
            t['win_rate'] = round(t['wins'] / total * 100, 1) if total > 0 else 0
            t['total_profit'] = round(t['total_profit'], 6)
            t['total_loss'] = round(t['total_loss'], 6)
            t['prefi_earned'] = round(t['prefi_earned'], 6)

        board.sort(key=lambda x: x['total_profit'], reverse=True)
        for i, t in enumerate(board):
            t['rank'] = i + 1
        return board

    def portfolio(self, address: str) -> Dict:
        """Full portfolio view: positions + predictions + stakes + claims"""
        self.resolve_predictions()
        positions = self._load_json(self.positions_path, [])
        stakes_data = self._load_json(self.stakes_path, [])
        treasury = self._init_treasury()
        balance = self.prefi_balance(address)
        user_preds = [p for p in self._load_json(self.predictions_path, [])
                      if p['predictor'].lower() == address.lower()]
        scored = [p for p in user_preds if p.get('resolved')]

        # Position summary
        user_pos = [p for p in positions if p['trader'].lower() == address.lower()]
        open_pos = [p for p in user_pos if not p.get('closed')]
        closed_pos = [p for p in user_pos if p.get('closed')]
        total_profit = sum(p.get('profit', 0) for p in closed_pos if (p.get('profit') or 0) > 0)
        total_loss = sum(abs(p.get('profit', 0)) for p in closed_pos if (p.get('profit') or 0) < 0)
        total_prefi = sum(p.get('prefi_earned', 0) for p in closed_pos if p.get('prefi_earned'))

        # Open position unrealized PnL
        unrealized = 0.0
        for p in open_pos:
            price = self._get_token_price(p['asset'])
            if price:
                unrealized += p['asset_amount'] * price - p['usdc_in']

        # Stake summary
        user_stakes = [s for s in stakes_data if s['staker'].lower() == address.lower()]
        active_stakes = [s for s in user_stakes if not s.get('withdrawn')]
        total_locked = sum(s['amount'] for s in active_stakes)
        total_staketime = sum(s['staketime'] for s in active_stakes)

        # Claims summary
        total_claimed = 0.0
        for epoch_rec in treasury.get('epochs', []):
            claim = epoch_rec.get('claims', {}).get(address.lower())
            if claim:
                total_claimed += claim.get('amount', 0)

        return {
            'address': address,
            'trading': {
                'open_positions': len(open_pos),
                'closed_positions': len(closed_pos),
                'total_volume': round(sum(p.get('usdc_in', 0) for p in user_pos), 2),
                'total_profit': round(total_profit, 6),
                'total_loss': round(total_loss, 6),
                'net_pnl': round(total_profit - total_loss, 6),
                'unrealized_pnl': round(unrealized, 6),
                'win_rate': round(
                    sum(1 for p in closed_pos if (p.get('profit') or 0) > 0) /
                    len(closed_pos) * 100, 1
                ) if closed_pos else 0,
            },
            'prefi': {
                'total_earned': round(total_prefi, 6),
                'total_locked': round(total_locked, 6),
                'total_staketime': total_staketime,
                'active_stakes': len(active_stakes),
                **{k: v for k, v in balance.items() if k != 'address'},
            },
            'predictions': {
                'total': len(user_preds),
                'open': sum(1 for p in user_preds if not p.get('resolved')),
                'resolved': len(scored),
                'burned': round(sum(p.get('burn', 0) for p in user_preds), 6),
                'payout': round(sum(p.get('payout') or 0 for p in scored), 6),
                'avg_score': round(sum(p.get('score') or 0 for p in scored) / len(scored), 4)
                             if scored else 0,
            },
            'treasury_claims': {
                'total_claimed': round(total_claimed, 6),
                'epochs_claimed': sum(
                    1 for e in treasury.get('epochs', [])
                    if address.lower() in e.get('claims', {})
                ),
            },
        }

    # ── Ticker ───────────────────────────────────────────────────────

    def get_prices(self) -> Dict:
        """Get current prices from CoinGecko (cached — the dashboard polls every
        15s and the free tier rate-limits well before that)"""
        cached = self._price_cache.get('_quotes')
        if cached and (time.time() - cached['ts']) < self._price_cache_ttl:
            return cached['quotes']
        try:
            resp = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={
                    'ids': 'ethereum,bitcoin,usd-coin',
                    'vs_currencies': 'usd',
                    'include_24hr_change': 'true',
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            quotes = {
                'ETH': {'price': data.get('ethereum', {}).get('usd', 0),
                         'change_24h': data.get('ethereum', {}).get('usd_24h_change', 0)},
                'BTC': {'price': data.get('bitcoin', {}).get('usd', 0),
                         'change_24h': data.get('bitcoin', {}).get('usd_24h_change', 0)},
                'USDC': {'price': data.get('usd-coin', {}).get('usd', 0),
                          'change_24h': data.get('usd-coin', {}).get('usd_24h_change', 0)},
                'timestamp': datetime.now().isoformat(),
            }
            now = time.time()
            self._price_cache['_quotes'] = {'quotes': quotes, 'ts': now}
            # One call warms the per-symbol cache the market list reads from.
            for cg_id in ('ethereum', 'bitcoin', 'usd-coin'):
                usd = data.get(cg_id, {}).get('usd')
                if usd:
                    self._price_cache[cg_id] = {'price': usd, 'ts': now}
            return quotes
        except Exception as e:
            # Rate limited or offline — last good quotes beat an error banner.
            if cached:
                return cached['quotes']
            # Nothing cached for the ticker yet: fall back to the per-symbol
            # cache the market list already fills (no 24h change there).
            fallback = {sym: {'price': p, 'change_24h': None}
                        for sym, p in ((s, self._get_token_price(s))
                                       for s in ('ETH', 'BTC', 'USDC'))
                        if p is not None}
            if not fallback:
                return {'error': str(e)}
            fallback['timestamp'] = datetime.now().isoformat()
            self._price_cache['_quotes'] = {'quotes': fallback, 'ts': time.time()}
            return fallback

    def get_asset_price(self, asset: str) -> Dict:
        """Get price for a specific asset"""
        price = self._get_token_price(asset)
        return {
            'asset': asset,
            'price': price,
            'timestamp': datetime.now().isoformat(),
        }

    # ── Deploy ───────────────────────────────────────────────────────

    def deploy(self, network: str = None) -> Dict:
        """Deploy contracts via hardhat"""
        network = network or self.network
        result = subprocess.run(
            ['npx', 'hardhat', 'run', 'src/scripts/deploy-prefi.js',
             '--network', network],
            cwd=str(self.module_dir),
            capture_output=True, text=True, timeout=300,
        )
        self._load_deployment()
        return {
            'network': network,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode,
            'contracts': self.contracts,
        }

    # ── Stake pool (real money, on HyperEVM) ─────────────────────────
    #
    # Everything above this line is PREFI — an internal token, minted and
    # burned on this server's own say-so. Everything below holds actual USDC
    # and USDT0 in a wallet on Hyperliquid's EVM, splits real pots by accuracy
    # every round, and pays out on chain. The engine lives in `pool.py`; these
    # are the protocol-facing names.

    @property
    def pool(self):
        """Lazily built so a call that never touches the pool never opens its
        files or resolves an RPC."""
        if self._pool is None:
            self._pool = pool_mod.Pool(
                self.store_dir,
                price_at=self._price_at,
                price_now=self._get_token_price,
                markets=lambda: self._load_json(self.markets_path, []),
                on_fee=self._pool_fee_to_treasury,
                liquidity_now=self._dex_liquidity,
                library=self.fns,
            )
        return self._pool

    @property
    def fns(self) -> curves.Library:
        """The score-function library, always beside whatever `store_dir` is
        right now (tests re-point it after construction). Also registered as
        the default the predict layer's scoring resolves names through."""
        lib = curves.Library(self.store_dir / 'functions.json')
        scoring.LIBRARY = lib
        return lib

    def _pool_fee_to_treasury(self, amount: float):
        """A settled pot's protocol cut lands in the same treasury the trading
        side feeds, so there is one number for "what the protocol earned"."""
        treasury = self._init_treasury()
        treasury['balance'] = treasury.get('balance', 0) + amount
        treasury['total_captured'] = treasury.get('total_captured', 0) + amount
        self._save_json(self.treasury_path, treasury)

    # -- setup ---------------------------------------------------------

    def pool_status(self) -> Dict:
        """Pool at a glance — rules, round, TVL, vault."""
        stats = self.pool.stats()
        stats['config'] = self.pool.config()
        stats['round_window'] = self.pool.window()
        return stats

    def pool_config(self) -> Dict:
        """The live rules: interval, scoring model, tolerance, limits, fee."""
        return self.pool.config()

    def set_pool_config(self, secret: str = None, owner: str = None,
                        signature: str = None, **params) -> Dict:
        """Owner-only. `interval=604800` is the weekly cadence; anything in
        pool.DEFAULT_CONFIG can be set the same way."""
        return self.pool.set_config(secret=secret, owner=owner,
                                    signature=signature, **params)

    def pool_owner(self) -> Dict:
        """Who owns the pool, and whether it has been claimed at all."""
        return self.pool.owner_status()

    def pool_claim_owner(self, address: str, secret: str = None) -> Dict:
        """Claim an unowned pool, or transfer it with the owner secret."""
        return self.pool.claim_owner(address, secret)

    def pool_vault(self) -> Dict:
        """The deposit address on HyperEVM, what it holds, and whether the
        on-chain balance still covers everything the ledger owes."""
        return self.pool.vault()

    def pool_create_vault(self, secret: str = None, owner: str = None,
                          signature: str = None) -> Dict:
        """Generate the custodial hot wallet that receives deposits."""
        return self.pool.create_vault(secret=secret, owner=owner, signature=signature)

    def pool_set_vault(self, address: str, secret: str = None, owner: str = None,
                       signature: str = None) -> Dict:
        """Use an address you already control instead of a generated key."""
        return self.pool.set_vault(address, secret=secret, owner=owner,
                                   signature=signature)

    def pool_tokens(self, verify: bool = False) -> Dict:
        """Accepted stablecoins. `verify=true` reads symbol/decimals back off
        the chain instead of trusting the registry."""
        return self.pool.tokens(verify=verify)

    def pool_add_token(self, symbol: str, address: str, secret: str = None,
                       owner: str = None, signature: str = None) -> Dict:
        """Register another stablecoin; decimals come from the contract."""
        return self.pool.add_token(symbol, address, secret=secret, owner=owner,
                                   signature=signature)

    # -- money in ------------------------------------------------------

    def pool_deposit(self, tx_hash: str) -> Dict:
        """Credit a deposit from its HyperEVM transaction hash."""
        return self.pool.deposit(tx_hash)

    def pool_sync(self, max_chunks: int = 20) -> Dict:
        """Sweep the chain for deposits nobody submitted a hash for."""
        return self.pool.sync(max_chunks=int(max_chunks))

    def pool_balance(self, address: str) -> Dict:
        """One account: available, at stake, deposited, won, withdrawn."""
        return self.pool.balance(address)

    def pool_ledger(self, address: str = None, limit: int = 100) -> List[Dict]:
        """Every credit and debit behind a balance, newest first."""
        return self.pool.ledger(address, limit)

    # -- staking -------------------------------------------------------

    def pool_sign(self, action: str, address: str, **fields) -> Dict:
        """The message a wallet has to sign for a spending action, and the
        nonce it is bound to. The UI shows this, signs it, posts it back."""
        return self.pool.sign_request(action, address, **fields)

    def pool_stake(self, address: str, asset: str, predicted_price: float,
                   amount: float, signature: str = None, nonce: int = None) -> Dict:
        """Stake dollars on where `asset` closes this round."""
        return self.pool.stake(address, asset, predicted_price, amount,
                               signature=signature, nonce=nonce)

    def pool_free_stake(self, address: str, asset: str, predicted_price: float,
                        signature: str = None, nonce: int = None) -> Dict:
        """Call a price with no money down — scored like a stake, paid nothing,
        and told what it would have won."""
        return self.pool.free_stake(address, asset, predicted_price,
                                    signature=signature, nonce=nonce)

    def pool_free_quota(self, address: str, index: int = None) -> Dict:
        """Free calls this address has left in the round."""
        return self.pool.free_quota(address, index)

    def pool_free_leaderboard(self, limit: int = 50) -> List[Dict]:
        """Free players ranked by accuracy, and by what they would have made."""
        return self.pool.free_leaderboard(limit)

    def pool_round(self, index: int = None, address: str = None) -> Dict:
        """One round with live provisional scores — the pot table."""
        return self.pool.round(index, address)

    def pool_rounds(self, limit: int = 20) -> List[Dict]:
        """Round history: what each pot paid and who took it."""
        return self.pool.rounds(limit)

    def pool_entries(self, address: str = None, limit: int = 100) -> List[Dict]:
        """Stakes, newest first — all of them or one address's."""
        return self.pool.entries(address, limit)

    def pool_settle(self, force: bool = False) -> Dict:
        """Settle every closed round. Safe to call on a cron and on read."""
        return self.pool.settle(force=force)

    def pool_settle_manual(self, index: int, asset: str, price: float,
                           secret: str = None, owner: str = None,
                           signature: str = None) -> Dict:
        """Owner escape hatch for a pot the oracle cannot price."""
        return self.pool.settle_manual(index, asset, price, secret=secret,
                                       owner=owner, signature=signature)

    def pool_leaderboard(self, limit: int = 50) -> List[Dict]:
        """Stakers ranked by realised profit."""
        return self.pool.leaderboard(limit)

    # -- money out -----------------------------------------------------

    def pool_withdraw(self, address: str, amount: float, token: str = None,
                      signature: str = None, nonce: int = None) -> Dict:
        """Queue (and, with auto_pay, immediately send) a payout on HyperEVM."""
        return self.pool.withdraw(address, amount, token, signature=signature,
                                  nonce=nonce)

    def pool_withdrawals(self, address: str = None, limit: int = 50) -> List[Dict]:
        """Withdrawal queue, newest first."""
        return self.pool.withdrawals(address, limit)

    def pool_pay_withdrawal(self, withdrawal_id: int, secret: str = None,
                            owner: str = None, signature: str = None) -> Dict:
        """Owner-only: release a queued withdrawal from the vault key."""
        return self.pool.pay_withdrawal(withdrawal_id, secret=secret, owner=owner,
                                        signature=signature)

    def pool_mark_paid(self, withdrawal_id: int, tx_hash: str, secret: str = None,
                       owner: str = None, signature: str = None) -> Dict:
        """Owner-only: record a withdrawal you paid by hand."""
        return self.pool.mark_paid(withdrawal_id, tx_hash, secret=secret,
                                   owner=owner, signature=signature)

    def hyperevm_status(self) -> Dict:
        """Is the HyperEVM RPC reachable, and is it the chain we think it is?"""
        chain = self.pool.chain()
        return {**chain.ping(), 'chain': chain.chain['name'],
                'chain_id': chain.chain_id, 'explorer': chain.chain['explorer']}

    # ── Service management ───────────────────────────────────────────

    LOG_DIR = Path('/tmp/prefi')

    def serve_api(self, port=None, dev=False):
        """Start the FastAPI server"""
        port = port or self.api_port
        api_dir = self.module_dir / 'src' / 'api'
        if not (api_dir / 'api.py').exists():
            return {'error': 'src/api/api.py missing'}

        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env['PORT'] = str(port)
        cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0',
               '--port', str(port)]
        if dev:
            cmd.append('--reload')
        log = open(self.LOG_DIR / 'api.log', 'w')
        subprocess.Popen(cmd, cwd=str(api_dir), env=env,
                         stdout=log, stderr=subprocess.STDOUT)
        return {'api': f'http://localhost:{port}', 'log': str(self.LOG_DIR / 'api.log')}

    def serve_app(self, port=None, dev=False):
        """Start the Next.js app (production build unless dev=True)"""
        port = port or self.app_port
        app_dir = self.module_dir / 'src' / 'app'
        if not app_dir.exists():
            return {'error': 'src/app missing'}
        if not dev and not (app_dir / '.next').exists():
            return {'error': 'no build — run `npx next build` in src/app first'}

        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env['PORT'] = str(port)
        cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(port)]
        log = open(self.LOG_DIR / 'app.log', 'w')
        subprocess.Popen(cmd, cwd=str(app_dir), env=env,
                         stdout=log, stderr=subprocess.STDOUT)
        return {'app': f'http://localhost:{port}/prefi', 'log': str(self.LOG_DIR / 'app.log')}

    def serve(self, api_port=None, app_port=None, dev=False):
        """Start the FastAPI server and Next.js app"""
        self.kill()
        results = self.serve_api(api_port, dev)
        results.update(self.serve_app(app_port, dev))
        results['logs'] = str(self.LOG_DIR)
        return results

    def kill(self):
        """Stop all PreFi services"""
        killed = []
        for pattern in [f'uvicorn.*{self.api_port}', f'next.*{self.app_port}']:
            try:
                result = subprocess.run(['pgrep', '-f', pattern],
                                        capture_output=True, text=True)
                for pid in result.stdout.strip().split('\n'):
                    if pid:
                        os.kill(int(pid), signal.SIGTERM)
                        killed.append(pid)
            except Exception:
                pass
        return {'killed': killed}

    def _port_open(self, port: int) -> bool:
        """True if something is listening on the port. A socket probe, not an
        HTTP call — /health is served by this process, so an HTTP self-check
        would block on its own single worker and always report down."""
        import socket
        with socket.socket() as s:
            s.settimeout(1)
            return s.connect_ex(('127.0.0.1', port)) == 0

    def health(self):
        """Check service health"""
        return {
            'service': 'prefi',
            'network': self.network,
            'contracts': self.contracts,
            'api': {'status': 'up' if self._port_open(self.api_port) else 'down',
                    'port': self.api_port},
            'app': {'status': 'up' if self._port_open(self.app_port) else 'down',
                    'port': self.app_port},
            'timestamp': datetime.now().isoformat(),
        }

    def status(self) -> Dict:
        """Get overall protocol status"""
        self.resolve_predictions()
        markets = self._load_json(self.markets_path, [])
        positions = self._load_json(self.positions_path, [])
        stakes = self._load_json(self.stakes_path, [])
        predictions = self._load_json(self.predictions_path, [])
        treasury = self._init_treasury()

        open_positions = [p for p in positions if not p.get('closed')]
        active_stakes = [s for s in stakes if not s.get('withdrawn')]
        total_volume = sum(p.get('usdc_in', 0) for p in positions)

        # Settling on read means a pot is never left hanging because nobody ran
        # a cron; it is a no-op until a round has actually closed.
        try:
            self.pool.settle()
            pool_stats = self.pool.stats()
        except Exception as exc:
            pool_stats = {'error': str(exc)}

        return {
            'service': 'prefi',
            'network': self.network,
            'contracts': self.contracts,
            'markets': len([m for m in markets if m.get('active')]),
            'positions_total': len(positions),
            'positions_open': len(open_positions),
            'total_volume': round(total_volume, 2),
            'traders': len(set(p['trader'] for p in positions)) if positions else 0,
            'stakes_active': len(active_stakes),
            'total_staked': sum(s['amount'] for s in active_stakes),
            'treasury_balance': treasury.get('balance', 0),
            'total_profit_captured': treasury.get('total_captured', 0),
            'total_prefi_minted': treasury.get('total_prefi_minted', 0),
            'total_prefi_burned': treasury.get('total_prefi_burned', 0),
            'predictions_total': len(predictions),
            'predictions_open': sum(1 for p in predictions if not p.get('resolved')),
            'predictions_free': sum(1 for p in predictions if p.get('free')),
            'forecasters': len(set(p['predictor'] for p in predictions)) if predictions else 0,
            'scoring': self.get_scoring(),
            'pool': pool_stats,
            'current_epoch': self._current_epoch(),
            'api_port': self.api_port,
            'app_port': self.app_port,
            'timestamp': datetime.now().isoformat(),
        }

    def get_deployment_info(self) -> Dict:
        return {
            'network': self.network,
            'contracts': self.contracts,
            'store_dir': str(self.store_dir),
            'api_port': self.api_port,
            'app_port': self.app_port,
        }

    # ── Test ─────────────────────────────────────────────────────────

    def test(self) -> Dict:
        """Run integration tests"""
        import tempfile
        import shutil

        print('=' * 60)
        print('PreFi Trading Protocol Tests')
        print('=' * 60)
        results = {'passed': 0, 'failed': 0, 'tests': []}

        def check(name, condition, detail=''):
            status = 'PASS' if condition else 'FAIL'
            results['passed' if condition else 'failed'] += 1
            results['tests'].append({'name': name, 'status': status, 'detail': detail})
            print(f'  [{status}] {name}' + (f' — {detail}' if detail else ''))

        tmp = tempfile.mkdtemp(prefix='prefi_test_')
        orig = {
            'store_dir': self.store_dir,
            'positions_path': self.positions_path,
            'stakes_path': self.stakes_path,
            'treasury_path': self.treasury_path,
            'markets_path': self.markets_path,
            'predictions_path': self.predictions_path,
            'scoring_path': self.scoring_path,
        }
        try:
            self.store_dir = Path(tmp)
            self.positions_path = self.store_dir / 'positions.json'
            self.stakes_path = self.store_dir / 'stakes.json'
            self.treasury_path = self.store_dir / 'treasury.json'
            self.markets_path = self.store_dir / 'markets.json'
            self.predictions_path = self.store_dir / 'predictions.json'
            self.scoring_path = self.store_dir / 'scoring.json'

            # 1. Markets
            print('\n1. Markets')
            r = self.add_market('0xWETH', 'WETH', 3000)
            check('add WETH market', r.get('status') == 'added')
            r2 = self.add_market('0xcbBTC', 'cbBTC', 3000)
            check('add cbBTC market', r2.get('status') == 'added')

            dup = self.add_market('0xWETH', 'WETH', 3000)
            check('duplicate token blocked', 'error' in dup)
            dup_sym = self.add_market('0xOther', 'WETH', 3000)
            check('duplicate symbol blocked', 'error' in dup_sym)
            bad_src = self.add_market('0xFoo', 'FOO', 3000, source='oracle-vibes')
            check('unknown price source blocked', 'error' in bad_src)

            markets = self.list_markets()
            check('2 markets listed', len(markets) == 2)

            # 2. Positions with mocked prices
            print('\n2. Positions')
            mock_positions = [
                {'id': 1, 'trader': '0xAlice', 'asset': 'WETH', 'token': '0xWETH',
                 'usdc_in': 1000.0, 'asset_amount': 0.5, 'entry_price': 2000.0,
                 'open_time': time.time(), 'closed': False,
                 'usdc_out': None, 'profit': None, 'prefi_earned': None},
                {'id': 2, 'trader': '0xBob', 'asset': 'WETH', 'token': '0xWETH',
                 'usdc_in': 500.0, 'asset_amount': 0.25, 'entry_price': 2000.0,
                 'open_time': time.time(), 'closed': False,
                 'usdc_out': None, 'profit': None, 'prefi_earned': None},
                {'id': 3, 'trader': '0xAlice', 'asset': 'cbBTC', 'token': '0xcbBTC',
                 'usdc_in': 2000.0, 'asset_amount': 0.02, 'entry_price': 100000.0,
                 'open_time': time.time(), 'closed': False,
                 'usdc_out': None, 'profit': None, 'prefi_earned': None},
                {'id': 4, 'trader': '0xBob', 'asset': 'WETH', 'token': '0xWETH',
                 'usdc_in': 1000.0, 'asset_amount': 0.5, 'entry_price': 2000.0,
                 'open_time': time.time(), 'closed': False,
                 'usdc_out': None, 'profit': None, 'prefi_earned': None},
            ]
            self._save_json(self.positions_path, mock_positions)

            positions = self.get_positions('0xAlice')
            check('Alice has 2 positions', len(positions) == 2)

            # Mock prices for close (signature matches _get_token_price(sym, source))
            orig_get_price = self._get_token_price
            self._get_token_price = lambda sym, source=None: 2500.0 if sym == 'WETH' else 110000.0

            c = self.close_position(1, '0xAlice')
            check('profitable close', c.get('status') == 'profitable', f'profit={c.get("profit")}')
            check('profit = 250', abs(c.get('profit', 0) - 250.0) < 0.01)
            check('prefi = profit', abs(c.get('prefi_earned', 0) - 250.0) < 0.01)
            check('return_pct = 25%', c.get('return_pct') == 25.0)

            bob_win = self.close_position(4, '0xBob')
            check('Bob profitable close', bob_win.get('status') == 'profitable',
                  f'profit={bob_win.get("profit")}')

            wrong = self.close_position(2, '0xAlice')
            check('wrong user blocked', 'error' in wrong)

            # Losing trade
            self._get_token_price = lambda sym, source=None: 1500.0
            loss = self.close_position(2, '0xBob')
            check('loss close', loss.get('status') == 'loss')
            check('no prefi on loss', loss.get('prefi_earned') == 0)

            # BTC profitable trade
            self._get_token_price = lambda sym, source=None: 110000.0
            btc = self.close_position(3, '0xAlice')
            check('BTC profitable', btc.get('status') == 'profitable',
                  f'profit={btc.get("profit")}')

            self._get_token_price = orig_get_price

            # 3. Treasury
            print('\n3. Treasury')
            t = self.treasury()
            check('treasury captured profit', t.get('total_captured', 0) > 0,
                  f'captured={t["total_captured"]}')
            check('prefi tracked', t.get('total_prefi_minted', 0) > 0)

            # 4. Leaderboard
            print('\n4. Leaderboard')
            board = self.leaderboard()
            check('2 traders on board', len(board) == 2)
            check('Alice ranked #1', board[0]['address'] == '0xAlice')
            check('Alice has wins', board[0]['wins'] >= 2)
            check('Bob has loss', board[1]['losses'] == 1)

            # 5. Staking
            print('\n5. Staking')
            s = self.lock_prefi(100.0, 604800, '0xAlice')
            check('PREFI locked', s.get('status') == 'locked')
            check('staketime = amount * duration',
                  s.get('staketime') == 100.0 * 604800)

            s2 = self.lock_prefi(200.0, 1209600, '0xBob')
            check('Bob locked 2 weeks', s2.get('status') == 'locked')
            check('Bob staketime > Alice', s2['staketime'] > s['staketime'])

            short = self.lock_prefi(50.0, 3600, '0xCharlie')
            check('short lock rejected', 'error' in short)

            broke = self.lock_prefi(50.0, 604800, '0xCharlie')
            check('lock without PREFI rejected', 'error' in broke,
                  broke.get('error', ''))

            # Extend lock
            ext = self.extend_lock(1, 604800, '0xAlice')
            check('lock extended', ext.get('status') == 'extended')
            check('staketime increased', ext['new_staketime'] > s['staketime'])

            stakes = self.get_stakes('0xAlice')
            check('Alice total locked = 100', stakes['total_locked'] == 100.0)

            unlock = self.unlock_prefi(1, '0xAlice')
            check('early unlock blocked', 'error' in unlock)

            # 6. Treasury distribution
            print('\n6. Treasury Distribution')
            treasury_data = self._load_json(self.treasury_path, {})
            treasury_data['genesis_time'] = time.time() - 700000
            self._save_json(self.treasury_path, treasury_data)

            dep = self.deposit_rewards()
            check('rewards deposited', 'epoch' in dep, f'deposited={dep.get("deposited")}')

            treasury_data = self._load_json(self.treasury_path, {})
            if treasury_data.get('epochs'):
                treasury_data['epochs'][-1]['epoch'] = 0
                treasury_data['genesis_time'] = time.time() - 700000
                self._save_json(self.treasury_path, treasury_data)

                stakes_data = self._load_json(self.stakes_path, [])
                for s in stakes_data:
                    s['start_epoch'] = 0
                self._save_json(self.stakes_path, stakes_data)

                claim = self.claim_treasury(0, '0xAlice')
                check('Alice claimed', claim.get('status') == 'claimed',
                      f'share={claim.get("share")} ({claim.get("pct_of_pool")}%)')

                dup_claim = self.claim_treasury(0, '0xAlice')
                check('double claim blocked', 'error' in dup_claim)

                bob_claim = self.claim_treasury(0, '0xBob')
                check('Bob claimed', bob_claim.get('status') == 'claimed')
                check('Bob share > Alice (more staketime)',
                      bob_claim.get('share', 0) > claim.get('share', 0))

            # 7. Portfolio
            print('\n7. Portfolio')
            port = self.portfolio('0xAlice')
            check('portfolio has trading', 'trading' in port)
            check('portfolio has prefi', 'prefi' in port)
            check('portfolio has claims', 'treasury_claims' in port)
            check('Alice net_pnl > 0', port['trading']['net_pnl'] > 0)
            check('Alice prefi earned > 0', port['prefi']['total_earned'] > 0)

            # 8. PREFI balance
            print('\n8. PREFI balance')
            bal = self.prefi_balance('0xAlice')
            check('Alice minted = trade profit', abs(bal['minted'] - 450.0) < 0.01,
                  f'minted={bal["minted"]}')
            check('Alice locked 100', abs(bal['locked'] - 100.0) < 0.01)
            check('available = minted - locked', abs(bal['available'] - 350.0) < 0.01)

            # 9. Scoring — modular models, parameterized
            print('\n9. Scoring')
            models = self.scoring_models()
            check('model registry exposed', len(models['models']) >= 4,
                  ','.join(sorted(models['models'])))
            check('defaults are l2 @ 1 day',
                  models['defaults']['model'] == 'l2'
                  and models['defaults']['horizon'] == 86400)

            perfect = self.score_preview(100.0, 100.0, burn=10)
            check('exact call scores 1.0', perfect['score'] == 1.0)
            check('exact call pays multiplier×burn', perfect['payout'] == 30.0,
                  f'payout={perfect["payout"]}')

            # Same dollar error at different price scales must score the same —
            # that is the whole point of normalizing.
            small = self.score_preview(101.0, 100.0)
            big = self.score_preview(64640.0, 64000.0)
            check('score is scale-free (1% off either way)',
                  abs(small['score'] - big['score']) < 1e-9,
                  f'{small["score"]} vs {big["score"]}')
            check('1% off is a $640 error on BTC', big['abs_error'] == 640.0)
            check('normalized error = 0.01', abs(big['normalized_error'] - 0.01) < 1e-9)

            worse = self.score_preview(110.0, 100.0)
            check('bigger miss scores lower', worse['score'] < small['score'])

            lin = self.score_preview(103.0, 100.0, model='linear', tolerance=0.02)
            check('linear zeroes past tolerance', lin['score'] == 0.0)
            thr = self.score_preview(100.5, 100.0, model='threshold', tolerance=0.01)
            check('threshold pays inside the band', thr['score'] == 1.0)
            check('l2 = ScoreL2.sol at tolerance 1',
                  abs(self.score_preview(200.0, 100.0, model='l2', tolerance=1)['score']
                      - 0.5) < 1e-9)

            bad_model = self.set_scoring(model='astrology')
            check('unknown model rejected', 'error' in bad_model)
            bad_tol = self.set_scoring(tolerance=0)
            check('non-positive tolerance rejected', 'error' in bad_tol)

            tuned = self.set_scoring(model='linear', tolerance=0.05, multiplier=2.0)
            check('scoring retuned', tuned.get('status') == 'updated')
            check('params persisted', self.get_scoring()['model'] == 'linear')

            # 10. Predictions — burn PREFI to call tomorrow's price
            print('\n10. Predictions')
            self.set_scoring(model='l2', tolerance=0.02, multiplier=3.0, min_burn=1.0)
            self._get_token_price = lambda sym, source=None: 2000.0

            before = self.prefi_balance('0xAlice')['available']
            pred = self.predict('WETH', 2050.0, 50.0, '0xAlice')
            check('prediction placed', pred.get('status') == 'open',
                  f'{pred.get("predicted_price")} in {pred.get("resolves_at")}')
            check('burn leaves the balance immediately',
                  abs(self.prefi_balance('0xAlice')['available'] - (before - 50.0)) < 0.01)
            check('implied move reported', pred.get('implied_move_pct') == 2.5)
            check('max payout = burn × multiplier', pred.get('max_payout') == 150.0)

            broke = self.predict('WETH', 2050.0, 5.0, '0xCharlie')
            check('predict without PREFI rejected', 'error' in broke)
            tiny = self.predict('WETH', 2050.0, 0.01, '0xAlice')
            check('sub-minimum burn rejected', 'error' in tiny)
            nomkt = self.predict('DOGE', 1.0, 5.0, '0xAlice')
            check('unknown market rejected', 'error' in nomkt)
            badhz = self.predict('WETH', 2050.0, 5.0, '0xAlice', horizon=60)
            check('out-of-range horizon rejected', 'error' in badhz)

            # Nothing is due yet
            check('nothing resolves early', self.resolve_predictions()['resolved'] == [])

            # Backdate it and settle: actual 2000 vs called 2050 → 2.5% off
            preds = self._load_json(self.predictions_path, [])
            preds[0]['resolve_at'] = time.time() - 10
            self._save_json(self.predictions_path, preds)
            self._price_at = lambda sym, ts, source=None: {'price': 2000.0,
                                                           'mode': 'historical'}

            res = self.resolve_predictions()
            check('due prediction resolved', res['resolved'] == [1], f'minted={res["prefi_minted"]}')

            settled = self.get_predictions('0xAlice')[0]
            check('actual price recorded', settled['actual_price'] == 2000.0)
            check('dollar error = $50', settled['abs_error'] == 50.0)
            check('normalized error = 2.5%', abs(settled['normalized_error'] - 0.025) < 1e-6)
            expected = 1 / (1 + (0.025 / 0.02) ** 2)
            check('l2 score matches the formula',
                  abs(settled['score'] - expected) < 1e-6, f'score={settled["score"]}')
            check('payout = burn × 3 × score',
                  abs(settled['payout'] - 50.0 * 3.0 * expected) < 1e-4,
                  f'payout={settled["payout"]}')
            check('payout credited to balance',
                  abs(self.prefi_balance('0xAlice')['from_predictions']
                      - settled['payout']) < 1e-6)
            check('resolved once, not twice', self.resolve_predictions()['resolved'] == [])

            # Params are snapshotted — retuning can't re-price a settled call
            self.set_scoring(model='threshold', tolerance=0.001)
            check('settled score unchanged by retune',
                  self.get_predictions('0xAlice')[0]['score'] == settled['score'])

            board = self.prediction_board()
            check('forecaster board built', len(board) == 1 and board[0]['resolved'] == 1)
            check('board tracks net PREFI',
                  abs(board[0]['net_prefi'] - (settled['payout'] - 50.0)) < 1e-4)

            self._get_token_price = orig_get_price

            # 11. Status
            print('\n11. Status')
            status = self.status()
            check('status has volume', status.get('total_volume', 0) > 0)
            check('status has traders', status.get('traders', 0) == 2)
            check('status has prefi minted', status.get('total_prefi_minted', 0) > 0)
            check('status counts predictions', status.get('predictions_total') == 1)
            check('status reports burned PREFI', status.get('total_prefi_burned') == 50.0)

            port2 = self.portfolio('0xAlice')
            check('portfolio has predictions', port2['predictions']['resolved'] == 1)

            # 12. Free predictions — the way in for an address holding nothing
            print('\n12. Free predictions')
            self._get_token_price = lambda sym, source=None: 2000.0
            self.set_scoring(model='l2', tolerance=0.02, free_per_day=2,
                             free_payout=1.0)
            check('broke address has no PREFI',
                  self.prefi_balance('0xNew')['available'] == 0)
            gratis = self.predict('WETH', 2000.0, address='0xNew')
            check('free call placed without a burn',
                  gratis.get('free') is True and gratis.get('status') == 'open')
            check('free call cost nothing', self.prefi_balance('0xNew')['burned'] == 0)
            check('free allowance decremented',
                  self.free_quota('0xNew')['remaining'] == 1)
            self.predict('WETH', 2000.0, address='0xNew')
            spent = self.predict('WETH', 2000.0, address='0xNew')
            check('free allowance runs out', 'error' in spent)
            check('other addresses unaffected',
                  self.free_quota('0xOther')['remaining'] == 2)

            preds = self._load_json(self.predictions_path, [])
            for p in preds:
                if p.get('free'):
                    p['resolve_at'] = time.time() - 10
            self._save_json(self.predictions_path, preds)
            self.resolve_predictions()
            gratis_settled = self.get_predictions('0xNew')[0]
            check('free call scored like any other', gratis_settled['score'] == 1.0)
            check('perfect free call mints free_payout',
                  gratis_settled['payout'] == 1.0, f'payout={gratis_settled["payout"]}')
            check('free minting shows in the balance',
                  self.prefi_balance('0xNew')['from_free'] == 2.0)
            self._get_token_price = orig_get_price

        finally:
            for k, v in orig.items():
                setattr(self, k, v)
            # Drop the price mocks so the class methods show through again.
            self.__dict__.pop('_get_token_price', None)
            self.__dict__.pop('_price_at', None)
            shutil.rmtree(tmp, ignore_errors=True)

        print('\n' + '=' * 60)
        total = results['passed'] + results['failed']
        print(f'Results: {results["passed"]}/{total} passed, {results["failed"]} failed')
        print('=' * 60)
        return results

    # ── CLI entry point ──────────────────────────────────────────────

    def forward(self, action=None, **kwargs):
        """CLI entry point: prefi <action> [args]

        Actions:
            serve       - Start API + app servers
            kill        - Stop all services
            health      - Check service health
            status      - Get protocol status
            deploy      - Deploy contracts

            markets     - List supported markets
            add-market  - Add market (token=, symbol=, fee_tier=, source=)
            hl-assets   - Browse every Hyperliquid pair (search=, limit=, kind=)
            hl-stats    - How many pairs are quoted, and how fresh the list is
            seed-hl     - List the busiest HL pairs at once (limit=, kind=)
            add-hl      - List a Hyperliquid pair as a market (coin=)
            bt-assets   - Browse every Bittensor subnet (search=, limit=)
            bt-stats    - How many subnets are quoted, and how fresh the list is
            seed-bt     - List the busiest subnets at once (limit=, min_volume=)
            add-bt      - List a Bittensor subnet as a market (subnet=)
            dex-assets  - Browse tokens on Solana or Base (chain=, search=, limit=)
            dex-stats   - Pools ranked, eligible under the floor, listed
            seed-dex    - List a chain's busiest eligible tokens (chain=, limit=)
            add-dex     - List a Solana/Base token (chain=, address= pool,
                          token or symbol) — must clear min_liquidity_usd
            add-sol     - add-dex chain=solana
            add-base    - add-dex chain=base

            open        - Open position (asset=, amount=, address=)
            close       - Close position (id=, address=)
            positions   - Get positions (address=)

            predict     - Call a price (asset=, price=, address=, horizon=,
                          burn= — omit burn for a FREE call)
            free        - Free calls left today (address=)
            predictions - List predictions (address=, limit=)
            resolve     - Settle every due prediction
            forecasters - Prediction leaderboard
            balance     - PREFI balance (address=)

            scoring     - Active scoring params
            set-scoring - Retune (model=, tolerance=, multiplier=, horizon=,
                          min_burn=, free_per_day=, free_payout=)
            models      - Available scoring models
            functions   - Every score function (defaults + library) and the language
            fn          - One function: name=
            fn-test     - Try one without saving: expr= params= [tolerance= calls= stake=]
            fn-save     - Save (signed): address= name= expr= params= description=
            fn-delete   - Remove yours: address= name=
            fn-share    - Share code (+ CID if published): name=
            fn-publish  - Put it in the store, get a CID: name= [token=]
            fn-import   - From a code or CID: source= [address= name=]
            preview     - Score a hypothetical (predicted=, actual=, model=,
                          tolerance=, burn=)

            lock        - Lock PREFI (amount=, duration=, address=)
            extend      - Extend lock (id=, duration=, address=)
            unlock      - Unlock stake (id=, address=)
            stakes      - Get stakes (address=)

            distribute  - Deposit treasury rewards for epoch
            claim       - Claim epoch share (epoch=, address=)
            treasury    - Treasury status
            history     - Treasury epoch history

            leaderboard - Trader rankings
            portfolio   - Full portfolio view (address=)

            prices      - Current asset prices
            price       - Single asset price (asset=)
            deployment  - Deployment info
            test        - Run test suite

          Stake pool (real USDC/USDT0 on HyperEVM):
            pool        - Pool status: rules, round, TVL, vault
            pool-config - Live rules
            pool-set    - Owner: interval=, model=, tolerance=, min_stake=,
                          max_stake=, fee_bps=, entry_cutoff=, auto_pay=,
                          free_per_round=, free_notional=,
                          min_liquidity_usd= (DEX listing/stake floor)
                          (auth: secret= or owner=+signature=)
            pool-owner  - Who owns the pool
            pool-claim  - Claim it (address=, secret= to transfer)

            pool-vault  - Deposit address, holdings, solvency
            pool-create-vault - Generate the custodial hot wallet
            pool-set-vault    - Use an address you control (address=)
            pool-tokens - Accepted stablecoins (verify=true to re-read on chain)
            pool-add-token    - Register one (symbol=, address=)

            deposit     - Credit a deposit by tx hash (tx=)
            pool-sync   - Sweep the chain for deposits (chunks=)
            pool-balance- Account balance (address=)
            pool-ledger - Credits and debits (address=, limit=)

            stake       - Stake dollars on a call (address=, asset=, price=,
                          amount=, signature=, nonce=)
            free-stake  - Call a price for nothing (address=, asset=, price=,
                          signature=, nonce=) — scored, never paid
            free-quota  - Free calls left this round (address=)
            free-board  - Free players ranked by accuracy
            round       - Round with live scores (round=, address=)
            rounds      - Round history (limit=)
            entries     - Stakes (address=, limit=)
            settle      - Settle every closed round
            settle-manual - Owner: settle a stuck pot (round=, asset=, price=)
            pool-board  - Stakers ranked by profit

            withdraw    - Cash out (address=, amount=, token=, signature=)
            withdrawals - Withdrawal queue (address=)
            pay         - Owner: send a queued withdrawal (id=)
            mark-paid   - Owner: record one paid by hand (id=, tx=)
            hyperevm    - RPC reachability
        """
        actions = {
            'serve': lambda: self.serve(
                api_port=kwargs.get('api_port'),
                app_port=kwargs.get('app_port'),
                dev=kwargs.get('dev', True),
            ),
            'kill': lambda: self.kill(),
            'health': lambda: self.health(),
            'status': lambda: self.status(),
            'deploy': lambda: self.deploy(kwargs.get('network')),

            'markets': lambda: self.list_markets(),
            'add-market': lambda: self.add_market(
                kwargs.get('token', ''),
                kwargs.get('symbol', ''),
                int(kwargs.get('fee_tier', 3000)),
                kwargs.get('source', 'coingecko'),
            ),
            'hl-assets': lambda: self.hl_assets(
                kwargs.get('search', ''),
                int(kwargs.get('limit', 50)),
                kwargs.get('kind', 'all'),
            ),
            'hl-stats': lambda: self.hl_stats(),
            'seed-hl': lambda: self.seed_hl(
                int(kwargs.get('limit', 20)),
                kwargs.get('kind', 'all'),
                float(kwargs.get('min_volume', 0)),
            ),
            'add-hl': lambda: self.add_hl_market(kwargs.get('coin', '')),
            'bt-assets': lambda: self.bt_assets(
                kwargs.get('search', ''),
                int(kwargs.get('limit', 50)),
            ),
            'bt-stats': lambda: self.bt_stats(),
            'seed-bt': lambda: self.seed_bt(
                int(kwargs.get('limit', 20)),
                float(kwargs.get('min_volume', 0)),
            ),
            'add-bt': lambda: self.add_bt_market(
                kwargs.get('subnet', kwargs.get('netuid', ''))),
            'dex-assets': lambda: self.dex_assets(
                kwargs.get('chain', 'solana'),
                kwargs.get('search', ''),
                int(kwargs.get('limit', 50)),
            ),
            'dex-stats': lambda: self.dex_stats(kwargs.get('chain', 'solana')),
            'seed-dex': lambda: self.seed_dex(
                kwargs.get('chain', 'solana'),
                int(kwargs.get('limit', 20)),
                float(kwargs.get('min_volume', 0)),
            ),
            'add-dex': lambda: self.add_dex_market(
                kwargs.get('chain', ''), kwargs.get('address', kwargs.get('token', ''))),
            'add-sol': lambda: self.add_dex_market(
                'solana', kwargs.get('address', kwargs.get('token', ''))),
            'add-base': lambda: self.add_dex_market(
                'base', kwargs.get('address', kwargs.get('token', ''))),

            'open': lambda: self.open_position(
                kwargs.get('asset', ''),
                float(kwargs.get('amount', 0)),
                kwargs.get('address', ''),
            ),
            'close': lambda: self.close_position(
                int(kwargs.get('id', 0)),
                kwargs.get('address', ''),
            ),
            'positions': lambda: self.get_positions(kwargs.get('address', '')),

            'predict': lambda: self.predict(
                kwargs.get('asset', ''),
                float(kwargs.get('price', 0)),
                float(kwargs.get('burn', 0)),
                kwargs.get('address', ''),
                int(kwargs['horizon']) if kwargs.get('horizon') else None,
            ),
            'predictions': lambda: self.get_predictions(
                kwargs.get('address'),
                int(kwargs.get('limit', 100)),
            ),
            'free': lambda: self.free_quota(kwargs.get('address', '')),
            'resolve': lambda: self.resolve_predictions(),
            'forecasters': lambda: self.prediction_board(),
            'balance': lambda: self.prefi_balance(kwargs.get('address', '')),

            'scoring': lambda: self.get_scoring(),
            'set-scoring': lambda: self.set_scoring(
                model=kwargs.get('model'),
                tolerance=kwargs.get('tolerance'),
                model_params=kwargs.get('model_params'),
                multiplier=kwargs.get('multiplier'),
                horizon=kwargs.get('horizon'),
                min_burn=kwargs.get('min_burn'),
                free_per_day=kwargs.get('free_per_day'),
                free_payout=kwargs.get('free_payout'),
            ),
            'models': lambda: self.scoring_models(),
            'functions': lambda: self.fn_list(sample=False),
            'fn': lambda: self.fn_get(kwargs.get('name', '')),
            'fn-test': lambda: self.fn_test(
                kwargs.get('expr', ''), kwargs.get('params'), kwargs.get('name'),
                kwargs.get('tolerance'), kwargs.get('actual', 100.0),
                kwargs.get('calls'), kwargs.get('stake', 100.0),
                kwargs.get('fee_bps', 0)),
            'fn-save': lambda: self.fn_save(
                kwargs.get('address', ''), kwargs.get('name', ''),
                kwargs.get('expr', ''), kwargs.get('params'),
                kwargs.get('description', ''), kwargs.get('signature'),
                kwargs.get('nonce')),
            'fn-delete': lambda: self.fn_delete(
                kwargs.get('address', ''), kwargs.get('name', ''),
                kwargs.get('signature'), kwargs.get('nonce')),
            'fn-share': lambda: self.fn_share(kwargs.get('name', '')),
            'fn-publish': lambda: self.fn_publish(kwargs.get('name', ''), kwargs.get('token')),
            'fn-import': lambda: self.fn_import(
                kwargs.get('source', ''), kwargs.get('address'),
                kwargs.get('signature'), kwargs.get('nonce'), kwargs.get('name')),
            'preview': lambda: self.score_preview(
                kwargs.get('predicted', 0),
                kwargs.get('actual', 0),
                kwargs.get('model'),
                kwargs.get('tolerance'),
                kwargs.get('burn'),
            ),

            'lock': lambda: self.lock_prefi(
                float(kwargs.get('amount', 0)),
                int(kwargs.get('duration', 604800)),
                kwargs.get('address', ''),
            ),
            'extend': lambda: self.extend_lock(
                int(kwargs.get('id', 0)),
                int(kwargs.get('duration', 604800)),
                kwargs.get('address', ''),
            ),
            'unlock': lambda: self.unlock_prefi(
                int(kwargs.get('id', 0)),
                kwargs.get('address', ''),
            ),
            'stakes': lambda: self.get_stakes(kwargs.get('address', '')),

            'distribute': lambda: self.deposit_rewards(
                float(kwargs['amount']) if kwargs.get('amount') else None,
            ),
            'claim': lambda: self.claim_treasury(
                int(kwargs.get('epoch', 0)),
                kwargs.get('address', ''),
            ),
            'treasury': lambda: self.treasury(),
            'history': lambda: self.treasury_history(),

            'leaderboard': lambda: self.leaderboard(),
            'portfolio': lambda: self.portfolio(kwargs.get('address', '')),

            'prices': lambda: self.get_prices(),
            'price': lambda: self.get_asset_price(kwargs.get('asset', 'ETH')),
            'deployment': lambda: self.get_deployment_info(),
            'test': lambda: self.test(),

            # ── the stake pool, on HyperEVM ──
            'pool': lambda: self.pool_status(),
            'pool-config': lambda: self.pool_config(),
            'pool-set': lambda: self.set_pool_config(
                secret=kwargs.get('secret'), owner=kwargs.get('owner'),
                signature=kwargs.get('signature'),
                **{k: v for k, v in kwargs.items()
                   if k in pool_mod.DEFAULT_CONFIG}),
            'pool-owner': lambda: self.pool_owner(),
            'pool-claim': lambda: self.pool_claim_owner(
                kwargs.get('address', ''), kwargs.get('secret')),

            'pool-vault': lambda: self.pool_vault(),
            'pool-create-vault': lambda: self.pool_create_vault(
                secret=kwargs.get('secret'), owner=kwargs.get('owner'),
                signature=kwargs.get('signature')),
            'pool-set-vault': lambda: self.pool_set_vault(
                kwargs.get('address', ''), secret=kwargs.get('secret'),
                owner=kwargs.get('owner'), signature=kwargs.get('signature')),
            'pool-tokens': lambda: self.pool_tokens(
                verify=str(kwargs.get('verify', '')).lower() in ('1', 'true', 'yes')),
            'pool-add-token': lambda: self.pool_add_token(
                kwargs.get('symbol', ''), kwargs.get('address', ''),
                secret=kwargs.get('secret'), owner=kwargs.get('owner'),
                signature=kwargs.get('signature')),

            'deposit': lambda: self.pool_deposit(kwargs.get('tx', '')),
            'pool-sync': lambda: self.pool_sync(int(kwargs.get('chunks', 20))),
            'pool-balance': lambda: self.pool_balance(kwargs.get('address', '')),
            'pool-ledger': lambda: self.pool_ledger(
                kwargs.get('address'), int(kwargs.get('limit', 100))),

            'stake': lambda: self.pool_stake(
                kwargs.get('address', ''), kwargs.get('asset', ''),
                float(kwargs.get('price', 0)), float(kwargs.get('amount', 0)),
                signature=kwargs.get('signature'),
                nonce=int(kwargs['nonce']) if kwargs.get('nonce') else None),
            'free-stake': lambda: self.pool_free_stake(
                kwargs.get('address', ''), kwargs.get('asset', ''),
                float(kwargs.get('price', 0)),
                signature=kwargs.get('signature'),
                nonce=int(kwargs['nonce']) if kwargs.get('nonce') else None),
            'free-quota': lambda: self.pool_free_quota(
                kwargs.get('address', ''),
                int(kwargs['round']) if kwargs.get('round') else None),
            'free-board': lambda: self.pool_free_leaderboard(
                int(kwargs.get('limit', 50))),
            'round': lambda: self.pool_round(
                int(kwargs['round']) if kwargs.get('round') else None,
                kwargs.get('address')),
            'rounds': lambda: self.pool_rounds(int(kwargs.get('limit', 20))),
            'entries': lambda: self.pool_entries(
                kwargs.get('address'), int(kwargs.get('limit', 100))),
            'settle': lambda: self.pool_settle(
                force=str(kwargs.get('force', '')).lower() in ('1', 'true', 'yes')),
            'settle-manual': lambda: self.pool_settle_manual(
                int(kwargs.get('round', 0)), kwargs.get('asset', ''),
                float(kwargs.get('price', 0)), secret=kwargs.get('secret'),
                owner=kwargs.get('owner'), signature=kwargs.get('signature')),
            'pool-board': lambda: self.pool_leaderboard(int(kwargs.get('limit', 50))),

            'withdraw': lambda: self.pool_withdraw(
                kwargs.get('address', ''), float(kwargs.get('amount', 0)),
                kwargs.get('token'), signature=kwargs.get('signature'),
                nonce=int(kwargs['nonce']) if kwargs.get('nonce') else None),
            'withdrawals': lambda: self.pool_withdrawals(
                kwargs.get('address'), int(kwargs.get('limit', 50))),
            'pay': lambda: self.pool_pay_withdrawal(
                int(kwargs.get('id', 0)), secret=kwargs.get('secret'),
                owner=kwargs.get('owner'), signature=kwargs.get('signature')),
            'mark-paid': lambda: self.pool_mark_paid(
                int(kwargs.get('id', 0)), kwargs.get('tx', ''),
                secret=kwargs.get('secret'), owner=kwargs.get('owner'),
                signature=kwargs.get('signature')),
            'hyperevm': lambda: self.hyperevm_status(),
        }

        if not action or action not in actions:
            return {
                'module': 'prefi',
                'description': self.description,
                'actions': list(actions.keys()),
                'status': self.status(),
            }

        return actions[action]()
