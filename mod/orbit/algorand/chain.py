"""algorand chain — algod + indexer over plain HTTPS, no SDK.

Algorand splits its read surface in two: algod is the node (current state,
params, submit, simulate, TEAL compile), the indexer is the history (search
transactions, assets, applications by anything). AlgoNode serves both free
and keyless for mainnet, testnet and betanet; ALGOD_URL / INDEXER_URL (and
ALGOD_TOKEN) point it at your own node instead.

Accounts may also be named: `alice.algo` resolves through NFDomains to the
address it deposits to.
"""

import base64
import json
import os
import string
import time
import urllib.error
import urllib.parse
import urllib.request

import codec

NETWORKS = {
    'mainnet': {'algod': 'https://mainnet-api.algonode.cloud',
                'indexer': 'https://mainnet-idx.algonode.cloud',
                'explorer': 'https://allo.info',
                'nfd': 'https://api.nf.domains'},
    'testnet': {'algod': 'https://testnet-api.algonode.cloud',
                'indexer': 'https://testnet-idx.algonode.cloud',
                'explorer': 'https://testnet.explorer.perawallet.app',
                'nfd': 'https://api.testnet.nf.domains',
                'faucet': 'https://bank.testnet.algorand.network'},
    'betanet': {'algod': 'https://betanet-api.algonode.cloud',
                'indexer': 'https://betanet-idx.algonode.cloud',
                'explorer': None, 'nfd': None},
}

# A few assets everyone means by name. Verified live by algo_asset.
KNOWN_ASSETS = {
    'mainnet': {'USDC': 31566704, 'USDT': 312769, 'GOBTC': 386192725,
                'GOETH': 386195940},
    'testnet': {'USDC': 10458941},
}

MICRO = 1_000_000
UA = {'user-agent': 'mod-algorand/0.1', 'accept': 'application/json'}


class AlgoError(Exception):
    def __init__(self, message, status=400, data=None):
        super().__init__(message)
        self.status, self.data = status, data

    def dict(self):
        out = {'error': str(self)}
        if self.data is not None:
            out['data'] = self.data
        return out


def algos(micro):
    return round((micro or 0) / MICRO, 6)


def default_network():
    return os.environ.get('ALGORAND_NETWORK', 'mainnet')


def _printable(b):
    try:
        s = b.decode()
    except UnicodeDecodeError:
        return None
    return s if s and all(c in string.printable for c in s) else None


def decode_kv(entries):
    """Global/local state as algod returns it (base64 keys, typed values) →
    {key: value}, bytes shown as text, an address, or hex."""
    out = {}
    for e in entries or []:
        kb = codec.unb64(e.get('key'))
        key = _printable(kb) or '0x' + kb.hex()
        v = e.get('value') or {}
        if v.get('type') == 2:
            out[key] = v.get('uint', 0)
        else:
            vb = codec.unb64(v.get('bytes'))
            if len(vb) == 32:
                out[key] = codec.encode_address(vb)
            else:
                out[key] = _printable(vb) or ('0x' + vb.hex() if vb else '')
    return out


