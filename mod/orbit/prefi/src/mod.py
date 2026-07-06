"""PreFi - Trading Protocol

Trade assets through Uniswap V3 on Base. Profit goes to treasury,
trader receives 1 PREFI per $1 profit. Lock PREFI for staketime
to claim weekly treasury distributions.
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


class Mod:
    description = """PreFi - Trading protocol on Base.
    Trade via Uniswap V3, profit → treasury, earn PREFI tokens.
    Lock PREFI for staketime → claim weekly treasury earnings."""

    def __init__(self, config=None):
        self.config = config or {}
        self.module_dir = Path(__file__).parent.parent
        self.store_dir = Path(os.path.expanduser('~/.prefi'))
        self.store_dir.mkdir(parents=True, exist_ok=True)

        # Storage paths
        self.positions_path = self.store_dir / 'positions.json'
        self.stakes_path = self.store_dir / 'stakes.json'
        self.treasury_path = self.store_dir / 'treasury.json'
        self.markets_path = self.store_dir / 'markets.json'

        # Network config
        self.network = self.config.get('network', 'baseSepolia')
        self.contracts = self.config.get('contracts', {})

        # Ports
        self.api_port = self.config.get('api_port', 8830)
        self.app_port = self.config.get('app_port', 8831)
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

    def add_market(self, token: str, symbol: str, fee_tier: int = 3000) -> Dict:
        """Add a supported asset market (token with Uniswap pool)"""
        markets = self._load_json(self.markets_path, [])

        for m in markets:
            if m['token'].lower() == token.lower():
                return {'error': f'{symbol} already listed', 'market': m}

        market = {
            'token': token,
            'symbol': symbol,
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

    def list_markets(self) -> List[Dict]:
        """Get all supported asset markets with prices and stats"""
        markets = self._load_json(self.markets_path, [])
        for m in markets:
            price = self._get_token_price(m['symbol'])
            if price:
                m['price_usd'] = price
            total = m.get('win_count', 0) + m.get('loss_count', 0)
            m['win_rate'] = round(m.get('win_count', 0) / total * 100, 1) if total > 0 else 0
        return markets

    def _get_token_price(self, symbol: str) -> Optional[float]:
        """Get current USD price from CoinGecko (cached 5 min)"""
        key = symbol.upper()
        cached = self._price_cache.get(key)
        if cached and (time.time() - cached['ts']) < self._price_cache_ttl:
            return cached['price']

        ids = {
            'WETH': 'ethereum', 'ETH': 'ethereum', 'BTC': 'bitcoin',
            'cbBTC': 'bitcoin', 'USDC': 'usd-coin', 'LINK': 'chainlink',
            'UNI': 'uniswap', 'AAVE': 'aave', 'SOL': 'solana',
            'ARB': 'arbitrum', 'OP': 'optimism',
        }
        cg_id = ids.get(key)
        if not cg_id:
            return None
        try:
            resp = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={'ids': cg_id, 'vs_currencies': 'usd'},
                timeout=10,
            )
            resp.raise_for_status()
            price = resp.json().get(cg_id, {}).get('usd')
            if price:
                self._price_cache[key] = {'price': price, 'ts': time.time()}
            return price
        except Exception:
            return cached['price'] if cached else None

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
        """Full portfolio view: positions + stakes + claims"""
        positions = self._load_json(self.positions_path, [])
        stakes_data = self._load_json(self.stakes_path, [])
        treasury = self._init_treasury()

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
            },
            'treasury_claims': {
                'total_claimed': round(total_claimed, 6),
                'epochs_claimed': sum(
                    1 for e in treasury.get('epochs', [])
                    if address.lower() in e.get('claims', {})
                ),
            },
        }

    # ── Prices ───────────────────────────────────────────────────────

    def get_prices(self) -> Dict:
        """Get current prices from CoinGecko"""
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
            return {
                'ETH': {'price': data.get('ethereum', {}).get('usd', 0),
                         'change_24h': data.get('ethereum', {}).get('usd_24h_change', 0)},
                'BTC': {'price': data.get('bitcoin', {}).get('usd', 0),
                         'change_24h': data.get('bitcoin', {}).get('usd_24h_change', 0)},
                'USDC': {'price': data.get('usd-coin', {}).get('usd', 0),
                          'change_24h': data.get('usd-coin', {}).get('usd_24h_change', 0)},
                'timestamp': datetime.now().isoformat(),
            }
        except Exception as e:
            return {'error': str(e)}

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

    def serve(self, api_port=None, app_port=None, dev=True):
        """Start the FastAPI server and Next.js app"""
        api_port = api_port or self.api_port
        app_port = app_port or self.app_port
        results = {}
        log_dir = Path('/tmp/prefi')
        log_dir.mkdir(parents=True, exist_ok=True)

        self.kill()

        api_dir = self.module_dir / 'src' / 'api'
        api_path = api_dir / 'api.py'
        if api_path.exists():
            env = os.environ.copy()
            env['PORT'] = str(api_port)
            env['PYTHONPATH'] = str(self.module_dir.parent.parent.parent)
            api_log = open(log_dir / 'api.log', 'w')
            cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0',
                   '--port', str(api_port)]
            if dev:
                cmd.append('--reload')
            subprocess.Popen(cmd, cwd=str(api_dir), env=env,
                             stdout=api_log, stderr=subprocess.STDOUT)
            results['api'] = f'http://localhost:{api_port}'

        app_dir = self.module_dir / 'src' / 'app'
        if app_dir.exists():
            env = os.environ.copy()
            env['NEXT_PUBLIC_API_URL'] = f'http://localhost:{api_port}'
            env['PORT'] = str(app_port)
            app_log = open(log_dir / 'app.log', 'w')
            cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
            subprocess.Popen(cmd, cwd=str(app_dir), env=env,
                             stdout=app_log, stderr=subprocess.STDOUT)
            results['app'] = f'http://localhost:{app_port}'

        results['logs'] = str(log_dir)
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

    def health(self):
        """Check service health"""
        status = {
            'service': 'prefi',
            'network': self.network,
            'contracts': self.contracts,
            'timestamp': datetime.now().isoformat(),
        }
        try:
            r = requests.get(f'http://localhost:{self.api_port}/health', timeout=2)
            status['api'] = {'status': 'up', 'port': self.api_port}
        except Exception:
            status['api'] = {'status': 'down', 'port': self.api_port}
        return status

    def status(self) -> Dict:
        """Get overall protocol status"""
        markets = self._load_json(self.markets_path, [])
        positions = self._load_json(self.positions_path, [])
        stakes = self._load_json(self.stakes_path, [])
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
        }
        try:
            self.store_dir = Path(tmp)
            self.positions_path = self.store_dir / 'positions.json'
            self.stakes_path = self.store_dir / 'stakes.json'
            self.treasury_path = self.store_dir / 'treasury.json'
            self.markets_path = self.store_dir / 'markets.json'

            # 1. Markets
            print('\n1. Markets')
            r = self.add_market('0xWETH', 'WETH', 3000)
            check('add WETH market', r.get('status') == 'added')
            r2 = self.add_market('0xcbBTC', 'cbBTC', 3000)
            check('add cbBTC market', r2.get('status') == 'added')

            dup = self.add_market('0xWETH', 'WETH', 3000)
            check('duplicate blocked', 'error' in dup)

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
            ]
            self._save_json(self.positions_path, mock_positions)

            positions = self.get_positions('0xAlice')
            check('Alice has 2 positions', len(positions) == 2)

            # Mock prices for close
            orig_get_price = self._get_token_price
            self._get_token_price = lambda sym: 2500.0 if sym == 'WETH' else 110000.0

            c = self.close_position(1, '0xAlice')
            check('profitable close', c.get('status') == 'profitable', f'profit={c.get("profit")}')
            check('profit = 250', abs(c.get('profit', 0) - 250.0) < 0.01)
            check('prefi = profit', abs(c.get('prefi_earned', 0) - 250.0) < 0.01)
            check('return_pct = 25%', c.get('return_pct') == 25.0)

            wrong = self.close_position(2, '0xAlice')
            check('wrong user blocked', 'error' in wrong)

            # Losing trade
            self._get_token_price = lambda sym: 1500.0
            loss = self.close_position(2, '0xBob')
            check('loss close', loss.get('status') == 'loss')
            check('no prefi on loss', loss.get('prefi_earned') == 0)

            # BTC profitable trade
            self._get_token_price = lambda sym: 110000.0
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

            # 8. Status
            print('\n8. Status')
            status = self.status()
            check('status has volume', status.get('total_volume', 0) > 0)
            check('status has traders', status.get('traders', 0) == 2)
            check('status has prefi minted', status.get('total_prefi_minted', 0) > 0)

        finally:
            for k, v in orig.items():
                setattr(self, k, v)
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
            add-market  - Add market (token=, symbol=, fee_tier=)

            open        - Open position (asset=, amount=, address=)
            close       - Close position (id=, address=)
            positions   - Get positions (address=)

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
            ),

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
