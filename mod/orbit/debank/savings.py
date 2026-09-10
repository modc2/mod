"""debank savings — place the savings account into index funds.

The savings account is the keyless rail: USDC/USDT/DAI sitting in the wallet.
An **index fund** here is a curated, weighted basket of yield venues in ONE
asset on ONE chain (funds.json), so placing it is a handful of wallet
signatures — approve + deposit per sleeve — built here, signed in the wallet.

Three kinds of venue, three deposit shapes, all plain ABI on the stdlib:

    erc4626      deposit(assets, receiver)            redeem(shares, to, owner)
    aave_v3      supply(asset, amount, onBehalf, 0)   withdraw(asset, MAX, to)
    compound_v3  supply(asset, amount)                withdraw(asset, MAX)

Every number says where it came from:
  - projected ROI: DefiLlama, live — through the local defi module when it is
    up, straight from yields.llama.fi otherwise, frozen funds.json hints as the
    last resort (marked `apy_source`).
  - liquidity locked: read from the chain right now (totalAssets / totalSupply
    on the venue contract) next to DefiLlama's pool TVL.
  - holdings: balanceOf + convertToAssets via the same public RPCs as the rail,
    so the savings page needs no key at all.

Nothing here signs anything. Plans are transactions for the OWNER's wallet.
"""

import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import client as C
from client import DebankError

HERE = os.path.dirname(os.path.abspath(__file__))
FUNDS_FILE = os.path.join(HERE, 'funds.json')
LEDGER_DIR = os.path.expanduser(os.environ.get('DEBANK_SAVINGS_DIR',
                                               '~/.mod/debank/savings'))
DEFI_URL = os.environ.get('DEBANK_DEFI_URL', 'http://localhost:50500')
LLAMA_CHART = 'https://yields.llama.fi/chart/'
YIELDS_TTL = float(os.environ.get('DEBANK_YIELDS_TTL', 600))
CHAIN_TTL = float(os.environ.get('DEBANK_CHAIN_TTL', 300))

# ── selectors (keccak4, verified against web3.keccak) ──
SEL = {
    'approve': '0x095ea7b3',            # approve(address,uint256)
    'allowance': '0xdd62ed3e',          # allowance(address,address)
    'balance_of': '0x70a08231',         # balanceOf(address)
    'total_supply': '0x18160ddd',       # totalSupply()
    'total_assets': '0x01e1d114',       # totalAssets()
    'convert_to_assets': '0x07a2d13a',  # convertToAssets(uint256)
    'deposit_4626': '0x6e553f65',       # deposit(uint256,address)
    'redeem_4626': '0xba087652',        # redeem(uint256,address,address)
    'supply_aave': '0x617ba037',        # supply(address,uint256,address,uint16)
    'withdraw_aave': '0x69328dec',      # withdraw(address,uint256,address)
    'supply_comet': '0xf2b9fdb8',       # supply(address,uint256)
    'withdraw_comet': '0xf3fef3a3',     # withdraw(address,uint256)
}
MAX_UINT = 'f' * 64


def _pad(value):
    if isinstance(value, str) and value.startswith('0x'):
        return value[2:].lower().rjust(64, '0')
    return hex(int(value))[2:].rjust(64, '0')


def _units(amount, decimals):
    """A float/str USD-ish amount → integer token units, floored."""
    s = f'{float(amount):.{decimals}f}'
    i, _, f = s.partition('.')
    return int(i or '0') * 10 ** decimals + int((f or '').ljust(decimals, '0')[:decimals] or '0')


def _from_units(units, decimals):
    return units / 10 ** decimals


# ── the registry ──

_funds_cache = {'at': 0.0, 'data': None}


def registry():
    m = os.path.getmtime(FUNDS_FILE)
    if _funds_cache['data'] is None or _funds_cache['at'] != m:
        with open(FUNDS_FILE) as f:
            _funds_cache.update(at=m, data=json.load(f))
    return _funds_cache['data']


def _venue(vid):
    v = registry()['venues'].get(vid)
    if not v:
        raise DebankError(f'no such venue: {vid}', status=404,
                          hint='GET /funds lists every venue and fund')
    return {'id': vid, **v}