class Client:
    def __init__(self, network=None, algod=None, indexer=None):
        self.network = (network or default_network()).lower()
        if self.network not in NETWORKS:
            raise AlgoError(f'unknown network {self.network!r} — '
                            f'{", ".join(NETWORKS)}')
        net = NETWORKS[self.network]
        own = self.network == default_network()
        self.algod = (algod or (own and os.environ.get('ALGOD_URL')) or net['algod']).rstrip('/')
        self.indexer = (indexer or (own and os.environ.get('INDEXER_URL')) or net['indexer']).rstrip('/')
        self.token = os.environ.get('ALGOD_TOKEN') if own else None
        self.explorer = net.get('explorer')
        self.nfd = net.get('nfd')

    # ── http ──────────────────────────────────────────────────────

    def _http(self, url, method='GET', data=None, ctype=None, raw=False, timeout=20):
        headers = dict(UA)
        if ctype:
            headers['content-type'] = ctype
        if self.token and url.startswith(self.algod):
            headers['X-Algo-API-Token'] = self.token
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    body = r.read()
                    return body if raw else (json.loads(body) if body else {})
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors='replace')
                try:
                    msg = json.loads(body).get('message') or body
                except Exception:
                    msg = body
                if e.code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                # CF strips 5xx bodies downstream — report upstream failures as 4xx.
                raise AlgoError(msg.strip() or f'HTTP {e.code}',
                                status=e.code if e.code < 500 else 424)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise AlgoError(f'{url.split("/v2")[0]} unreachable: {e}', status=424)

    def get(self, path, **params):
        q = {k: v for k, v in params.items() if v not in (None, '')}
        url = self.algod + path + ('?' + urllib.parse.urlencode(q) if q else '')
        return self._http(url)

    def idx(self, path, **params):
        q = {k: v for k, v in params.items() if v not in (None, '')}
        url = self.indexer + path + ('?' + urllib.parse.urlencode(q) if q else '')
        return self._http(url)

    def post(self, path, data, ctype='application/x-binary'):
        if isinstance(data, (dict, list)):
            data, ctype = json.dumps(data).encode(), 'application/json'
        return self._http(self.algod + path, 'POST', data, ctype)

    # ── names ─────────────────────────────────────────────────────

    def resolve(self, who):
        """An address, or an NFD name (alice.algo) → address."""
        who = (who or '').strip()
        if codec.is_address(who):
            return who
        if who.lower().endswith('.algo'):
            if not self.nfd:
                raise AlgoError(f'NFD names do not exist on {self.network}')
            try:
                r = self._http(f'{self.nfd}/nfd/{urllib.parse.quote(who.lower())}?view=brief')
            except AlgoError as e:
                raise AlgoError(f'no NFD named {who} on {self.network} ({e})', status=404)
            addr = r.get('depositAccount') or r.get('owner')
            if not addr:
                raise AlgoError(f'NFD {who} has no deposit account', status=404)
            return addr
        raise AlgoError(f'{who!r} is neither an Algorand address nor a .algo name')

    def name_of(self, address):
        """Reverse NFD lookup — the primary name for an address, if any."""
        if not self.nfd:
            return None
        try:
            r = self._http(f'{self.nfd}/nfd/lookup?address={address}&view=tiny', timeout=6)
            return (r.get(address) or {}).get('name')
        except AlgoError:
            return None

    # ── reads ─────────────────────────────────────────────────────

    def status(self):
        return self.get('/v2/status')

    def params(self):
        return self.get('/v2/transactions/params')

    def account(self, who, names=True):
        addr = self.resolve(who)
        a = self.get(f'/v2/accounts/{addr}')
        holdings = a.get('assets') or []
        meta = {}
        for h in holdings[:25]:
            try:
                meta[h['asset-id']] = self.asset_params(h['asset-id'])
            except AlgoError:
                pass
        assets = []
        for h in holdings:
            p = meta.get(h['asset-id']) or {}
            dec = p.get('decimals') or 0
            assets.append({'asset_id': h['asset-id'], 'unit': p.get('unit-name'),
                           'name': p.get('name'), 'decimals': dec,
                           'amount': h.get('amount', 0),
                           'balance': h.get('amount', 0) / 10 ** dec if dec else h.get('amount', 0),
                           'frozen': h.get('is-frozen', False)})
        price = None
        if self.network == 'mainnet':
            try:
                price = price_usd().get('usd')
            except Exception:
                pass
        bal = algos(a.get('amount'))
        return {
            'network': self.network, 'address': addr,
            'name': self.name_of(addr) if names else None,
            'balance_algo': bal,
            'min_balance_algo': algos(a.get('min-balance')),
            'spendable_algo': round(max(0, bal - algos(a.get('min-balance'))), 6),
            'usd': round(bal * price, 2) if price else None,
            'pending_rewards_algo': algos(a.get('pending-rewards')),
            'status': a.get('status'),
            'incentive_eligible': a.get('incentive-eligible', False),
            'rekeyed_to': a.get('auth-addr'),
            'round': a.get('round'),
            'assets': assets,
            'apps_opted_in': [x['id'] for x in a.get('apps-local-state') or []],
            'apps_created': [x['id'] for x in a.get('created-apps') or []],
            'assets_created': [x['index'] for x in a.get('created-assets') or []],
            'total_box_bytes': a.get('total-box-bytes', 0),
            'explorer': f'{self.explorer}/account/{addr}' if self.explorer else None,
        }

    def asset_params(self, asset_id):
        return self.get(f'/v2/assets/{int(asset_id)}').get('params') or {}

    def asset(self, asset, holder=None):
        aid = self.asset_id(asset)
        p = self.asset_params(aid)
        dec = p.get('decimals') or 0
        out = {'network': self.network, 'asset_id': aid, 'name': p.get('name'),
               'unit': p.get('unit-name'), 'decimals': dec,
               'total': p.get('total'), 'total_units': p.get('total', 0) / 10 ** dec,
               'url': p.get('url'), 'creator': p.get('creator'),
               'manager': p.get('manager'), 'reserve': p.get('reserve'),
               'freeze': p.get('freeze'), 'clawback': p.get('clawback'),
               'default_frozen': p.get('default-frozen', False),
               'metadata_hash': p.get('metadata-hash'),
               'explorer': f'{self.explorer}/asset/{aid}' if self.explorer else None}
        if holder:
            addr = self.resolve(holder)
            try:
                h = self.get(f'/v2/accounts/{addr}/assets/{aid}').get('asset-holding') or {}
                out['holder'] = {'address': addr, 'opted_in': True,
                                 'amount': h.get('amount', 0),
                                 'balance': h.get('amount', 0) / 10 ** dec,
                                 'frozen': h.get('is-frozen', False)}
            except AlgoError as e:
                if e.status != 404:
                    raise
                out['holder'] = {'address': addr, 'opted_in': False}
        return out

    def asset_id(self, asset):
        """An id, or a known unit name like USDC."""
        s = str(asset).strip()
        if s.isdigit():
            return int(s)
        known = KNOWN_ASSETS.get(self.network, {})
        if s.upper() in known:
            return known[s.upper()]
        raise AlgoError(f'asset {asset!r}: pass a numeric id or one of '
                        f'{", ".join(known) or "(none known here)"} — '
                        'algo_assets searches by name')

    def assets(self, q=None, creator=None, limit=20):
        params = {'limit': int(limit)}
        if creator:
            params['creator'] = self.resolve(creator)
        elif q and str(q).isdigit():
            params['asset-id'] = q
        elif q and len(q) <= 8 and q.upper() == q:
            params['unit'] = q
        elif q:
            params['name'] = q
        r = self.idx('/v2/assets', **params)
        if q and not r.get('assets') and 'unit' in params:   # unit miss → try name
            r = self.idx('/v2/assets', name=q, limit=int(limit))
        return {'network': self.network, 'query': q, 'assets': [
            {'asset_id': a['index'], 'name': a['params'].get('name'),
             'unit': a['params'].get('unit-name'),
             'decimals': a['params'].get('decimals', 0),
             'total': a['params'].get('total'), 'creator': a['params'].get('creator'),
             'deleted': a.get('deleted', False)}
            for a in r.get('assets') or []], 'next': r.get('next-token')}

    def app(self, app_id, program=False):
        aid = int(app_id)
        a = self.get(f'/v2/applications/{aid}')
        p = a.get('params') or {}
        out = {'network': self.network, 'app_id': aid, 'creator': p.get('creator'),
               'address': codec.app_address(aid),
               'global_state': decode_kv(p.get('global-state')),
               'schema': {'global': p.get('global-state-schema'),
                          'local': p.get('local-state-schema')},
               'extra_pages': p.get('extra-program-pages', 0),
               'version': p.get('version', 0),
               'approval_bytes': len(codec.unb64(p.get('approval-program'))),
               'clear_bytes': len(codec.unb64(p.get('clear-state-program'))),
               'explorer': f'{self.explorer}/application/{aid}' if self.explorer else None}
        try:
            bal = self.get(f'/v2/accounts/{out["address"]}', exclude='all')
            out['balance_algo'] = algos(bal.get('amount'))
        except AlgoError:
            pass
        try:
            boxes = self.get(f'/v2/applications/{aid}/boxes', max=50).get('boxes') or []
            out['boxes'] = [_box_name(b.get('name')) for b in boxes]
        except AlgoError:
            out['boxes'] = []
        if program:
            out['approval_teal'] = self.disassemble(codec.unb64(p.get('approval-program')))
            out['clear_teal'] = self.disassemble(codec.unb64(p.get('clear-state-program')))
        return out

    def box(self, app_id, name):
        enc = name if ':' in str(name) else f'str:{name}'
        r = self.get(f'/v2/applications/{int(app_id)}/box', name=enc)
        v = codec.unb64(r.get('value'))
        return {'app_id': int(app_id), 'name': _box_name(r.get('name')),
                'round': r.get('round'), 'size': len(v),
                'text': _printable(v), 'hex': v.hex(),
                'uint64': int.from_bytes(v, 'big') if len(v) == 8 else None}

    def local_state(self, who, app_id):
        addr = self.resolve(who)
        r = self.get(f'/v2/accounts/{addr}/applications/{int(app_id)}')
        ls = r.get('app-local-state')
        return {'address': addr, 'app_id': int(app_id), 'opted_in': ls is not None,
                'state': decode_kv((ls or {}).get('key-value'))}

    def disassemble(self, program):
        try:
            return self._http(self.algod + '/v2/teal/disassemble', 'POST', program,
                              'application/x-binary').get('result')
        except AlgoError as e:
            return f'// disassembly unavailable: {e}'

    def compile(self, source):
        r = self.post('/v2/teal/compile', source.encode(), 'text/plain')
        return {'hash': r['hash'], 'program': codec.unb64(r['result']),
                'b64': r['result']}

    def tx(self, txid):
        try:
            r = self.idx(f'/v2/transactions/{txid}')
            t = r.get('transaction') or {}
            return {'network': self.network, 'confirmed': True, **summarize(t),
                    'raw': t}
        except AlgoError as e:
            if e.status != 404:
                raise
        try:   # not indexed yet — maybe still in the pool, or just confirmed
            p = self.get(f'/v2/transactions/pending/{txid}')
            return {'network': self.network, 'id': txid,
                    'confirmed': bool(p.get('confirmed-round')),
                    'round': p.get('confirmed-round'),
                    'pool_error': p.get('pool-error') or None,
                    'app_id': p.get('application-index'),
                    'asset_id': p.get('asset-index'), 'logs': p.get('logs')}
        except AlgoError:
            raise AlgoError(f'no transaction {txid} on {self.network}', status=404)

    def history(self, who, limit=25, kind=None, asset=None):
        addr = self.resolve(who)
        r = self.idx(f'/v2/accounts/{addr}/transactions', limit=int(limit),
                     **{'tx-type': kind, 'asset-id': asset})
        return {'network': self.network, 'address': addr,
                'transactions': [summarize(t, addr) for t in r.get('transactions') or []],
                'next': r.get('next-token')}

    def block(self, rnd=None):
        rnd = int(rnd) if rnd not in (None, '') else self.status()['last-round']
        b = self.get(f'/v2/blocks/{rnd}').get('block') or {}
        txns = b.get('txns') or []
        try:
            txns_idx = self.idx('/v2/transactions', **{'round': rnd, 'limit': 50})
            transactions = [summarize(t) for t in txns_idx.get('transactions') or []]
        except Exception:
            transactions = []
        return {'network': self.network, 'round': rnd, 'timestamp': b.get('ts'),
                'time': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(b.get('ts', 0))),
                'proposer': b.get('prp'), 'fees_collected_algo': algos(b.get('fc')),
                'proposer_payout_algo': algos(b.get('pp')),
                'txn_count': len(txns),
                'txn_types': _count(t.get('txn', {}).get('type') for t in txns),
                'seed': b.get('seed'), 'prev': b.get('prev'),
                'protocol': b.get('proto'),
                'transactions': transactions}

    def network_info(self):
        s = self.status()
        p = self.params()
        sup = self.get('/v2/ledger/supply')
        out = {'network': self.network, 'genesis_id': p.get('genesis-id'),
               'genesis_hash': p.get('genesis-hash'),
               'round': s.get('last-round'),
               'last_version': s.get('last-version', '').rsplit('/', 1)[-1],
               'time_since_last_round_ms': (s.get('time-since-last-round') or 0) // 1_000_000,
               'min_fee_algo': algos(p.get('min-fee')),
               'total_supply_algo': algos(sup.get('total-money')),
               'online_stake_algo': algos(sup.get('online-money')),
               'algod': self.algod, 'indexer': self.indexer,
               'explorer': self.explorer}
        if self.network == 'mainnet':
            try:
                out['price'] = price_usd()
            except Exception:
                pass
        return out

    # ── submit / simulate ─────────────────────────────────────────

    def submit(self, signed_envelopes):
        data = b''.join(codec.msgpack(e) for e in signed_envelopes)
        return self.post('/v2/transactions', data).get('txId')

    def wait(self, txid, rounds=10):
        last = self.status()['last-round']
        for _ in range(rounds):
            p = self.get(f'/v2/transactions/pending/{txid}')
            if p.get('confirmed-round'):
                return p
            if p.get('pool-error'):
                raise AlgoError(f'transaction rejected: {p["pool-error"]}', data={'txid': txid})
            last += 1
            self.get(f'/v2/status/wait-for-block-after/{last - 1}')
        raise AlgoError(f'{txid} not confirmed after {rounds} rounds', status=408,
                        data={'txid': txid})

    def simulate(self, txns, signed=None):
        """Simulate unsigned (or signed) transactions as one group: logs,
        state deltas, budget — no keys, no fees."""
        envs = signed or [{'txn': t} for t in txns]
        body = codec.msgpack({'txn-groups': [{'txns': envs}],
                              'allow-empty-signatures': True,
                              'allow-unnamed-resources': True,
                              'exec-trace-config': {}})
        raw = self._http(self.algod + '/v2/transactions/simulate?format=json',
                         'POST', body, 'application/msgpack')
        return raw


