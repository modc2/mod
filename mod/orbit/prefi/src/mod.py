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

try:
    import scoring
except ImportError:  # imported as a package (`src.mod`) rather than from src/
    from . import scoring


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
                   source: str = 'coingecko') -> Dict:
        """Add a supported asset market

        source picks where the price comes from: 'coingecko' (Base tokens with a
        Uniswap pool, keyed by CG_IDS) or 'hyperliquid' (any coin in the HL
        universe — see add_hl_market, which fills token/source for you).
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
        """List a Hyperliquid perp as a market. The coin must be in the HL
        universe — that check is the whole point, it keeps typos out of the
        market list where they'd become unpriceable positions."""
        coin = (coin or '').strip().upper()
        if not coin:
            return {'error': 'coin required'}

        mids = self._hl_mids()
        if not mids:
            return {'error': 'Hyperliquid unreachable — no asset list to verify against'}
        if coin not in mids:
            return {'error': f'{coin} is not a Hyperliquid perp'}

        # fee_tier is a Uniswap concept; HL markets carry 0 and the UI hides it.
        return self.add_market(f'hl:{coin}', coin, 0, source='hyperliquid')

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

    def list_markets(self) -> List[Dict]:
        """Get all supported asset markets with prices and stats"""
        markets = self._load_json(self.markets_path, [])
        for m in markets:
            price = self._get_token_price(m['symbol'], m.get('source'))
            if price:
                m['price_usd'] = price
            m.setdefault('source', 'coingecko')
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

    PRICE_SOURCES = ('coingecko', 'hyperliquid')
    HL_INFO_URL = 'https://api.hyperliquid.xyz/info'
    # Hyperliquid rate-limits per IP, and the `hyperliquid` module on this host
    # already holds a client against it. Ask that module first and fall back to
    # the public endpoint — one HL client per box, not one per module.
    HL_MOD_URL = os.environ.get('PREFI_HL_API', 'http://localhost:8919')

    # Symbols that share a CoinGecko id share a cache entry — pricing WETH
    # prices ETH too, which keeps the free tier out of rate-limit territory.
    CG_IDS = {
        'WETH': 'ethereum', 'ETH': 'ethereum', 'BTC': 'bitcoin',
        'CBBTC': 'bitcoin', 'USDC': 'usd-coin', 'LINK': 'chainlink',
        'UNI': 'uniswap', 'AAVE': 'aave', 'SOL': 'solana',
        'ARB': 'arbitrum', 'OP': 'optimism', 'AERO': 'aerodrome-finance',
    }

    def _hl_mod_get(self, path: str, timeout: int = 10):
        """GET from the local hyperliquid module. None if it isn't running."""
        try:
            resp = requests.get(f'{self.HL_MOD_URL}{path}', timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _hl_post(self, body: Dict, timeout: int = 10):
        """POST to the public Hyperliquid info endpoint — no key needed"""
        resp = requests.post(self.HL_INFO_URL, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _hl_named(raw: Dict) -> Dict[str, float]:
        """Spot pairs and prediction legs come back as '@1' / '#10010' indexes —
        only named perps ('BTC', '0G') are addressable by a symbol someone would
        type into the market list."""
        out = {}
        for k, v in (raw or {}).items():
            if k[:1] in ('@', '#'):
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

    def hl_assets(self, search: str = '', limit: int = 50) -> List[Dict]:
        """Browse the Hyperliquid perp universe — the pool add_hl_market draws
        from. Delisted coins are filtered out; they have no tradeable price."""
        cached = self._price_cache.get('_hl_universe')
        if cached and (time.time() - cached['ts']) < 300:
            universe = cached['universe']
        else:
            meta = self._hl_mod_get('/market/meta')
            if isinstance(meta, list):   # the module returns [meta, assetCtxs]
                meta = meta[0] if meta else {}
            if meta is None:
                try:
                    meta = self._hl_post({'type': 'meta'})
                except Exception:
                    meta = {}
            universe = [u for u in (meta or {}).get('universe', [])
                        if not u.get('isDelisted')]
            if universe:
                self._price_cache['_hl_universe'] = {'universe': universe, 'ts': time.time()}
            elif cached:
                universe = cached['universe']

        mids = self._hl_mids()
        listed = {m['symbol'].upper() for m in self._load_json(self.markets_path, [])}
        q = (search or '').strip().upper()

        out = []
        for u in universe:
            name = u.get('name', '')
            if q and q not in name.upper():
                continue
            if name not in mids:
                continue
            out.append({
                'coin': name,
                'price': mids[name],
                'max_leverage': u.get('maxLeverage'),
                'listed': name.upper() in listed,
            })
        out.sort(key=lambda a: (a['listed'], a['coin']))
        return out[:max(1, int(limit))]

    def _get_token_price(self, symbol: str, source: str = None) -> Optional[float]:
        """Current USD price for a symbol. `source` defaults to the one recorded
        on the market — callers that already hold the market should pass it and
        skip the lookup."""
        if source is None:
            market = self._market(symbol)
            source = market.get('source', 'coingecko') if market else 'coingecko'

        if source == 'hyperliquid':
            return self._hl_mids().get(symbol.upper())

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
                # The module only takes a lookback in hours, so ask for enough
                # to cover ts and pick the candle that contains it.
                hours = int((time.time() - ts) // 3600) + 2
                candles = self._hl_mod_get(
                    f'/candles/{symbol.upper()}?interval=1h&hours={hours}', timeout=15)
                if candles is None:
                    candles = self._hl_post({
                        'type': 'candleSnapshot',
                        'req': {'coin': symbol.upper(), 'interval': '1h',
                                'startTime': start, 'endTime': start + 3600_000},
                    })
                match = [c for c in (candles or []) if c.get('t') == start]
                if match:
                    return {'price': float(match[0]['c']), 'mode': 'historical'}
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
        burned = sum(p.get('burn', 0) for p in predictions)
        locked = sum(s['amount'] for s in self._load_json(self.stakes_path, [])
                     if s['staker'].lower() == addr and not s.get('withdrawn'))

        minted = from_trades + from_predictions
        return {
            'address': address,
            'minted': round(minted, 6),
            'from_trades': round(from_trades, 6),
            'from_predictions': round(from_predictions, 6),
            'burned': round(burned, 6),
            'locked': round(locked, 6),
            'available': round(minted - burned - locked, 6),
        }

    # ── Predictions ──────────────────────────────────────────────────

    def predict(self, asset: str, predicted_price: float, burn: float,
                address: str, horizon: int = None) -> Dict:
        """Burn PREFI to call an asset's price one horizon from now.

        The burn is gone the moment it is placed. What comes back at resolution
        is freshly minted and scaled by how close the call was — see
        `scoring.py`. Scoring params are snapshotted onto the prediction so
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
            burn = float(burn)
        except (TypeError, ValueError):
            return {'error': 'predicted_price and burn must be numbers'}
        if predicted_price <= 0:
            return {'error': 'Predicted price must be positive'}
        if burn < params['min_burn']:
            return {'error': f'Minimum burn is {params["min_burn"]} PREFI'}

        market = self._market(asset)
        if not market:
            return {'error': f'Market not found for {asset}'}
        if not market.get('active'):
            return {'error': f'{asset} market not active'}

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

        treasury = self._init_treasury()
        treasury['total_prefi_burned'] = treasury.get('total_prefi_burned', 0) + burn
        self._save_json(self.treasury_path, treasury)

        return {
            'prediction_id': prediction['id'],
            'asset': prediction['asset'],
            'entry_price': entry_price,
            'predicted_price': predicted_price,
            'implied_move_pct': round((predicted_price - entry_price) / entry_price * 100, 2),
            'burned': burn,
            'max_payout': scoring.payout(burn, 1.0, params),
            'resolves_at': datetime.fromtimestamp(prediction['resolve_at']).isoformat(),
            'model': params['model'],
            'status': 'open',
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
            payout = scoring.payout(p['burn'], result['score'], params)

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
                'total_burned': 0.0, 'total_payout': 0.0, 'score_sum': 0.0,
                'best_score': 0.0,
            })
            row['predictions'] += 1
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
        """Active scoring params, defaults filled in"""
        try:
            return scoring.validate(self._load_json(self.scoring_path, {}))
        except ValueError:
            # A hand-edited file with junk in it shouldn't brick predictions.
            return dict(scoring.DEFAULT_PARAMS)

    def set_scoring(self, **params) -> Dict:
        """Retune the score. Only affects predictions placed after this call —
        open ones carry the params they were made under."""
        given = {k: v for k, v in params.items() if v is not None}
        if not given:
            return {'error': f'nothing to set — params are {list(scoring.DEFAULT_PARAMS)}'}
        try:
            merged = scoring.validate({**self.get_scoring(), **given})
        except ValueError as e:
            return {'error': str(e)}
        self._save_json(self.scoring_path, merged)
        return {'status': 'updated', 'scoring': merged, 'changed': list(given)}

    def scoring_models(self) -> Dict:
        """The model registry — name → what its curve does"""
        return {
            'models': scoring.describe_models(),
            'defaults': dict(scoring.DEFAULT_PARAMS),
            'active': self.get_scoring(),
        }

    def score_preview(self, predicted: float, actual: float,
                      model: str = None, tolerance: float = None,
                      burn: float = None) -> Dict:
        """Score a hypothetical without placing anything — the same code path
        the resolver uses, so the number shown is the number paid."""
        params = self.get_scoring()
        if model:
            params['model'] = model
        if tolerance:
            params['tolerance'] = float(tolerance)
        try:
            params = scoring.validate(params)
        except ValueError as e:
            return {'error': str(e)}

        result = scoring.score(float(predicted), float(actual), params)
        stake = float(burn) if burn else params['min_burn']
        result['burn'] = stake
        result['payout'] = scoring.payout(stake, result['score'], params)
        result['net'] = round(result['payout'] - stake, 6)
        return result

    # ── Staking ──────────────────────────────────────────────────────

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
            'forecasters': len(set(p['predictor'] for p in predictions)) if predictions else 0,
            'scoring': self.get_scoring(),
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
            hl-assets   - Browse the Hyperliquid universe (search=, limit=)
            add-hl      - List a Hyperliquid perp as a market (coin=)

            open        - Open position (asset=, amount=, address=)
            close       - Close position (id=, address=)
            positions   - Get positions (address=)

            predict     - Burn PREFI on a price call (asset=, price=, burn=,
                          address=, horizon=)
            predictions - List predictions (address=, limit=)
            resolve     - Settle every due prediction
            forecasters - Prediction leaderboard
            balance     - PREFI balance (address=)

            scoring     - Active scoring params
            set-scoring - Retune (model=, tolerance=, multiplier=, horizon=,
                          min_burn=)
            models      - Available scoring models
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
            ),
            'add-hl': lambda: self.add_hl_market(kwargs.get('coin', '')),

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
            'resolve': lambda: self.resolve_predictions(),
            'forecasters': lambda: self.prediction_board(),
            'balance': lambda: self.prefi_balance(kwargs.get('address', '')),

            'scoring': lambda: self.get_scoring(),
            'set-scoring': lambda: self.set_scoring(
                model=kwargs.get('model'),
                tolerance=kwargs.get('tolerance'),
                multiplier=kwargs.get('multiplier'),
                horizon=kwargs.get('horizon'),
                min_burn=kwargs.get('min_burn'),
            ),
            'models': lambda: self.scoring_models(),
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
        }

        if not action or action not in actions:
            return {
                'module': 'prefi',
                'description': self.description,
                'actions': list(actions.keys()),
                'status': self.status(),
            }

        return actions[action]()