def _fund(fid):
    for f in registry()['funds']:
        if f['id'] == fid:
            return f
    if fid.startswith('venue:'):
        v = _venue(fid[6:])
        return {'id': fid, 'name': v['name'], 'chain': v['chain'],
                'asset': v['asset']['symbol'], 'tier': 'single',
                'strategy': v['blurb'], 'single': True,
                'sleeves': [{'venue': v['id'], 'weight': 1.0}]}
    raise DebankError(f'no such fund: {fid}', status=404,
                      hint='GET /funds lists them; venue:<id> makes a fund of one')


# ── live yields: defi module → DefiLlama chart → frozen hints ──

_yields_cache = {'at': 0.0, 'rows': {}}
_yields_lock = threading.Lock()


def _http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={
        'accept': 'application/json', 'user-agent': 'mod-debank/0.3'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b'{}')


def _yield_one(vid, venue):
    pool = venue.get('llama_pool')
    try:
        d = _http_json(f'{DEFI_URL}/yields/pool/{pool}', timeout=6)['pool']
        return vid, {'apy': d.get('apy'), 'apy_30d': d.get('apy_mean_30d'),
                     'tvl_usd': d.get('tvl_usd'), 'apy_source': 'defillama via defi'}
    except Exception:
        pass
    try:
        pts = _http_json(LLAMA_CHART + pool).get('data') or []
        last = pts[-1]
        month = [p['apy'] for p in pts[-30:] if p.get('apy') is not None]
        return vid, {'apy': last.get('apy'),
                     'apy_30d': round(sum(month) / len(month), 2) if month else None,
                     'tvl_usd': last.get('tvlUsd'), 'apy_source': 'defillama'}
    except Exception:
        return vid, {'apy': venue.get('apy_hint'), 'apy_30d': venue.get('apy_30d_hint'),
                     'tvl_usd': None, 'apy_source': 'frozen 2026-08-31 — live feed unreachable'}


def live_yields(refresh=False):
    """{venue_id: {apy, apy_30d, tvl_usd, apy_source}} — cached YIELDS_TTL."""
    with _yields_lock:
        if not refresh and _yields_cache['rows'] and \
                time.time() - _yields_cache['at'] < YIELDS_TTL:
            return _yields_cache['rows']
        venues = registry()['venues']
        with ThreadPoolExecutor(max_workers=min(8, len(venues))) as pool:
            rows = dict(pool.map(lambda kv: _yield_one(*kv), venues.items()))
        _yields_cache.update(at=time.time(), rows=rows)
        return rows


# ── on-chain: locked liquidity and holdings, keyless ──

_chain_cache = {'at': 0.0, 'rows': {}}


def _call(to, data):
    return ('eth_call', [{'to': to, 'data': data}, 'latest'])


def _hexint(v):
    try:
        return int(v, 16)
    except (TypeError, ValueError):
        return None


def locked_onchain(refresh=False):
    """Per venue: how much is deposited in the protocol RIGHT NOW, read from
    the venue contract itself (totalAssets for vaults, receipt totalSupply for
    aave/comet — both denominate in the asset), plus the asset sitting idle in
    the contract as `available` where that is meaningful (aave/comet)."""
    if not refresh and _chain_cache['rows'] and \
            time.time() - _chain_cache['at'] < CHAIN_TTL:
        return _chain_cache['rows']
    venues = registry()['venues']
    by_chain = {}
    for vid, v in venues.items():
        by_chain.setdefault(v['chain'], []).append((vid, v))
    rows = {}

    def one(chain):
        items = by_chain[chain]
        calls, keys = [], []
        for vid, v in items:
            if v['kind'] == 'erc4626':
                calls.append(_call(v['address'], SEL['total_assets']))
                keys.append((vid, 'locked', v['asset']['decimals']))
            else:
                calls.append(_call(v['receipt']['address'], SEL['total_supply']))
                keys.append((vid, 'locked', v['receipt']['decimals']))
                calls.append(_call(v['asset']['address'],
                                   SEL['balance_of'] + _pad(v['kind'] == 'aave_v3'
                                                            and v['receipt']['address']
                                                            or v['address'])))
                keys.append((vid, 'available', v['asset']['decimals']))
        try:
            results = C.rpc(C.NETWORKS[chain]['rpc'], calls)
        except Exception:
            return
        for (vid, field, dec), res in zip(keys, results):
            n = _hexint(res)
            if n is not None:
                rows.setdefault(vid, {})[field + '_usd'] = round(_from_units(n, dec), 2)

    with ThreadPoolExecutor(max_workers=len(by_chain) or 1) as pool:
        list(pool.map(one, by_chain))
    if rows:
        _chain_cache.update(at=time.time(), rows=rows)
    return rows or _chain_cache['rows']


