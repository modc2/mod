#!/usr/bin/env python3
"""debank client — the DeBank Cloud API, normalized.

DeBank answers one question better than anything else: *what does this address
actually own, across every chain, right now* — tokens, LP positions, debt,
staked and locked balances, NFTs, and the approvals that could take it all away.
The raw API returns that as deeply nested per-protocol structures with amounts
and prices in separate places; this client multiplies them out, filters the
dust, and returns flat rows with a USD number on each.

BYOK. Every call spends the CALLER'S DeBank units — this module holds no house
key. Resolution order:

    explicit key argument
    → DEBANK_ACCESS_KEY / DEBANK_API_KEY in the environment
    → ~/.mod/debank/key.json (0600, off-tree)

Python stdlib only.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://pro-openapi.debank.com'
PUBLIC_BASE = 'https://api.debank.com'          # keyless, catalog reads only
KEY_DIR = os.path.expanduser('~/.mod/debank')
KEY_FILE = os.path.join(KEY_DIR, 'key.json')
TIMEOUT = float(os.environ.get('DEBANK_TIMEOUT', 30))

# DeBank's own chain ids are short and not always guessable. Accept the names
# people actually type and translate; unknown values pass through untouched so a
# new chain works the day DeBank adds it.
CHAIN_ALIASES = {
    'ethereum': 'eth', 'mainnet': 'eth', 'erc20': 'eth',
    'binance': 'bsc', 'bnb': 'bsc', 'bnbchain': 'bsc', 'bep20': 'bsc',
    'polygon': 'matic', 'poly': 'matic',
    'gnosis': 'xdai', 'dai': 'xdai',
    'avalanche': 'avax',
    'fantom': 'ftm',
    'optimism': 'op',
    'arbitrum': 'arb', 'arbitrum-one': 'arb',
    'zksync': 'era', 'zksync-era': 'era',
    'scroll': 'scrl',
    'mantle': 'mnt',
    'polygon-zkevm': 'pze',
    'cronos': 'cro',
    'moonbeam': 'mobm',
    'harmony': 'hmy',
    'metis': 'metis',
}

# ── the bank rail: chains a browser wallet can be pointed at ──
#
# DeBank is the full picture, but it needs a key. These are the chains a
# browser wallet actually switches to, each with a public RPC that answers
# balance reads keyless. Native coin + the major stablecoins, priced by
# CoinGecko's free tier, is the floor the bank stands on when no AccessKey is
# present — real numbers, not an empty page.
NETWORKS = {
    'eth': {'name': 'Ethereum', 'chain_id': 1, 'native': 'ETH', 'coingecko': 'ethereum',
            'rpc': 'https://ethereum-rpc.publicnode.com', 'explorer': 'https://etherscan.io',
            'tokens': {'USDC': ('0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', 6),
                       'USDT': ('0xdAC17F958D2ee523a2206206994597C13D831ec7', 6),
                       'DAI': ('0x6B175474E89094C44Da98b954EedeAC495271d0F', 18)}},
    'base': {'name': 'Base', 'chain_id': 8453, 'native': 'ETH', 'coingecko': 'ethereum',
             'rpc': 'https://base-rpc.publicnode.com', 'explorer': 'https://basescan.org',
             'tokens': {'USDC': ('0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', 6),
                        'DAI': ('0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb', 18)}},
    'arb': {'name': 'Arbitrum', 'chain_id': 42161, 'native': 'ETH', 'coingecko': 'ethereum',
            'rpc': 'https://arbitrum-one-rpc.publicnode.com', 'explorer': 'https://arbiscan.io',
            'tokens': {'USDC': ('0xaf88d065e77c8cC2239327C5EDb3A432268e5831', 6),
                       'USDT': ('0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9', 6),
                       'DAI': ('0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1', 18)}},
    'op': {'name': 'Optimism', 'chain_id': 10, 'native': 'ETH', 'coingecko': 'ethereum',
           'rpc': 'https://optimism-rpc.publicnode.com',
           'explorer': 'https://optimistic.etherscan.io',
           'tokens': {'USDC': ('0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85', 6),
                      'USDT': ('0x94b008aA00579c1307B0EF2c499aD98a8ce58e58', 6),
                      'DAI': ('0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1', 18)}},
    'matic': {'name': 'Polygon', 'chain_id': 137, 'native': 'POL', 'coingecko': 'matic-network',
              'rpc': 'https://polygon-bor-rpc.publicnode.com',
              'explorer': 'https://polygonscan.com',
              'tokens': {'USDC': ('0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359', 6),
                         'USDT': ('0xc2132D05D31c914a87C6611C10748AEb04B58e8F', 6),
                         'DAI': ('0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063', 18)}},
    'bsc': {'name': 'BNB Chain', 'chain_id': 56, 'native': 'BNB', 'coingecko': 'binancecoin',
            'rpc': 'https://bsc-rpc.publicnode.com', 'explorer': 'https://bscscan.com',
            'tokens': {'USDC': ('0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d', 18),
                       'USDT': ('0x55d398326f99059fF775485246999027B3197955', 18),
                       'DAI': ('0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3', 18)}},
    'avax': {'name': 'Avalanche', 'chain_id': 43114, 'native': 'AVAX', 'coingecko': 'avalanche-2',
             'rpc': 'https://avalanche-c-chain-rpc.publicnode.com',
             'explorer': 'https://snowtrace.io',
             'tokens': {'USDC': ('0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E', 6),
                        'USDT': ('0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7', 6),
                        'DAI': ('0xd586E7F844cEa2F87f50152665BCbc2C279D8d70', 18)}},
    'xdai': {'name': 'Gnosis', 'chain_id': 100, 'native': 'xDAI', 'coingecko': 'xdai',
             'rpc': 'https://gnosis-rpc.publicnode.com', 'explorer': 'https://gnosisscan.io',
             'tokens': {'USDC': ('0xDDAfbb505ad214D7b80b1f830fcCc89B60fb7A83', 6),
                        'USDT': ('0x4ECaBa5870353805a9F068101A40E0f32ed605C6', 6)}},
}
STABLE_IDS = {'USDC': 'usd-coin', 'USDT': 'tether', 'DAI': 'dai'}
PRICE_URL = 'https://api.coingecko.com/api/v3/simple/price'
RPC_TIMEOUT = float(os.environ.get('DEBANK_RPC_TIMEOUT', 12))

# Categories DeBank tags history rows with, in plain words.
CATES = {
    'send': 'send', 'receive': 'receive', 'approve': 'approve',
    'cancel': 'cancel', 'swap': 'swap', 'contract': 'contract',
}


class DebankError(Exception):
    """A failure worth showing the caller, with a hint about the fix."""

    def __init__(self, message, status=400, hint=None):
        super().__init__(message)
        self.status = status
        self.hint = hint

    def dict(self):
        out = {'error': str(self), 'status': self.status}
        if self.hint:
            out['hint'] = self.hint
        return out


# ── keys ──

def _keystore():
    try:
        with open(KEY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_key(key=None):
    """The caller's key, or None. Never logged, never returned by any route."""
    return (key
            or os.environ.get('DEBANK_ACCESS_KEY')
            or os.environ.get('DEBANK_API_KEY')
            or _keystore().get('access_key')
            or None)