def _box_name(b64name):
    b = codec.unb64(b64name)
    return _printable(b) or ('0x' + b.hex())


def _count(it):
    out = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out


def summarize(t, me=None):
    kind = t.get('tx-type')
    out = {'id': t.get('id'), 'type': kind, 'round': t.get('confirmed-round'),
           'time': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t.get('round-time', 0)))
           if t.get('round-time') else None,
           'sender': t.get('sender'), 'fee_algo': algos(t.get('fee')),
           'group': t.get('group')}
    if kind == 'pay':
        p = t.get('payment-transaction') or {}
        out.update(receiver=p.get('receiver'), amount_algo=algos(p.get('amount')),
                   close_to=p.get('close-remainder-to'))
    elif kind == 'axfer':
        x = t.get('asset-transfer-transaction') or {}
        out.update(receiver=x.get('receiver'), asset_id=x.get('asset-id'),
                   amount=x.get('amount'),
                   opt_in=x.get('receiver') == t.get('sender') and not x.get('amount'))
    elif kind == 'appl':
        x = t.get('application-transaction') or {}
        out.update(app_id=x.get('application-id') or t.get('created-application-index'),
                   on_complete=x.get('on-completion'),
                   args=len(x.get('application-args') or []))
    elif kind == 'acfg':
        x = t.get('asset-config-transaction') or {}
        out.update(asset_id=x.get('asset-id') or t.get('created-asset-index'),
                   created=bool(t.get('created-asset-index')))
    elif kind == 'keyreg':
        x = t.get('keyreg-transaction') or {}
        out.update(online=bool(x.get('vote-participation-key')))
    if me:
        out['direction'] = 'out' if t.get('sender') == me else 'in'
    if t.get('note'):
        n = codec.unb64(t['note'])
        out['note'] = _printable(n) or '0x' + n[:32].hex()
    return out


_PRICE = {'at': 0, 'v': None}


def price_usd():
    ttl = float(os.environ.get('ALGORAND_PRICE_TTL', 60))
    if _PRICE['v'] and time.time() - _PRICE['at'] < ttl:
        return _PRICE['v']
    url = ('https://api.coingecko.com/api/v3/simple/price?ids=algorand&vs_currencies=usd'
           '&include_24hr_change=true&include_market_cap=true')
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10) as r:
        d = json.loads(r.read()).get('algorand') or {}
    v = {'usd': d.get('usd'), 'change_24h_pct': round(d.get('usd_24h_change') or 0, 2),
         'market_cap_usd': round(d.get('usd_market_cap') or 0)}
    _PRICE.update(at=time.time(), v=v)
    return v


def _b64(b):
    return base64.b64encode(b).decode()