def holdings(id):
    """What this address already holds in every venue, in asset terms —
    balanceOf per receipt, pushed through convertToAssets for share vaults.
    Keyless, straight from the public RPCs."""
    addr = C._addr(id)
    venues = registry()['venues']
    by_chain = {}
    for vid, v in venues.items():
        by_chain.setdefault(v['chain'], []).append((vid, v))
    held, errors = {}, {}

    def one(chain):
        items = by_chain[chain]
        rpc_url = C.NETWORKS[chain]['rpc']
        try:
            bals = C.rpc(rpc_url, [
                _call(v['receipt']['address'], SEL['balance_of'] + _pad(addr))
                for _, v in items])
            conv = C.rpc(rpc_url, [
                _call(v['address'], SEL['convert_to_assets'] + _pad(b))
                if v['kind'] == 'erc4626' and _hexint(b) else ('eth_chainId', [])
                for (_, v), b in zip(items, bals)])
        except Exception as e:
            errors[chain] = f'{type(e).__name__}: {e}'
            return
        for (vid, v), bal, cv in zip(items, bals, conv):
            shares = _hexint(bal) or 0
            if not shares:
                continue
            if v['kind'] == 'erc4626':
                assets = _hexint(cv) or 0
                amount = _from_units(assets, v['asset']['decimals'])
            else:
                amount = _from_units(shares, v['receipt']['decimals'])
            held[vid] = {'shares': shares, 'amount': round(amount, 6),
                         'symbol': v['asset']['symbol']}

    with ThreadPoolExecutor(max_workers=len(by_chain) or 1) as pool:
        list(pool.map(one, by_chain))
    return {'held': held, 'errors': errors or None}


# ── the public answers ──

def _sleeve_row(vid, weight, ys, locked, amount=None):
    v = _venue(vid)
    y = ys.get(vid, {})
    lk = locked.get(vid, {})
    row = {'venue': vid, 'protocol': v['protocol'], 'name': v['name'],
           'chain': v['chain'], 'asset': v['asset']['symbol'], 'weight': weight,
           'kind': v['kind'], 'address': v['address'],
           'receipt': v['receipt']['symbol'], 'since': v['since'],
           'apy': y.get('apy'), 'apy_30d': y.get('apy_30d'),
           'apy_source': y.get('apy_source'),
           'liquidity': {'locked_usd': lk.get('locked_usd'),
                         'available_usd': lk.get('available_usd'),
                         'pool_tvl_usd': y.get('tvl_usd'),
                         'source': 'locked read from the venue contract on chain; '
                                   'pool TVL from DefiLlama'},
           'exit': v['exit'], 'blurb': v['blurb']}
    if amount is not None:
        slice_ = round(amount * weight, 2)
        row['allocation_usd'] = slice_
        row['projected_1y_usd'] = _proj(slice_, row['apy_30d'], row['apy'])
    return row


def _proj(amount, apy_30d, apy):
    rate = apy_30d if apy_30d is not None else apy
    return round(amount * rate / 100, 2) if rate is not None else None


def _fund_row(f, ys, locked, amount=None):
    sleeves = [_sleeve_row(s['venue'], s['weight'], ys, locked, amount)
               for s in f['sleeves']]
    def wavg(key):
        pairs = [(s['weight'], s[key]) for s in sleeves if s[key] is not None]
        return round(sum(w * x for w, x in pairs) / sum(w for w, _ in pairs), 2) \
            if pairs else None
    worst = max(sleeves, key=lambda s: s['exit'].get('delay_days', 0))
    out = {'id': f['id'], 'name': f['name'], 'chain': f['chain'],
           'chain_name': C.NETWORKS[f['chain']]['name'], 'asset': f['asset'],
           'tier': f.get('tier'), 'strategy': f.get('strategy'),
           'sleeves': sleeves,
           'projected_apy': wavg('apy_30d') if any(s['apy_30d'] is not None for s in sleeves) else wavg('apy'),
           'current_apy': wavg('apy'),
           'projection_basis': '30-day mean APY per sleeve, weighted; the current '
                               'APY is today\'s spot rate. Neither is a promise.',
           'liquidity_locked_usd': round(sum(s['liquidity']['locked_usd'] or 0
                                             for s in sleeves), 2),
           'exit': {'kind': worst['exit']['kind'],
                    'delay_days': worst['exit'].get('delay_days', 0),
                    'note': 'slowest sleeve: ' + worst['exit']['note']}}
    if amount is not None:
        out['amount_usd'] = round(float(amount), 2)
        out['projected_1y_usd'] = round(sum(s['projected_1y_usd'] or 0
                                            for s in sleeves), 2)
    return out