def set_key(key, persist=True):
    """Store an AccessKey off-tree at 0600. Returns state, never the key."""
    key = (key or '').strip()
    if not key:
        raise DebankError('key is required', status=400)
    if persist:
        os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
        with open(KEY_FILE, 'w') as f:
            json.dump({'access_key': key}, f)
        os.chmod(KEY_FILE, 0o600)
    else:
        os.environ['DEBANK_ACCESS_KEY'] = key
    return {'ok': True, 'stored': KEY_FILE if persist else 'process environment',
            'key': _mask(key)}


def _mask(key):
    if not key:
        return None
    return f'{key[:4]}…{key[-4:]}' if len(key) > 10 else '…'


# ── helpers ──

def chain_id(chain):
    """`ethereum`, `Polygon`, `arb` → the id DeBank knows."""
    if not chain:
        return None
    c = str(chain).strip().lower()
    return CHAIN_ALIASES.get(c, c)


def _addr(value, name='id'):
    """DeBank keys everything on a lowercase 0x address."""
    v = str(value or '').strip().lower()
    if not v:
        raise DebankError(f'{name} is required — an EVM address (0x…)', status=400)
    if not (v.startswith('0x') and len(v) == 42):
        raise DebankError(f'{name} must be a 0x-prefixed 40-hex-character EVM address, '
                          f'got {value!r}', status=400,
                          hint='DeBank indexes addresses, not ENS names — resolve the '
                               'name first')
    return v


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _usd(token):
    """A token row's USD value: DeBank ships amount and price separately."""
    return _f(token.get('amount')) * _f(token.get('price'))


def _token_row(t, chain=None):
    amount, price = _f(t.get('amount')), _f(t.get('price'))
    return {
        'chain': t.get('chain') or chain,
        'symbol': t.get('optimized_symbol') or t.get('symbol') or t.get('display_symbol'),
        'name': t.get('name'),
        'amount': amount,
        'price': price,
        'usd': round(amount * price, 2),
        'token_id': t.get('id'),
        'verified': t.get('is_verified'),
    }


def _rank(rows, min_usd, limit, key='usd'):
    """Biggest first, dust dropped, and always say what was dropped."""
    kept = [r for r in rows if r.get(key, 0) >= min_usd]
    kept.sort(key=lambda r: r.get(key, 0), reverse=True)
    hidden = len(rows) - len(kept)
    out = kept[:limit] if limit else kept
    return out, {'shown': len(out), 'matched': len(kept), 'total': len(rows),
                 'hidden_below_min_usd': hidden, 'min_usd': min_usd}


# ── keyless reads ──

_price_cache = {'at': 0, 'prices': {}}


