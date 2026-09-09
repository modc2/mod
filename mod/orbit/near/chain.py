#!/usr/bin/env python3
"""near chain — a JSON-RPC client for NEAR with a failover pool.

NEAR's public RPC story is fragmented: rpc.mainnet.near.org rate-limits hard
and is scheduled for deprecation, FastNEAR runs a free tier, and a handful of
third parties fill the gaps. This client keeps a pool per network and fails
over between endpoints, so one throttled provider does not take the module
down. Set NEAR_RPC to your own node to skip the pool entirely.

Everything here is read-only. NEAR keeps no ABI on chain, but a contract's
WASM export section names every callable method — `contract()` parses it, so
you can see what a contract offers before calling it with `view()`.
"""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

NETWORKS = {
    'mainnet': [
        'https://free.rpc.fastnear.com',
        'https://near.lava.build',
        'https://rpc.mainnet.near.org',
        'https://1rpc.io/near',
    ],
    'testnet': [
        'https://test.rpc.fastnear.com',
        'https://rpc.testnet.near.org',
    ],
}
INDEXERS = {
    'mainnet': 'https://api.nearblocks.io',
    'testnet': 'https://api-testnet.nearblocks.io',
}
EXPLORERS = {
    'mainnet': 'https://nearblocks.io',
    'testnet': 'https://testnet.nearblocks.io',
}
YOCTO = 10 ** 24
TGAS = 10 ** 12
CACHE_TTL = float(os.environ.get('NEAR_CACHE_TTL', 30))
ACCOUNT_RE = re.compile(r'^(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+$')

_price_cache = {'at': 0.0, 'data': None}


class NearError(Exception):
    def __init__(self, message, status=400, detail=None):
        super().__init__(message)
        self.status = status
        self.detail = detail

    def dict(self):
        out = {'error': str(self)}
        if self.detail:
            out['detail'] = self.detail
        return out


def near(yocto):
    """yoctoNEAR (a string on the wire — it overflows f64) → NEAR, as float."""
    try:
        return int(yocto) / YOCTO
    except (TypeError, ValueError):
        return 0.0


def is_account_id(s):
    """Valid NEAR account: 2-64 chars of the account grammar. A 64-hex string
    is an implicit account and also matches."""
    return isinstance(s, str) and 2 <= len(s) <= 64 and bool(ACCOUNT_RE.match(s))


def _http(url, payload=None, timeout=12):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json', 'User-Agent': 'mod-near/0.2'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _wasm_exports(code):
    """Function names from a WASM binary's export section (id 7). NEAR stores
    no ABI, but every callable method is an exported function, so this is the
    contract's interface as the chain itself sees it."""
    def leb(buf, i):
        shift = val = 0
        while True:
            b = buf[i]
            i += 1
            val |= (b & 0x7f) << shift
            if not b & 0x80:
                return val, i
            shift += 7

    if code[:4] != b'\0asm':
        return []
    i, out = 8, []
    while i < len(code):
        sec_id = code[i]
        size, i = leb(code, i + 1)
        if sec_id == 7:
            count, j = leb(code, i)
            for _ in range(count):
                n, j = leb(code, j)
                name = code[j:j + n].decode('utf-8', 'replace')
                j += n
                kind = code[j]
                _, j = leb(code, j + 1)
                if kind == 0:
                    out.append(name)
            break
        i += size
    return sorted(out)


def _decode_action(action):
    """One transaction action → a one-line summary dict."""
    if action == 'CreateAccount':
        return {'type': 'CreateAccount'}
    if not isinstance(action, dict):
        return {'type': str(action)}
    kind, body = next(iter(action.items()))
    out = {'type': kind}
    if kind == 'Transfer':
        out['near'] = near(body.get('deposit'))
    elif kind == 'FunctionCall':
        out['method'] = body.get('method_name')
        out['deposit_near'] = near(body.get('deposit'))
        out['tgas'] = round((body.get('gas') or 0) / TGAS, 1)
        args = body.get('args')
        if args:
            try:
                out['args'] = json.loads(base64.b64decode(args))
            except Exception:
                out['args_base64_bytes'] = len(args)
    elif kind == 'Stake':
        out['near'] = near(body.get('stake'))
    elif kind in ('AddKey', 'DeleteKey'):
        out['public_key'] = body.get('public_key')
    elif kind == 'DeleteAccount':
        out['beneficiary'] = body.get('beneficiary_id')
    elif kind == 'DeployContract':
        out['code_bytes'] = len(base64.b64decode(body.get('code') or '')) \
            if body.get('code') else None
    return out