def funds(amount=None, refresh=False):
    """Every index fund, with live projected ROI and the liquidity locked in
    each protocol. Pass amount= to see the dollar projection at that size."""
    amount = float(amount) if amount not in (None, '') else None
    ys, locked = live_yields(refresh), locked_onchain(refresh)
    rows = [_fund_row(f, ys, locked, amount) for f in registry()['funds']]
    venues = [_sleeve_row(vid, 1.0, ys, locked, amount)
              for vid in registry()['venues']]
    return {'funds': rows, 'count': len(rows),
            'venues': venues,
            'note': registry().get('note'),
            'single': 'any venue is enterable alone as fund id venue:<id>'}


def fund(fid, amount=None, refresh=False):
    """One fund in full — sleeves, live APYs, on-chain locked liquidity, exit
    terms, and the projection at amount= dollars."""
    amount = float(amount) if amount not in (None, '') else None
    return _fund_row(_fund(fid), live_yields(refresh), locked_onchain(refresh),
                     amount)


def savings(id):
    """The savings picture for one address: what sits idle on the rail, what is
    already placed in each venue (keyless, from chain), what it earns."""
    addr = C._addr(id)
    ys, locked = live_yields(False), locked_onchain(False)
    rail = C.balances(addr)
    stables = [t for t in rail['tokens'] if not t['native'] and t['amount'] > 0]
    idle = round(sum(t['usd'] for t in stables), 2)
    h = holdings(addr)
    rows = []
    for vid, held in h['held'].items():
        if held['amount'] < 0.01:       # dust shares that round to nothing
            continue
        row = _sleeve_row(vid, 1.0, ys, locked)
        row.update(amount=held['amount'],
                   held_usd=round(held['amount'], 2),   # stables ≈ $1
                   projected_1y_usd=_proj(held['amount'], row['apy_30d'], row['apy']))
        rows.append(row)
    rows.sort(key=lambda r: r['held_usd'], reverse=True)
    placed = round(sum(r['held_usd'] for r in rows), 2)
    total_proj = round(sum(r['projected_1y_usd'] or 0 for r in rows), 2)
    return {'id': addr,
            'idle': {'usd': idle, 'tokens': stables,
                     'note': 'stablecoins sitting in the wallet on the bank rail — '
                             'the savings account'},
            'placed': {'usd': placed, 'venues': rows,
                       'blended_apy': round(100 * total_proj / placed, 2) if placed else None,
                       'projected_1y_usd': total_proj},
            'total_usd': round(idle + placed, 2),
            'ledger': ledger(addr),
            'errors': h['errors'],
            'source': 'rpc — holdings and locked liquidity read from chain, '
                      'no key needed'}


# ── the plan: transactions for the owner's wallet ──

