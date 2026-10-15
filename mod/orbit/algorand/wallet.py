"""algorand wallet — the write half: keystore, transaction builders, signing.

Keys live in ~/.mod/algorand/keys.json (0600), never the repo, and no fn,
route or tool returns a secret — import takes a mnemonic, nothing gives one
back except `export` from the local shell. Writes default to testnet; any
other network refuses without confirm=true; a write arriving over HTTP must
carry the token from ~/.mod/algorand/token.

Every write builds plain transaction dicts (canonical msgpack field names),
signs them with the selected key, submits and waits for confirmation. Several
transactions in one call (an app call with a payment, an opt-in plus a
transfer) go out as one atomic group.
"""

import hmac
import json
import os
import secrets
import time

import codec
from chain import AlgoError, Client, MICRO, algos

HOME = os.path.expanduser(os.environ.get('ALGORAND_HOME', '~/.mod/algorand'))
KEYS = os.path.join(HOME, 'keys.json')
TOKEN = os.path.join(HOME, 'token')

ON_COMPLETE = {'noop': 0, 'optin': 1, 'closeout': 2, 'clear': 3,
               'update': 4, 'delete': 5}


# ── keystore ─────────────────────────────────────────────────────

def _load():
    try:
        with open(KEYS) as f:
            return json.load(f)
    except FileNotFoundError:
        return {'accounts': {}, 'selected': None}


def _save(store):
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    tmp = KEYS + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(store, f, indent=2)
    os.replace(tmp, KEYS)


def token():
    """The remote-write token, created on first use."""
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    if not os.path.exists(TOKEN):
        fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32))
    with open(TOKEN) as f:
        return f.read().strip()


def check_token(given):
    if not given or not hmac.compare_digest(str(given), token()):
        raise AlgoError('writes over HTTP need token= (cat ~/.mod/algorand/token)',
                        status=401)


def _public(store):
    sel = store.get('selected')
    return {'selected': sel, 'keystore': KEYS,
            'accounts': [{'address': a, 'label': v.get('label'),
                          'created': v.get('created'), 'selected': a == sel}
                         for a, v in store['accounts'].items()]}


def wallet(op='status', mnemonic=None, address=None, label=None, network=None):
    store = _load()
    if op in ('status', 'list'):
        out = _public(store)
        if network and store.get('selected'):
            try:
                a = Client(network).account(store['selected'], names=False)
                out['balance'] = {'network': a['network'], 'algo': a['balance_algo'],
                                  'spendable_algo': a['spendable_algo']}
            except AlgoError as e:
                out['balance'] = {'error': str(e)}
        return out
    if op in ('generate', 'import'):
        if op == 'generate':
            seed = secrets.token_bytes(32)
        else:
            if not mnemonic:
                raise AlgoError('import needs mnemonic= (25 words)')
            seed = codec.from_mnemonic(mnemonic)
        addr = codec.encode_address(codec.public_key(seed))
        store['accounts'][addr] = {'seed': seed.hex(), 'label': label,
                                   'created': int(time.time())}
        store['selected'] = addr
        _save(store)
        out = _public(store)
        out['address'] = addr
        if op == 'generate':
            out['fund'] = ('testnet: paste the address into '
                           'https://bank.testnet.algorand.network '
                           '(or the AlgoKit dispenser); back up with '
                           '`m algorand/export` from the host shell')
        return out
    if op == 'select':
        if address not in store['accounts']:
            raise AlgoError(f'{address} is not in the keystore', status=404)
        store['selected'] = address
        _save(store)
        return _public(store)
    if op == 'forget':
        if address not in store['accounts']:
            raise AlgoError(f'{address} is not in the keystore', status=404)
        del store['accounts'][address]
        if store.get('selected') == address:
            store['selected'] = next(iter(store['accounts']), None)
        _save(store)
        return _public(store)
    raise AlgoError(f'unknown op {op!r} — status|generate|import|select|forget')


def export_mnemonic(address=None):
    """Local shell only — never mounted on a route or a tool."""
    addr, seed = signer(address)
    return {'address': addr, 'mnemonic': codec.to_mnemonic(seed)}