def prices(refresh=False):
    """USD prices for every native coin and stablecoin on the bank rail.

    CoinGecko's free tier, cached 60s. A failure returns the last good answer
    (or pegs the stables at 1.0) rather than blanking the whole page — a bank
    that shows amounts without prices beats one that shows nothing.
    """
    cache = _price_cache
    if not refresh and cache['prices'] and time.time() - cache['at'] < 60:
        return cache['prices']
    ids = sorted({n['coingecko'] for n in NETWORKS.values()} | set(STABLE_IDS.values()))
    try:
        req = urllib.request.Request(
            f'{PRICE_URL}?ids={",".join(ids)}&vs_currencies=usd',
            headers={'accept': 'application/json', 'user-agent': 'mod-debank/0.1'})
        with urllib.request.urlopen(req, timeout=RPC_TIMEOUT) as r:
            got = json.loads(r.read() or b'{}')
        out = {k: _f((v or {}).get('usd')) for k, v in got.items()}
        if out:
            cache.update(at=time.time(), prices=out)
    except Exception:
        pass
    out = dict(cache['prices'])
    for sid in STABLE_IDS.values():
        out.setdefault(sid, 1.0)
    return out


def rpc(url, calls):
    """One batched JSON-RPC POST. `calls` is [(method, params), ...]; returns
    the results in order, None where a call errored."""
    body = [{'jsonrpc': '2.0', 'id': i, 'method': m, 'params': p}
            for i, (m, p) in enumerate(calls)]
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        'content-type': 'application/json', 'accept': 'application/json',
        'user-agent': 'mod-debank/0.1'})
    with urllib.request.urlopen(req, timeout=RPC_TIMEOUT) as r:
        answers = json.loads(r.read() or b'[]')
    if isinstance(answers, dict):
        answers = [answers]
    by_id = {a.get('id'): a.get('result') for a in answers if isinstance(a, dict)}
    return [by_id.get(i) for i in range(len(calls))]


def _hex_amount(value, decimals):
    if not value or not isinstance(value, str):
        return 0.0
    try:
        return int(value, 16) / (10 ** decimals)
    except ValueError:
        return 0.0


def _balance_of(addr):
    # balanceOf(address) — selector 0x70a08231, one 32-byte padded argument.
    return '0x70a08231' + addr[2:].lower().rjust(64, '0')


def chain_balances(chain, addr, px):
    """Native coin plus the stablecoins on one bank-rail chain, via its public RPC."""
    net = NETWORKS[chain]
    tokens = list(net['tokens'].items())
    calls = [('eth_getBalance', [addr, 'latest'])] + [
        ('eth_call', [{'to': contract, 'data': _balance_of(addr)}, 'latest'])
        for _, (contract, _) in tokens]
    results = rpc(net['rpc'], calls)
    rows = []
    native_amt = _hex_amount(results[0], 18)
    price = px.get(net['coingecko'], 0.0)
    rows.append({'chain': chain, 'symbol': net['native'], 'name': net['name'] + ' native',
                 'amount': native_amt, 'price': price, 'usd': round(native_amt * price, 2),
                 'token_id': chain, 'verified': True, 'native': True, 'decimals': 18})
    for (sym, (contract, dec)), res in zip(tokens, results[1:]):
        amt = _hex_amount(res, dec)
        p = px.get(STABLE_IDS.get(sym, ''), 1.0)
        rows.append({'chain': chain, 'symbol': sym, 'name': sym + ' on ' + net['name'],
                     'amount': amt, 'price': p, 'usd': round(amt * p, 2),
                     'token_id': contract.lower(), 'verified': True, 'native': False,
                     'decimals': dec})
    return rows


def networks():
    """The bank rail, in the shape a browser wallet needs: hex chain ids, RPCs,
    explorers, and the stablecoin contracts with their decimals."""
    return {'count': len(NETWORKS), 'networks': [
        {'chain': k, 'name': n['name'], 'chain_id': n['chain_id'],
         'chain_id_hex': hex(n['chain_id']), 'native': n['native'], 'decimals': 18,
         'rpc': n['rpc'], 'explorer': n['explorer'],
         'tokens': [{'symbol': s, 'address': a, 'decimals': d}
                    for s, (a, d) in n['tokens'].items()]}
        for k, n in NETWORKS.items()]}


def balances(id, chains=None, min_usd=0.0):
    """What an address holds on the bank rail, with no key at all.

    Native coin and the major stablecoins on every supported chain, read in
    parallel from public RPCs and priced by CoinGecko. It is deliberately
    narrow — no LP tokens, no DeFi, no long tail — so it can be honest: the
    `source` says `rpc`, and `coverage` says exactly what was looked at.
    """
    addr = _addr(id)
    wanted = [chain_id(c) for c in chains] if chains else list(NETWORKS)
    unknown = [c for c in wanted if c not in NETWORKS]
    if unknown:
        raise DebankError(f'not on the bank rail: {", ".join(unknown)}', status=400,
                          hint=f'keyless balances cover {", ".join(NETWORKS)} — '
                               'use debank_tokens with an AccessKey for the rest')
    px = prices()
    from concurrent.futures import ThreadPoolExecutor
    errors, per_chain = {}, {}

    def one(chain):
        try:
            per_chain[chain] = chain_balances(chain, addr, px)
        except Exception as e:
            errors[chain] = f'{type(e).__name__}: {e}'

    with ThreadPoolExecutor(max_workers=min(8, len(wanted)) or 1) as pool:
        list(pool.map(one, wanted))

    rows = [r for c in wanted for r in per_chain.get(c, [])]
    chain_rows = []
    for c in wanted:
        if c not in per_chain:
            continue
        usd = round(sum(r['usd'] for r in per_chain[c]), 2)
        chain_rows.append({'chain': c, 'name': NETWORKS[c]['name'],
                           'chain_id': NETWORKS[c]['chain_id'],
                           'native': NETWORKS[c]['native'],
                           'native_amount': per_chain[c][0]['amount'],
                           'usd': usd, 'tokens': per_chain[c]})
    chain_rows.sort(key=lambda r: r['usd'], reverse=True)
    ranked, meta = _rank(rows, min_usd, 0)
    return {'id': addr, 'total_usd': round(sum(r['usd'] for r in rows), 2),
            'chains': chain_rows, 'tokens': ranked, **meta,
            'source': 'rpc', 'priced_by': 'coingecko' if _price_cache['prices'] else 'peg',
            'coverage': 'native coin + USDC/USDT/DAI on ' + ', '.join(wanted),
            'errors': errors or None,
            'note': 'keyless — an AccessKey adds every other token, DeFi, NFTs, '
                    'history and approvals'}


