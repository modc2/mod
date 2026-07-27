"""
bt.tools — the single tool registry over the Bittensor protocol.

Every surface (MCP stdio server, MCP-over-HTTP, JSON API, web console,
docs) is generated from this one table. Each tool maps a name + JSON
schema onto a method of Bt (chain surface) or BtTrader (staking/trading).

Engines are constructed lazily and cached, so listing tools never touches
the network.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

DEFAULT_NETWORK = 'finney'

_lock = threading.Lock()
_bt_cache: Dict[str, Any] = {}
_trader_cache: Dict[str, Any] = {}


def get_bt(network: str = DEFAULT_NETWORK):
    """Cached Bt chain client per network."""
    with _lock:
        if network not in _bt_cache:
            from .bt import Bt
            _bt_cache[network] = Bt(network=network)
        return _bt_cache[network]


def get_trader(wallet: str = 'default', hotkey: str = 'default',
               network: str = DEFAULT_NETWORK):
    """Cached BtTrader per (wallet, hotkey, network)."""
    key = f'{wallet}/{hotkey}/{network}'
    with _lock:
        if key not in _trader_cache:
            from .bt import BtTrader
            try:
                _trader_cache[key] = BtTrader(
                    wallet_name=wallet, hotkey=hotkey, network=network)
            except Exception as e:
                if 'KeyFile' in type(e).__name__ or 'Keyfile' in str(e):
                    raise ValueError(
                        f"no local wallet '{wallet}' with hotkey '{hotkey}' — "
                        "create one first with bt_create_wallet "
                        f"(name='{wallet}', hotkey='{hotkey}')") from e
                raise
        return _trader_cache[key]


def _jsonable(x: Any) -> Any:
    """Best-effort conversion of SDK objects to plain JSON values."""
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_jsonable(v) for v in x]
    for attr in ('tao', 'rao'):  # bittensor Balance
        if hasattr(x, attr) and hasattr(x, '__float__'):
            return float(x)
    if hasattr(x, '__dict__'):
        try:
            return {k: _jsonable(v) for k, v in vars(x).items()
                    if not k.startswith('_')}
        except Exception:
            pass
    return str(x)


class Tool:
    def __init__(self, name: str, description: str, group: str,
                 params: Dict[str, Dict], handler: Callable[..., Any],
                 mutates: bool = False):
        self.name = name
        self.description = description
        self.group = group
        self.params = params            # name -> {type, description, default?, required?}
        self.handler = handler
        self.mutates = mutates

    @property
    def input_schema(self) -> Dict:
        props, required = {}, []
        for pname, p in self.params.items():
            prop = {'type': p['type'], 'description': p['description']}
            if 'default' in p:
                prop['default'] = p['default']
            props[pname] = prop
            if p.get('required'):
                required.append(pname)
        schema: Dict[str, Any] = {'type': 'object', 'properties': props}
        if required:
            schema['required'] = required
        return schema

    def call(self, args: Optional[Dict] = None) -> Any:
        args = dict(args or {})
        for pname, p in self.params.items():
            if p.get('required') and pname not in args:
                raise ValueError(f'missing required argument: {pname}')
        unknown = set(args) - set(self.params)
        if unknown:
            raise ValueError(f'unknown argument(s): {", ".join(sorted(unknown))}')
        return _jsonable(self.handler(**args))


def _wallet_params(extra: Dict[str, Dict]) -> Dict[str, Dict]:
    base = {
        'wallet': {'type': 'string', 'description': 'Coldkey wallet name', 'default': 'default'},
        'hotkey': {'type': 'string', 'description': 'Hotkey name', 'default': 'default'},
        'network': {'type': 'string', 'description': "Chain network ('finney' mainnet, 'test', or custom endpoint)", 'default': DEFAULT_NETWORK},
    }
    base.update(extra)
    return base


def _net_param() -> Dict[str, Dict]:
    return {'network': {'type': 'string', 'description': "Chain network ('finney' mainnet, 'test', or custom endpoint)", 'default': DEFAULT_NETWORK}}


# ---------------------------------------------------------------- handlers

def _subnets(search=None, n=10, network=DEFAULT_NETWORK):
    return get_bt(network).subnets(search=search, n=n)

def _subnet(netuid=1, network=DEFAULT_NETWORK):
    return get_bt(network).subnet(netuid=netuid)

def _neurons(netuid=1, limit=64, network=DEFAULT_NETWORK):
    out = get_bt(network).neurons(netuid=netuid)
    return out[:limit] if isinstance(out, list) else out

def _neuron_count(netuid=1, network=DEFAULT_NETWORK):
    return {'netuid': netuid, 'neurons': get_bt(network).n(netuid=netuid)}

def _block(network=DEFAULT_NETWORK):
    return {'block': int(get_bt(network).subtensor.block),
            'ts': int(time.time()), 'network': network}

def _balance(address, network=DEFAULT_NETWORK):
    return {'address': address, 'tao': get_bt(network).balance(address)}

def _wallets():
    import os
    root = os.path.expanduser('~/.bittensor/wallets')
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        wdir = os.path.join(root, name)
        if not os.path.isdir(wdir):
            continue
        hks_dir = os.path.join(wdir, 'hotkeys')
        hotkeys = sorted(os.listdir(hks_dir)) if os.path.isdir(hks_dir) else []
        coldpub = None
        try:
            import json as _json
            with open(os.path.join(wdir, 'coldkeypub.txt')) as f:
                coldpub = _json.load(f).get('ss58Address')
        except Exception:
            pass
        out.append({'name': name, 'coldkey': coldpub, 'hotkeys': hotkeys})
    return out

def _wallet(name='default', hotkey='default', network=DEFAULT_NETWORK):
    return get_bt(network).get_wallet(name, hotkey=hotkey)

def _create_wallet(name, hotkey=None):
    return get_bt().create_wallet(name, hotkey=hotkey)

def _transfer(wallet, dest, amount, network=DEFAULT_NETWORK):
    return {'ok': bool(get_bt(network).transfer(wallet, dest, amount)),
            'wallet': wallet, 'dest': dest, 'amount_tao': amount}

def _market_row(s: Dict) -> Dict:
    price = s.get('price') or 0.0
    alpha_total = (s.get('alpha_in') or 0.0) + (s.get('alpha_out') or 0.0)
    return {
        'netuid': s.get('netuid'),
        'name': s.get('subnet_name') or s.get('name'),
        'symbol': s.get('symbol'),
        'price': price,
        'market_cap': price * alpha_total,
        'tao_in': s.get('tao_in'),
        'alpha_in': s.get('alpha_in'),
        'alpha_out': s.get('alpha_out'),
        'emission': s.get('emission'),
    }

def _price(netuid, network=DEFAULT_NETWORK):
    # Wallet-free: price lives on the subnet's dynamic pool info.
    return _market_row(get_bt(network).subnet(netuid=netuid))

def _scan(sort_by='price', limit=20, network=DEFAULT_NETWORK):
    # Wallet-free scan over all subnets; BtTrader.fast_scan needs a local
    # wallet, which a read-only market view must not require.
    rows = [_market_row(s) for s in get_bt(network).subnets(n=10000)]
    rows.sort(key=lambda r: r.get(sort_by) or 0.0, reverse=True)
    return rows[:limit]

def _leaderboard(top_n=20, network=DEFAULT_NETWORK):
    try:
        return get_trader(network=network).fast_leaderboard(top_n=top_n)
    except ValueError:
        return _scan(sort_by='market_cap', limit=top_n, network=network)

def _portfolio(wallet='default', hotkey='default', network=DEFAULT_NETWORK):
    return get_trader(wallet, hotkey, network).portfolio()

def _trader_balance(wallet='default', hotkey='default', network=DEFAULT_NETWORK):
    return get_trader(wallet, hotkey, network).balance()

def _buy(netuid, amount_tao, wallet='default', hotkey='default', network=DEFAULT_NETWORK):
    return {'ok': bool(get_trader(wallet, hotkey, network).buy(netuid, amount_tao)),
            'netuid': netuid, 'amount_tao': amount_tao, 'side': 'buy'}

def _sell(netuid, amount_tao, wallet='default', hotkey='default', network=DEFAULT_NETWORK):
    return {'ok': bool(get_trader(wallet, hotkey, network).sell(netuid, amount_tao)),
            'netuid': netuid, 'amount_tao': amount_tao, 'side': 'sell'}

def _sell_all(netuid, wallet='default', hotkey='default', network=DEFAULT_NETWORK):
    return {'ok': bool(get_trader(wallet, hotkey, network).sell_all(netuid)),
            'netuid': netuid, 'side': 'sell_all'}

def _swap(from_netuid, to_netuid, amount_tao, wallet='default', hotkey='default',
          network=DEFAULT_NETWORK):
    return {'ok': bool(get_trader(wallet, hotkey, network).swap(
                from_netuid, to_netuid, amount_tao)),
            'from_netuid': from_netuid, 'to_netuid': to_netuid,
            'amount_tao': amount_tao, 'side': 'swap'}

def _screener(sort_by='market_cap', limit=0, search=None, sparks=True):
    from . import history
    return history.screener(sort_by=sort_by, limit=limit, search=search,
                            sparks=sparks)

def _history(netuid, hours=24, points=300):
    from . import history
    return {'netuid': netuid, 'hours': hours,
            'series': history.series(netuid, hours=hours, points=points)}

def _stats():
    from . import history
    return history.stats()

def _validators(netuid, limit=15, network=DEFAULT_NETWORK):
    neurons = get_bt(network).neurons(netuid=netuid)
    rows = [{'uid': n.get('uid'), 'hotkey': n.get('hotkey'),
             'coldkey': n.get('coldkey'),
             'stake': n.get('total_stake') or n.get('stake') or 0.0,
             'validator_trust': n.get('validator_trust'),
             'dividends': n.get('dividends'),
             'incentive': n.get('incentive'),
             'emission': n.get('emission'),
             'active': n.get('active'),
             'validator_permit': n.get('validator_permit')}
            for n in neurons]
    rows.sort(key=lambda r: r['stake'] or 0.0, reverse=True)
    return {'netuid': netuid, 'neurons': len(rows), 'top': rows[:limit]}

def _account(address, network=DEFAULT_NETWORK):
    from . import history
    bt = get_bt(network)
    out = {'address': address, 'free_tao': bt.balance(address),
           'positions': [], 'staked_value_tao': 0.0}
    prices = {r['netuid']: r['price']
              for r in history.screener(sparks=False).get('rows', [])}
    try:
        infos = bt.subtensor.get_stake_info_for_coldkey(address) or []
    except Exception as e:
        out['stake_error'] = f'{type(e).__name__}: {e}'
        infos = []
    for si in infos:
        alpha = float(getattr(si, 'stake', 0) or 0)
        if alpha <= 0:
            continue
        netuid = getattr(si, 'netuid', None)
        price = prices.get(netuid)
        value = alpha * price if price is not None else None
        out['positions'].append({
            'netuid': netuid, 'hotkey': getattr(si, 'hotkey_ss58', None),
            'alpha': alpha, 'price': price, 'value_tao': value})
        if value:
            out['staked_value_tao'] += value
    out['positions'].sort(key=lambda p: p['value_tao'] or 0.0, reverse=True)
    out['total_value_tao'] = out['free_tao'] + out['staked_value_tao']
    return out

def _rpc_health(network=DEFAULT_NETWORK):
    return get_trader(network=network).rpc_health()

def _best_rpc(network=DEFAULT_NETWORK):
    return {'endpoint': get_trader(network=network).best_rpc()}

def _trades(days=30, limit=200, network=DEFAULT_NETWORK):
    return get_trader(network=network).fast_trades(days=days, limit=limit)


# --- tracked traders (bt.traders index)

def _track(address, label=None, network=DEFAULT_NETWORK):
    from . import traders
    return traders.track(address, label=label, network=network)

def _untrack(address, purge=False):
    from . import traders
    return traders.untrack(address, purge=purge)

def _traders(sort_by='total_tao', limit=0, sparks=True):
    from . import traders
    return traders.traders(sort_by=sort_by, limit=limit, sparks=sparks)

def _trader(address, hours=168, flows_limit=50):
    from . import traders
    return traders.trader(address, hours=hours, flows_limit=flows_limit)

def _trader_history(address, hours=168, points=300):
    from . import traders
    return traders.series(address, hours=hours, points=points)

def _trader_flows(address=None, hours=168, limit=100):
    from . import traders
    return traders.flows(address=address, hours=hours, limit=limit)

def _trader_snapshot(address=None):
    from . import traders
    if address:
        return traders.snapshot(address)
    return traders.snapshot_all()

def _trader_at(address, ts=None, tolerance_sec=0):
    from . import traders
    return traders.positions_at(address, ts=ts, tolerance_sec=tolerance_sec)

def _prices_at(ts=None):
    from . import traders
    return traders.prices_at(ts=ts)


# ---------------------------------------------------------------- registry

TOOLS: List[Tool] = [
    # --- chain / discovery
    Tool('bt_subnets', 'List Bittensor subnets with metadata (name, symbol, owner, emission). Optionally filter by search string.', 'Chain',
         dict(search={'type': 'string', 'description': 'Filter subnets by name/symbol substring'},
              n={'type': 'integer', 'description': 'Max subnets to return', 'default': 10},
              **_net_param()), _subnets),
    Tool('bt_subnet', 'Get detailed info for one subnet by netuid (hyperparameters, owner, emission, tempo).', 'Chain',
         dict(netuid={'type': 'integer', 'description': 'Subnet UID', 'required': True},
              **_net_param()), _subnet),
    Tool('bt_neurons', 'List neurons (miners/validators) registered on a subnet: hotkey, coldkey, stake, rank, trust, incentive, emission, axon endpoint.', 'Chain',
         dict(netuid={'type': 'integer', 'description': 'Subnet UID', 'required': True},
              limit={'type': 'integer', 'description': 'Max neurons to return', 'default': 64},
              **_net_param()), _neurons),
    Tool('bt_neuron_count', 'Get the number of neurons registered on a subnet.', 'Chain',
         dict(netuid={'type': 'integer', 'description': 'Subnet UID', 'required': True},
              **_net_param()), _neuron_count),
    Tool('bt_validators', 'Top validators/neurons on a subnet ranked by stake: hotkey, stake, vtrust, dividends, incentive.', 'Chain',
         dict(netuid={'type': 'integer', 'description': 'Subnet UID', 'required': True},
              limit={'type': 'integer', 'description': 'Rows to return', 'default': 15},
              **_net_param()), _validators),
    Tool('bt_block', 'Current finalized block height of the chain.', 'Chain',
         dict(**_net_param()), _block),
    # --- wallet
    Tool('bt_balance', 'Get the free TAO balance of any ss58 coldkey address.', 'Wallet',
         dict(address={'type': 'string', 'description': 'ss58 coldkey address', 'required': True},
              **_net_param()), _balance),
    Tool('bt_account', 'Full account explorer for ANY ss58 coldkey: free TAO + every alpha stake position across subnets with live TAO valuation. Works for addresses you do not own.', 'Wallet',
         dict(address={'type': 'string', 'description': 'ss58 coldkey address', 'required': True},
              **_net_param()), _account),
    Tool('bt_wallets', 'List local Bittensor wallets (~/.bittensor/wallets): names, coldkey addresses, hotkeys.', 'Wallet',
         {}, _wallets),
    Tool('bt_wallet', 'Get info for one local wallet (coldkey + hotkey addresses).', 'Wallet',
         dict(name={'type': 'string', 'description': 'Wallet name', 'default': 'default'},
              hotkey={'type': 'string', 'description': 'Hotkey name', 'default': 'default'},
              **_net_param()), _wallet),
    Tool('bt_create_wallet', 'Create a new local wallet (coldkey + hotkey). Mnemonics are stored under ~/.bittensor/wallets — back them up.', 'Wallet',
         dict(name={'type': 'string', 'description': 'New wallet name', 'required': True},
              hotkey={'type': 'string', 'description': 'Hotkey name (defaults to "default")'}),
         _create_wallet, mutates=True),
    Tool('bt_transfer', 'Transfer TAO from a local wallet to any ss58 address. IRREVERSIBLE on-chain transaction.', 'Wallet',
         dict(wallet={'type': 'string', 'description': 'Local wallet name to send from', 'required': True},
              dest={'type': 'string', 'description': 'Destination ss58 address', 'required': True},
              amount={'type': 'number', 'description': 'Amount in TAO', 'required': True},
              **_net_param()), _transfer, mutates=True),
    # --- markets
    Tool('bt_price', "Get the current alpha token price for a subnet (TAO in/out reserves, price, market cap).", 'Markets',
         dict(netuid={'type': 'integer', 'description': 'Subnet UID', 'required': True},
              **_net_param()), _price),
    Tool('bt_scan', 'Scan all subnets and rank alpha markets by price, market cap, or emission.', 'Markets',
         dict(sort_by={'type': 'string', 'description': "Sort key: 'price', 'market_cap', 'emission'", 'default': 'price'},
              limit={'type': 'integer', 'description': 'Max rows', 'default': 20},
              **_net_param()), _scan),
    Tool('bt_leaderboard', 'Subnet leaderboard built by the fast Rust engine (round-robin RPC).', 'Markets',
         dict(top_n={'type': 'integer', 'description': 'Rows to return', 'default': 20},
              **_net_param()), _leaderboard),
    Tool('bt_screener', 'INSTANT market screener served from the local open indexer (no chain round-trip): every subnet with price, market cap, 1h/24h/7d change %, real 24h volume, liquidity, identity (github/site/discord/logo) and a 24h price sparkline.', 'Markets',
         dict(sort_by={'type': 'string', 'description': "Sort key: 'market_cap', 'price', 'change_24h', 'change_1h', 'change_7d', 'vol_24h', 'tao_in', 'emission'", 'default': 'market_cap'},
              limit={'type': 'integer', 'description': 'Max rows (0 = all)', 'default': 0},
              search={'type': 'string', 'description': 'Filter by name/symbol/netuid'},
              sparks={'type': 'boolean', 'description': 'Include 24h sparkline series', 'default': True}),
         _screener),
    Tool('bt_history', "A subnet's price/mcap/volume time series from the local indexer — chart-ready.", 'Markets',
         dict(netuid={'type': 'integer', 'description': 'Subnet UID', 'required': True},
              hours={'type': 'integer', 'description': 'Lookback window in hours', 'default': 24},
              points={'type': 'integer', 'description': 'Max points (downsampled)', 'default': 300}),
         _history),
    Tool('bt_stats', 'Network-wide totals: subnet count, total alpha market cap, TAO in pools, 24h volume.', 'Markets',
         {}, _stats),
    Tool('bt_trades', 'Recent on-chain staking events (buys/sells across subnets).', 'Markets',
         dict(days={'type': 'integer', 'description': 'Lookback window in days', 'default': 30},
              limit={'type': 'integer', 'description': 'Max events', 'default': 200},
              **_net_param()), _trades),
    # --- trading
    Tool('bt_portfolio', 'Get all staked alpha positions for a local wallet across subnets, with TAO value.', 'Trading',
         _wallet_params({}), _portfolio),
    Tool('bt_trader_balance', "Get the trading wallet's free TAO balance.", 'Trading',
         _wallet_params({}), _trader_balance),
    Tool('bt_buy', 'Buy into a subnet: stake TAO, receive alpha. REAL on-chain trade.', 'Trading',
         _wallet_params(dict(
             netuid={'type': 'integer', 'description': 'Subnet UID to buy', 'required': True},
             amount_tao={'type': 'number', 'description': 'TAO to spend', 'required': True})),
         _buy, mutates=True),
    Tool('bt_sell', 'Sell out of a subnet: unstake alpha back to TAO. REAL on-chain trade.', 'Trading',
         _wallet_params(dict(
             netuid={'type': 'integer', 'description': 'Subnet UID to sell', 'required': True},
             amount_tao={'type': 'number', 'description': 'TAO-equivalent to unstake', 'required': True})),
         _sell, mutates=True),
    Tool('bt_sell_all', 'Liquidate the entire position in one subnet. REAL on-chain trade.', 'Trading',
         _wallet_params(dict(
             netuid={'type': 'integer', 'description': 'Subnet UID to exit', 'required': True})),
         _sell_all, mutates=True),
    Tool('bt_swap', 'Swap stake from one subnet directly into another (same hotkey). REAL on-chain trade.', 'Trading',
         _wallet_params(dict(
             from_netuid={'type': 'integer', 'description': 'Source subnet UID', 'required': True},
             to_netuid={'type': 'integer', 'description': 'Destination subnet UID', 'required': True},
             amount_tao={'type': 'number', 'description': 'TAO-equivalent to move', 'required': True})),
         _swap, mutates=True),
    # --- traders
    Tool('bt_track', 'Start tracking any ss58 coldkey as a trader. The indexer then snapshots its free TAO and every alpha position on an interval, building an equity curve, PnL and an inferred trade tape. Snapshots once immediately.', 'Traders',
         dict(address={'type': 'string', 'description': 'ss58 coldkey to track', 'required': True},
              label={'type': 'string', 'description': 'Human label for this trader'},
              **_net_param()), _track),
    Tool('bt_untrack', 'Stop tracking a coldkey. Its recorded history is kept unless purge=true.', 'Traders',
         dict(address={'type': 'string', 'description': 'ss58 coldkey', 'required': True},
              purge={'type': 'boolean', 'description': 'Also delete stored snapshots and flows', 'default': False}),
         _untrack),
    Tool('bt_traders', 'INSTANT tracked-trader table from the local index: portfolio value, free vs staked, subnet count, 24h/7d change and PnL, recent trade count, and an equity sparkline — no chain round-trip.', 'Traders',
         dict(sort_by={'type': 'string', 'description': "Sort key: 'total_tao', 'change_24h', 'change_7d', 'pnl_24h', 'staked_tao', 'subnets'", 'default': 'total_tao'},
              limit={'type': 'integer', 'description': 'Max rows (0 = all)', 'default': 0},
              sparks={'type': 'boolean', 'description': 'Include equity sparkline', 'default': True}),
         _traders),
    Tool('bt_trader', 'Full profile for one tracked trader: current positions per subnet with TAO value and portfolio weight, equity curve, inferred trades, and windowed PnL.', 'Traders',
         dict(address={'type': 'string', 'description': 'ss58 coldkey', 'required': True},
              hours={'type': 'integer', 'description': 'Equity-curve lookback in hours', 'default': 168},
              flows_limit={'type': 'integer', 'description': 'Max inferred trades to include', 'default': 50}),
         _trader),
    Tool('bt_trader_history', "A tracked trader's portfolio value over time (free / staked / total) — chart-ready.", 'Traders',
         dict(address={'type': 'string', 'description': 'ss58 coldkey', 'required': True},
              hours={'type': 'integer', 'description': 'Lookback window in hours', 'default': 168},
              points={'type': 'integer', 'description': 'Max points (downsampled)', 'default': 300}),
         _trader_history),
    Tool('bt_trader_flows', 'The inferred trade tape: buys/sells derived from how tracked traders\' positions changed between snapshots. Omit address for the tape across every tracked trader.', 'Traders',
         dict(address={'type': 'string', 'description': 'ss58 coldkey (omit for all tracked traders)'},
              hours={'type': 'integer', 'description': 'Lookback window in hours', 'default': 168},
              limit={'type': 'integer', 'description': 'Max rows', 'default': 100}),
         _trader_flows),
    Tool('bt_trader_snapshot', 'Force a snapshot now (chain read) for one tracked trader, or for all of them if address is omitted.', 'Traders',
         dict(address={'type': 'string', 'description': 'ss58 coldkey (omit for all tracked traders)'}),
         _trader_snapshot),
    Tool('bt_trader_at', 'Time machine: what a tracked coldkey held at a past moment, from the local index — the answer that otherwise needs an archive node.', 'Traders',
         dict(address={'type': 'string', 'description': 'ss58 coldkey', 'required': True},
              ts={'type': 'integer', 'description': 'Unix timestamp (default: now)'},
              tolerance_sec={'type': 'integer', 'description': 'Reject the nearest snapshot if it misses ts by more than this (0 = accept any)', 'default': 0}),
         _trader_at),
    Tool('bt_prices_at', 'Every subnet alpha price at a past moment, from the local indexer — historical marks without an archive node.', 'Traders',
         dict(ts={'type': 'integer', 'description': 'Unix timestamp (default: now)'}),
         _prices_at),
    # --- network
    Tool('bt_rpc_health', 'Health/latency stats for the RPC endpoint pool.', 'Network',
         dict(**_net_param()), _rpc_health),
    Tool('bt_best_rpc', 'The lowest-latency RPC endpoint right now.', 'Network',
         dict(**_net_param()), _best_rpc),
]

TOOL_MAP: Dict[str, Tool] = {t.name: t for t in TOOLS}


def list_tools() -> List[Dict]:
    """MCP-shaped tool listing."""
    return [{'name': t.name, 'description': t.description,
             'inputSchema': t.input_schema} for t in TOOLS]


_call_lock = threading.RLock()  # substrate websocket is not thread-safe


def call_tool(name: str, args: Optional[Dict] = None) -> Any:
    tool = TOOL_MAP.get(name)
    if tool is None:
        raise ValueError(f'unknown tool: {name}')
    with _call_lock:
        return tool.call(args)


def docs() -> List[Dict]:
    """Grouped tool docs for the console Docs section."""
    groups: Dict[str, List[Dict]] = {}
    for t in TOOLS:
        groups.setdefault(t.group, []).append({
            'name': t.name, 'description': t.description,
            'mutates': t.mutates,
            'params': [
                {'name': k, **{kk: vv for kk, vv in v.items()}}
                for k, v in t.params.items()],
        })
    return [{'group': g, 'tools': ts} for g, ts in groups.items()]