def signer(address=None):
    store = _load()
    addr = address or store.get('selected')
    if not addr:
        raise AlgoError('no key in the keystore — algo_wallet op=generate '
                        'or op=import mnemonic="…"', status=404)
    entry = store['accounts'].get(addr)
    if not entry:
        raise AlgoError(f'{addr} is not in the keystore', status=404)
    return addr, bytes.fromhex(entry['seed'])


def guard(network, confirm):
    net = (network or 'testnet').lower()
    if net != 'testnet' and not (confirm is True or str(confirm).lower() in ('true', '1', 'yes')):
        raise AlgoError(f'{net} moves real value — pass confirm=true to sign there',
                        status=403)
    return net


# ── building ─────────────────────────────────────────────────────

def _base(client, sender, kind, note=None, params=None):
    p = params or client.params()
    first = p['last-round']
    t = {'type': kind, 'snd': codec.decode_address(sender),
         'fee': max(p.get('min-fee', 1000), p.get('fee', 0)),
         'fv': first, 'lv': first + 1000,
         'gen': p['genesis-id'], 'gh': codec.unb64(p['genesis-hash'])}
    if note:
        t['note'] = note.encode() if isinstance(note, str) else note
    return t


def _addr(client, who):
    return codec.decode_address(client.resolve(who))


def _units(client, asset_id, amount, raw=False):
    if raw:
        return int(amount)
    dec = client.asset_params(asset_id).get('decimals') or 0
    return int(round(float(amount) * 10 ** dec))


def _list(v):
    if v in (None, ''):
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith('['):
            return json.loads(s)
        return [x.strip() for x in s.split(',') if x.strip()]
    return list(v)


def pay_txn(client, sender, to, amount_algo, note=None, close_to=None, params=None):
    t = _base(client, sender, 'pay', note, params)
    t['rcv'] = _addr(client, to)
    t['amt'] = int(round(float(amount_algo) * MICRO))
    if close_to:
        t['close'] = _addr(client, close_to)
    return t


def axfer_txn(client, sender, asset_id, to, amount, raw=False, note=None, params=None):
    t = _base(client, sender, 'axfer', note, params)
    t['xaid'] = int(asset_id)
    t['arcv'] = _addr(client, to)
    t['aamt'] = _units(client, asset_id, amount, raw) if amount else 0
    return t


def appl_txn(client, sender, app_id=0, on_complete='noop', args=None, method=None,
             accounts=None, apps=None, assets=None, boxes=None, note=None,
             approval=None, clear=None, schema=None, extra_pages=0, params=None):
    t = _base(client, sender, 'appl', note, params)
    t['apid'] = int(app_id or 0)
    t['apan'] = ON_COMPLETE[str(on_complete).lower()] if not isinstance(on_complete, int) \
        else on_complete
    raw, ret = abi_args(method, args)
    t['apaa'] = raw
    t['apat'] = [_addr(client, a) for a in _list(accounts)]
    t['apfa'] = [int(a) for a in _list(apps)]
    t['apas'] = [int(a) for a in _list(assets)]
    t['apbx'] = [{'i': 0, 'n': codec.app_arg(b if ':' in str(b) else f'str:{b}')}
                 for b in _list(boxes)]
    if approval is not None:
        t['apap'], t['apsu'] = approval, clear
        s = schema or {}
        t['apgs'] = {'nui': int(s.get('global_ints', 0)), 'nbs': int(s.get('global_bytes', 0))}
        t['apls'] = {'nui': int(s.get('local_ints', 0)), 'nbs': int(s.get('local_bytes', 0))}
        t['apep'] = int(extra_pages or 0)
    return t, ret


def abi_args(method, args):
    """method='add(uint64,uint64)uint64' + args → ARC-4 encoded app args and
    the return type; without a method, args go raw (see codec.app_arg)."""
    args = _list(args) if not isinstance(args, list) else args
    if not method:
        return [codec.app_arg(a) for a in args], None
    _, types, ret = codec.parse_signature(method)
    if len(types) != len(args):
        raise AlgoError(f'{method} takes {len(types)} args, got {len(args)}')
    return [codec.method_selector(method)] + \
        [codec.abi_encode(t, v) for t, v in zip(types, args)], ret


def _program(client, source=None, b64=None, path=None):
    if path:
        with open(os.path.expanduser(path)) as f:
            source = f.read()
    if source:
        return client.compile(source)['program']
    if b64:
        return codec.unb64(b64)
    return None


