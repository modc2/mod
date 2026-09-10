#!/usr/bin/env python3
"""sui client — one class behind the REST API, the MCP tools and the console.

Two things about Sui shape everything here.

**The transport moved.** Mysten's public fullnodes answer every JSON-RPC method
with "JSON-RPC on public fullnodes has been deprecated". The protocol still
exists and third-party nodes still serve it, so this module keeps a pool of
working endpoints and fails over between them rather than pointing at
fullnode.mainnet.sui.io and reporting that the chain is down. Set SUI_RPC to
your own node and the pool stops mattering.

**Everything is an object, and an object ID looks exactly like an account
address.** Both are 32 bytes of hex. `what()` is the entry point for that: it
asks the chain what a string turned out to be instead of guessing from shape.

Prices come from DexScreener, which indexes Sui AMMs and needs no key. When it
has never seen a coin the price is null and a warning says so — a coin with no
market is not a coin worth zero.
"""

import concurrent.futures
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from bcs import (SUI_TYPE, build_transfer, digest_of, sign_transaction, spendable,
                 is_synthetic)
from keys import (SuiError, address_of, is_address, is_digest, normalize,
                  pubkey_of, short, signer)

MIST = 1_000_000_000

# Mysten's own fullnodes no longer answer JSON-RPC. These do. Ordered by how
# they behaved under test: publicnode first, blockvision last because it starts
# returning 429 after a handful of calls.
NETWORKS = {
    'mainnet': ['https://sui-rpc.publicnode.com',
                'https://rpc-mainnet.suiscan.xyz',
                'https://sui-mainnet-endpoint.blockvision.org'],
    'testnet': ['https://sui-testnet-rpc.publicnode.com'],
    'devnet': ['https://sui-devnet-rpc.publicnode.com'],
}
ALIASES = {'main': 'mainnet', 'm': 'mainnet', 'prod': 'mainnet',
           'test': 'testnet', 't': 'testnet', 'dev': 'devnet', 'd': 'devnet'}
FAUCETS = {'testnet': 'https://faucet.testnet.sui.io/v2/gas',
           'devnet': 'https://faucet.devnet.sui.io/v2/gas'}
EXPLORER = {'mainnet': 'https://suiscan.xyz/mainnet',
            'testnet': 'https://suiscan.xyz/testnet',
            'devnet': 'https://suiscan.xyz/devnet'}

DEX = 'https://api.dexscreener.com/latest/dex'

# A symbol is not an identifier — anyone can publish a coin called USDC. These
# three are pinned to the canonical types and never resolved by search.
MAJORS = {
    'SUI': SUI_TYPE,
    'USDC': '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7'
            '::usdc::USDC',
    'USDT': '0x375f70cf2ae4c00bf37117d0c85a2c71545e6ee05c4a5c7d282cd66a4504b068'
            '::usdt::USDT',
}

FRAMEWORK = {
    '0x1': 'Move stdlib', '0x2': 'Sui framework', '0x3': 'Sui system',
    '0x5': 'Sui system state', '0x6': 'Clock', '0x8': 'Random',
    '0xdee9': 'DeepBook v2',
}

# A transfer worth more than this needs confirm=true. Cheap insurance against a
# misread decimal point in a tool call.
SPEND_USD = float(os.environ.get('SUI_SPEND_USD', '25') or 25)
CACHE_TTL = float(os.environ.get('SUI_CACHE_TTL', '30') or 30)
# DexScreener returns at most this many pairs per request, whatever it
# was asked for. Hitting the cap means the answer is truncated, not empty.
DEX_PAIR_CAP = 30
UA = 'mod-sui/0.1 (+https://modc2.com/sui)'
_CACHE = {}


def _cached(key, ttl, produce):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = produce()
    _CACHE[key] = (time.time(), value)
    return value


def short_address(address):
    """0x0000…0002 → 0x2, the way Sui writes framework addresses."""
    trimmed = normalize(address)[2:].lstrip('0')
    return '0x' + (trimmed or '0')


def sui(mist, decimals=9):
    try:
        return int(mist) / (10 ** decimals)
    except Exception:
        return None


def _usd(amount, price):
    if amount is None or price is None:
        return None
    return round(amount * price, 2 if abs(amount * price) >= 0.01 else 6)


def _until(ms):
    """How long until a future timestamp. _ago() clamps the future to zero."""
    if not ms:
        return None
    seconds = int(ms) / 1000 - time.time()
    if seconds <= 0:
        return 'due'
    for unit, size in (('d', 86400), ('h', 3600), ('m', 60)):
        if seconds >= size:
            return f'{seconds / size:.1f}{unit}'
    return f'{seconds:.0f}s'


def _ago(ms):
    if not ms:
        return None
    seconds = max(0, time.time() - int(ms) / 1000)
    for unit, size in (('d', 86400), ('h', 3600), ('m', 60)):
        if seconds >= size:
            return f'{seconds / size:.0f}{unit} ago'
    return f'{seconds:.0f}s ago'


def norm_type(coin_type):
    """`0x2::sui::SUI` and its 64-hex twin are the same coin. Compare long."""
    if not isinstance(coin_type, str) or '::' not in coin_type:
        raise SuiError(f'a coin type looks like 0x2::sui::SUI, got {coin_type!r}')
    package, rest = coin_type.split('::', 1)
    return normalize(package, 'coin type package') + '::' + rest


def type_symbol(coin_type):
    """The last segment — a label, never an identifier."""
    return coin_type.rsplit('::', 1)[-1] if '::' in coin_type else coin_type


def _http(url, body=None, headers=None, timeout=30):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers={'user-agent': UA, 'accept': 'application/json',
                 **({'content-type': 'application/json'} if body is not None else {}),
                 **(headers or {})},
        method='POST' if body is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b'null')