class Client:
    def __init__(self, network=None, rpc=None):
        self.network = (network or os.environ.get('NEAR_NETWORK') or 'mainnet').strip()
        if self.network not in NETWORKS and not rpc:
            if self.network.startswith('http'):
                rpc, self.network = self.network, 'custom'
            else:
                raise NearError(f'unknown network {self.network!r} — '
                                f'{", ".join(NETWORKS)}, or pass rpc=')
        override = rpc or os.environ.get('NEAR_RPC')
        self.endpoints = [override] if override else list(NETWORKS[self.network])

    # ── transport ────────────────────────────────────────────────

    def call(self, method, params):
        """One JSON-RPC call, failing over across the pool. An RPC-level error
        (unknown account, bad method) is final; a transport error moves on."""
        last = None
        for url in self.endpoints:
            try:
                resp = _http(url, {'jsonrpc': '2.0', 'id': 'mod-near',
                                   'method': method, 'params': params})
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f'{url}: {e}'
                continue
            err = resp.get('error')
            if err:
                cause = (err.get('cause') or {})
                name = cause.get('name') or err.get('name') or ''
                # Server-side throttling/unavailability: try the next node.
                if name in ('TIMEOUT_ERROR', 'INTERNAL_ERROR') or \
                        err.get('code') == -429:
                    last = f'{url}: {name or err.get("message")}'
                    continue
                raise NearError(f'{name or "RPC error"}: '
                                f'{err.get("message") or ""}'.strip(' :'),
                                status=422, detail=cause.get('info') or err.get('data'))
            return resp.get('result')
        raise NearError(f'every {self.network} RPC endpoint failed — last: {last}',
                        status=502)

    def rpc(self, method, params=None):
        if isinstance(params, str) and params.strip():
            try:
                params = json.loads(params)
            except Exception:
                raise NearError('params must be JSON (an object or array)')
        return self.call(method, params if params is not None else [None])

    def _query(self, request):
        return self.call('query', {'finality': 'final', **request})

    def _indexer(self, path, what):
        base = INDEXERS.get(self.network)
        if not base:
            raise NearError(f'no indexer for network {self.network!r}')
        try:
            return _http(base + path)
        except urllib.error.HTTPError as e:
            raise NearError(f'{what} unavailable — NearBlocks answered {e.code} '
                            '(the free indexer rate-limits by IP; retry shortly)',
                            status=502)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NearError(f'{what} unavailable — {e}', status=502)

    # ── reads ────────────────────────────────────────────────────

    def account(self, account_id):
        account_id = str(account_id).strip().lower()
        if not is_account_id(account_id):
            raise NearError(f'{account_id!r} is not a NEAR account ID')
        a = self._query({'request_type': 'view_account', 'account_id': account_id})
        liquid, locked = near(a.get('amount')), near(a.get('locked'))
        storage = a.get('storage_usage') or 0
        code_hash = a.get('code_hash')
        out = {
            'account_id': account_id,
            'network': self.network,
            'balance': {
                'total_near': liquid + locked,
                'liquid_near': liquid,
                'staked_near': locked,
                # 1e19 yocto per byte, the protocol's storage_amount_per_byte.
                'storage_reserved_near': storage * 1e19 / YOCTO,
            },
            'storage_bytes': storage,
            'is_contract': code_hash not in (None, '11111111111111111111111111111111'),
            'code_hash': code_hash,
            'block_height': a.get('block_height'),
            'explorer': f'{EXPLORERS.get(self.network, EXPLORERS["mainnet"])}'
                        f'/address/{account_id}',
        }
        p = self.price_quiet()
        if p:
            out['balance']['total_usd'] = round(out['balance']['total_near'] * p, 2)
            out['price_usd'] = p
        return out

    def keys(self, account_id):
        r = self._query({'request_type': 'view_access_key_list',
                         'account_id': str(account_id).strip().lower()})
        keys = []
        for k in r.get('keys') or []:
            perm = k.get('access_key', {}).get('permission')
            if perm == 'FullAccess':
                keys.append({'public_key': k.get('public_key'), 'permission': 'FullAccess'})
            else:
                fc = (perm or {}).get('FunctionCall') or {}
                keys.append({'public_key': k.get('public_key'),
                             'permission': 'FunctionCall',
                             'receiver': fc.get('receiver_id'),
                             'methods': fc.get('method_names') or ['(any)'],
                             'allowance_near': near(fc['allowance'])
                             if fc.get('allowance') else None})
        return {'account_id': account_id, 'count': len(keys), 'keys': keys}

    def contract(self, account_id):
        """The contract's interface, read out of its WASM export section."""
        account_id = str(account_id).strip().lower()
        r = self._query({'request_type': 'view_code', 'account_id': account_id})
        code = base64.b64decode(r.get('code_base64') or '')
        if not code:
            return {'account_id': account_id, 'is_contract': False,
                    'note': 'no contract deployed on this account'}
        internal = {'__contract_abi', '__data_type'}
        methods = [m for m in _wasm_exports(code)
                   if m not in internal and not m.startswith('__')]
        return {'account_id': account_id, 'is_contract': True,
                'code_hash': r.get('hash'), 'code_bytes': len(code),
                'methods': methods, 'method_count': len(methods),
                'note': 'exported WASM functions — view or change is not marked '
                        'on chain; try near_view, a change method will refuse'}

    def view(self, contract, method, args=None):
        """Call a view function. args is a JSON object (or string of one)."""
        if isinstance(args, str) and args.strip():
            try:
                args = json.loads(args)
            except Exception:
                raise NearError('args must be a JSON object, e.g. {"account_id":"x"}')
        blob = base64.b64encode(json.dumps(args or {}).encode()).decode()
        r = self._query({'request_type': 'call_function',
                         'account_id': str(contract).strip().lower(),
                         'method_name': method, 'args_base64': blob})
        raw = bytes(r.get('result') or [])
        try:
            result = json.loads(raw) if raw else None
        except Exception:
            result = {'raw_base64': base64.b64encode(raw).decode()}
        return {'contract': contract, 'method': method, 'args': args or {},
                'result': result, 'logs': r.get('logs') or [],
                'block_height': r.get('block_height')}

    def ft(self, contract, account_id=None):
        """A fungible token by contract: NEP-141 metadata, and a balance if
        account_id is given, scaled by the token's own decimals."""
        meta = self.view(contract, 'ft_metadata')['result'] or {}
        decimals = int(meta.get('decimals') or 0)
        out = {'contract': contract, 'symbol': meta.get('symbol'),
               'name': meta.get('name'), 'decimals': decimals,
               'total_supply': None, 'network': self.network}
        try:
            supply = self.view(contract, 'ft_total_supply')['result']
            out['total_supply'] = int(supply) / 10 ** decimals if supply else None
        except NearError:
            pass
        if account_id:
            bal = self.view(contract, 'ft_balance_of',
                            {'account_id': str(account_id).strip().lower()})['result']
            out['account_id'] = account_id
            out['balance'] = int(bal or 0) / 10 ** decimals
        return out

    def history(self, account_id, limit=25):
        """Recent transactions, newest first — from the NearBlocks indexer,
        because the RPC layer has no by-account query at all."""
        account_id = str(account_id).strip().lower()
        limit = max(1, min(int(limit or 25), 25))
        r = self._indexer(f'/v1/account/{account_id}/txns?per_page={limit}',
                          f'history for {account_id}')
        txns = []
        for t in r.get('txns') or []:
            ok = (t.get('outcomes') or {}).get('status')
            txns.append({
                'hash': t.get('transaction_hash'),
                'signer': t.get('signer_account_id'),
                'receiver': t.get('receiver_account_id'),
                'direction': 'out' if t.get('signer_account_id') == account_id else 'in',
                'deposit_near': near((t.get('actions_agg') or {}).get('deposit') or 0),
                'actions': [a.get('method') or a.get('action')
                            for a in (t.get('actions') or [])],
                'status': 'ok' if ok else ('failed' if ok is False else 'unknown'),
                'time': time.strftime(
                    '%Y-%m-%d %H:%M', time.gmtime(int(t['block_timestamp']) / 1e9))
                if t.get('block_timestamp') else None,
            })
        return {'account_id': account_id, 'count': len(txns), 'txns': txns,
                'source': INDEXERS.get(self.network)}

    def tx(self, tx_hash, sender=None):
        """One transaction. The RPC needs the sender to route the lookup to the
        right shard; when it is not given, the indexer resolves it."""
        tx_hash = str(tx_hash).strip()
        if not sender:
            r = self._indexer(f'/v1/txns/{tx_hash}', f'sender lookup for {tx_hash}')
            found = (r.get('txns') or [{}])[0]
            sender = found.get('signer_account_id')
            if not sender:
                raise NearError(f'transaction {tx_hash} not found on {self.network} '
                                '— pass sender= to ask the RPC directly', status=404)
        r = self.call('tx', {'tx_hash': tx_hash, 'sender_account_id': sender,
                             'wait_until': 'NONE'})
        t = r.get('transaction') or {}
        status = r.get('status') or {}
        outcome = (r.get('transaction_outcome') or {}).get('outcome') or {}
        burnt = int(outcome.get('tokens_burnt') or 0)
        for ro in r.get('receipts_outcome') or []:
            burnt += int((ro.get('outcome') or {}).get('tokens_burnt') or 0)
        failure = status.get('Failure')
        return {
            'hash': tx_hash, 'network': self.network,
            'signer': t.get('signer_id'), 'receiver': t.get('receiver_id'),
            'status': 'failed' if failure else 'ok',
            'failure': failure,
            'actions': [_decode_action(a) for a in t.get('actions') or []],
            'fee_near': burnt / YOCTO,
            'block_hash': (r.get('transaction_outcome') or {}).get('block_hash'),
            'logs': outcome.get('logs') or [],
            'explorer': f'{EXPLORERS.get(self.network, EXPLORERS["mainnet"])}'
                        f'/txns/{tx_hash}',
        }

    def block(self, block_id=None):
        if block_id in (None, '', 'final', 'latest'):
            params = {'finality': 'final'}
        else:
            try:
                params = {'block_id': int(block_id)}
            except (TypeError, ValueError):
                params = {'block_id': str(block_id)}
        r = self.call('block', params)
        h = r.get('header') or {}
        return {
            'height': h.get('height'), 'hash': h.get('hash'),
            'time': time.strftime('%Y-%m-%d %H:%M:%S UTC',
                                  time.gmtime(int(h.get('timestamp') or 0) / 1e9)),
            'author': r.get('author'),
            'gas_price_yocto': h.get('gas_price'),
            'chunks': len(r.get('chunks') or []),
            'prev_hash': h.get('prev_hash'),
            'epoch_id': h.get('epoch_id'),
            'network': self.network,
        }

    def status(self):
        s = self.call('status', [])
        sync = s.get('sync_info') or {}
        gas = self.call('gas_price', [None]) or {}
        vals = self.call('validators', [None]) or {}
        out = {
            'network': self.network,
            'chain_id': s.get('chain_id'),
            'rpc': self.endpoints[0],
            'block_height': sync.get('latest_block_height'),
            'block_time': sync.get('latest_block_time'),
            'protocol_version': s.get('protocol_version'),
            'gas_price_yocto': gas.get('gas_price'),
            'validators': len(vals.get('current_validators') or []),
            'total_stake_near': round(sum(
                near(v.get('stake')) for v in vals.get('current_validators') or [])),
        }
        p = self.price_quiet()
        if p:
            out['near_usd'] = p
        return out

    def validators(self, limit=30):
        r = self.call('validators', [None]) or {}
        current = r.get('current_validators') or []
        total = sum(near(v.get('stake')) for v in current) or 1
        rows = sorted(current, key=lambda v: -near(v.get('stake')))
        out, running = [], 0.0
        nakamoto = None
        for i, v in enumerate(rows):
            stake = near(v.get('stake'))
            running += stake
            if nakamoto is None and running > total / 3:
                nakamoto = i + 1
            produced, expected = v.get('num_produced_blocks'), v.get('num_expected_blocks')
            out.append({'account_id': v.get('account_id'),
                        'stake_near': round(stake),
                        'share_pct': round(100 * stake / total, 2),
                        'uptime_pct': round(100 * produced / expected, 1)
                        if expected else None})
        return {'count': len(rows), 'total_stake_near': round(total),
                'nakamoto_coefficient': nakamoto,
                'validators': out[:max(1, min(int(limit or 30), 200))]}

    def price(self):
        now = time.time()
        if _price_cache['data'] and now - _price_cache['at'] < CACHE_TTL:
            return _price_cache['data']
        r = _http('https://api.coingecko.com/api/v3/simple/price'
                  '?ids=near&vs_currencies=usd&include_24hr_change=true'
                  '&include_market_cap=true')
        n = r.get('near') or {}
        data = {'symbol': 'NEAR', 'usd': n.get('usd'),
                'change_24h_pct': round(n.get('usd_24h_change') or 0, 2),
                'market_cap_usd': n.get('usd_market_cap'),
                'source': 'coingecko'}
        _price_cache.update(at=now, data=data)
        return data

    def price_quiet(self):
        try:
            return (self.price() or {}).get('usd')
        except Exception:
            return None