# ── proof of humanity: the tag on the id ──
#
# The bank's account id is a bare address; nothing about it says a person is
# behind it. These registries do — each one an on-chain record that a human
# (Kleros-vouched video + deposit, or a KYC'd Coinbase account) controls the
# address. All of them sit on rail chains, so the check is keyless: the same
# public RPCs, one eth_call each. Selectors are hardcoded like _balance_of —
# the stdlib has no keccak. Verified live 2026-09-04 against each contract.
HUMANITY_REGISTRIES = [
    {'source': 'poh-v2', 'name': 'Proof of Humanity v2', 'chain': 'eth',
     'contract': '0xbE9834097A4E97689d9B667441acafb456D0480A',
     'selector': '0xf72c436f', 'kind': 'bool',       # isHuman(address)
     'register': 'https://v2.proofofhumanity.id'},
    {'source': 'poh-v2-gnosis', 'name': 'Proof of Humanity v2 (Gnosis)',
     'chain': 'xdai',
     'contract': '0xa4AC94C4fa65Bb352eFa30e3408e64F72aC857bc',
     'selector': '0xf72c436f', 'kind': 'bool',       # isHuman(address)
     'register': 'https://v2.proofofhumanity.id'},
    {'source': 'poh-v1', 'name': 'Proof of Humanity v1', 'chain': 'eth',
     'contract': '0xC5E9dDebb09Cd64DfaCab4011A0D5cEDaf7c9BDb',
     'selector': '0xc3c5a547', 'kind': 'bool',       # isRegistered(address)
     'register': 'https://app.proofofhumanity.id'},
    {'source': 'coinbase', 'name': 'Coinbase Verified Account', 'chain': 'base',
     'contract': '0x2c7eE1E5f416dfF40054c27A62f7B357C4E8619C',
     'selector': '0xab2717dd', 'kind': 'uid',        # getAttestationUid(address,bytes32)
     'suffix': 'f8b05c79f090979bf4a80270aba232dff11a10d9ca55c4f88de95317970f0de9',
     'register': 'https://www.coinbase.com/onchain-verify'},
]

TAG_SCHEME = 'debank.humanity.v1'


def _humanity_tag(addr, evidence):
    """The tag itself: SHA3-256 over the canonical evidence string.

    The registries bind humanity to the address with today's signatures
    (ECDSA), which a large quantum computer would eventually forge. The tag
    doesn't try to outlive that with more signatures — it commits the *state*
    (which registry said yes, in which contract, at which block) under a
    hash that keeps ~128-bit security against Grover. Archive the tag and the
    basis string anywhere durable and the claim "this id was human-verified
    at that block" stays checkable, and unforgeable, after ECDSA falls.
    """
    import hashlib
    basis = TAG_SCHEME + '|' + addr + '|' + ';'.join(
        f"{e['source']}:{e['chain']}:{e['contract'].lower()}:"
        f"{e.get('block') if e.get('block') is not None else '?'}:{int(bool(e['verified']))}"
        for e in evidence)
    return {'scheme': 'sha3-256', 'basis': basis,
            'value': hashlib.sha3_256(basis.encode()).hexdigest()}


def humanity(id):
    """Is a human behind this id? Read from on-chain registries, keyless.

    One batched eth_call per chain, in parallel, exactly like balances().
    A registry that can't be reached is reported in errors and counted as
    unverified — the tag only ever asserts what was actually read.
    """
    addr = _addr(id)
    by_chain = {}
    for reg in HUMANITY_REGISTRIES:
        by_chain.setdefault(reg['chain'], []).append(reg)

    results, errors = {}, {}

    def one(chain, regs):
        calls = [('eth_blockNumber', [])] + [
            ('eth_call', [{'to': r['contract'],
                           'data': r['selector'] + addr[2:].rjust(64, '0')
                                   + r.get('suffix', '')}, 'latest'])
            for r in regs]
        try:
            res = rpc(NETWORKS[chain]['rpc'], calls)
            block = int(res[0], 16) if res[0] else None
            for r, v in zip(regs, res[1:]):
                results[r['source']] = (block, v)
        except Exception as e:
            errors[chain] = f'{type(e).__name__}: {e}'

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(by_chain)) as pool:
        list(pool.map(lambda kv: one(*kv), by_chain.items()))

    sources = []
    for reg in HUMANITY_REGISTRIES:
        block, raw = results.get(reg['source'], (None, None))
        word = (raw or '')[2:].rjust(64, '0')
        verified = bool(raw) and int(word or '0', 16) != 0
        sources.append({'source': reg['source'], 'name': reg['name'],
                        'chain': reg['chain'], 'contract': reg['contract'],
                        'verified': verified, 'block': block,
                        'result': raw if verified else None,
                        'register': reg['register']})
    human = any(s['verified'] for s in sources)
    return {'id': addr, 'human': human,
            'verified_by': [s['name'] for s in sources if s['verified']] or None,
            'tag': _humanity_tag(addr, sources),
            'sources': sources, 'errors': errors or None,
            'source': 'rpc', 'checked_at': int(time.time()),
            'note': 'keyless — on-chain humanity registries read straight from '
                    'public RPCs; nothing here identifies the person, only that '
                    'a registry accepted one',
            'pq': 'the tag is a SHA3-256 commitment to the evidence (hash-based, '
                  'quantum-resistant); keep tag.basis and tag.value and the claim '
                  'can be re-verified even if ECDSA signatures stop being proof'}


