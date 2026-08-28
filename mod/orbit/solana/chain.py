#!/usr/bin/env python3
"""solana client — JSON-RPC, prices, and locally-signed transactions.

One class. Every REST route, every MCP tool and every console panel is a call
into it, so the three surfaces cannot answer the same question differently.

Two upstreams: a Solana JSON-RPC node (the chain) and Jupiter's public API
(what things are worth, and what a swap would actually get you). Neither needs
a key, so reading works out of the box; writing needs a key the caller controls.

Nothing here holds a house wallet. A transfer is signed in this process with a
seed that came from the caller, the operator's env, or the off-tree keystore,
and the signed bytes go straight to the node.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from keys import (SolError, b58decode, b58encode, find_program_address, is_address,
                  need_address, pubkey_of, sign, signer)

LAMPORTS = 1_000_000_000
NETWORKS = {
    'mainnet': 'https://api.mainnet-beta.solana.com',
    'devnet': 'https://api.devnet.solana.com',
    'testnet': 'https://api.testnet.solana.com',
}
ALIASES = {'mainnet-beta': 'mainnet', 'main': 'mainnet', 'm': 'mainnet',
           'dev': 'devnet', 'd': 'devnet', 'test': 'testnet', 't': 'testnet'}
JUP = 'https://lite-api.jup.ag'

SYSTEM = '11111111111111111111111111111111'
TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
ATA_PROGRAM = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'
STAKE_PROGRAM = 'Stake11111111111111111111111111111111111111'
VOTE_PROGRAM = 'Vote111111111111111111111111111111111111111'
MEMO = 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr'
WSOL = 'So11111111111111111111111111111111111111112'

# Symbols are not unique on Solana — anyone can mint a token called USDC, and
# the search index will happily return it. For the three where a wrong match
# means real money, the canonical mint is pinned here rather than looked up.
MAJORS = {
    'SOL': WSOL, 'WSOL': WSOL,
    'USDC': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
    'USDT': 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
}

KNOWN = {
    SYSTEM: 'System Program', TOKEN: 'SPL Token', TOKEN_2022: 'SPL Token-2022',
    ATA_PROGRAM: 'Associated Token Account', STAKE_PROGRAM: 'Stake Program',
    VOTE_PROGRAM: 'Vote Program', MEMO: 'Memo',
    'ComputeBudget111111111111111111111111111111': 'Compute Budget',
    'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4': 'Jupiter Aggregator v6',
    'JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB': 'Jupiter Aggregator v4',
    'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc': 'Orca Whirlpools',
    '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': 'Raydium AMM v4',
    'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK': 'Raydium CLMM',
    'srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX': 'OpenBook',
    'metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s': 'Metaplex Token Metadata',
    '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P': 'Pump.fun',
    'BPFLoaderUpgradeab1e11111111111111111111111': 'BPF Upgradeable Loader',
}

# A transfer worth more than this needs confirm=true. Cheap insurance against a
# fat-fingered amount or a tool call that misread a decimal point.
SPEND_USD = float(os.environ.get('SOLANA_SPEND_USD', '25') or 25)
UA = 'mod-solana/0.1 (+https://modc2.com/solana)'
_CACHE = {}
_CACHE_TTL = float(os.environ.get('SOLANA_CACHE_TTL', '60') or 60)


def _cached(key, ttl, produce):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = produce()
    _CACHE[key] = (time.time(), value)
    return value


def _http(url, body=None, headers=None, timeout=30, retries=1):
    """One request, with a single polite retry on 429.

    Both upstreams are free public endpoints and both throttle, so a 429 is a
    normal event rather than an error — but it is worth saying *which* host
    throttled, since the RPC node and the price API fail very differently.
    """
    host = urllib.parse.urlparse(url).netloc
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers={'user-agent': UA, 'accept': 'application/json',
                 **({'content-type': 'application/json'} if body is not None else {}),
                 **(headers or {})},
        method='POST' if body is not None else 'GET')
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b'null')
        except urllib.error.HTTPError as e:
            raw = (e.read() or b'').decode('utf-8', 'replace')[:1000]
            try:
                detail = json.loads(raw)
            except Exception:
                detail = raw
            if e.code == 429:
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise SolError(
                    f'{host} is rate-limiting this box' +
                    (' — pass rpc=… with your own endpoint, or set SOLANA_RPC'
                     if 'jup.ag' not in host else ' — wait a few seconds and retry'),
                    status=429, detail=detail)
            raise SolError(f'{host} returned HTTP {e.code}',
                           status=e.code if e.code < 600 else 502, detail=detail)
        except urllib.error.URLError as e:
            raise SolError(f'cannot reach {host}: {e.reason}', status=502)
        except TimeoutError:
            raise SolError(f'{host} timed out after {timeout}s', status=504)


def sol(lamports):
    return round((lamports or 0) / LAMPORTS, 9)


def _usd(amount, price):
    return None if price in (None, 0) else round(amount * price, 4)


def _ago(unix_ts):
    if not unix_ts:
        return None
    s = int(time.time() - unix_ts)
    for size, name in ((86400, 'd'), (3600, 'h'), (60, 'm')):
        if abs(s) >= size:
            return f'{s // size}{name} ago'
    return f'{s}s ago'


class Client:
    """Everything the module knows how to ask a Solana node."""

    def __init__(self, network=None, rpc=None, timeout=30):
        name = ALIASES.get(str(network or '').lower(), str(network or '').lower())
        if network and name not in NETWORKS and not str(network).startswith('http'):
            raise SolError(f'unknown network {network!r} — one of {", ".join(NETWORKS)} '
                           f'or a full RPC url')
        if str(network or '').startswith('http'):
            rpc, name = rpc or network, 'custom'
        self.network = name or os.environ.get('SOLANA_NETWORK', 'mainnet')
        self.network = ALIASES.get(self.network, self.network)
        self.rpc = (rpc or os.environ.get('SOLANA_RPC') or
                    NETWORKS.get(self.network, NETWORKS['mainnet']))
        # An explicit endpoint that is not the named cluster's own IS a
        # different cluster — a local validator answering on 8899 is not
        # mainnet, and an answer that claims to be would be a lie.
        if rpc and rpc != NETWORKS.get(self.network):
            self.network = 'custom'
        self.timeout = timeout
        self._id = 0
        # Prices are a nicety layered on top of chain data: when Jupiter is
        # throttling, the balances stay right and the USD columns go null. That
        # is only acceptable if the answer SAYS so rather than implying zero.
        self.warnings = []

    # ── transports ───────────────────────────────────────────────

    def call(self, method, params=None):
        """One JSON-RPC call. Node-level errors surface as SolError, not as a
        success payload with an `error` key buried in it."""
        self._id += 1
        out = _http(self.rpc, {'jsonrpc': '2.0', 'id': self._id, 'method': method,
                               'params': params or []}, timeout=self.timeout)
        if isinstance(out, dict) and out.get('error'):
            err = out['error']
            msg = err.get('message') if isinstance(err, dict) else str(err)
            raise SolError(f'{method}: {msg}', status=400, detail=err)
        return (out or {}).get('result')

    def batch(self, calls):
        """Several RPC calls in one round trip — the difference between a
        portfolio that takes 200ms and one that takes two seconds."""
        if not calls:
            return []
        body = [{'jsonrpc': '2.0', 'id': i, 'method': m, 'params': p or []}
                for i, (m, p) in enumerate(calls)]
        out = _http(self.rpc, body, timeout=self.timeout)
        if isinstance(out, dict):
            raise SolError(f"batch rejected: {out.get('error') or out}", status=400)
        by_id = {r.get('id'): r for r in (out or [])}
        return [None if by_id.get(i, {}).get('error') else by_id.get(i, {}).get('result')
                for i in range(len(calls))]

    def jup(self, path, params=None, timeout=None):
        url = f'{JUP}{path}'
        if params:
            url += '?' + urllib.parse.urlencode({k: v for k, v in params.items()
                                                 if v not in (None, '')})
        return _http(url, timeout=timeout or self.timeout)


    def _warn(self, reason):
        if reason not in self.warnings:
            self.warnings.append(reason)

    def _out(self, payload):
        return {**payload, 'warnings': list(self.warnings)} if self.warnings else payload

    # ── prices ───────────────────────────────────────────────────

    def prices(self, mints):
        """USD per token for up to 50 mints, from Jupiter. Cached briefly —
        a portfolio and the balance under it should not disagree by a tick."""
        mints = [m for m in dict.fromkeys(mints) if m]
        if not mints:
            return {}
        out = {}
        for i in range(0, len(mints), 50):
            chunk = mints[i:i + 50]
            key = 'price:' + ','.join(sorted(chunk))
            try:
                data = _cached(key, _CACHE_TTL,
                               lambda c=chunk: self.jup('/price/v3', {'ids': ','.join(c)}))
            except SolError as e:
                self._warn(f'prices unavailable ({e}) — usd fields are null, '
                           'the on-chain amounts are not')
                continue
            for mint, row in (data or {}).items():
                if isinstance(row, dict) and row.get('usdPrice') is not None:
                    out[mint] = {'usd': row['usdPrice'],
                                 'change_24h_pct': row.get('priceChange24h'),
                                 'liquidity_usd': row.get('liquidity'),
                                 'decimals': row.get('decimals')}
        return out

    def sol_price(self):
        return (self.prices([WSOL]).get(WSOL) or {}).get('usd')

    def meta(self, mints):
        """Symbol, name and icon for mints, from Jupiter's token search."""
        mints = [m for m in dict.fromkeys(mints) if m]
        out = {}
        for i in range(0, len(mints), 50):
            chunk = mints[i:i + 50]
            try:
                rows = _cached('meta:' + ','.join(sorted(chunk)), 900,
                               lambda c=chunk: self.jup('/tokens/v2/search',
                                                        {'query': ','.join(c)}))
            except SolError as e:
                self._warn(f'token metadata unavailable ({e}) — symbols and names '
                           'are null, mints are not')
                continue
            for row in rows or []:
                out[row.get('id')] = {
                    'symbol': row.get('symbol'), 'name': row.get('name'),
                    'icon': row.get('icon'), 'decimals': row.get('decimals'),
                    'token_program': row.get('tokenProgram'),
                    'holders': row.get('holderCount'),
                    'verified': bool(row.get('isVerified') or row.get('tags')),
                }
        return out

    def resolve(self, query):
        """A symbol or a mint in, a mint out. Symbols are ambiguous on Solana —
        anyone can name a token USDC — so ties break on liquidity, and the mint
        that won comes back with the answer so the caller can see the choice."""
        query = str(query).strip()
        if is_address(query):
            return query
        if query.upper() in MAJORS:
            return MAJORS[query.upper()]
        rows = _cached('resolve:' + query.lower(), 900,
                       lambda: self.jup('/tokens/v2/search', {'query': query})) or []
        rows = [r for r in rows if r.get('id')]
        rows.sort(key=lambda r: ((r.get('symbol') or '').upper() != query.upper(),
                                 -(r.get('liquidity') or 0)))
        return rows[0]['id'] if rows else None

    def price(self, ids):
        """Price by mint or by symbol — a symbol is resolved through the search
        index first, and the mint it resolved to comes back with the answer."""
        wanted = [s.strip() for s in (ids.split(',') if isinstance(ids, str) else ids)
                  if str(s).strip()]
        if not wanted:
            raise SolError('give at least one mint address or token symbol')
        mints, resolved = [], {}
        for item in wanted:
            resolved[item] = self.resolve(item)
            if resolved[item]:
                mints.append(resolved[item])
        px, md = self.prices(mints), self.meta(mints)
        out = []
        for item in wanted:
            mint = resolved.get(item)
            if not mint:
                out.append({'query': item, 'error': 'no token matched'})
                continue
            p = px.get(mint) or {}
            out.append({'query': item, 'mint': mint,
                        'symbol': (md.get(mint) or {}).get('symbol'),
                        'name': (md.get(mint) or {}).get('name'),
                        'usd': p.get('usd'), 'change_24h_pct': p.get('change_24h_pct'),
                        'liquidity_usd': p.get('liquidity_usd')})
        return self._out({'network': self.network, 'prices': out})

    # ── reading accounts ─────────────────────────────────────────

    def balance(self, address):
        """SOL for one address or a comma-separated list, priced."""
        addrs = [need_address(a.strip(), 'address') for a in
                 (address.split(',') if isinstance(address, str) else address)
                 if str(a).strip()]
        if not addrs:
            raise SolError('address is required')
        results = self.batch([('getBalance', [a]) for a in addrs])
        px = self.sol_price()
        rows = []
        for addr, res in zip(addrs, results):
            lam = (res or {}).get('value') if isinstance(res, dict) else None
            rows.append({'address': addr, 'lamports': lam, 'sol': sol(lam),
                         'usd': _usd(sol(lam), px)})
        return self._out({'network': self.network, 'sol_price_usd': px,
                          'total_sol': round(sum(r['sol'] for r in rows), 9),
                          'balances': rows} if len(rows) > 1 else
                         {'network': self.network, 'sol_price_usd': px, **rows[0]})

    def token_accounts(self, owner):
        """Every SPL position an owner holds, across both token programs."""
        owner = need_address(owner, 'owner')
        out = []
        for program in (TOKEN, TOKEN_2022):
            res = self.call('getTokenAccountsByOwner',
                            [owner, {'programId': program},
                             {'encoding': 'jsonParsed'}]) or {}
            for row in res.get('value') or []:
                info = (((row.get('account') or {}).get('data') or {})
                        .get('parsed') or {}).get('info') or {}
                amt = info.get('tokenAmount') or {}
                out.append({'account': row.get('pubkey'), 'mint': info.get('mint'),
                            'amount': float(amt.get('uiAmount') or 0),
                            'raw': amt.get('amount'), 'decimals': amt.get('decimals'),
                            'program': program, 'state': info.get('state'),
                            'rent_lamports': (row.get('account') or {}).get('lamports')})
        return out

    def portfolio(self, address, min_usd=0.01, include_dust=False, limit=200):
        """What a wallet is actually worth: SOL plus every priced SPL position,
        largest first. Unpriced and dust rows are counted, not padded into the
        list — the total is what you could sell, not what you hold."""
        address = need_address(address, 'address')
        lam = (self.call('getBalance', [address]) or {}).get('value')
        positions = self.token_accounts(address)
        held = [p for p in positions if p['amount'] > 0]
        px = self.prices([WSOL] + [p['mint'] for p in held])
        md = self.meta([p['mint'] for p in held])
        sol_px = (px.get(WSOL) or {}).get('usd')

        # One mint can sit in several token accounts; a holder thinks in mints.
        merged = {}
        for p in held:
            row = merged.setdefault(p['mint'], {**p, 'accounts': []})
            if row is not p:
                row['amount'] += p['amount']
            row['accounts'].append(p['account'])
        tokens, dust, unpriced = [], 0.0, 0
        for mint, p in merged.items():
            price = (px.get(mint) or {}).get('usd')
            value = _usd(p['amount'], price)
            if price is None:
                unpriced += 1
            row = {'mint': mint, 'symbol': (md.get(mint) or {}).get('symbol'),
                   'name': (md.get(mint) or {}).get('name'),
                   'amount': p['amount'], 'decimals': p['decimals'],
                   'price_usd': price, 'value_usd': value,
                   'change_24h_pct': (px.get(mint) or {}).get('change_24h_pct'),
                   'accounts': p['accounts'],
                   'token_2022': p['program'] == TOKEN_2022}
            if not include_dust and (value or 0) < float(min_usd) and price is not None:
                dust += value or 0
                continue
            tokens.append(row)
        tokens.sort(key=lambda r: -(r['value_usd'] or 0))
        tokens = tokens[:int(limit)]
        token_usd = round(sum(t['value_usd'] or 0 for t in tokens), 2)
        sol_usd = _usd(sol(lam), sol_px) or 0
        return self._out({
            'network': self.network, 'address': address,
            'sol': sol(lam), 'sol_usd': round(sol_usd, 2), 'sol_price_usd': sol_px,
            'token_usd': token_usd, 'total_usd': round(sol_usd + token_usd, 2),
            'token_count': len(merged), 'shown': len(tokens),
            'unpriced_tokens': unpriced,
            'hidden_dust_usd': round(dust, 4) if dust else 0,
            'empty_accounts': len(positions) - len(held),
            'tokens': tokens,
        })

    def account(self, address):
        """What an address *is*. The same call answers 'is this a wallet, a
        mint, a token account, a program or a stake account' — which is the
        question you actually have when you paste an address you don't know."""
        address = need_address(address, 'address')
        res = self.call('getAccountInfo', [address, {'encoding': 'jsonParsed'}]) or {}
        value = res.get('value')
        if value is None:
            on_curve_ = len(b58decode(address)) == 32
            return {'network': self.network, 'address': address, 'exists': False,
                    'kind': 'unused', 'lamports': 0, 'sol': 0,
                    'note': 'no account at this address — never funded, or closed. '
                            'A system wallet only exists once it holds lamports.'
                            if on_curve_ else 'no account at this address'}
        owner = value.get('owner')
        data = value.get('data') or {}
        parsed = data.get('parsed') if isinstance(data, dict) else None
        info = (parsed or {}).get('info') if isinstance(parsed, dict) else None
        kind = 'wallet' if owner == SYSTEM else \
            'program' if value.get('executable') else \
            (parsed or {}).get('type') if parsed else 'data'
        out = {'network': self.network, 'address': address, 'exists': True,
               'kind': kind, 'owner': owner, 'owner_name': KNOWN.get(owner),
               'executable': bool(value.get('executable')),
               'lamports': value.get('lamports'), 'sol': sol(value.get('lamports')),
               'data_bytes': value.get('space'),
               'program': (data or {}).get('program') if isinstance(data, dict) else None}
        px = self.sol_price()
        out['usd'] = _usd(out['sol'], px)
        if kind == 'wallet':
            positions = [p for p in self.token_accounts(address) if p['amount'] > 0]
            out['token_positions'] = len(positions)
            out['hint'] = 'sol_portfolio for what it holds, sol_history for what it did'
        if kind == 'mint' and info:
            supply = int(info.get('supply') or 0) / (10 ** int(info.get('decimals') or 0))
            md = (self.meta([address]) or {}).get(address) or {}
            p = (self.prices([address]) or {}).get(address) or {}
            out['mint'] = {'symbol': md.get('symbol'), 'name': md.get('name'),
                           'decimals': info.get('decimals'), 'supply': supply,
                           'price_usd': p.get('usd'),
                           'market_cap_usd': _usd(supply, p.get('usd')),
                           'mint_authority': info.get('mintAuthority'),
                           'freeze_authority': info.get('freezeAuthority'),
                           'holders': md.get('holders')}
        if kind == 'account' and info:                      # a token account
            amt = info.get('tokenAmount') or {}
            out['token_account'] = {'mint': info.get('mint'), 'owner': info.get('owner'),
                                    'amount': amt.get('uiAmount'),
                                    'state': info.get('state')}
        if info and kind not in ('mint', 'account'):
            out['parsed'] = info
        return self._out(out)

    def token(self, mint):
        """A mint in full: supply and authorities from the chain, price,
        liquidity and holder count from Jupiter."""
        mint = need_address(mint, 'mint')
        acct = self.account(mint)
        if acct.get('kind') != 'mint':
            raise SolError(f'{mint} is not a token mint — it is a '
                           f'{acct.get("kind")}. Use sol_account for it.', status=400)
        md = (self.meta([mint]) or {}).get(mint) or {}
        p = (self.prices([mint]) or {}).get(mint) or {}
        m = acct['mint']
        return self._out({
            'network': self.network, 'mint': mint,
            'symbol': m.get('symbol'), 'name': m.get('name'), 'icon': md.get('icon'),
            'decimals': m.get('decimals'), 'supply': m.get('supply'),
            'price_usd': p.get('usd'), 'change_24h_pct': p.get('change_24h_pct'),
            'liquidity_usd': p.get('liquidity_usd'),
            'market_cap_usd': m.get('market_cap_usd'), 'holders': md.get('holders'),
            'mint_authority': m.get('mint_authority'),
            'freeze_authority': m.get('freeze_authority'),
            'token_2022': acct.get('owner') == TOKEN_2022,
            'risk': [r for r in (
                'mint authority is live — supply can still be inflated'
                if m.get('mint_authority') else None,
                'freeze authority is live — your account can be frozen'
                if m.get('freeze_authority') else None,
                'thin liquidity — a market order will move the price'
                if (p.get('liquidity_usd') or 0) < 25_000 else None,
            ) if r],
        })

    # ── history ──────────────────────────────────────────────────

    def history(self, address, limit=20, before=None, until=None, detail=False):
        """Recent signatures for an address, newest first. `detail` fetches and
        summarises each transaction, which costs one RPC call apiece."""
        address = need_address(address, 'address')
        limit = max(1, min(int(limit), 100))
        opts = {'limit': limit}
        if before:
            opts['before'] = before
        if until:
            opts['until'] = until
        rows = self.call('getSignaturesForAddress', [address, opts]) or []
        out = [{'signature': r.get('signature'), 'slot': r.get('slot'),
                'time': r.get('blockTime'), 'age': _ago(r.get('blockTime')),
                'ok': r.get('err') is None, 'error': r.get('err'),
                'memo': r.get('memo'), 'status': r.get('confirmationStatus')}
               for r in rows]
        if detail and out:
            for row, tx in zip(out, self.batch(
                    [('getTransaction', [r['signature'],
                                         {'encoding': 'jsonParsed',
                                          'maxSupportedTransactionVersion': 0}])
                     for r in out])):
                if tx:
                    row.update(self._summarise(tx, focus=address))
        return {'network': self.network, 'address': address, 'count': len(out),
                'next_before': out[-1]['signature'] if len(out) == limit else None,
                'transactions': out}

    def tx(self, signature, logs=False):
        """One transaction, read the way a human reads it: who paid, what moved,
        which programs ran — not a wall of account indexes."""
        if not isinstance(signature, str) or len(signature.strip()) < 80:
            raise SolError('signature must be a base58 transaction signature (~88 chars)')
        signature = signature.strip()
        raw = self.call('getTransaction',
                        [signature, {'encoding': 'jsonParsed',
                                     'maxSupportedTransactionVersion': 0}])
        if raw is None:
            raise SolError('no transaction with that signature on '
                           f'{self.network} — it may be unconfirmed, dropped, or older '
                           'than this node\'s history', status=404)
        out = {'network': self.network, 'signature': signature,
               **self._summarise(raw, verbose=True)}
        if logs:
            out['logs'] = ((raw.get('meta') or {}).get('logMessages') or [])
        return out

    def _summarise(self, raw, focus=None, verbose=False):
        meta = raw.get('meta') or {}
        msg = (raw.get('transaction') or {}).get('message') or {}
        keys = [k.get('pubkey') if isinstance(k, dict) else k
                for k in (msg.get('accountKeys') or [])]
        fee_payer = keys[0] if keys else None
        pre, post = meta.get('preBalances') or [], meta.get('postBalances') or []
        moves = []
        for i, key in enumerate(keys):
            if i < len(pre) and i < len(post) and pre[i] != post[i]:
                delta = post[i] - pre[i]
                moves.append({'address': key, 'sol': sol(delta),
                              'is_fee_payer': i == 0})
        moves.sort(key=lambda m: -abs(m['sol']))

        # Token balances arrive as before/after snapshots per account index;
        # what anyone wants is the delta per (owner, mint).
        def by_index(rows):
            return {r.get('accountIndex'): r for r in rows or []}
        pre_t, post_t = by_index(meta.get('preTokenBalances')), \
            by_index(meta.get('postTokenBalances'))
        token_moves = []
        for idx in sorted(set(pre_t) | set(post_t)):
            a, b = pre_t.get(idx) or {}, post_t.get(idx) or {}
            ref = b or a
            before = float((a.get('uiTokenAmount') or {}).get('uiAmount') or 0)
            after = float((b.get('uiTokenAmount') or {}).get('uiAmount') or 0)
            if before != after:
                token_moves.append({'owner': ref.get('owner'), 'mint': ref.get('mint'),
                                    'amount': round(after - before, 9),
                                    'account': keys[idx] if idx < len(keys) else None})
        if token_moves:
            md = self.meta([t['mint'] for t in token_moves])
            for t in token_moves:
                t['symbol'] = (md.get(t['mint']) or {}).get('symbol')
        token_moves.sort(key=lambda t: -abs(t['amount']))

        ins = msg.get('instructions') or []
        actions, programs = [], []
        for i in ins:
            pid = i.get('programId')
            if pid and pid not in programs:
                programs.append(pid)
            parsed = i.get('parsed')
            if isinstance(parsed, dict):
                actions.append({'program': i.get('program') or KNOWN.get(pid) or pid,
                                'type': parsed.get('type'),
                                **({'info': parsed.get('info')} if verbose else {})})
            else:
                actions.append({'program': KNOWN.get(pid) or i.get('program') or pid,
                                'type': 'unparsed'})
        err = meta.get('err')
        out = {'slot': raw.get('slot'), 'time': raw.get('blockTime'),
               'age': _ago(raw.get('blockTime')), 'ok': err is None, 'error': err,
               'fee_sol': sol(meta.get('fee')), 'fee_payer': fee_payer,
               'compute_units': meta.get('computeUnitsConsumed'),
               'version': raw.get('version'),
               'programs': [{'id': p, 'name': KNOWN.get(p)} for p in programs],
               'sol_moves': moves[:20], 'token_moves': token_moves[:20],
               'actions': actions[:30] if verbose else
                          [a['type'] for a in actions][:12]}
        if focus:
            mine = next((m for m in moves if m['address'] == focus), None)
            out['net_sol'] = mine['sol'] if mine else 0
            out['my_token_moves'] = [t for t in token_moves if t['owner'] == focus]
        if err and not verbose:
            out['summary'] = 'failed'
        return out

    # ── network ──────────────────────────────────────────────────

    def status(self):
        """Where the chain is right now, and how fast it is going."""
        epoch, version, supply, samples, health, inflation, blockhash = self.batch([
            ('getEpochInfo', []), ('getVersion', []),
            ('getSupply', [{'excludeNonCirculatingAccountsList': True}]),
            ('getRecentPerformanceSamples', [5]), ('getHealth', []),
            ('getInflationRate', []), ('getLatestBlockhash', []),
        ])
        epoch = epoch or {}
        samples = samples or []
        secs = sum(s.get('samplePeriodSecs') or 0 for s in samples) or 1
        tps = sum(s.get('numTransactions') or 0 for s in samples) / secs
        real_tps = sum(s.get('numNonVoteTransactions') or 0 for s in samples) / secs
        sv = ((supply or {}).get('value') or {})
        slots = epoch.get('slotsInEpoch') or 1
        done = epoch.get('slotIndex') or 0
        px = self.sol_price()
        return self._out({
            'network': self.network, 'rpc': self.rpc,
            'healthy': health == 'ok', 'health': health,
            'version': (version or {}).get('solana-core'),
            'slot': epoch.get('absoluteSlot'), 'block_height': epoch.get('blockHeight'),
            'epoch': epoch.get('epoch'),
            'epoch_progress_pct': round(100 * done / slots, 2),
            'epoch_ends_in_hours': round((slots - done) * 0.4 / 3600, 1),
            'tps': round(tps, 1), 'tps_non_vote': round(real_tps, 1),
            'total_transactions': epoch.get('transactionCount'),
            'blockhash': (blockhash or {}).get('value', {}).get('blockhash'),
            'sol_price_usd': px,
            'supply': {'circulating': sol(sv.get('circulating')),
                       'total': sol(sv.get('total')),
                       'market_cap_usd': _usd(sol(sv.get('circulating')), px)},
            'inflation_pct': round(100 * (inflation or {}).get('total', 0), 3),
        })

    def validators(self, limit=20, sort='stake', delinquent=None):
        """Who is producing blocks, ordered by stake, with the decentralisation
        number that matters: how few validators could halt the chain."""
        res = self.call('getVoteAccounts') or {}
        rows = []
        for group, flag in (('current', False), ('delinquent', True)):
            for v in res.get(group) or []:
                rows.append({'vote_account': v.get('votePubkey'),
                             'identity': v.get('nodePubkey'),
                             'stake_sol': sol(v.get('activatedStake')),
                             'commission_pct': v.get('commission'),
                             'delinquent': flag,
                             'last_vote': v.get('lastVote'),
                             'root_slot': v.get('rootSlot')})
        total = sum(r['stake_sol'] for r in rows) or 1
        for r in rows:
            r['stake_pct'] = round(100 * r['stake_sol'] / total, 4)
        if delinquent is not None:
            rows = [r for r in rows if r['delinquent'] == bool(delinquent)]
        rows.sort(key=lambda r: -r['stake_sol'] if sort == 'stake' else
                  (r['commission_pct'], -r['stake_sol']))
        # Nakamoto coefficient: validators needed to pass the 33.4% halt threshold.
        running, naka = 0.0, 0
        for r in sorted(rows, key=lambda r: -r['stake_sol']):
            running += r['stake_pct']
            naka += 1
            if running > 33.4:
                break
        return {'network': self.network, 'validators': len(rows),
                'delinquent': sum(1 for r in rows if r['delinquent']),
                'total_stake_sol': round(total, 2),
                'nakamoto_coefficient': naka,
                'top': rows[:max(1, min(int(limit), 200))]}

    def stakes(self, address):
        """Stake accounts a wallet can withdraw from, and what they are doing."""
        address = need_address(address, 'address')
        rows = self.call('getProgramAccounts', [STAKE_PROGRAM, {
            'encoding': 'jsonParsed',
            'filters': [{'memcmp': {'offset': 44, 'bytes': address}}]}]) or []
        epoch = (self.call('getEpochInfo') or {}).get('epoch')
        out = []
        for row in rows:
            info = ((((row.get('account') or {}).get('data') or {}).get('parsed')
                     or {}).get('info') or {})
            stake = (info.get('stake') or {}).get('delegation') or {}
            lam = (row.get('account') or {}).get('lamports')
            activate = stake.get('activationEpoch')
            deactivate = stake.get('deactivationEpoch')
            far = str(deactivate) in ('18446744073709551615', 'None')
            out.append({
                'stake_account': row.get('pubkey'), 'sol': sol(lam),
                'validator': stake.get('voter'),
                'delegated_sol': sol(int(stake.get('stake') or 0)),
                'state': 'inactive' if not stake else
                         'activating' if str(activate) == str(epoch) else
                         'deactivating' if not far and int(deactivate) >= (epoch or 0)
                         else 'active' if far else 'deactivated',
                'activation_epoch': activate,
                'staker': ((info.get('meta') or {}).get('authorized') or {}).get('staker'),
            })
        out.sort(key=lambda r: -r['sol'])
        px = self.sol_price()
        total = round(sum(r['sol'] for r in out), 9)
        return {'network': self.network, 'address': address, 'epoch': epoch,
                'accounts': len(out), 'total_sol': total, 'total_usd': _usd(total, px),
                'stakes': out}

    # ── swaps ────────────────────────────────────────────────────

    def _route(self, input_mint, output_mint, amount, slippage_bps=50):
        """Resolve both sides and ask Jupiter for its best route.

        Split out of quote() because a swap needs the *raw* quote object back —
        Jupiter builds the transaction from the exact route it priced, so
        re-quoting between the price and the signature would trade something
        the caller never saw.
        """
        pair = []
        for side, val in (('input_mint', input_mint), ('output_mint', output_mint)):
            mint = self.resolve(val)
            if not mint:
                raise SolError(f'no token matched {side}={val!r} — pass a mint address')
            pair.append(mint)
        in_mint, out_mint = pair
        if in_mint == out_mint:
            raise SolError('both sides of the swap are the same mint')
        md = self.meta([in_mint, out_mint])
        in_dec = (md.get(in_mint) or {}).get('decimals')
        out_dec = (md.get(out_mint) or {}).get('decimals')
        if in_dec is None:
            in_dec = (self.account(in_mint).get('mint') or {}).get('decimals') or 0
        if out_dec is None:
            out_dec = (self.account(out_mint).get('mint') or {}).get('decimals') or 0
        raw_in = int(round(float(amount) * 10 ** int(in_dec)))
        if raw_in <= 0:
            raise SolError('amount must be greater than zero')
        q = self.jup('/swap/v1/quote', {'inputMint': in_mint, 'outputMint': out_mint,
                                        'amount': raw_in,
                                        'slippageBps': int(slippage_bps)})
        return q, in_mint, out_mint, md, int(in_dec), int(out_dec)

    def quote(self, input_mint, output_mint, amount, slippage_bps=50):
        """What one token actually gets you in another, through Jupiter's best
        route — the honest price including impact, not the mid."""
        q, in_mint, out_mint, md, in_dec, out_dec = self._route(
            input_mint, output_mint, amount, slippage_bps)
        got = int(q.get('outAmount') or 0) / 10 ** int(out_dec)
        worst = int(q.get('otherAmountThreshold') or 0) / 10 ** int(out_dec)
        route = [{'amm': (h.get('swapInfo') or {}).get('label'),
                  'percent': h.get('percent')} for h in (q.get('routePlan') or [])]
        px = self.prices([in_mint, out_mint])
        return {
            'network': self.network,
            'sell': {'mint': in_mint, 'symbol': (md.get(in_mint) or {}).get('symbol'),
                     'amount': float(amount),
                     'usd': _usd(float(amount), (px.get(in_mint) or {}).get('usd'))},
            'buy': {'mint': out_mint, 'symbol': (md.get(out_mint) or {}).get('symbol'),
                    'amount': round(got, 9),
                    'usd': _usd(got, (px.get(out_mint) or {}).get('usd'))},
            'rate': round(got / float(amount), 9) if float(amount) else None,
            'worst_case_out': round(worst, 9),
            'price_impact_pct': float(q.get('priceImpactPct') or 0),
            'slippage_bps': int(slippage_bps),
            'route': route, 'hops': len(route),
            'note': 'a quote, not a swap — sol_swap signs and sends this same '
                    'route with a key from the local keystore.',
        }

    def swap(self, input_mint, output_mint, amount, slippage_bps=50, wallet=None,
             secret=None, confirm=False, wait=True, priority_lamports=None,
             dry_run=False):
        """Trade one token for another on Solana, for real.

        Jupiter prices the route and builds the transaction; the signing happens
        here, with a key from the local keystore, and the bytes that get signed
        are the bytes that came back — nothing is re-quoted in between. Guarded
        the same way a transfer is: over SOLANA_SPEND_USD it returns
        needs_confirm instead of trading.
        """
        if self.network != 'mainnet':
            raise SolError(f'Jupiter routes mainnet liquidity only — this client '
                           f'is on {self.network}')
        seed, address = signer(wallet, secret)
        q, in_mint, out_mint, md, in_dec, out_dec = self._route(
            input_mint, output_mint, amount, slippage_bps)

        got = int(q.get('outAmount') or 0) / 10 ** out_dec
        worst = int(q.get('otherAmountThreshold') or 0) / 10 ** out_dec
        px = self.prices([in_mint, out_mint])
        spend_usd = _usd(float(amount), (px.get(in_mint) or {}).get('usd'))
        plan = {
            'network': self.network, 'wallet': address,
            'sell': {'mint': in_mint, 'symbol': (md.get(in_mint) or {}).get('symbol'),
                     'amount': float(amount), 'usd': spend_usd},
            'buy': {'mint': out_mint, 'symbol': (md.get(out_mint) or {}).get('symbol'),
                    'amount': round(got, 9),
                    'usd': _usd(got, (px.get(out_mint) or {}).get('usd'))},
            'worst_case_out': round(worst, 9),
            'price_impact_pct': float(q.get('priceImpactPct') or 0),
            'slippage_bps': int(slippage_bps),
            'route': [{'amm': (h.get('swapInfo') or {}).get('label'),
                       'percent': h.get('percent')} for h in (q.get('routePlan') or [])],
        }
        if dry_run:
            return {**plan, 'sent': False, 'dry_run': True,
                    'reason': 'dry_run=true — this is the route that would be signed'}
        if not confirm:
            # An unknown USD value is not a small one. When the price API is
            # throttled the guard cannot say whether this is $5 or $50,000, so
            # it asks rather than assuming the safe half.
            if spend_usd is None:
                return {**plan, 'sent': False, 'needs_confirm': True,
                        'guard_usd': SPEND_USD,
                        'reason': 'the price API did not answer, so the '
                                  f'${SPEND_USD:,.2f} guard cannot be applied to this '
                                  'trade — call again with confirm=true if the size '
                                  'above is what you meant'}
            if spend_usd > SPEND_USD:
                return {**plan, 'sent': False, 'needs_confirm': True,
                        'guard_usd': SPEND_USD,
                        'reason': f'${spend_usd:,.2f} is over the ${SPEND_USD:,.2f} guard — '
                                  f'call again with confirm=true to trade it'}

        built = _http(f'{JUP}/swap/v1/swap', body={
            'quoteResponse': q,
            'userPublicKey': address,
            'wrapAndUnwrapSol': True,
            'dynamicComputeUnitLimit': True,
            **({'prioritizationFeeLamports': {'priorityLevelWithMaxLamports': {
                'maxLamports': int(priority_lamports), 'priorityLevel': 'high'}}}
               if priority_lamports else {}),
        }, timeout=self.timeout) or {}
        encoded = built.get('swapTransaction')
        if not encoded:
            raise SolError('Jupiter would not build that swap', status=502,
                           detail=built)

        import base64
        wire = _sign_wire(base64.b64decode(encoded), seed, address)
        try:
            sent = self.call('sendTransaction', [
                base64.b64encode(wire).decode(),
                {'encoding': 'base64', 'skipPreflight': False,
                 'preflightCommitment': 'confirmed', 'maxRetries': 3}])
        except SolError as e:
            raise SolError(f'the network rejected the swap: {e}', status=400,
                           detail=getattr(e, 'detail', None))
        out = {**plan, 'sent': True, 'signature': sent,
               'bytes': len(wire), 'explorer': self.explorer(sent),
               'fee_lamports': built.get('prioritizationFeeLamports')}
        if wait:
            out['confirmation'] = self.wait_for(sent)
        return out

    # ── writing ──────────────────────────────────────────────────

    def transfer(self, to, amount, mint=None, wallet=None, secret=None, memo=None,
                 confirm=False, wait=True):
        """Send SOL, or an SPL token, from a key this process can sign with.

        Guarded: anything worth more than SOLANA_SPEND_USD comes back as
        needs_confirm instead of moving, and mainnet always says what it is
        about to do before it does it.
        """
        to = need_address(to, 'to')
        amount = float(amount)
        if amount <= 0:
            raise SolError('amount must be greater than zero')
        seed, sender = signer(wallet, secret)
        if to == sender:
            raise SolError('the destination is the sender — nothing would move')

        if mint:
            mint = need_address(mint, 'mint')
            plan = self._plan_token(sender, to, mint, amount)
        else:
            plan = self._plan_sol(sender, to, amount)
        px = self.prices([WSOL] + ([mint] if mint else []))
        unit = mint or WSOL
        value = _usd(amount, (px.get(unit) or {}).get('usd'))
        plan.update({'from': sender, 'to': to, 'amount': amount,
                     'symbol': plan.get('symbol') or ('SOL' if not mint else None),
                     'usd': value, 'network': self.network})

        if value is not None and value > SPEND_USD and not confirm:
            return {**plan, 'sent': False, 'needs_confirm': True,
                    'guard_usd': SPEND_USD,
                    'reason': f'${value:,.2f} is over the ${SPEND_USD:,.2f} guard — '
                              f'call again with confirm=true to send it'}

        blockhash = ((self.call('getLatestBlockhash') or {}).get('value') or {})
        if not blockhash.get('blockhash'):
            raise SolError('the node would not give a blockhash to sign against',
                           status=502)
        ins = list(plan.pop('_instructions'))
        if memo:
            ins.append((MEMO, [], str(memo).encode()[:566]))
        message = _message(sender, ins, blockhash['blockhash'])
        signature = sign(seed, message)
        wire = bytes([1]) + signature + message
        import base64
        sig58 = b58encode(signature)
        try:
            sent = self.call('sendTransaction', [
                base64.b64encode(wire).decode(),
                {'encoding': 'base64', 'skipPreflight': False,
                 'preflightCommitment': 'confirmed', 'maxRetries': 3}])
        except SolError as e:
            raise SolError(f'the network rejected the transfer: {e}', status=400,
                           detail=getattr(e, 'detail', None))
        out = {**plan, 'sent': True, 'signature': sent or sig58,
               'explorer': f'https://solscan.io/tx/{sent or sig58}' +
                           ('' if self.network == 'mainnet' else
                            f'?cluster={self.network}'),
               'blockhash': blockhash['blockhash']}
        if wait:
            out['confirmation'] = self.wait_for(sent or sig58)
        return out

    # ── arbitrary transactions ───────────────────────────────────
    # transfer() above is one shaped instruction; deploying a program or calling
    # one is any instruction at all, with any number of signers. Everything from
    # program.py comes through here.

    def rent(self, space):
        """Lamports that make an account of `space` bytes rent-exempt."""
        return int(self.call('getMinimumBalanceForRentExemption', [int(space)]) or 0)

    def blockhash(self):
        value = (self.call('getLatestBlockhash') or {}).get('value') or {}
        if not value.get('blockhash'):
            raise SolError('the node would not give a blockhash to sign against',
                           status=502)
        return value['blockhash']

    def explorer(self, signature):
        if self.network == 'mainnet':
            return f'https://solscan.io/tx/{signature}'
        if self.network == 'custom':
            return (f'https://solscan.io/tx/{signature}?cluster=custom&customUrl='
                    + urllib.parse.quote(self.rpc, safe=''))
        return f'https://solscan.io/tx/{signature}?cluster={self.network}'

    def sign_tx(self, fee_payer, instructions, seeds, blockhash=None):
        """Serialise and sign. `seeds` is pubkey → 32-byte seed, and every key
        the message says must sign has to be in it — a deploy is signed by the
        payer, the buffer keypair and the program keypair at once."""
        blockhash = blockhash or self.blockhash()
        message, signers = _build(fee_payer, instructions, blockhash)
        missing = [k for k in signers if k not in seeds]
        if missing:
            raise SolError('nothing here can sign for ' + ', '.join(missing) +
                           ' — that account is marked as a signer on this '
                           'transaction', status=400)
        blob = b''.join(sign(seeds[k], message) for k in signers)
        return (_compact(len(signers)) + blob + message, b58encode(blob[:64]),
                blockhash)

    def send_ix(self, fee_payer, instructions, seeds, wait=True, blockhash=None,
                skip_preflight=False, seconds=45):
        """Sign it, send it, and (by default) wait for the cluster to commit."""
        import base64
        wire, sig58, used = self.sign_tx(fee_payer, instructions, seeds, blockhash)
        if len(wire) > 1232:
            raise SolError(f'that transaction is {len(wire)} bytes — a Solana '
                           'packet holds 1232, so it has to be split', status=400)
        sent = self.call('sendTransaction', [
            base64.b64encode(wire).decode(),
            {'encoding': 'base64', 'skipPreflight': bool(skip_preflight),
             'preflightCommitment': 'confirmed', 'maxRetries': 3}])
        out = {'signature': sent or sig58, 'blockhash': used,
               'bytes': len(wire), 'explorer': self.explorer(sent or sig58)}
        if wait:
            out['confirmation'] = self.wait_for(sent or sig58, seconds)
        return out

    def simulate_ix(self, fee_payer, instructions, accounts=None):
        """Run instructions against live cluster state without sending them.

        Signatures are left as zeros and the node is told not to check them, so
        you can simulate a call whose signers you do not hold the keys for —
        which is most of what 'play with this program' means before you commit.
        """
        import base64
        message, signers = _build(fee_payer, instructions, self.blockhash())
        wire = _compact(len(signers)) + b'\x00' * 64 * len(signers) + message
        cfg = {'encoding': 'base64', 'sigVerify': False,
               'replaceRecentBlockhash': True, 'commitment': 'confirmed'}
        if accounts:
            cfg['accounts'] = {'encoding': 'base64', 'addresses': list(accounts)}
        res = (self.call('simulateTransaction',
                         [base64.b64encode(wire).decode(), cfg]) or {})
        value = res.get('value') or {}
        logs = value.get('logs') or []
        return {'ok': value.get('err') is None, 'error': value.get('err'),
                'units': value.get('unitsConsumed'), 'logs': logs,
                'return_data': value.get('returnData'),
                'accounts': value.get('accounts'),
                'signers': signers, 'bytes': len(wire),
                'reason': _sim_reason(value.get('err'), logs)}

    def _plan_sol(self, sender, to, amount):
        lamports = int(round(amount * LAMPORTS))
        have = (self.call('getBalance', [sender]) or {}).get('value') or 0
        fee_headroom = 10_000
        if have < lamports + fee_headroom:
            raise SolError(f'{sender} holds {sol(have)} SOL — not enough for '
                           f'{amount} SOL plus fees', status=400)
        data = (2).to_bytes(4, 'little') + lamports.to_bytes(8, 'little')
        return {'kind': 'sol', 'lamports': lamports, 'symbol': 'SOL',
                'sender_balance_sol': sol(have),
                '_instructions': [(SYSTEM,
                                   [(sender, True, True), (to, False, True)], data)]}

    def _plan_token(self, sender, to, mint, amount):
        """SPL transfer, creating the recipient's associated account if needed —
        the single most common reason a hand-rolled token transfer fails."""
        acct = self.account(mint)
        if acct.get('kind') != 'mint':
            raise SolError(f'{mint} is not a token mint')
        program = acct.get('owner')
        decimals = int((acct.get('mint') or {}).get('decimals') or 0)
        symbol = (acct.get('mint') or {}).get('symbol')
        raw = int(round(amount * 10 ** decimals))
        if raw <= 0:
            raise SolError(f'{amount} rounds to zero at {decimals} decimals')
        src = self._ata(sender, mint, program)
        dst = self._ata(to, mint, program)
        held = self.call('getTokenAccountBalance', [src])
        have = float(((held or {}).get('value') or {}).get('uiAmount') or 0) \
            if held else 0.0
        if have < amount:
            raise SolError(f'{sender} holds {have} {symbol or mint} — not enough to '
                           f'send {amount}', status=400)
        ins = []
        if (self.call('getAccountInfo', [dst, {'encoding': 'base64'}])
                or {}).get('value') is None:
            ins.append((ATA_PROGRAM,
                        [(sender, True, True), (dst, False, True), (to, False, False),
                         (mint, False, False), (SYSTEM, False, False),
                         (program, False, False)], bytes([1])))   # CreateIdempotent
        ins.append((program,
                    [(src, False, True), (mint, False, False), (dst, False, True),
                     (sender, True, False)],
                    bytes([12]) + raw.to_bytes(8, 'little') + bytes([decimals])))
        return {'kind': 'token', 'mint': mint, 'symbol': symbol, 'decimals': decimals,
                'raw_amount': raw, 'from_token_account': src,
                'to_token_account': dst, 'sender_balance': have,
                'creates_recipient_account': len(ins) > 1,
                'token_2022': program == TOKEN_2022, '_instructions': ins}

    def _ata(self, owner, mint, program=TOKEN):
        return find_program_address(
            [b58decode(owner), b58decode(program), b58decode(mint)], ATA_PROGRAM)[0]

    def wait_for(self, signature, seconds=30):
        """Poll until the cluster commits it, or say plainly that it did not."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            res = (self.call('getSignatureStatuses', [[signature]]) or {})
            status = ((res.get('value') or [None])[0]) or {}
            if status.get('err'):
                return {'confirmed': False, 'error': status['err'],
                        'status': status.get('confirmationStatus')}
            if status.get('confirmationStatus') in ('confirmed', 'finalized'):
                return {'confirmed': True, 'status': status['confirmationStatus'],
                        'slot': status.get('slot')}
            time.sleep(1)
        return {'confirmed': False, 'status': 'pending',
                'note': f'not committed within {seconds}s — check sol_tx later; '
                        'it may still land'}

    def airdrop(self, address=None, sol_amount=1, wallet=None):
        """Test SOL. Refuses on mainnet, because there is no such thing there."""
        if self.network == 'mainnet':
            raise SolError('there is no mainnet faucet — pass network=devnet',
                           status=400)
        address = need_address(address, 'address') if address else signer(wallet)[1]
        lamports = int(float(sol_amount) * LAMPORTS)
        sig = self.call('requestAirdrop', [address, lamports])
        return {'network': self.network, 'address': address, 'sol': float(sol_amount),
                'signature': sig, 'confirmation': self.wait_for(sig, 40)}


# ── legacy transaction encoding ──────────────────────────────────

def _compact_read(raw, offset):
    """Read a compact-u16 (Solana's ULEB128-ish length prefix)."""
    value = 0
    for shift in (0, 7, 14):
        if offset >= len(raw):
            raise SolError('truncated transaction from the router', status=502)
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
    raise SolError('malformed transaction from the router', status=502)


def _sign_wire(raw, seed, address):
    """Put our signature into a transaction somebody else built.

    A router returns a fully-formed (usually versioned) transaction with an
    empty slot where the payer's signature goes. Rather than rebuild it — which
    would mean re-deriving a route we did not compute — we find our slot by
    matching pubkeys in the message header and sign the exact message bytes
    that came back.
    """
    count, cursor = _compact_read(raw, 0)
    if count < 1:
        raise SolError('that transaction wants no signatures', status=502)
    sig_start = cursor
    message = raw[cursor + 64 * count:]
    if len(raw) < cursor + 64 * count + 4:
        raise SolError('truncated transaction from the router', status=502)

    body = message
    if body and body[0] & 0x80:                      # versioned (v0) message
        body = body[1:]
    required = body[0] if body else 0
    keys, offset = _compact_read(body, 3)
    signers = []
    for i in range(min(required, keys)):
        start = offset + 32 * i
        signers.append(b58encode(body[start:start + 32]))
    if address not in signers:
        raise SolError(f'{address} is not a signer on the transaction the router '
                       f'built — it wants ' + ', '.join(signers or ['nobody']),
                       status=400)
    index = signers.index(address)
    signature = sign(seed, message)
    slots = [raw[sig_start + 64 * i:sig_start + 64 * (i + 1)] for i in range(count)]
    for i, slot in enumerate(slots):
        if i != index and slot == b'\x00' * 64:
            raise SolError('that transaction needs a second signer this box does '
                           'not hold a key for', status=400)
    slots[index] = signature
    return raw[:sig_start] + b''.join(slots) + message


def _sim_reason(err, logs):
    """Why a simulation failed, in words. The err field is a shape like
    {'InstructionError': [0, {'Custom': 6000}]}; the useful sentence is
    usually sitting in the logs right above it."""
    if err is None:
        return None
    for line in reversed(logs or []):
        if 'Error' in line or 'failed' in line or 'panicked' in line:
            return line.split('Program log: ')[-1].strip()
    if isinstance(err, dict) and 'InstructionError' in err:
        which = err['InstructionError']
        detail = which[1] if len(which) > 1 else which
        if isinstance(detail, dict) and 'Custom' in detail:
            return (f'instruction {which[0]} returned custom error '
                    f'{detail["Custom"]} — that code is the program\'s own; '
                    'an anchor program numbers its first one 6000')
        return f'instruction {which[0]}: {detail}'
    return str(err)


def _compact(n):
    """ShortVec: the length prefix Solana puts in front of every array."""
    out = bytearray()
    while True:
        if n < 0x80:
            out.append(n)
            return bytes(out)
        out.append((n & 0x7F) | 0x80)
        n >>= 7


def _message(fee_payer, instructions, blockhash):
    """The serialised message alone, for the single-signer case."""
    return _build(fee_payer, instructions, blockhash)[0]


def _build(fee_payer, instructions, blockhash):
    """Serialise a legacy message, and say which keys have to sign it.

    Account ordering is not cosmetic — the header counts depend on it: writable
    signers, then readonly signers, then writable accounts, then readonly ones
    (program ids among them). Get the order wrong and the node rejects the
    signature rather than the intent, which is a confusing way to fail.

    The signer list comes back in the same order, because a transaction is
    signatures-then-message with the two arrays lined up by position: a deploy
    signed by payer, buffer and program keypairs is three signatures that only
    verify if they are in the order the message put the keys in.
    """
    meta = {fee_payer: [True, True]}
    for program, accounts, _ in instructions:
        for pubkey, is_signer, is_writable in accounts:
            cur = meta.setdefault(pubkey, [False, False])
            cur[0] = cur[0] or is_signer
            cur[1] = cur[1] or is_writable
        meta.setdefault(program, [False, False])

    def rank(item):
        key, (is_signer, is_writable) = item
        return (key != fee_payer, not is_signer, not is_writable, key)

    ordered = [k for k, _ in sorted(meta.items(), key=rank)]
    index = {k: i for i, k in enumerate(ordered)}
    signers = [k for k in ordered if meta[k][0]]
    ro_signed = sum(1 for k in signers if not meta[k][1])
    ro_unsigned = sum(1 for k in ordered if not meta[k][0] and not meta[k][1])

    body = bytearray([len(signers), ro_signed, ro_unsigned])
    body += _compact(len(ordered))
    for key in ordered:
        body += b58decode(key)
    body += b58decode(blockhash)
    body += _compact(len(instructions))
    for program, accounts, data in instructions:
        body.append(index[program])
        body += _compact(len(accounts))
        for pubkey, _, _ in accounts:
            body.append(index[pubkey])
        body += _compact(len(data))
        body += data
    return bytes(body), signers