# ── sign, submit, confirm ────────────────────────────────────────

def send_group(client, sender, seed, txns, wait=True):
    if len(txns) > 16:
        raise AlgoError('an atomic group holds at most 16 transactions')
    if len(txns) > 1:
        gid = codec.group_id(txns)
        for t in txns:
            t['grp'] = gid
    auth = None
    try:
        auth = client.get(f'/v2/accounts/{sender}', exclude='all').get('auth-addr')
    except AlgoError:
        pass
    signed = []
    for t in txns:
        env = codec.sign_txn(seed, t)
        if auth and auth != sender:
            env['sgnr'] = codec.decode_address(auth)
        signed.append(env)
    first = client.submit(signed)
    ids = [codec.txid(t) for t in txns]
    out = {'network': client.network, 'txids': ids, 'txid': ids[-1],
           'group': codec.b64(txns[0]['grp']) if len(txns) > 1 else None,
           'explorer': f'{client.explorer}/tx/{ids[-1]}' if client.explorer else None}
    if first and first not in ids:
        out['node_txid'] = first
    if wait:
        conf = client.wait(ids[-1])
        out.update(confirmed_round=conf.get('confirmed-round'),
                   app_id=conf.get('application-index'),
                   asset_id=conf.get('asset-index'),
                   logs=conf.get('logs'))
    return out


def _go(network, confirm, account, build, wait=True):
    net = guard(network, confirm)
    client = Client(net)
    addr, seed = signer(account)
    p = client.params()
    txns = build(client, addr, p)
    if isinstance(txns, dict):
        txns = [txns]
    return client, send_group(client, addr, seed, txns, wait=wait)


# ── the writes ───────────────────────────────────────────────────

def send(to, amount_algo, note=None, close_to=None, account=None,
         network='testnet', confirm=False):
    _, out = _go(network, confirm, account, lambda c, a, p:
                 pay_txn(c, a, to, amount_algo, note, close_to, p))
    out['amount_algo'] = float(amount_algo)
    return out


def asset_create(name, unit, total, decimals=0, url=None, default_frozen=False,
                 manager=None, reserve=None, freeze=None, clawback=None,
                 note=None, account=None, network='testnet', confirm=False):
    def build(c, a, p):
        t = _base(c, a, 'acfg', note, p)
        me = codec.decode_address(a)
        role = lambda r: me if r in (None, '', 'me') else \
            (b'' if r == 'none' else _addr(c, r))
        t['apar'] = {'t': int(total), 'dc': int(decimals), 'un': unit, 'an': name,
                     'au': url, 'df': bool(default_frozen),
                     'm': role(manager), 'r': role(reserve),
                     'f': role(freeze), 'c': role(clawback)}
        return t
    _, out = _go(network, confirm, account, build)
    return out


def asset_optin(asset_id, account=None, network='testnet', confirm=False):
    _, out = _go(network, confirm, account, lambda c, a, p:
                 axfer_txn(c, a, c.asset_id(asset_id), a, 0, params=p))
    return out


def asset_send(asset_id, to, amount, raw=False, note=None, account=None,
               network='testnet', confirm=False):
    def build(c, a, p):
        return axfer_txn(c, a, c.asset_id(asset_id), to, amount, raw, note, p)
    _, out = _go(network, confirm, account, build)
    return out


def app_create(approval=None, clear=None, approval_b64=None, clear_b64=None,
               approval_path=None, clear_path=None, global_ints=0, global_bytes=0,
               local_ints=0, local_bytes=0, extra_pages=0, args=None, method=None,
               account=None, network='testnet', confirm=False):
    def build(c, a, p):
        ap = _program(c, approval, approval_b64, approval_path)
        cl = _program(c, clear or '#pragma version 10\nint 1', clear_b64, clear_path)
        if not ap:
            raise AlgoError('app_create needs approval= (TEAL source), '
                            'approval_b64= or approval_path=')
        t, _ = appl_txn(c, a, 0, 'noop', args, method, approval=ap, clear=cl,
                        schema={'global_ints': global_ints, 'global_bytes': global_bytes,
                                'local_ints': local_ints, 'local_bytes': local_bytes},
                        extra_pages=extra_pages, params=p)
        return t
    _, out = _go(network, confirm, account, build)
    if out.get('app_id'):
        out['app_address'] = codec.app_address(out['app_id'])
    return out