def plan(id, fund_id, amount):
    """Split amount across the fund's sleeves and build the exact transactions
    the wallet must sign: an approve (exact units, reset first for USDT-style
    tokens) then the deposit, per sleeve. Nothing is signed here."""
    addr = C._addr(id)
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise DebankError('amount must be a number of dollars', status=400)
    if amount <= 0:
        raise DebankError('amount must be above zero', status=400)
    f = _fund(fund_id)
    ys, locked = live_yields(False), locked_onchain(False)
    row = _fund_row(f, ys, locked, amount)
    chain = f['chain']
    net = C.NETWORKS[chain]

    # what the wallet actually holds of the fund's asset on that chain
    have = None
    try:
        bal = C.balances(addr, chains=[chain])
        t = next((t for t in bal['tokens'] if t['symbol'] == f['asset']), None)
        have = t['amount'] if t else 0.0
    except Exception:
        pass

    # current allowances, so USDT-style tokens get their reset leg only when needed
    allowances = {}
    try:
        res = C.rpc(net['rpc'], [
            _call(_venue(s['venue'])['asset']['address'],
                  SEL['allowance'] + _pad(addr) + _pad(_venue(s['venue'])['address']))
            for s in f['sleeves']])
        allowances = {s['venue']: _hexint(r) or 0
                      for s, r in zip(f['sleeves'], res)}
    except Exception:
        pass

    legs = []
    for s in row['sleeves']:
        v = _venue(s['venue'])
        dec = v['asset']['decimals']
        units = _units(amount * s['weight'], dec)
        spender, txs = v['address'], []
        current = allowances.get(s['venue'])
        if current and current < units and v['asset']['symbol'] == 'USDT':
            txs.append({'step': 'reset approval (USDT requires going through zero)',
                        'to': v['asset']['address'],
                        'data': SEL['approve'] + _pad(spender) + _pad(0)})
        if current is None or current < units:
            txs.append({'step': f"approve {v['name']} to take "
                                f"{_from_units(units, dec)} {v['asset']['symbol']}",
                        'to': v['asset']['address'],
                        'data': SEL['approve'] + _pad(spender) + _pad(units)})
        if v['kind'] == 'erc4626':
            data = SEL['deposit_4626'] + _pad(units) + _pad(addr)
        elif v['kind'] == 'aave_v3':
            data = SEL['supply_aave'] + _pad(v['asset']['address']) + _pad(units) \
                + _pad(addr) + _pad(0)
        else:
            data = SEL['supply_comet'] + _pad(v['asset']['address']) + _pad(units)
        txs.append({'step': f"deposit {_from_units(units, dec)} "
                            f"{v['asset']['symbol']} into {v['name']}",
                    'to': v['address'], 'data': data})
        legs.append({**s, 'units': str(units), 'txs': txs})

    short = have is not None and have + 1e-9 < amount
    return {'id': addr, 'fund': {k: row[k] for k in
                                 ('id', 'name', 'chain', 'chain_name', 'asset', 'tier',
                                  'projected_apy', 'current_apy', 'projected_1y_usd',
                                  'liquidity_locked_usd', 'exit', 'projection_basis')},
            'amount_usd': round(amount, 2),
            'wallet_has': have, 'funded': not short,
            'shortfall': round(amount - have, 2) if short else None,
            'chain': chain, 'chain_id': net['chain_id'],
            'legs': legs,
            'signatures': sum(len(leg['txs']) for leg in legs),
            'note': 'every tx here is for the OWNER\'s wallet to sign — approve is '
                    'exact-amount, never unlimited; this server signs nothing'}


def exit_tx(vid, addr, shares=None):
    """The withdraw-everything transaction for one venue: redeem all shares
    (ERC-4626) or withdraw MAX (aave/comet withdraw the full balance)."""
    v = _venue(vid)
    addr = C._addr(addr)
    if v['kind'] == 'erc4626':
        if not shares:
            try:
                shares = _hexint(C.rpc(C.NETWORKS[v['chain']]['rpc'], [
                    _call(v['receipt']['address'], SEL['balance_of'] + _pad(addr))])[0])
            except Exception:
                shares = None
        if not shares:
            raise DebankError(f'nothing held in {vid}', status=400)
        data = SEL['redeem_4626'] + _pad(int(shares)) + _pad(addr) + _pad(addr)
    elif v['kind'] == 'aave_v3':
        data = SEL['withdraw_aave'] + _pad(v['asset']['address']) + MAX_UINT + _pad(addr)
    else:
        data = SEL['withdraw_comet'] + _pad(v['asset']['address']) + MAX_UINT
    return {'venue': vid, 'chain': v['chain'],
            'chain_id': C.NETWORKS[v['chain']]['chain_id'],
            'tx': {'to': v['address'], 'data': data},
            'note': 'withdraws the full position back to the wallet; '
                    'signed by the owner, never here'}


# ── the ledger: what was placed from here (off-chain, off-tree) ──

_ledger_lock = threading.Lock()


def ledger(addr):
    try:
        with open(os.path.join(LEDGER_DIR, addr.lower() + '.json')) as f:
            return json.load(f)
    except Exception:
        return []


def record(id, fund_id, venue, amount, tx, chain=None):
    """Append one placed leg to ~/.mod/debank/savings/<addr>.json (0600)."""
    addr = C._addr(id)
    entry = {'time': int(time.time()), 'fund': fund_id, 'venue': venue,
             'amount': float(amount), 'tx': tx,
             'chain': chain or _venue(venue)['chain']}
    os.makedirs(LEDGER_DIR, exist_ok=True)
    path = os.path.join(LEDGER_DIR, addr.lower() + '.json')
    with _ledger_lock:      # concurrent legs record in parallel; lose none
        rows = ledger(addr)
        rows.append(entry)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(rows, f, indent=2)
    return {'recorded': entry, 'count': len(rows), 'stored': path}
