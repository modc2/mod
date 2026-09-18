#!/usr/bin/env python3
"""near wallet — keys, borsh, ed25519, and every write the chain accepts.

The read half of this module never touches a key; this file is the write
half, and it is deliberately boring: a keystore under ~/.mod/near/ (secrets
stay off the repo, per the fleet convention), a borsh serializer for exactly
the transaction schema NEAR uses, and ed25519 via PyNaCl when it is
installed with a pure-Python RFC 8032 fallback so the module still has zero
hard dependencies.

Safety model, copied from orbit/eth because it works:
  - a MAINNET write refuses without confirm=true — testnet is free
  - remote callers (HTTP REST or HTTP MCP) must present the write token from
    ~/.mod/near/token; local callers (stdio MCP, `m near/...`) are exempt
  - secret keys never leave this file: no tool, route or log returns one
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.request

from chain import (Client, EXPLORERS, NearError, TGAS, YOCTO, _http,
                   is_account_id, near)

MOD_DIR = os.path.expanduser(os.environ.get('NEAR_HOME', '~/.mod/near'))
KEYS_FILE = os.path.join(MOD_DIR, 'keys.json')
TOKEN_FILE = os.path.join(MOD_DIR, 'token')
FAUCET = 'https://helper.testnet.near.org/account'
DEFAULT_GAS_TGAS = 100
MAX_GAS_TGAS = 300


# ── base58 (Bitcoin alphabet — keys, hashes, signatures on NEAR) ──

_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
_B58_INDEX = {c: i for i, c in enumerate(_B58)}


def b58encode(raw):
    n = int.from_bytes(raw, 'big')
    out = ''
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(raw) - len(raw.lstrip(b'\0'))
    return '1' * pad + (out or '')


def b58decode(s):
    n = 0
    for c in s:
        if c not in _B58_INDEX:
            raise NearError(f'invalid base58 character {c!r}')
        n = n * 58 + _B58_INDEX[c]
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    pad = len(s) - len(s.lstrip('1'))
    return b'\0' * pad + raw


# ── ed25519 — PyNaCl when present, RFC 8032 in pure Python otherwise ──

try:
    from nacl.signing import SigningKey as _NaclKey
except ImportError:
    _NaclKey = None

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _xrecover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    return _P - x if x % 2 else x


_BY = 4 * pow(5, _P - 2, _P) % _P
_BX = _xrecover(_BY)
_BASE = (_BX, _BY, 1, _BX * _BY % _P)


def _ed_add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _ed_mul(p, e):
    q = (0, 1, 1, 0)
    while e:
        if e & 1:
            q = _ed_add(q, p)
        p = _ed_add(p, p)
        e >>= 1
    return q


def _ed_compress(p):
    x, y, z, _ = p
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, 'little')


def _pure_expand(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public(seed):
    """32-byte public key from a 32-byte seed."""
    if _NaclKey:
        return bytes(_NaclKey(seed).verify_key)
    a, _ = _pure_expand(seed)
    return _ed_compress(_ed_mul(_BASE, a))


def ed25519_sign(seed, message):
    """64-byte detached signature."""
    if _NaclKey:
        return _NaclKey(seed).sign(message).signature
    a, prefix = _pure_expand(seed)
    pub = _ed_compress(_ed_mul(_BASE, a))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), 'little') % _Q
    rp = _ed_compress(_ed_mul(_BASE, r))
    k = int.from_bytes(hashlib.sha512(rp + pub + message).digest(), 'little') % _Q
    return rp + ((r + k * a) % _Q).to_bytes(32, 'little')


# ── NEAR key strings ─────────────────────────────────────────────

def key_to_str(raw):
    return 'ed25519:' + b58encode(raw)


def pub_from_str(s):
    s = str(s).strip()
    raw = b58decode(s.split(':', 1)[1] if ':' in s else s)
    if len(raw) != 32:
        raise NearError(f'public key must be 32 bytes, got {len(raw)}')
    return raw


def seed_from_secret(s):
    """NEAR secret keys are base58 of seed||pubkey (64 bytes); accept a bare
    32-byte seed too."""
    s = str(s).strip()
    raw = b58decode(s.split(':', 1)[1] if ':' in s else s)
    if len(raw) == 64:
        return raw[:32]
    if len(raw) == 32:
        return raw
    raise NearError(f'secret key must decode to 64 (seed||pub) or 32 bytes, '
                    f'got {len(raw)}')


def secret_to_str(seed):
    return 'ed25519:' + b58encode(seed + ed25519_public(seed))


# ── borsh — just the slices of the schema a transaction needs ────

def _u32(n):
    return int(n).to_bytes(4, 'little')


def _u64(n):
    return int(n).to_bytes(8, 'little')


def _u128(n):
    return int(n).to_bytes(16, 'little')


def _bstr(s):
    b = s.encode()
    return _u32(len(b)) + b


def _bbytes(b):
    return _u32(len(b)) + b


def _pubkey(raw):
    return b'\0' + raw                     # key_type 0 = ed25519


# Action builders → borsh bytes, enum tags as nearcore declares them.

def a_create_account():
    return b'\x00'


def a_deploy(code):
    return b'\x01' + _bbytes(code)


def a_function_call(method, args_bytes, gas, deposit_yocto):
    return (b'\x02' + _bstr(method) + _bbytes(args_bytes) +
            _u64(gas) + _u128(deposit_yocto))


def a_transfer(deposit_yocto):
    return b'\x03' + _u128(deposit_yocto)


def a_add_key(pub_raw, receiver=None, methods=None, allowance_yocto=None):
    """FullAccess when receiver is None; FunctionCall-scoped otherwise."""
    ak = _u64(0)                           # access-key nonce starts at 0
    if receiver is None:
        ak += b'\x01'                      # permission 1 = FullAccess
    else:
        ak += b'\x00'                      # permission 0 = FunctionCall
        ak += (b'\x01' + _u128(allowance_yocto)) if allowance_yocto else b'\x00'
        ak += _bstr(receiver)
        methods = methods or []
        ak += _u32(len(methods)) + b''.join(_bstr(m) for m in methods)
    return b'\x05' + _pubkey(pub_raw) + ak


def a_delete_key(pub_raw):
    return b'\x06' + _pubkey(pub_raw)


def serialize_tx(signer_id, pub_raw, nonce, receiver_id, block_hash_raw, actions):
    return (_bstr(signer_id) + _pubkey(pub_raw) + _u64(nonce) +
            _bstr(receiver_id) + block_hash_raw +
            _u32(len(actions)) + b''.join(actions))


def sign_tx(seed, tx_bytes):
    """SignedTransaction = Transaction ++ Signature(ed25519 over sha256(tx))."""
    sig = ed25519_sign(seed, hashlib.sha256(tx_bytes).digest())
    return tx_bytes + b'\0' + sig


# ── keystore — ~/.mod/near/keys.json, plus the remote-write token ─

def _load_store():
    try:
        with open(KEYS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {'default': {}, 'accounts': {}}
    except Exception as e:
        raise NearError(f'keystore unreadable at {KEYS_FILE}: {e}', status=500)


def _save_store(store):
    os.makedirs(MOD_DIR, mode=0o700, exist_ok=True)
    tmp = KEYS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(store, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, KEYS_FILE)
    ensure_token()


def ensure_token():
    """The remote-write token exists as soon as any key does."""
    if not os.path.exists(TOKEN_FILE):
        os.makedirs(MOD_DIR, mode=0o700, exist_ok=True)
        with open(TOKEN_FILE, 'w') as f:
            f.write(secrets.token_hex(16))
        os.chmod(TOKEN_FILE, 0o600)
    return read_token()


def read_token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def check_token(token):
    held = read_token()
    if not held:
        raise NearError('no wallet exists yet — create one locally first '
                        '(m near/wallet op=generate)', status=403)
    if not secrets.compare_digest(str(token or ''), held):
        raise NearError('remote writes need the token from ~/.mod/near/token '
                        'on the host — pass token=', status=403)


def _key(network, account_id):
    return f'{network}:{account_id}'


def save_account(account_id, seed, network, make_default=True):
    store = _load_store()
    store['accounts'][_key(network, account_id)] = {
        'account_id': account_id, 'network': network,
        'public_key': key_to_str(ed25519_public(seed)),
        'secret_key': secret_to_str(seed),
        'created': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
    }
    if make_default or not store['default'].get(network):
        store['default'][network] = account_id
    _save_store(store)


def get_account(network, account_id=None):
    """(account_id, seed) — the named account, or the network default."""
    store = _load_store()
    account_id = account_id or store['default'].get(network)
    if not account_id:
        raise NearError(f'no wallet for {network} — near_wallet op=generate '
                        'creates one, op=import brings your own', status=404)
    entry = store['accounts'].get(_key(network, account_id))
    if not entry:
        have = [k.split(':', 1)[1] for k in store['accounts']
                if k.startswith(network + ':')]
        raise NearError(f'no key for {account_id} on {network} — '
                        f'have: {", ".join(have) or "none"}', status=404)
    return account_id, seed_from_secret(entry['secret_key'])


def wallet_status():
    """Every stored account, public halves only. Secret keys never leave."""
    store = _load_store()
    accounts = [{'account_id': e['account_id'], 'network': e['network'],
                 'public_key': e['public_key'], 'created': e.get('created'),
                 'default': store['default'].get(e['network']) == e['account_id']}
                for e in store['accounts'].values()]
    return {'accounts': accounts, 'count': len(accounts),
            'defaults': store['default'], 'keystore': KEYS_FILE,
            'remote_write_token': TOKEN_FILE if read_token() else
            'created with the first key',
            'note': 'secret keys stay in the keystore — nothing returns one'}


def wallet(op='status', account_id=None, secret_key=None, network='testnet',
           make_default=True):
    """The keystore, one op at a time: status | generate | import | select |
    forget."""
    network = (network or 'testnet').strip()
    if op in (None, '', 'status', 'list'):
        return wallet_status()
    if op == 'generate':
        seed = secrets.token_bytes(32)
        pub = ed25519_public(seed)
        account_id = account_id or pub.hex()   # implicit account = hex(pubkey)
        save_account(account_id, seed, network, make_default)
        out = {'ok': True, 'account_id': account_id, 'network': network,
               'public_key': key_to_str(pub),
               'keystore': KEYS_FILE}
        if account_id == pub.hex():
            out['note'] = ('implicit account — it exists once funded; on '
                           'testnet, near_create_account with a .testnet name '
                           'uses the faucet instead')
        return out
    if op == 'import':
        if not secret_key:
            raise NearError('op=import needs secret_key (ed25519:base58, the '
                            'NEAR CLI format)')
        seed = seed_from_secret(secret_key)
        account_id = account_id or ed25519_public(seed).hex()
        save_account(account_id, seed, network, make_default)
        return {'ok': True, 'account_id': account_id, 'network': network,
                'public_key': key_to_str(ed25519_public(seed))}
    if op == 'select':
        store = _load_store()
        if _key(network, account_id or '') not in store['accounts']:
            raise NearError(f'{account_id!r} is not in the keystore for {network}')
        store['default'][network] = account_id
        _save_store(store)
        return {'ok': True, 'default': store['default']}
    if op == 'forget':
        store = _load_store()
        gone = store['accounts'].pop(_key(network, account_id or ''), None)
        if store['default'].get(network) == account_id:
            store['default'].pop(network, None)
        _save_store(store)
        return {'ok': gone is not None, 'forgot': account_id,
                'note': 'key removed from this keystore only — it still '
                        'exists on chain until near_key op=delete'}
    raise NearError(f'unknown wallet op {op!r} — status, generate, import, '
                    'select, forget')


# ── signing client — nonce, block hash, broadcast, decode ────────

def guard(network, confirm):
    """Testnet is free money; everything else must be asked for twice."""
    if network != 'testnet' and not confirm:
        raise NearError(f'a {network} write spends real NEAR — refused '
                        'without confirm=true', status=403)


def _yocto(amount_near):
    """NEAR (float/str) → yocto int, via Decimal so 0.1 NEAR stays exactly
    10^23 yocto instead of picking up binary-float noise."""
    from decimal import Decimal
    return int(Decimal(str(amount_near)) * YOCTO)


def transact(client, account_id, seed, receiver_id, actions, wait='EXECUTED_OPTIMISTIC'):
    """Build → sign → broadcast one transaction, and say what happened."""
    pub_raw = ed25519_public(seed)
    # Optimistic finality: after a just-sent transaction the FINAL view of the
    # key still holds the old nonce for a couple of blocks, and a stale nonce
    # is an INVALID_TRANSACTION.
    key = client.call('query', {'finality': 'optimistic',
                                'request_type': 'view_access_key',
                                'account_id': account_id,
                                'public_key': key_to_str(pub_raw)})
    if key.get('error'):
        raise NearError(f'{key_to_str(pub_raw)} is not a key on {account_id}: '
                        f'{key["error"]}', status=422)
    nonce = int(key.get('nonce') or 0) + 1
    block_hash = b58decode(client.call('block', {'finality': 'final'})
                           ['header']['hash'])
    tx = serialize_tx(account_id, pub_raw, nonce, receiver_id, block_hash, actions)
    signed = base64.b64encode(sign_tx(seed, tx)).decode()
    try:
        r = client.call('send_tx', {'signed_tx_base64': signed, 'wait_until': wait})
    except NearError as e:
        if 'method not found' not in str(e).lower():
            raise
        r = client.call('broadcast_tx_commit', [signed])   # pre-send_tx nodes
    return _outcome(client, r)


def _outcome(client, r):
    status = r.get('status') or {}
    outcome = (r.get('transaction_outcome') or {}).get('outcome') or {}
    burnt = int(outcome.get('tokens_burnt') or 0)
    logs = list(outcome.get('logs') or [])
    for ro in r.get('receipts_outcome') or []:
        o = ro.get('outcome') or {}
        burnt += int(o.get('tokens_burnt') or 0)
        logs += o.get('logs') or []
    failure = status.get('Failure')
    tx_hash = (r.get('transaction') or {}).get('hash')
    result = None
    if isinstance(status.get('SuccessValue'), str) and status['SuccessValue']:
        raw = base64.b64decode(status['SuccessValue'])
        try:
            result = json.loads(raw)
        except Exception:
            result = {'raw_base64': status['SuccessValue']}
    return {'ok': not failure, 'hash': tx_hash, 'network': client.network,
            'failure': failure, 'result': result, 'logs': logs,
            'fee_near': burnt / YOCTO,
            'explorer': f'{EXPLORERS.get(client.network, EXPLORERS["mainnet"])}'
                        f'/txns/{tx_hash}' if tx_hash else None}


# ── the write operations ─────────────────────────────────────────

def _client(network=None, rpc=None):
    return Client(network=network or 'testnet', rpc=rpc)


def _load_wasm(wasm=None, wasm_path=None, wasm_url=None):
    if wasm_path:
        with open(os.path.expanduser(str(wasm_path)), 'rb') as f:
            code = f.read()
    elif wasm_url:
        req = urllib.request.Request(str(wasm_url),
                                     headers={'User-Agent': 'mod-near/0.3'})
        with urllib.request.urlopen(req, timeout=60) as r:
            code = r.read()
    elif wasm:
        try:
            code = base64.b64decode(wasm, validate=True)
        except Exception:
            raise NearError('wasm must be base64 of the compiled .wasm — or '
                            'pass wasm_path= / wasm_url=')
    else:
        raise NearError('nothing to deploy — pass wasm= (base64), wasm_path= '
                        'or wasm_url=')
    if code[:4] != b'\0asm':
        raise NearError('that is not a WASM binary (missing \\0asm magic) — '
                        'compile the contract first (cargo near build, or '
                        'npm run build for JS contracts)')
    return code


def deploy(wasm=None, wasm_path=None, wasm_url=None, account_id=None,
           init_method=None, init_args=None, network='testnet', rpc=None,
           confirm=False):
    """Deploy a contract to the signer's own account — on NEAR the account IS
    the contract, and deploying again over old code is how you upgrade.
    Optionally batch an init call into the same transaction."""
    c = _client(network, rpc)
    guard(c.network, confirm)
    code = _load_wasm(wasm, wasm_path, wasm_url)
    account_id, seed = get_account(c.network, account_id)
    actions = [a_deploy(code)]
    if init_method:
        if isinstance(init_args, str) and init_args.strip():
            init_args = json.loads(init_args)
        actions.append(a_function_call(str(init_method),
                                       json.dumps(init_args or {}).encode(),
                                       DEFAULT_GAS_TGAS * TGAS, 0))
    out = transact(c, account_id, seed, account_id, actions)
    out.update(deployed_to=account_id, code_bytes=len(code),
               code_sha256=hashlib.sha256(code).hexdigest(),
               init=init_method or None)
    return out


def call(contract, method, args=None, gas_tgas=None, deposit_near=0,
         account_id=None, network='testnet', rpc=None, confirm=False):
    """A change call — signed, gas-metered, optionally attaching a deposit."""
    c = _client(network, rpc)
    guard(c.network, confirm)
    if not is_account_id(str(contract).strip().lower()):
        raise NearError(f'{contract!r} is not a NEAR account ID')
    if isinstance(args, str) and args.strip():
        args = json.loads(args)
    gas = int(min(float(gas_tgas or DEFAULT_GAS_TGAS), MAX_GAS_TGAS) * TGAS)
    account_id, seed = get_account(c.network, account_id)
    out = transact(c, account_id, seed, str(contract).strip().lower(),
                   [a_function_call(str(method), json.dumps(args or {}).encode(),
                                    gas, _yocto(deposit_near or 0))])
    out.update(contract=contract, method=method, signer=account_id)
    return out


def send(to, amount_near, account_id=None, network='testnet', rpc=None,
         confirm=False):
    """Plain NEAR transfer."""
    c = _client(network, rpc)
    guard(c.network, confirm)
    to = str(to).strip().lower()
    if not is_account_id(to):
        raise NearError(f'{to!r} is not a NEAR account ID')
    if float(amount_near) <= 0:
        raise NearError('amount_near must be positive')
    account_id, seed = get_account(c.network, account_id)
    out = transact(c, account_id, seed, to, [a_transfer(_yocto(amount_near))])
    out.update({'from': account_id, 'to': to, 'amount_near': float(amount_near)})
    return out


def create_account(new_account_id, initial_near=0.1, public_key=None,
                   account_id=None, network='testnet', rpc=None, confirm=False):
    """A named account. On testnet, a fresh *.testnet name comes from the
    faucet helper — free, funded, no signer needed. Anywhere else the new
    name must be a sub-account of the signer (app.you.near from you.near),
    created and funded in one transaction."""
    c = _client(network, rpc)
    new_account_id = str(new_account_id).strip().lower()
    if not is_account_id(new_account_id):
        raise NearError(f'{new_account_id!r} is not a valid NEAR account ID')

    # The key the new account starts with: the one given, or a fresh one
    # generated into the keystore so the account is usable immediately.
    if public_key:
        pub_raw = pub_from_str(public_key)
        seed = None
    else:
        seed = secrets.token_bytes(32)
        pub_raw = ed25519_public(seed)

    faucet_eligible = (c.network == 'testnet' and
                       new_account_id.endswith('.testnet') and
                       new_account_id.count('.') == 1)
    if faucet_eligible:
        try:
            r = _http(FAUCET, {'newAccountId': new_account_id,
                               'newAccountPublicKey': key_to_str(pub_raw)},
                      timeout=30)
        except Exception as e:
            raise NearError(f'testnet faucet unreachable: {e}', status=502)
        failure = (r.get('status') or {}).get('Failure')
        if failure:
            raise NearError(f'faucet refused {new_account_id}: '
                            f'{json.dumps(failure)[:300]}', status=422)
        if seed:
            save_account(new_account_id, seed, c.network)
        return {'ok': True, 'account_id': new_account_id, 'network': c.network,
                'public_key': key_to_str(pub_raw), 'via': 'testnet faucet',
                'hash': (r.get('transaction') or {}).get('hash'),
                'in_keystore': bool(seed),
                'explorer': f'{EXPLORERS["testnet"]}/address/{new_account_id}'}

    guard(c.network, confirm)
    signer, signer_seed = get_account(c.network, account_id)
    if not new_account_id.endswith('.' + signer):
        raise NearError(f'{new_account_id} must be a sub-account of the signer '
                        f'({signer}) — only the registrar mints top-level names',
                        status=422)
    actions = [a_create_account(), a_add_key(pub_raw),
               a_transfer(_yocto(initial_near or 0))]
    out = transact(c, signer, signer_seed, new_account_id, actions)
    if out.get('ok') and seed:
        save_account(new_account_id, seed, c.network, make_default=False)
    out.update(account_id=new_account_id, funded_near=float(initial_near or 0),
               public_key=key_to_str(pub_raw), in_keystore=bool(seed))
    return out


def key(op, public_key=None, contract=None, methods=None, allowance_near=None,
        account_id=None, network='testnet', rpc=None, confirm=False):
    """Access keys on the signer's account: op=add (FullAccess, or scoped to
    contract= with methods= and allowance_near=) or op=delete."""
    c = _client(network, rpc)
    guard(c.network, confirm)
    if not public_key:
        raise NearError('public_key is required (ed25519:base58)')
    pub_raw = pub_from_str(public_key)
    account_id, seed = get_account(c.network, account_id)
    if op == 'add':
        if isinstance(methods, str):
            methods = [m.strip() for m in methods.split(',') if m.strip()]
        action = a_add_key(pub_raw, receiver=contract, methods=methods,
                           allowance_yocto=_yocto(allowance_near)
                           if allowance_near else None)
    elif op == 'delete':
        if key_to_str(pub_raw) == key_to_str(ed25519_public(seed)) and not confirm:
            raise NearError('that is the signing key itself — deleting it '
                            'locks you out; pass confirm=true if you mean it',
                            status=403)
        action = a_delete_key(pub_raw)
    else:
        raise NearError(f'unknown key op {op!r} — add or delete')
    out = transact(c, account_id, seed, account_id, [action])
    out.update(account_id=account_id, op=op, public_key=key_to_str(pub_raw),
               scope='FullAccess' if op == 'add' and not contract
               else contract or None)
    return out