class Client:
    """One DeBank account's view of the chains. Stateless apart from the key."""

    def __init__(self, key=None):
        self._key = key

    # ── transport ──

    @property
    def key(self):
        return resolve_key(self._key)

    def key_state(self):
        k = self.key
        return {'key': _mask(k), 'present': bool(k),
                'source': self._source(), 'upstream': BASE}

    def _source(self):
        if self._key:
            return 'request'
        if os.environ.get('DEBANK_ACCESS_KEY') or os.environ.get('DEBANK_API_KEY'):
            return 'environment'
        if _keystore().get('access_key'):
            return KEY_FILE
        return None

    def get(self, path, _public=False, _retries=2, **params):
        """One GET against DeBank. 429 is retried; everything else is reported."""
        key = self.key
        if not (key or _public):
            raise DebankError(
                'no DeBank AccessKey — this module is BYOK and holds no house key',
                status=401,
                hint='set one with `m debank/set_key <key>`, the x-debank-key header, '
                     'or DEBANK_ACCESS_KEY. Keys come from cloud.debank.com.')
        clean = {k: v for k, v in params.items() if v not in (None, '')}
        url = (PUBLIC_BASE if _public else BASE) + path
        if clean:
            url += '?' + urllib.parse.urlencode(clean, doseq=True)
        req = urllib.request.Request(url, headers={
            'accept': 'application/json',
            'user-agent': 'mod-debank/0.1',
            **({'AccessKey': key} if key and not _public else {}),
        })
        for attempt in range(_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    return json.loads(r.read() or b'null')
            except urllib.error.HTTPError as e:
                body = (e.read() or b'').decode('utf-8', 'replace')[:400]
                if e.code in (429, 502, 503, 504) and attempt < _retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise DebankError(f'DeBank {e.code} on {path}: {body or e.reason}',
                                  status=e.code, hint=self._hint(e.code)) from None
            except urllib.error.URLError as e:
                if attempt < _retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise DebankError(f'cannot reach DeBank: {e.reason}', status=502) from None
            except json.JSONDecodeError:
                raise DebankError(f'DeBank returned non-JSON on {path}',
                                  status=502) from None

    @staticmethod
    def _hint(code):
        return {
            401: 'the AccessKey was rejected — check it at cloud.debank.com',
            403: 'this route is not enabled for your plan',
            429: 'rate limited — DeBank caps requests per second, not just units',
            404: 'no such route on the Cloud API — try debank_raw with the exact path',
        }.get(code)

    # ── portfolio ──

    def portfolio(self, id, min_usd=1.0):
        """Net worth in one call: the total, then every chain carrying it."""
        addr = _addr(id)
        total = self.get('/v1/user/total_balance', id=addr) or {}
        chains = []
        for c in total.get('chain_list') or []:
            usd = _f(c.get('usd_value'))
            if usd >= min_usd:
                chains.append({'chain': c.get('id'), 'name': c.get('name'),
                               'usd': round(usd, 2),
                               'native': c.get('native_token_id'),
                               'community_id': c.get('community_id')})
        chains.sort(key=lambda c: c['usd'], reverse=True)
        return {'address': addr,
                'total_usd': round(_f(total.get('total_usd_value')), 2),
                'chains': chains, 'chain_count': len(chains),
                'chains_below_min_usd': len(total.get('chain_list') or []) - len(chains),
                'min_usd': min_usd}

    def chains_used(self, id):
        """Which chains this address has ever touched — cheap, one unit."""
        addr = _addr(id)
        used = self.get('/v1/user/used_chain_list', id=addr) or []
        return {'address': addr, 'count': len(used),
                'chains': [{'chain': c.get('id'), 'name': c.get('name'),
                            'born_at': c.get('born_at')} for c in used]}

    def chain_balance(self, id, chain):
        """Net worth on one chain."""
        addr, cid = _addr(id), chain_id(chain)
        if not cid:
            raise DebankError('chain is required', status=400)
        r = self.get('/v1/user/chain_balance', id=addr, chain_id=cid) or {}
        return {'address': addr, 'chain': cid,
                'total_usd': round(_f(r.get('usd_value')), 2)}

    def net_curve(self, id, chain=None):
        """Net worth over time — DeBank's own daily curve, newest last."""
        addr, cid = _addr(id), chain_id(chain)
        raw = (self.get('/v1/user/chain_net_curve', id=addr, chain_id=cid) if cid
               else self.get('/v1/user/total_net_curve', id=addr)) or []
        points = [{'time': p.get('timestamp'), 'usd': round(_f(p.get('usd_value')), 2)}
                  for p in raw]
        first, last = (points[0]['usd'] if points else 0), (points[-1]['usd'] if points else 0)
        return {'address': addr, 'chain': cid or 'all', 'points': points,
                'count': len(points),
                'change_usd': round(last - first, 2),
                'change_pct': round((last - first) / first * 100, 2) if first else None}

    # ── tokens ──

    def tokens(self, id, chain=None, min_usd=1.0, limit=100, all_tokens=False):
        """Every token balance, priced, biggest first. Dust is dropped, not hidden."""
        addr, cid = _addr(id), chain_id(chain)
        is_all = 'true' if all_tokens else 'false'
        raw = (self.get('/v1/user/token_list', id=addr, chain_id=cid, is_all=is_all)
               if cid else
               self.get('/v1/user/all_token_list', id=addr, is_all=is_all)) or []
        rows = [_token_row(t, cid) for t in raw]
        out, stats = _rank(rows, min_usd, limit)
        return {'address': addr, 'chain': cid or 'all', 'tokens': out,
                'total_usd': round(sum(r['usd'] for r in rows), 2), **stats,
                'note': ('unverified/spam tokens excluded — pass all_tokens=true to '
                         'include them' if not all_tokens else
                         'includes unverified tokens; treat prices as unreliable')}

    def token_balance(self, id, chain, token_id):
        """One token's balance for one address."""
        return self.get('/v1/user/token', id=_addr(id), chain_id=chain_id(chain),
                        token_id=token_id)

    def token(self, chain, id):
        """Token metadata and current price."""
        t = self.get('/v1/token', chain_id=chain_id(chain), id=id) or {}
        return {**t, 'price_usd': _f(t.get('price'))}

    def token_price_history(self, chain, id, date_at=None):
        """Closing price on a date (YYYY-MM-DD, UTC)."""
        return self.get('/v1/token/history_price', chain_id=chain_id(chain), id=id,
                        date_at=date_at)

    def token_holders(self, chain, id, start=0, limit=20):
        """The biggest holders of a token, largest first."""
        raw = self.get('/v1/token/top_holders', chain_id=chain_id(chain), id=id,
                       start=start, limit=min(int(limit), 100)) or []
        return {'chain': chain_id(chain), 'token': id, 'count': len(raw),
                'holders': [{'address': h[0], 'amount': _f(h[1])}
                            if isinstance(h, list) else h for h in raw]}

    # ── defi positions ──

    def protocols(self, id, chain=None, min_usd=1.0, limit=50, detail=False):
        """Open DeFi positions with the net USD in each — supplied minus borrowed."""
        addr, cid = _addr(id), chain_id(chain)
        raw = (self.get('/v1/user/complex_protocol_list', id=addr, chain_id=cid) if cid
               else self.get('/v1/user/all_complex_protocol_list', id=addr)) or []
        rows = []
        for p in raw:
            supplied = borrowed = rewards = 0.0
            items = []
            for item in p.get('portfolio_item_list') or []:
                d = item.get('detail') or {}
                s = sum(_usd(t) for t in (d.get('supply_token_list') or []))
                s += sum(_usd(t) for t in (d.get('token_list') or []))
                b = sum(_usd(t) for t in (d.get('borrow_token_list') or []))
                r = sum(_usd(t) for t in (d.get('reward_token_list') or []))
                supplied, borrowed, rewards = supplied + s, borrowed + b, rewards + r
                items.append({
                    'type': item.get('name'),
                    'net_usd': round(s + r - b, 2),
                    'supplied_usd': round(s, 2),
                    'borrowed_usd': round(b, 2),
                    'rewards_usd': round(r, 2),
                    'health_rate': (item.get('detail') or {}).get('health_rate'),
                    'assets': [_token_row(t) for t in
                               ((d.get('supply_token_list') or []) +
                                (d.get('token_list') or []))] if detail else None,
                    'debt': [_token_row(t) for t in (d.get('borrow_token_list') or [])]
                            if detail else None,
                })
            rows.append({
                'protocol': p.get('id'), 'name': p.get('name'), 'chain': p.get('chain'),
                'usd': round(supplied + rewards - borrowed, 2),
                'supplied_usd': round(supplied, 2),
                'borrowed_usd': round(borrowed, 2),
                'rewards_usd': round(rewards, 2),
                'site': p.get('site_url'),
                'positions': [i for i in items
                              if detail or i['net_usd'] or i['borrowed_usd']],
            })
        out, stats = _rank(rows, min_usd, limit)
        return {'address': addr, 'chain': cid or 'all', 'protocols': out,
                'total_usd': round(sum(r['usd'] for r in rows), 2),
                'total_borrowed_usd': round(sum(r['borrowed_usd'] for r in rows), 2),
                **stats}

    def protocol_position(self, id, protocol_id):
        """One protocol's position for one address, in full."""
        return self.get('/v1/user/protocol', id=_addr(id), protocol_id=protocol_id)

    def protocol(self, id=None, chain=None, limit=100):
        """A protocol by id, or every protocol on a chain ranked by TVL."""
        if id:
            return self.get('/v1/protocol', id=id)
        cid = chain_id(chain)
        raw = (self.get('/v1/protocol/list', chain_id=cid) if cid
               else self.get('/v1/protocol/all_list')) or []
        rows = [{'protocol': p.get('id'), 'name': p.get('name'), 'chain': p.get('chain'),
                 'tvl': round(_f(p.get('tvl')), 2), 'site': p.get('site_url')}
                for p in raw]
        rows.sort(key=lambda r: r['tvl'], reverse=True)
        return {'chain': cid or 'all', 'count': len(rows), 'protocols': rows[:limit]}

    def protocol_holders(self, id, start=0, limit=20):
        """The biggest depositors in a protocol."""
        raw = self.get('/v1/protocol/top_holders', id=id, start=start,
                       limit=min(int(limit), 100)) or []
        return {'protocol': id, 'count': len(raw),
                'holders': [{'address': h[0], 'usd': round(_f(h[1]), 2)}
                            if isinstance(h, list) else h for h in raw]}

    # ── nfts ──

    def nfts(self, id, chain=None, limit=50, all_nfts=False):
        """NFTs held, with floor-price USD where DeBank has one."""
        addr, cid = _addr(id), chain_id(chain)
        is_all = 'true' if all_nfts else 'false'
        raw = (self.get('/v1/user/nft_list', id=addr, chain_id=cid, is_all=is_all)
               if cid else
               self.get('/v1/user/all_nft_list', id=addr, is_all=is_all)) or []
        rows = []
        for n in raw:
            usd = _f(n.get('usd_price')) or _f((n.get('collection') or {}).get('floor_price'))
            rows.append({'chain': n.get('chain'), 'name': n.get('name'),
                         'collection': (n.get('collection') or {}).get('name')
                                       or n.get('contract_name'),
                         'contract': n.get('contract_id'),
                         'token_id': n.get('inner_id'),
                         'amount': _f(n.get('amount'), 1),
                         'usd': round(usd, 2)})
        rows.sort(key=lambda r: r['usd'], reverse=True)
        return {'address': addr, 'chain': cid or 'all', 'count': len(rows),
                'nfts': rows[:limit],
                'total_usd': round(sum(r['usd'] for r in rows), 2)}

    # ── history ──

    def history(self, id, chain=None, start_time=None, page_count=20, token_id=None):
        """Recent transactions, decoded into what moved and what it cost in gas."""
        addr, cid = _addr(id), chain_id(chain)
        n = max(1, min(int(page_count), 20))     # DeBank caps a page at 20
        raw = (self.get('/v1/user/history_list', id=addr, chain_id=cid,
                        start_time=start_time, page_count=n, token_id=token_id)
               if cid else
               self.get('/v1/user/all_history_list', id=addr, start_time=start_time,
                        page_count=n)) or {}
        tokens = raw.get('token_dict') or {}
        projects = raw.get('project_dict') or {}
        rows = []
        for h in raw.get('history_list') or []:
            tx = h.get('tx') or {}
            chain_of = h.get('chain') or cid
            project = projects.get(h.get('project_id') or '') or {}
            rows.append({
                'time': h.get('time_at'),
                'chain': chain_of,
                'type': CATES.get(h.get('cate_id') or '', h.get('cate_id') or 'contract'),
                'action': tx.get('name') or h.get('tx', {}).get('name'),
                'project': project.get('name') or h.get('project_id'),
                'sent': [self._leg(s, tokens, chain_of) for s in (h.get('sends') or [])],
                'received': [self._leg(r, tokens, chain_of) for r in (h.get('receives') or [])],
                'gas_usd': round(_f(tx.get('usd_gas_fee')), 4) if tx else None,
                'status': 'failed' if tx.get('status') == 0 else 'ok',
                'hash': h.get('id'),
            })
        return {'address': addr, 'chain': cid or 'all', 'count': len(rows),
                'transactions': rows,
                'oldest_time': rows[-1]['time'] if rows else None,
                'next': ('pass start_time=<oldest_time> to page further back'
                         if rows else None)}

    @staticmethod
    def _leg(leg, tokens, chain):
        """One transfer inside a transaction, resolved against the token dict.

        The dict is keyed differently on the single-chain and all-chain routes,
        so try the shapes rather than assuming one.
        """
        tid = leg.get('token_id') or ''
        t = (tokens.get(tid) or tokens.get(f'{chain}:{tid}')
             or tokens.get(f'{chain}_{tid}') or {})
        amount = _f(leg.get('amount'))
        price = _f(t.get('price'))
        return {'symbol': t.get('optimized_symbol') or t.get('symbol')
                          or (tid[:8] + '…' if len(tid) > 10 else tid),
                'amount': amount,
                'usd': round(amount * price, 2) if price else None,
                'counterparty': leg.get('to_addr') or leg.get('from_addr')}

    # ── risk ──

    def approvals(self, id, chain, min_usd=0.0, limit=100):
        """Live token approvals — the standing permission to move someone's money.

        Ranked by exposure (what the spender could take today), because that is
        the number that decides which one to revoke first.
        """
        addr, cid = _addr(id), chain_id(chain)
        if not cid:
            raise DebankError('chain is required — approvals are per chain', status=400,
                              hint='call debank_portfolio first to see which chains '
                                   'hold value, then check those')
        raw = self.get('/v1/user/token_authorized_list', id=addr, chain_id=cid) or []
        rows = []
        for t in raw:
            price, balance = _f(t.get('price')), _f(t.get('balance'))
            for s in t.get('spenders') or []:
                exposed = min(_f(s.get('value'), balance), balance) * price
                rows.append({
                    'token': t.get('symbol'), 'token_id': t.get('id'),
                    'balance_usd': round(balance * price, 2),
                    'spender': s.get('id'),
                    'spender_name': s.get('protocol', {}).get('name')
                                    if isinstance(s.get('protocol'), dict) else None,
                    'unlimited': _f(s.get('value')) >= 1e30 or s.get('value') is None,
                    'exposure_usd': round(exposed, 2),
                    'last_approved': s.get('last_approve_at'),
                    'risk': s.get('risk_exposure'),
                })
        out, stats = _rank(rows, min_usd, limit, key='exposure_usd')
        return {'address': addr, 'chain': cid, 'approvals': out,
                'total_exposure_usd': round(sum(r['exposure_usd'] for r in rows), 2),
                'unlimited_count': sum(1 for r in rows if r['unlimited']), **stats}

    def nft_approvals(self, id, chain):
        """NFT approvals — contract and per-token, as DeBank reports them."""
        return self.get('/v1/user/nft_authorized_list', id=_addr(id),
                        chain_id=chain_id(chain))

    # ── the bank rail (keyless) ──

    def balances(self, id, chains=None, min_usd=0.0):
        """Native + stablecoin balances on the bank rail. Needs no key."""
        return balances(id, chains=chains, min_usd=min_usd)

    def networks(self):
        return networks()

    def humanity(self, id):
        """The proof-of-humanity tag on an id. Needs no key."""
        return humanity(id)

    # ── chains & gas ──

    _chain_cache = {'at': 0, 'rows': None}

    def chains(self, q=None, refresh=False):
        """The chain catalog. Works signed-out — this one route has a public twin."""
        cache = Client._chain_cache
        if refresh or not cache['rows'] or time.time() - cache['at'] > 600:
            source = 'pro'
            try:
                rows = self.get('/v1/chain/list') or []
            except DebankError as e:
                if e.status not in (401, 403):
                    raise
                rows = ((self.get('/chain/list', _public=True) or {})
                        .get('data', {}).get('chains') or [])
                source = 'public'
            cache.update(at=time.time(), rows=rows, source=source)
        rows = cache['rows']
        # The pro and public catalogs name the same fields differently; read both
        # so a signed-out answer is not half empty.
        out = [{'chain': c.get('id'), 'name': c.get('name'),
                'native': (c.get('native_token_id') or c.get('token_symbol')
                           or c.get('token_id')),
                'community_id': c.get('community_id') or c.get('network_id'),
                'explorer': c.get('explorer_host')} for c in rows]
        if q:
            needle = str(q).lower()
            # `optimism` should find `op`: try the alias table before free text,
            # since DeBank's own names have drifted away from the common ones.
            alias = chain_id(needle)
            hit = [c for c in out if c['chain'] == alias]
            out = hit or [c for c in out
                          if needle in json.dumps(c, default=str).lower()]
        return {'count': len(out), 'chains': out,
                'source': cache.get('source', 'pro'),
                'aliases': CHAIN_ALIASES if not q else None}

    def gas(self, chain):
        """Current gas market: slow / normal / fast, with the USD each implies."""
        cid = chain_id(chain)
        raw = self.get('/v1/wallet/gas_market', chain_id=cid) or []
        return {'chain': cid, 'levels': [
            {'level': g.get('level'), 'price_wei': _f(g.get('price')),
             'price_gwei': round(_f(g.get('price')) / 1e9, 3),
             'estimated_seconds': g.get('estimated_seconds'),
             'front_tx_count': g.get('front_tx_count')} for g in raw]}

    # ── account ──

    def account(self):
        """Does the key work, and what is left on it.

        The unit-balance route is not on every plan, so this proves the key with
        a cheap catalog call first and reports the balance only if it answers.
        """
        state = self.key_state()
        if not state['present']:
            return {**state, 'ok': False,
                    'hint': 'no key — `m debank/set_key <key>` (cloud.debank.com)'}
        try:
            self.get('/v1/chain/list')
            state['ok'] = True
        except DebankError as e:
            return {**state, 'ok': False, **e.dict()}
        try:
            state['units'] = self.get('/v1/account/units')
        except DebankError as e:
            state['units'] = None
            state['units_note'] = f'unit balance unavailable ({e.status}) — ' \
                                  'check usage at cloud.debank.com'
        return state

    def raw(self, path, params=None, public=False):
        """Escape hatch: any Cloud API route, with the caller's key attached."""
        if not str(path).startswith('/'):
            path = '/' + str(path)
        return self.get(path, _public=bool(public), **(params or {}))