class Client:
    """Everything the module can ask Sui, in one place."""

    def __init__(self, network=None, rpc=None, timeout=30):
        name = (network or os.environ.get('SUI_NETWORK') or 'mainnet').strip().lower()
        self.network = ALIASES.get(name, name)
        override = rpc or os.environ.get('SUI_RPC')
        if self.network.startswith('http'):
            override, self.network = self.network, 'custom'
        if override:
            self.endpoints = [override]
        elif self.network in NETWORKS:
            self.endpoints = list(NETWORKS[self.network])
        else:
            raise SuiError(f'unknown network {self.network!r} — '
                           f'{", ".join(NETWORKS)}, or pass a full rpc url')
        self.timeout = timeout
        self.warnings = []

    # ── transport ────────────────────────────────────────────────

    def call(self, method, params=None):
        """One JSON-RPC call, walking the endpoint pool until one answers.

        A node that is rate-limiting or down is a transport problem; a node that
        answers with a JSON-RPC error has understood the question, so that comes
        straight back to the caller rather than being retried elsewhere.
        """
        body = {'jsonrpc': '2.0', 'id': 1, 'method': method,
                'params': params if params is not None else []}
        transport = []
        for url in self.endpoints:
            try:
                answer = _http(url, body, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                raw = (e.read() or b'')[:200].decode('utf-8', 'replace')
                transport.append(f'{urllib.parse.urlparse(url).netloc} HTTP {e.code}'
                                 f'{" " + raw if raw else ""}')
                continue
            except Exception as e:
                transport.append(f'{urllib.parse.urlparse(url).netloc} {type(e).__name__}')
                continue
            if isinstance(answer, dict) and answer.get('error'):
                err = answer['error']
                message = err.get('message') or json.dumps(err)
                if err.get('code') == -32601:
                    transport.append(f'{urllib.parse.urlparse(url).netloc} '
                                     'no such method (deprecated JSON-RPC?)')
                    continue
                raise SuiError(f'{method}: {message}', detail=err.get('data'))
            if url is not self.endpoints[0]:
                self._warn(f'answered by {urllib.parse.urlparse(url).netloc} — '
                           f'{len(transport)} endpoint(s) ahead of it did not')
            return (answer or {}).get('result')
        raise SuiError(
            f'no Sui RPC endpoint answered {method} — {"; ".join(transport)}. '
            'Public Mysten fullnodes dropped JSON-RPC; set SUI_RPC or pass rpc= '
            'to use your own node.', status=502)

    def parallel(self, calls):
        """Several independent calls at once. Sui nodes reject JSON-RPC batches,
        so concurrency has to happen on this side of the wire."""
        out = [None] * len(calls)

        def run(i):
            method, params = calls[i]
            try:
                return i, self.call(method, params)
            except SuiError as e:
                return i, {'_error': str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(calls) or 1)) as pool:
            for i, value in pool.map(run, range(len(calls))):
                out[i] = value
        return out

    def _warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def _out(self, payload):
        if self.warnings and isinstance(payload, dict):
            payload = {**payload, 'warnings': list(self.warnings)}
        return payload

    def explorer(self, kind, value):
        base = EXPLORER.get(self.network)
        return f'{base}/{kind}/{value}' if base else None

    # ── prices ───────────────────────────────────────────────────

    def _dex(self, path):
        """None when the feed did not answer, a dict when it did.

        The difference matters more than it looks: "DexScreener says this coin
        has no pool" and "DexScreener did not answer" both produce no price, but
        only the first is worth remembering. Caching the second would turn a
        throttle into a permanent null with nothing left to explain it.
        """
        try:
            return _http(f'{DEX}/{path}', timeout=20) or {}
        except Exception as e:
            self._warn(f'DexScreener unavailable ({type(e).__name__}) — USD values '
                       'are null, not zero, and were not cached as absent')
            return None

    def prices(self, coin_types):
        """USD per coin type, from the deepest Sui pool DexScreener knows.

        Returns only what it found. A missing key means no market was found,
        which is not the same as a price of zero, and callers must keep the
        difference.
        """
        wanted = [norm_type(t) for t in coin_types]
        if self.network != 'mainnet':
            self._warn(f'no price feed for {self.network} — USD values are null')
            return {}
        found, missing = {}, []
        for t in wanted:
            hit = _CACHE.get(('price', t))
            if hit and time.time() - hit[0] < CACHE_TTL:
                if hit[1] is not None:
                    found[t] = hit[1]
            else:
                missing.append(t)
        for chunk in [missing[i:i + 5] for i in range(0, len(missing), 5)]:
            best, pairs_seen, answered = self._dex_best(chunk)
            if not answered:
                continue          # the feed is down; leave these unknown, uncached
            unresolved = [t for t in chunk if t not in best]
            # DexScreener answers with at most 30 pairs however many tokens you
            # asked about, so one busy coin can crowd a quiet one out of its own
            # response. A miss on a capped answer is not evidence of no market —
            # ask again for those, one at a time.
            if unresolved and pairs_seen >= DEX_PAIR_CAP:
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=min(5, len(unresolved))) as pool:
                    for single, _, ok in pool.map(self._dex_best,
                                                   ([t] for t in unresolved)):
                        if ok:
                            best.update(single)
            for t in chunk:
                _CACHE[('price', t)] = (time.time(), best.get(t))
                if t in best:
                    found[t] = best[t]
        return found

    def _dex_best(self, coin_types):
        """Deepest Sui pool per coin type, and whether the feed answered at all."""
        # SUI is always written short on the wire and DexScreener indexes it
        # that way, so ask in the short form and key the answer in the long one.
        ask = [SUI_TYPE if t == norm_type(SUI_TYPE) else t for t in coin_types]
        answer = self._dex('tokens/' + ','.join(ask))
        if answer is None:
            return {}, 0, False
        pairs = answer.get('pairs') or []
        best = {}
        for pair in pairs:
            if pair.get('chainId') != 'sui':
                continue
            try:
                key = norm_type(pair['baseToken']['address'])
            except Exception:
                continue
            liquidity = ((pair.get('liquidity') or {}).get('usd') or 0)
            if liquidity <= (best.get(key, {}).get('liquidity') or -1):
                continue
            try:
                price = float(pair.get('priceUsd'))
            except (TypeError, ValueError):
                continue
            best[key] = {'usd': price, 'liquidity': round(liquidity),
                         'change_24h': (pair.get('priceChange') or {}).get('h24'),
                         'dex': pair.get('dexId'),
                         'volume_24h': round((pair.get('volume') or {}).get('h24') or 0)}
        return best, len(pairs), True

    def resolve_type(self, token):
        """A symbol or a coin type in; a coin type out, plus how we got there."""
        token = (token or '').strip()
        if '::' in token:
            return norm_type(token), 'exact'
        upper = token.upper()
        if upper in MAJORS:
            return norm_type(MAJORS[upper]), 'pinned'
        data = self._dex(f'search?q={urllib.parse.quote(token + " SUI")}')
        if data is None:
            raise SuiError(f'cannot resolve the symbol {token!r} — the price feed '
                           'did not answer. Pass the full coin type '
                           '(0x…::module::NAME) and this works without it',
                           status=502)
        best, best_liquidity = None, -1
        for pair in (data.get('pairs') or []):
            if pair.get('chainId') != 'sui':
                continue
            base = pair.get('baseToken') or {}
            if (base.get('symbol') or '').upper() != upper:
                continue
            liquidity = (pair.get('liquidity') or {}).get('usd') or 0
            if liquidity > best_liquidity:
                best, best_liquidity = base.get('address'), liquidity
        if not best:
            raise SuiError(f'no Sui coin called {token!r} has a market DexScreener '
                           'indexes — pass the full coin type (0x…::module::NAME)',
                           status=404)
        self._warn(f'{token!r} resolved by liquidity to {best} — a symbol is not an '
                   'identifier on Sui; check the type before trusting it')
        return norm_type(best), 'by_liquidity'

    def price(self, ids):
        """USD price for coin types or symbols, comma-separated."""
        tokens = [t for t in str(ids).replace(' ', ',').split(',') if t]
        if not tokens:
            raise SuiError('nothing to price')
        resolved = {}
        for token in tokens:
            try:
                resolved[token] = self.resolve_type(token)[0]
            except SuiError as e:
                resolved[token] = None
                self._warn(str(e))
        quotes = self.prices([t for t in resolved.values() if t])
        rows = []
        for token, coin_type in resolved.items():
            quote = quotes.get(coin_type) or {}
            rows.append({'query': token, 'coin_type': coin_type,
                         'symbol': type_symbol(coin_type) if coin_type else None,
                         'usd': quote.get('usd'),
                         'change_24h': quote.get('change_24h'),
                         'liquidity_usd': quote.get('liquidity'),
                         'volume_24h_usd': quote.get('volume_24h'),
                         'dex': quote.get('dex'),
                         'priced': bool(quote)})
        return self._out({'network': self.network, 'prices': rows,
                          'source': 'DexScreener — deepest Sui pool per coin'})

    def sui_price(self):
        return (self.prices([SUI_TYPE]).get(norm_type(SUI_TYPE)) or {}).get('usd')

    # ── metadata ─────────────────────────────────────────────────

    def metadata(self, coin_types):
        """decimals/symbol/name per coin type. Decimals decide whether a balance
        reads as 5 or as 5,000,000,000, so this is never optional."""
        types = [norm_type(t) for t in dict.fromkeys(coin_types)]
        out, missing = {}, []
        for t in types:
            hit = _CACHE.get(('meta', self.network, t))
            if hit:
                out[t] = hit[1]
            else:
                missing.append(t)
        if missing:
            asked = [SUI_TYPE if t == norm_type(SUI_TYPE) else t for t in missing]
            results = self.parallel([('suix_getCoinMetadata', [t]) for t in asked])
            for t, meta in zip(missing, results):
                if isinstance(meta, dict) and not meta.get('_error'):
                    value = {'decimals': meta.get('decimals'),
                             'symbol': meta.get('symbol'),
                             'name': meta.get('name'),
                             'icon': meta.get('iconUrl'),
                             'description': meta.get('description')}
                else:
                    value = {'decimals': None, 'symbol': type_symbol(t),
                             'name': None, 'icon': None, 'description': None}
                _CACHE[('meta', self.network, t)] = (time.time(), value)
                out[t] = value
        return out

    def _decimals(self, coin_type):
        meta = self.metadata([coin_type])[norm_type(coin_type)]
        if meta.get('decimals') is None:
            raise SuiError(f'{coin_type} publishes no CoinMetadata, so this module '
                           'cannot know its decimals — refusing to guess at a '
                           'transfer amount')
        return meta['decimals']

    # ── identification ───────────────────────────────────────────

    def what(self, query):
        """What a string IS. The question Sui makes hard on purpose.

        An account address and an object ID are both 32 bytes of hex and nothing
        distinguishes them, so this asks the chain both questions and reports
        whichever came back — occasionally both, when an address has been used
        as an object ID or an object is owned by nobody.
        """
        query = (query or '').strip()
        if not query:
            raise SuiError('nothing to identify')

        if '::' in query:                    # nothing else on Sui contains ::
            try:
                return self.coin(query)
            except SuiError:
                return self._out({'query': query, 'kind': 'move_type',
                                  'note': 'a Move type that is not a coin — read the '
                                          'package with sui_package'})

        if is_digest(query):
            return self.tx(query)

        name = None
        if query.endswith('.sui') or query.startswith('@'):
            name = query
            resolved = self.call('suix_resolveNameServiceAddress',
                                 [query[1:] + '.sui' if query.startswith('@') else query])
            if not resolved:
                raise SuiError(f'SuiNS has no record for {query!r}', status=404)
            query = resolved

        if not is_address(query):
            raise SuiError(
                f'{query!r} is not a Sui identifier. Addresses and object IDs are '
                '0x + up to 64 hex; digests are 32 bytes of base58; coin types look '
                'like 0x2::sui::SUI; names end in .sui', status=400)

        address = normalize(query)
        obj, balances, owned = self.parallel([
            ('sui_getObject', [address, {'showType': True, 'showOwner': True,
                                         'showContent': True, 'showDisplay': True,
                                         'showPreviousTransaction': True}]),
            ('suix_getAllBalances', [address]),
            ('suix_getOwnedObjects', [address, {'options': {'showType': True}},
                                      None, 50]),
        ])
        out = {'query': name or query, 'address': address,
               'suins': name, 'network': self.network,
               'explorer': self.explorer('account', address)}

        as_object = isinstance(obj, dict) and obj.get('data')
        holdings = balances if isinstance(balances, list) else []
        object_count = len((owned or {}).get('data') or []) if isinstance(owned, dict) else 0
        more = bool(isinstance(owned, dict) and owned.get('hasNextPage'))

        if as_object:
            out.update(self._describe_object(obj['data']))
        if holdings or object_count:
            out['account'] = self._account_summary(address, holdings, object_count, more)

        if as_object and out.get('account'):
            out['kind'] = out.get('kind') or 'object'
            out['note'] = ('this hex is BOTH a live object and an address holding '
                           'value — on Sui the two namespaces overlap')
        elif not as_object and not out.get('account'):
            out['kind'] = 'unused'
            out['note'] = ('nothing on chain: no object with this ID, no balance and '
                           'no owned objects. A valid address nobody has used yet '
                           'looks exactly like this.')
        return self._out(out)

    def _account_summary(self, address, balances, object_count, more):
        types = [norm_type(b['coinType']) for b in balances]
        meta = self.metadata(types) if types else {}
        quotes = self.prices(types) if types else {}
        rows, total = [], 0.0
        for balance in balances:
            coin_type = norm_type(balance['coinType'])
            info = meta.get(coin_type, {})
            decimals = info.get('decimals')
            amount = sui(balance['totalBalance'], decimals) if decimals is not None else None
            price = (quotes.get(coin_type) or {}).get('usd')
            value = _usd(amount, price)
            if value:
                total += value
            rows.append({'coin_type': coin_type, 'symbol': info.get('symbol') or
                         type_symbol(coin_type), 'amount': amount,
                         'raw': balance['totalBalance'], 'decimals': decimals,
                         'usd': value, 'coin_objects': balance.get('coinObjectCount')})
        rows.sort(key=lambda r: (r['usd'] is None, -(r['usd'] or 0)))
        return {'coin_types': len(rows), 'holdings': rows[:12],
                'total_usd': round(total, 2),
                'objects': f'{object_count}+' if more else object_count}

    def _describe_object(self, data):
        object_type = data.get('type') or ''
        owner = data.get('owner')
        out = {'object_id': data.get('objectId'), 'version': data.get('version'),
               'digest': data.get('digest'), 'type': object_type,
               'storage_rebate': data.get('storageRebate'),
               'previous_transaction': data.get('previousTransaction')}
        if object_type == 'package':
            out['kind'] = 'package'
            out['known_as'] = FRAMEWORK.get(short_address(data.get('objectId')))
            out['note'] = 'published Move code — read its modules with sui_package'
            return out
        if isinstance(owner, dict):
            if 'AddressOwner' in owner:
                out['owner'] = owner['AddressOwner']
                out['ownership'] = 'address'
            elif 'ObjectOwner' in owner:
                out['owner'] = owner['ObjectOwner']
                out['ownership'] = 'object'
                out['note'] = 'owned by another object — a dynamic field or a wrapped item'
            elif 'Shared' in owner:
                out['ownership'] = 'shared'
                out['initial_shared_version'] = (owner['Shared'] or {}).get(
                    'initial_shared_version')
                out['note'] = ('shared: anyone can use it in a transaction, and it '
                               'needs consensus rather than the fast path')
            elif 'ConsensusAddressOwner' in owner:
                out['ownership'] = 'consensus_address'
                out['owner'] = (owner['ConsensusAddressOwner'] or {}).get('owner')
        elif owner == 'Immutable':
            out['ownership'] = 'immutable'
            out['note'] = 'frozen — it can be read by anyone and changed by nobody'
        if object_type.startswith('0x2::coin::Coin<'):
            inner = object_type[len('0x2::coin::Coin<'):-1]
            fields = ((data.get('content') or {}).get('fields') or {})
            decimals = self.metadata([inner])[norm_type(inner)].get('decimals')
            out['kind'] = 'coin'
            out['coin_type'] = inner
            out['balance'] = sui(fields.get('balance'), decimals) \
                if decimals is not None else None
            out['balance_raw'] = fields.get('balance')
            return out
        display = ((data.get('display') or {}).get('data') or {})
        if display:
            out['kind'] = 'nft'
            out['display'] = {k: v for k, v in display.items() if v}
        else:
            out.setdefault('kind', 'object')
            fields = ((data.get('content') or {}).get('fields') or {})
            if fields:
                out['fields'] = {k: v for k, v in list(fields.items())[:20]
                                 if not isinstance(v, (dict, list))}
        return out

    # ── holdings ─────────────────────────────────────────────────

    def balance(self, address, coin_type=SUI_TYPE):
        """One coin type for one address or several, comma-separated."""
        addresses = [normalize(a) for a in str(address).replace(' ', ',').split(',') if a]
        wanted = norm_type(self.resolve_type(coin_type)[0])
        meta = self.metadata([wanted])[wanted]
        decimals = meta.get('decimals')
        price = (self.prices([wanted]).get(wanted) or {}).get('usd')
        results = self.parallel([('suix_getBalance', [a, wanted]) for a in addresses])
        rows = []
        for a, result in zip(addresses, results):
            if isinstance(result, dict) and result.get('_error'):
                rows.append({'address': a, 'error': result['_error']})
                continue
            amount = sui(result.get('totalBalance'), decimals) if decimals is not None else None
            rows.append({'address': a, 'amount': amount,
                         'raw': result.get('totalBalance'),
                         'usd': _usd(amount, price),
                         'coin_objects': result.get('coinObjectCount')})
        return self._out({'network': self.network, 'coin_type': wanted,
                          'symbol': meta.get('symbol') or type_symbol(wanted),
                          'decimals': decimals, 'price_usd': price,
                          'balances': rows if len(rows) > 1 else None,
                          **(rows[0] if len(rows) == 1 else {})})

    def portfolio(self, address, min_usd=0.01, include_dust=False, limit=100):
        """Every coin type an address holds, priced, plus staked SUI.

        Staked SUI is in a StakedSui object, not in any balance, so a wallet can
        report a small balance and control a large position. It is counted here.
        """
        address = normalize(address)
        balances, stakes, owned = self.parallel([
            ('suix_getAllBalances', [address]),
            ('suix_getStakes', [address]),
            ('suix_getOwnedObjects', [address, {'options': {'showType': True}},
                                      None, 50]),
        ])
        balances = balances if isinstance(balances, list) else []
        types = [norm_type(b['coinType']) for b in balances]
        meta = self.metadata(types) if types else {}
        quotes = self.prices(types) if types else {}
        rows, dust, dust_usd, unpriced = [], 0, 0.0, 0
        total = 0.0
        for balance in balances:
            coin_type = norm_type(balance['coinType'])
            info = meta.get(coin_type, {})
            decimals = info.get('decimals')
            amount = sui(balance['totalBalance'], decimals) if decimals is not None else None
            quote = quotes.get(coin_type) or {}
            value = _usd(amount, quote.get('usd'))
            row = {'coin_type': coin_type,
                   'symbol': info.get('symbol') or type_symbol(coin_type),
                   'name': info.get('name'), 'amount': amount,
                   'raw': balance['totalBalance'], 'decimals': decimals,
                   'price_usd': quote.get('usd'), 'usd': value,
                   'change_24h': quote.get('change_24h'),
                   'coin_objects': balance.get('coinObjectCount')}
            if value is None:
                unpriced += 1
                rows.append(row)
            elif value < float(min_usd) and not include_dust:
                dust += 1
                dust_usd += value
                total += value
            else:
                total += value
                rows.append(row)
        rows.sort(key=lambda r: (r['usd'] is None, -(r['usd'] or 0)))

        staked, staked_usd = self._stake_rows(stakes)
        sui_usd = (quotes.get(norm_type(SUI_TYPE)) or {}).get('usd') or self.sui_price()
        staked_total = sum(s['principal'] for s in staked)
        rewards = sum(s['estimated_reward'] or 0 for s in staked)
        object_count = len((owned or {}).get('data') or []) if isinstance(owned, dict) else 0
        more = bool(isinstance(owned, dict) and owned.get('hasNextPage'))

        return self._out({
            'address': address, 'network': self.network,
            'explorer': self.explorer('account', address),
            'coins': rows[:int(limit)],
            'coin_types': len(balances),
            'liquid_usd': round(total, 2),
            'staked_sui': round(staked_total, 6) if staked else 0,
            'staked_rewards_sui': round(rewards, 6) if staked else 0,
            'staked_usd': _usd(staked_total + rewards, sui_usd) if staked else 0,
            'total_usd': round(total + (_usd(staked_total + rewards, sui_usd) or 0), 2),
            'objects': f'{object_count}+' if more else object_count,
            'dust_hidden': dust, 'dust_usd': round(dust_usd, 4),
            'unpriced_coin_types': unpriced,
            'note': ('total_usd is what could be sold: coins with no market are '
                     'listed with usd=null and add nothing. staked SUI is included '
                     'and appears in no balance call.'),
        })

    def objects(self, address, type=None, limit=50, cursor=None):
        """Owned objects — NFTs, coins, capabilities, receipts."""
        address = normalize(address)
        query = {'options': {'showType': True, 'showDisplay': True,
                             'showOwner': True, 'showContent': False}}
        if type:
            query['filter'] = {'StructType': type}
        page = self.call('suix_getOwnedObjects',
                         [address, query, cursor, min(int(limit), 50)])
        rows = []
        for entry in (page.get('data') or []):
            data = entry.get('data') or {}
            display = ((data.get('display') or {}).get('data') or {})
            object_type = data.get('type') or ''
            rows.append({
                'object_id': data.get('objectId'), 'version': data.get('version'),
                'type': object_type,
                'kind': 'coin' if object_type.startswith('0x2::coin::Coin<')
                        else ('nft' if display else 'object'),
                'name': display.get('name'), 'image': display.get('image_url'),
                'description': display.get('description'),
            })
        return self._out({'address': address, 'network': self.network,
                          'objects': rows, 'count': len(rows),
                          'next_cursor': page.get('nextCursor'),
                          'has_more': page.get('hasNextPage')})

    def object(self, object_id):
        """One object in full."""
        data = self.call('sui_getObject', [normalize(object_id, 'object id'), {
            'showType': True, 'showOwner': True, 'showContent': True,
            'showDisplay': True, 'showPreviousTransaction': True,
            'showStorageRebate': True}])
        if not data or not data.get('data'):
            error = (data or {}).get('error') or {}
            raise SuiError(f'no object {object_id} — {error.get("code") or "not found"}'
                           '. Deleted and wrapped objects report the same way.',
                           status=404)
        out = self._describe_object(data['data'])
        content = (data['data'].get('content') or {})
        if content.get('fields'):
            out['fields'] = content['fields']
        out['explorer'] = self.explorer('object', out['object_id'])
        out['network'] = self.network
        return self._out(out)

    def coin(self, coin_type):
        """A coin type in full: metadata, supply, market."""
        resolved, how = self.resolve_type(coin_type)
        meta = self.metadata([resolved])[resolved]
        supply, quote = self.parallel([('suix_getTotalSupply', [resolved]),
                                       ('suix_getCoinMetadata', [resolved])])
        decimals = meta.get('decimals')
        raw_supply = (supply or {}).get('value') if isinstance(supply, dict) else None
        total = sui(raw_supply, decimals) if (raw_supply and decimals is not None) else None
        market = self.prices([resolved]).get(resolved) or {}
        price = market.get('usd')
        return self._out({
            'coin_type': resolved, 'resolved_by': how,
            'symbol': meta.get('symbol'), 'name': meta.get('name'),
            'decimals': decimals, 'description': meta.get('description'),
            'icon': meta.get('icon'),
            'total_supply': total, 'total_supply_raw': raw_supply,
            'price_usd': price, 'change_24h': market.get('change_24h'),
            'market_cap_usd': _usd(total, price) if total else None,
            'liquidity_usd': market.get('liquidity'),
            'volume_24h_usd': market.get('volume_24h'), 'top_dex': market.get('dex'),
            'metadata_object': (quote or {}).get('id') if isinstance(quote, dict) else None,
            'network': self.network,
            'explorer': self.explorer('coin', resolved),
            'note': ('market_cap uses total supply, which on Sui includes anything '
                     'the publisher has not burned. A TreasuryCap that still exists '
                     'means more can be minted; this module cannot see who holds it.'),
        })

    # ── history ──────────────────────────────────────────────────

    def history(self, address, limit=20, cursor=None, direction='both', detail=False):
        """Recent transactions for an address, newest first.

        Sui filters are one-sided — `FromAddress` and `ToAddress` are separate
        queries — so "everything this address touched" means asking twice and
        merging, which is what happens here unless you narrow `direction`.
        """
        address = normalize(address)
        limit = max(1, min(int(limit), 50))
        options = {'showEffects': True, 'showBalanceChanges': True, 'showInput': detail}
        wanted = []
        if direction in ('both', 'from', 'out'):
            wanted.append({'filter': {'FromAddress': address}, 'options': options})
        if direction in ('both', 'to', 'in'):
            wanted.append({'filter': {'ToAddress': address}, 'options': options})
        pages = self.parallel([('suix_queryTransactionBlocks', [q, cursor, limit, True])
                               for q in wanted])
        seen, rows = set(), []
        for page in pages:
            if not isinstance(page, dict) or page.get('_error'):
                continue
            for tx in (page.get('data') or []):
                if tx['digest'] in seen:
                    continue
                seen.add(tx['digest'])
                rows.append(self._history_row(tx, address))
        rows.sort(key=lambda r: r.get('timestamp_ms') or 0, reverse=True)
        rows = rows[:limit]
        if rows:
            self._price_changes(rows)
        return self._out({'address': address, 'network': self.network,
                          'direction': direction, 'transactions': rows,
                          'count': len(rows),
                          'next_cursor': (pages[0] or {}).get('nextCursor')
                          if isinstance(pages[0], dict) else None})

    def _history_row(self, tx, focus):
        effects = tx.get('effects') or {}
        status = (effects.get('status') or {}).get('status')
        gas = effects.get('gasUsed') or {}
        mine = []
        for change in (tx.get('balanceChanges') or []):
            owner = change.get('owner') or {}
            if isinstance(owner, dict) and owner.get('AddressOwner') and \
                    normalize(owner['AddressOwner']) == focus:
                mine.append({'coin_type': norm_type(change['coinType']),
                             'raw': change['amount']})
        return {'digest': tx['digest'], 'timestamp_ms': int(tx.get('timestampMs') or 0),
                'age': _ago(tx.get('timestampMs')), 'status': status,
                'success': status == 'success',
                'checkpoint': tx.get('checkpoint'),
                'sender': (tx.get('transaction') or {}).get('data', {}).get('sender'),
                'gas_sui': sui(int(gas.get('computationCost') or 0) +
                               int(gas.get('storageCost') or 0) -
                               int(gas.get('storageRebate') or 0)),
                'net_changes': mine,
                'explorer': self.explorer('tx', tx['digest'])}

    def _price_changes(self, rows):
        """Turn raw balance deltas into human amounts, once per coin type."""
        types = {c['coin_type'] for row in rows for c in row.get('net_changes', [])}
        if not types:
            return
        meta = self.metadata(types)
        for row in rows:
            for change in row['net_changes']:
                info = meta.get(change['coin_type'], {})
                decimals = info.get('decimals')
                change['symbol'] = info.get('symbol') or type_symbol(change['coin_type'])
                change['amount'] = sui(change['raw'], decimals) \
                    if decimals is not None else None

    def tx(self, digest, events=False):
        """One transaction, decoded into what moved and who ran what."""
        digest = digest.strip()
        if not is_digest(digest):
            raise SuiError(f'{digest!r} is not a transaction digest — those are 32 '
                           'bytes of base58, about 44 characters, and never 0x')
        tx = self.call('sui_getTransactionBlock', [digest, {
            'showInput': True, 'showEffects': True, 'showEvents': events,
            'showBalanceChanges': True, 'showObjectChanges': True}])
        if not tx:
            raise SuiError(f'no transaction {digest}', status=404)
        effects = tx.get('effects') or {}
        status = effects.get('status') or {}
        gas = effects.get('gasUsed') or {}
        data = (tx.get('transaction') or {}).get('data') or {}
        kind = (data.get('transaction') or {})

        changes = []
        types = {norm_type(c['coinType']) for c in (tx.get('balanceChanges') or [])}
        meta = self.metadata(types) if types else {}
        quotes = self.prices(types) if types else {}
        for change in (tx.get('balanceChanges') or []):
            coin_type = norm_type(change['coinType'])
            info = meta.get(coin_type, {})
            decimals = info.get('decimals')
            amount = sui(change['amount'], decimals) if decimals is not None else None
            owner = change.get('owner')
            who = owner.get('AddressOwner') or owner.get('ObjectOwner') \
                if isinstance(owner, dict) else str(owner)
            changes.append({'owner': who,
                            'symbol': info.get('symbol') or type_symbol(coin_type),
                            'coin_type': coin_type, 'amount': amount,
                            'raw': change['amount'],
                            'usd': _usd(amount, (quotes.get(coin_type) or {}).get('usd'))})
        changes.sort(key=lambda c: -(abs(c['amount'] or 0)))

        object_changes = {}
        notable = []
        for change in (tx.get('objectChanges') or []):
            object_changes[change['type']] = object_changes.get(change['type'], 0) + 1
            if change['type'] in ('created', 'published') and len(notable) < 12:
                notable.append({'change': change['type'],
                                'object_id': change.get('objectId') or
                                change.get('packageId'),
                                'type': change.get('objectType')})

        commands = []
        for command in (kind.get('transactions') or []):
            name = next(iter(command)) if isinstance(command, dict) else str(command)
            entry = {'command': name}
            if name == 'MoveCall':
                call = command['MoveCall']
                entry['target'] = (f"{call.get('package')}::{call.get('module')}"
                                   f"::{call.get('function')}")
                entry['type_arguments'] = call.get('type_arguments')
            commands.append(entry)

        gas_total = (int(gas.get('computationCost') or 0) +
                     int(gas.get('storageCost') or 0) -
                     int(gas.get('storageRebate') or 0))
        return self._out({
            'digest': digest, 'network': self.network,
            'status': status.get('status'), 'success': status.get('status') == 'success',
            'error': status.get('error'),
            'sender': data.get('sender'),
            'gas_owner': (data.get('gasData') or {}).get('owner'),
            'gas_sui': sui(gas_total), 'gas_price': (data.get('gasData') or {}).get('price'),
            'gas_budget_sui': sui((data.get('gasData') or {}).get('budget') or 0),
            'timestamp_ms': tx.get('timestampMs'), 'age': _ago(tx.get('timestampMs')),
            'checkpoint': tx.get('checkpoint'), 'epoch': effects.get('executedEpoch'),
            'kind': kind.get('kind'),
            'commands': commands,
            'balance_changes': changes,
            'object_changes': object_changes,
            'notable_objects': notable,
            'events': [{'type': e.get('type'), 'sender': e.get('sender'),
                        'json': e.get('parsedJson')}
                       for e in (tx.get('events') or [])[:20]] if events else None,
            'explorer': self.explorer('tx', digest),
        })

    # ── the chain ────────────────────────────────────────────────

    def status(self):
        """Epoch, checkpoint, throughput, gas price, stake, price."""
        system, checkpoint, total, gas_price, chain = self.parallel([
            ('suix_getLatestSuiSystemState', []),
            ('sui_getLatestCheckpointSequenceNumber', []),
            ('sui_getTotalTransactionBlocks', []),
            ('suix_getReferenceGasPrice', []),
            ('sui_getChainIdentifier', []),
        ])
        system = system if isinstance(system, dict) and not system.get('_error') else {}
        tps = None
        if checkpoint and not isinstance(checkpoint, dict):
            recent, older = self.parallel([
                ('sui_getCheckpoint', [str(checkpoint)]),
                ('sui_getCheckpoint', [str(max(1, int(checkpoint) - 200))]),
            ])
            try:
                span_ms = int(recent['timestampMs']) - int(older['timestampMs'])
                span_tx = (int(recent['networkTotalTransactions']) -
                           int(older['networkTotalTransactions']))
                tps = round(span_tx / (span_ms / 1000), 1) if span_ms > 0 else None
            except Exception:
                pass
        total_stake = sui(system.get('totalStake') or 0)
        epoch_start = int(system.get('epochStartTimestampMs') or 0)
        duration = int(system.get('epochDurationMs') or 0)
        return self._out({
            'network': self.network, 'chain_id': chain if isinstance(chain, str) else None,
            'rpc': self.endpoints[0],
            'epoch': system.get('epoch'),
            'epoch_progress': round(min(1.0, (time.time() * 1000 - epoch_start) /
                                        duration), 3) if duration else None,
            'epoch_ends_in': _until(epoch_start + duration) if duration else None,
            'protocol_version': system.get('protocolVersion'),
            'checkpoint': checkpoint, 'total_transactions': total,
            'tps': tps,
            'reference_gas_price_mist': gas_price,
            'validators': len(system.get('activeValidators') or []),
            'total_stake_sui': round(total_stake, 2) if total_stake else None,
            'storage_fund_sui': sui(system.get('storageFundTotalObjectStorageRebates') or 0),
            'sui_price_usd': self.sui_price(),
            'safe_mode': system.get('safeMode'),
        })

    def validators(self, limit=20, sort='stake'):
        """The validator set, and how few of them could stop the chain."""
        system, apys = self.parallel([('suix_getLatestSuiSystemState', []),
                                      ('suix_getValidatorsApy', [])])
        active = (system or {}).get('activeValidators') or []
        apy_by_address = {a['address']: a.get('apy')
                          for a in ((apys or {}).get('apys') or [])}
        total = sum(int(v.get('stakingPoolSuiBalance') or 0) for v in active) or 1
        rows = []
        for v in active:
            stake = int(v.get('stakingPoolSuiBalance') or 0)
            rows.append({
                'name': v.get('name'), 'address': v.get('suiAddress'),
                'stake_sui': round(stake / MIST, 2),
                'share': round(stake / total, 5),
                'apy': round((apy_by_address.get(v.get('suiAddress')) or 0) * 100, 2),
                'commission_rate': int(v.get('commissionRate') or 0) / 100,
                'next_epoch_stake_sui': round(
                    int(v.get('nextEpochStake') or 0) / MIST, 2),
                'image': v.get('imageUrl'), 'url': v.get('projectUrl'),
            })
        rows.sort(key=lambda r: -r['stake_sui'] if sort == 'stake' else -r['apy'])
        running = 0.0
        nakamoto = 0
        for row in sorted(rows, key=lambda r: -r['share']):
            running += row['share']
            nakamoto += 1
            if running > 1 / 3:
                break
        return self._out({
            'network': self.network, 'epoch': (system or {}).get('epoch'),
            'validators': rows[:int(limit)], 'count': len(rows),
            'total_stake_sui': round(total / MIST, 2),
            'nakamoto_coefficient': nakamoto,
            'note': (f'{nakamoto} validators hold more than a third of the stake — '
                     'Sui needs two thirds to make progress, so that many colluding '
                     'or failing together is enough to stop it.'),
        })

    def _stake_rows(self, stakes):
        rows, total_usd = [], 0.0
        for pool in (stakes if isinstance(stakes, list) else []):
            for stake in (pool.get('stakes') or []):
                principal = sui(stake.get('principal') or 0)
                reward = sui(stake.get('estimatedReward') or 0) \
                    if stake.get('estimatedReward') else 0
                rows.append({
                    'validator': pool.get('validatorAddress'),
                    'staked_sui_id': stake.get('stakedSuiId'),
                    'principal': principal, 'estimated_reward': reward,
                    'status': stake.get('status'),
                    'stake_active_epoch': stake.get('stakeActiveEpoch'),
                    'stake_request_epoch': stake.get('stakeRequestEpoch'),
                })
        rows.sort(key=lambda r: -(r['principal'] or 0))
        return rows, total_usd

    def stakes(self, address):
        """Delegated SUI — the half of a balance that no balance call shows."""
        address = normalize(address)
        rows, _ = self._stake_rows(self.call('suix_getStakes', [address]))
        principal = sum(r['principal'] or 0 for r in rows)
        rewards = sum(r['estimated_reward'] or 0 for r in rows)
        price = self.sui_price()
        return self._out({
            'address': address, 'network': self.network,
            'stakes': rows, 'positions': len(rows),
            'principal_sui': round(principal, 6), 'rewards_sui': round(rewards, 6),
            'total_sui': round(principal + rewards, 6),
            'total_usd': _usd(principal + rewards, price),
            'note': 'pending stakes earn nothing until stake_active_epoch',
        })

    def package(self, package_id, module=None, limit=40):
        """What a package can DO — Move keeps its interface, unlike EVM bytecode."""
        package_id = normalize(package_id, 'package id')
        modules = self.call('sui_getNormalizedMoveModulesByPackage', [package_id])
        if not modules:
            raise SuiError(f'{package_id} is not a published package', status=404)
        if module:
            if module not in modules:
                raise SuiError(f'no module {module!r} in this package — '
                               f'{", ".join(sorted(modules))}', status=404)
            body = modules[module]
            functions = []
            for name, fn in sorted((body.get('exposedFunctions') or {}).items()):
                functions.append({
                    'function': name, 'visibility': fn.get('visibility'),
                    'entry': fn.get('isEntry'),
                    'type_parameters': len(fn.get('typeParameters') or []),
                    'parameters': [_type_name(p) for p in (fn.get('parameters') or [])],
                    'returns': [_type_name(r) for r in (fn.get('return') or [])],
                    'target': f'{short_address(package_id)}::{module}::{name}',
                })
            return self._out({
                'package': package_id, 'module': module,
                'address': body.get('address'),
                'structs': sorted((body.get('structs') or {}).keys()),
                'functions': [f for f in functions
                              if f['visibility'] == 'Public' or f['entry']][:int(limit)],
                'friends_only': len([f for f in functions
                                     if f['visibility'] not in ('Public',) and not f['entry']]),
                'network': self.network,
            })
        rows = []
        for name, body in sorted(modules.items()):
            exposed = body.get('exposedFunctions') or {}
            callable_now = [f for f, fn in exposed.items()
                            if fn.get('visibility') == 'Public' or fn.get('isEntry')]
            rows.append({'module': name, 'functions': len(exposed),
                         'callable': len(callable_now),
                         'structs': len(body.get('structs') or {})})
        return self._out({
            'package': package_id, 'known_as': FRAMEWORK.get(short_address(package_id)),
            'modules': rows, 'module_count': len(rows),
            'network': self.network, 'explorer': self.explorer('object', package_id),
            'note': 'pass module= to see its callable functions and their signatures',
        })

    # ── writing ──────────────────────────────────────────────────

    def gas_coins(self, address, need):
        """SUI objects that can actually pay for gas.

        `suix_getCoins` also reports balances held in Sui's address accumulator,
        with synthetic object digests. Those are real money but they are not
        objects, and passing one as a transaction input fails with a "withdraw
        reservation" error that never says which coin it meant — so they are
        filtered out here and reported separately.
        """
        page = self.call('suix_getCoins', [address, SUI_TYPE, None, 50])
        all_coins = page.get('data') or []
        usable = spendable(all_coins)
        held_off_object = sum(int(c['balance']) for c in all_coins if is_synthetic(c))
        picked, total = [], 0
        for coin in usable:
            picked.append(coin)
            total += int(coin['balance'])
            if total >= need:
                break
        return picked, total, held_off_object

    def transfer(self, to, amount, coin_type=SUI_TYPE, wallet=None, secret=None,
                 confirm=False, dry_run=False):
        """Send SUI or any coin, signed here, dry-run first, guarded by value.

        The dry run is not optional: it is how the gas budget is computed, and
        it is the only chance to see the transaction fail before it costs
        anything.
        """
        seed, sender = signer(wallet, secret)
        recipient = to.strip()
        suins = None
        if recipient.endswith('.sui') or recipient.startswith('@'):
            suins = recipient
            resolved = self.call('suix_resolveNameServiceAddress',
                                 [recipient[1:] + '.sui' if recipient.startswith('@')
                                  else recipient])
            if not resolved:
                raise SuiError(f'SuiNS has no record for {suins!r}', status=404)
            recipient = resolved
        recipient = normalize(recipient, 'recipient')
        if recipient == sender:
            raise SuiError('sender and recipient are the same address')

        wanted, _ = self.resolve_type(coin_type)
        is_sui = wanted == norm_type(SUI_TYPE)
        decimals = self._decimals(wanted)
        meta = self.metadata([wanted])[wanted]
        try:
            base_units = int(round(float(amount) * (10 ** decimals)))
        except (TypeError, ValueError):
            raise SuiError(f'amount must be a number of {meta.get("symbol") or "coin"}, '
                           f'got {amount!r}')
        if base_units <= 0:
            raise SuiError('amount must be greater than zero')

        gas_price = int(self.call('suix_getReferenceGasPrice') or 1000)
        provisional_budget = 50_000_000

        if is_sui:
            gas, gas_total, off_object = self.gas_coins(
                sender, base_units + provisional_budget)
            coins = None
            if gas_total < base_units + provisional_budget:
                raise SuiError(self._gas_shortfall(
                    sender, gas_total, base_units + provisional_budget, off_object,
                    decimals), status=400)
        else:
            gas, gas_total, off_object = self.gas_coins(sender, provisional_budget)
            if gas_total < provisional_budget:
                raise SuiError(self._gas_shortfall(sender, gas_total,
                                                   provisional_budget, off_object, 9),
                               status=400)
            page = self.call('suix_getCoins', [sender, wanted, None, 50])
            usable = spendable(page.get('data') or [])
            coins, held = [], 0
            for coin in usable:
                coins.append(coin)
                held += int(coin['balance'])
                if held >= base_units:
                    break
            if held < base_units:
                raise SuiError(
                    f'{sui(held, decimals)} {meta.get("symbol")} available in coin '
                    f'objects, {sui(base_units, decimals)} requested'
                    + (f' (a further {sui(sum(int(c["balance"]) for c in (page.get("data") or []) if is_synthetic(c)), decimals)} '
                       'sits in the address balance, which this module cannot spend '
                       'as an input yet)' if any(is_synthetic(c) for c in (page.get('data') or [])) else ''),
                    status=400)

        def build(budget):
            return build_transfer(sender, recipient, base_units, gas, gas_price,
                                  budget, coins=coins)

        import base64
        probe = build(provisional_budget)
        simulation = self.call('sui_dryRunTransactionBlock',
                               [base64.b64encode(probe).decode()])
        effects = (simulation or {}).get('effects') or {}
        sim_status = (effects.get('status') or {}).get('status')
        if sim_status != 'success':
            raise SuiError(
                f'the network rejected this transfer in simulation: '
                f'{(effects.get("status") or {}).get("error") or sim_status}. '
                'Nothing was sent.', status=400,
                detail={'balance_changes': simulation.get('balanceChanges')})
        used = (int(effects.get('gasUsed', {}).get('computationCost') or 0) +
                int(effects.get('gasUsed', {}).get('storageCost') or 0))
        budget = max(2_000_000, int(used * 1.25))
        tx_bytes = build(budget)
        encoded = base64.b64encode(tx_bytes).decode()

        price = (self.prices([wanted]).get(wanted) or {}).get('usd')
        human = sui(base_units, decimals)
        value = _usd(human, price)
        plan = {
            'network': self.network, 'from': sender, 'to': recipient, 'suins': suins,
            'amount': human, 'symbol': meta.get('symbol') or type_symbol(wanted),
            'coin_type': wanted, 'raw': str(base_units), 'usd': value,
            'gas_budget_sui': sui(budget), 'gas_price_mist': gas_price,
            'estimated_gas_sui': sui(used),
            'gas_coins': len(gas), 'input_coins': len(coins or []),
            'simulated': 'success', 'digest': digest_of(tx_bytes),
            'simulated_balance_changes': simulation.get('balanceChanges'),
        }
        if dry_run:
            return self._out({**plan, 'executed': False,
                              'note': 'dry run only — signed nothing, sent nothing'})
        if value is not None and value > SPEND_USD and not confirm:
            return self._out({**plan, 'needs_confirm': True, 'executed': False,
                              'guard_usd': SPEND_USD,
                              'note': f'this moves ${value:,.2f}, over the '
                                      f'${SPEND_USD:,.0f} guard. Nothing was signed. '
                                      'Call again with confirm=true.'})

        signature = sign_transaction(tx_bytes, seed)
        result = self.call('sui_executeTransactionBlock', [
            encoded, [signature],
            {'showEffects': True, 'showBalanceChanges': True},
            'WaitForLocalExecution'])
        result_effects = (result or {}).get('effects') or {}
        result_status = (result_effects.get('status') or {}).get('status')
        return self._out({
            **plan, 'executed': True,
            'digest': (result or {}).get('digest') or plan['digest'],
            'status': result_status, 'success': result_status == 'success',
            'error': (result_effects.get('status') or {}).get('error'),
            'gas_used_sui': sui(
                int(result_effects.get('gasUsed', {}).get('computationCost') or 0) +
                int(result_effects.get('gasUsed', {}).get('storageCost') or 0) -
                int(result_effects.get('gasUsed', {}).get('storageRebate') or 0)),
            'balance_changes': (result or {}).get('balanceChanges'),
            'explorer': self.explorer('tx', (result or {}).get('digest') or ''),
        })

    def _gas_shortfall(self, sender, have, need, off_object, decimals):
        message = (f'not enough SUI in spendable coin objects: '
                   f'{sui(have)} available, {sui(need)} needed for the amount plus '
                   f'a gas budget')
        if off_object:
            message += (f'. A further {sui(off_object)} SUI is in this address\'s '
                        'balance accumulator rather than in coin objects — Sui '
                        'reports it in suix_getCoins with a synthetic digest, and it '
                        'cannot be used as a transaction input')
        return message

    def faucet(self, address=None, wallet=None):
        """Test SUI. There is no mainnet faucet and never will be."""
        if self.network not in FAUCETS:
            raise SuiError(f'no faucet on {self.network} — testnet and devnet only. '
                           'Mainnet SUI has to be bought.', status=400)
        if not address:
            _, address = signer(wallet)
        address = normalize(address)
        try:
            answer = _http(FAUCETS[self.network],
                           {'FixedAmountRequest': {'recipient': address}}, timeout=45)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise SuiError('the faucet is rate-limiting this box, not refusing '
                               'the address — wait a minute and ask again',
                               status=429)
            raise SuiError(f'faucet returned HTTP {e.code}: '
                           f'{(e.read() or b"")[:200].decode("utf-8", "replace")}',
                           status=502)
        except Exception as e:
            raise SuiError(f'faucet unreachable: {type(e).__name__}', status=502)
        return self._out({'network': self.network, 'address': address,
                          'requested': True, 'response': answer,
                          'note': 'coins take a few seconds to land'})

    def rpc(self, method, params=None):
        """Any Sui JSON-RPC method, raw — the escape hatch."""
        if isinstance(params, str):
            params = json.loads(params)
        return {'network': self.network, 'rpc': self.endpoints[0],
                'method': method, 'result': self.call(method, params or [])}


def _type_name(node):
    """Move's normalized type JSON → something readable in one glance."""
    if isinstance(node, str):
        return node.lower()
    if not isinstance(node, dict):
        return str(node)
    if 'Struct' in node:
        s = node['Struct']
        name = f"{s.get('address')}::{s.get('module')}::{s.get('name')}"
        args = s.get('typeArguments') or []
        short_address = name.replace('0x' + '0' * 63, '0x')
        return short_address + (f"<{', '.join(_type_name(a) for a in args)}>"
                                if args else '')
    for wrapper, prefix in (('Reference', '&'), ('MutableReference', '&mut '),
                            ('Vector', 'vector<')):
        if wrapper in node:
            inner = _type_name(node[wrapper])
            return f'{prefix}{inner}>' if wrapper == 'Vector' else f'{prefix}{inner}'
    if 'TypeParameter' in node:
        return f"T{node['TypeParameter']}"
    return json.dumps(node)