def app_call(app_id, method=None, args=None, on_complete='noop', accounts=None,
             apps=None, assets=None, boxes=None, pay_algo=None, note=None,
             account=None, network='testnet', confirm=False):
    """A signed app call. pay_algo= puts a payment to the app's account in
    front of it, atomically — the usual way to fund a method."""
    ret = {}

    def build(c, a, p):
        t, ret['type'] = appl_txn(c, a, app_id, on_complete, args, method, accounts,
                                  apps, assets, boxes, note, params=p)
        if pay_algo:
            return [pay_txn(c, a, codec.app_address(app_id), pay_algo, params=p), t]
        return [t]
    _, out = _go(network, confirm, account, build)
    out['return'] = _abi_return(out.get('logs'), ret.get('type'))
    return out


def app_update(app_id, approval=None, clear=None, approval_b64=None, clear_b64=None,
               approval_path=None, clear_path=None, account=None,
               network='testnet', confirm=False):
    def build(c, a, p):
        ap = _program(c, approval, approval_b64, approval_path)
        cl = _program(c, clear or '#pragma version 10\nint 1', clear_b64, clear_path)
        if not ap:
            raise AlgoError('app_update needs the new approval program')
        t, _ = appl_txn(c, a, app_id, 'update', params=p)
        t['apap'], t['apsu'] = ap, cl
        return t
    _, out = _go(network, confirm, account, build)
    return out


def _abi_return(logs, ret_type):
    if not logs or not ret_type or ret_type == 'void':
        return None
    last = codec.unb64(logs[-1])
    if not last.startswith(codec.ABI_RETURN_PREFIX):
        return None
    try:
        return codec.abi_decode(ret_type, last[4:])
    except Exception:
        return '0x' + last[4:].hex()


def simulate_call(app_id, method=None, args=None, sender=None, accounts=None,
                  apps=None, assets=None, boxes=None, on_complete='noop',
                  network=None):
    """Call an app with no key and no fee: algod simulates it, the logs and
    the ARC-4 return come back. Algorand's answer to a view call."""
    c = Client(network)
    who = c.resolve(sender) if sender else _sim_sender(c)
    t, ret = appl_txn(c, who, app_id, on_complete, args, method, accounts,
                      apps, assets, boxes)
    sim = c.simulate([t])
    grp = (sim.get('txn-groups') or [{}])[0]
    res = (grp.get('txn-results') or [{}])[0].get('txn-result') or {}
    logs = res.get('logs') or []
    return {'network': c.network, 'app_id': int(app_id), 'sender': who,
            'method': method, 'ok': not grp.get('failure-message'),
            'failure': grp.get('failure-message'),
            'return': _abi_return(logs, ret),
            'logs': [_log(x) for x in logs],
            'app_budget_consumed': grp.get('app-budget-consumed'),
            'global_delta': res.get('global-state-delta'),
            'round': sim.get('last-round')}


def _sim_sender(c):
    """Simulation still checks the sender can pay the fee: use the selected
    key if it is funded on this network, else the fee sink (always funded)."""
    store = _load()
    if store.get('selected'):
        try:
            if c.get(f'/v2/accounts/{store["selected"]}', exclude='all').get('amount', 0) > 2 * MICRO:
                return store['selected']
        except AlgoError:
            pass
    return FEE_SINK.get(c.network, codec.ZERO_ADDRESS)


FEE_SINK = {'mainnet': 'Y76M3MSY6DKBRHBL7C3NNDXGS5IIMQVQVUAB6MP4XEMMGVF2QWNPL226CA',
            'testnet': 'A7NMWS3NT3IUDMLVO26ULGXGIIOUQ3ND2TXSER6EBGRZNOBOUIQXHIBGDE',
            'betanet': 'A7NMWS3NT3IUDMLVO26ULGXGIIOUQ3ND2TXSER6EBGRZNOBOUIQXHIBGDE'}


def _log(b64log):
    b = codec.unb64(b64log)
    try:
        s = b.decode()
        if s.isprintable():
            return s
    except UnicodeDecodeError:
        pass
    return '0x' + b.hex()


def summary():
    return {'home': HOME, 'balance_fmt': algos}
