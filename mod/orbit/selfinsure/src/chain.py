#!/usr/bin/env python3
"""selfinsure chain — the bridge that makes a pool's reserves real.

A mutual that says it holds 12 ETH and cannot show you 12 ETH is a spreadsheet.
This file is what turns the spreadsheet into a claim on an address: premiums are
credited only against a transaction that actually landed in the pool's vault, and
a payout is not marked paid until a transfer leaves it. Everything here is a
question asked of a chain, or an instruction sent to one.

It signs nothing itself. Ethereum keys live in the `eth` module's keystore and
Solana keys in the `solana` module's; this module holds no secret material and
cannot move money the operator of those modules has not unlocked. That is
deliberate — one module holding both the ledger and the keys is the shape of
every mutual that quietly became an insurer.

Two chains, one vocabulary:

    ethereum   native ETH or any ERC-20, on the 14 networks the eth module knows
    solana     native SOL or any SPL token, on mainnet/devnet/testnet

Amounts crossing this boundary are integer base units — wei, lamports, the
token's smallest unit — because a mutual that rounds is a mutual that leaks.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ETH_URL = os.environ.get('SELFINSURE_ETH_URL', 'http://127.0.0.1:50730')
SOL_URL = os.environ.get('SELFINSURE_SOL_URL', 'http://127.0.0.1:50710')
ACTIVATOR = os.environ.get('SELFINSURE_ACTIVATOR', 'http://127.0.0.1:9000')
TIMEOUT = float(os.environ.get('SELFINSURE_CHAIN_TIMEOUT', 90))
SOL_SECRET_FILE = os.path.expanduser(
    os.environ.get('SELFINSURE_SOL_SECRET', '~/.mod/solana/server.secret'))

# keccak256("Transfer(address,address,uint256)") — the only ERC-20 event this
# module needs, and reading it off the receipt beats needing the token's ABI.
ERC20_TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

CHAINS = {
    'ethereum': {
        'module': 'eth', 'url': ETH_URL, 'symbol': 'ETH', 'decimals': 18,
        'unit': 'wei', 'id_name': 'transaction hash',
        'default_network': os.environ.get('SELFINSURE_ETH_NETWORK', 'local'),
        'about': 'native ETH or any ERC-20, across the eth module\'s 14 EVM networks',
    },
    'solana': {
        'module': 'solana', 'url': SOL_URL, 'symbol': 'SOL', 'decimals': 9,
        'unit': 'lamports', 'id_name': 'signature',
        'default_network': os.environ.get('SELFINSURE_SOL_NETWORK', 'devnet'),
        'about': 'native SOL or any SPL token, on mainnet, devnet or testnet',
    },
}

ALIASES = {'eth': 'ethereum', 'evm': 'ethereum', 'ether': 'ethereum',
           'sol': 'solana', 'svm': 'solana'}


class ChainError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra

    def dict(self):
        return {'error': self.message, **self.extra}


# ── plumbing ─────────────────────────────────────────────────────

def _knock(module):
    """A slept module does not wake on a direct-port call — the activator has to
    see the request. Knocking is best-effort and never the reason a call fails."""
    try:
        urllib.request.urlopen(f'{ACTIVATOR}/api/{module}/health', timeout=20).read()
    except Exception:
        pass


def _request(chain, path, method='GET', params=None, body=None, token=None,
             timeout=None, _retried=False):
    spec = chain_spec(chain)
    url = spec['url'].rstrip('/') + path
    if params:
        live = {k: v for k, v in params.items() if v not in (None, '')}
        if live:
            url += '?' + urllib.parse.urlencode(live)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('content-type', 'application/json')
    if token:
        req.add_header('authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            return json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        raw = e.read().decode()[:600]
        try:
            payload = json.loads(raw)
            msg = payload.get('error') or payload.get('detail') or raw
            if isinstance(msg, dict):
                msg = msg.get('error') or json.dumps(msg)
        except Exception:
            msg = raw
        raise ChainError(f'{spec["module"]}: {msg}', status=e.code if e.code < 500 else 502)
    except urllib.error.URLError as e:
        if not _retried:
            _knock(spec['module'])
            return _request(chain, path, method, params, body, token, timeout,
                            _retried=True)
        raise ChainError(
            f'the {spec["module"]} module is not answering at {spec["url"]} ({e.reason}) '
            f'— selfinsure does not talk to a node directly, it asks {spec["module"]}',
            status=503)


_eth_token = {'value': None, 'at': 0}


def eth_token():
    """A mod-protocol token for this box's own identity. The eth module scopes
    accounts by caller, so this is what makes the vault's keystore visible."""
    if os.environ.get('SELFINSURE_ETH_TOKEN'):
        return os.environ['SELFINSURE_ETH_TOKEN']
    if _eth_token['value'] and time.time() - _eth_token['at'] < 300:
        return _eth_token['value']
    try:
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        # this directory has its own mod.py; it must not win over the protocol's
        saved = [p for p in sys.path if os.path.abspath(p or '.') == here]
        for p in saved:
            sys.path.remove(p)
        try:
            import mod as m
            token = m.mod('auth')().token({})
        finally:
            sys.path.extend(saved)
    except Exception as e:
        raise ChainError(f'could not mint a mod-protocol token for the eth module: {e}',
                         status=503)
    _eth_token.update(value=token, at=time.time())
    return token


def sol_token():
    """The solana module's write secret. Reads need none; signing does."""
    if os.environ.get('SELFINSURE_SOL_TOKEN'):
        return os.environ['SELFINSURE_SOL_TOKEN']
    try:
        with open(SOL_SECRET_FILE) as f:
            return f.read().strip()
    except OSError:
        return None


# ── vocabulary ───────────────────────────────────────────────────

def chain_spec(chain):
    key = ALIASES.get(str(chain or '').lower(), str(chain or '').lower())
    if key not in CHAINS:
        raise ChainError(f'{chain!r} is not a chain this module speaks — '
                         f'{", ".join(CHAINS)}', status=404)
    return CHAINS[key]


def chain_name(chain):
    return ALIASES.get(str(chain or '').lower(), str(chain or '').lower())


def is_address(chain, address):
    a = str(address or '').strip()
    if chain_name(chain) == 'ethereum':
        return len(a) == 42 and a.startswith('0x') and \
            all(c in '0123456789abcdefABCDEF' for c in a[2:])
    b58 = set('123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz')
    return 32 <= len(a) <= 44 and set(a) <= b58


def need_address(chain, address, field='address'):
    a = str(address or '').strip()
    if not a:
        raise ChainError(f'{field} is required for a {chain_name(chain)} pool — '
                         'that is where the money goes')
    if not is_address(chain, a):
        shape = ('0x followed by 40 hex characters' if chain_name(chain) == 'ethereum'
                 else 'a base58 public key, 32-44 characters')
        raise ChainError(f'{a!r} is not a {chain_name(chain)} address ({shape})')
    return a


def same_address(chain, a, b):
    if not a or not b:
        return False
    if chain_name(chain) == 'ethereum':
        return str(a).lower() == str(b).lower()
    return str(a) == str(b)


def to_base(amount, decimals):
    """Human amount → integer base units, through Decimal so 0.1 ETH is exactly
    10**17 wei and not one wei less."""
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise ChainError(f'{amount!r} is not a number')
    return int((d * (Decimal(10) ** int(decimals))).to_integral_value())


def to_human(base, decimals):
    from decimal import Decimal
    return float(Decimal(int(base or 0)) / (Decimal(10) ** int(decimals)))


def human_str(base, decimals):
    """Exact decimal text — what a float would round away at 18 decimals."""
    from decimal import Decimal
    d = Decimal(int(base or 0)) / (Decimal(10) ** int(decimals))
    return format(d.normalize(), 'f')


# ── networks and assets ──────────────────────────────────────────

def networks(chain):
    c = chain_name(chain)
    if c == 'ethereum':
        out = _request(c, '/status').get('networks') or []
        return [{'name': n['name'], 'testnet': n.get('testnet'),
                 'chain_id': n.get('chain_id'), 'symbol': n.get('currency') or 'ETH',
                 'explorer': n.get('explorer'), 'label': n.get('label')} for n in out]
    return [{'name': n, 'testnet': n != 'mainnet', 'symbol': 'SOL',
             'explorer': 'https://solscan.io',
             'label': f'Solana {n}'} for n in ('mainnet', 'devnet', 'testnet')]


def network_info(chain, network=None):
    c = chain_name(chain)
    spec = chain_spec(c)
    net = network or spec['default_network']
    if c == 'ethereum':
        st = _request(c, '/status', params={'network': net})
        n = st.get('network') or {}
        if network:
            n = next((x for x in ([n] + (st.get('networks') or []))
                      if x.get('name') == network), n)
        return {'chain': c, 'network': n.get('name') or net, 'ok': bool(n.get('ok', True)),
                'testnet': n.get('testnet'), 'chain_id': n.get('chain_id'),
                'symbol': n.get('currency') or 'ETH', 'decimals': 18,
                'height': n.get('block'), 'rpc': n.get('rpc'),
                'explorer': n.get('explorer'), 'via': spec['url']}
    n = _request(c, '/network', params={'network': net})
    return {'chain': c, 'network': n.get('network') or net, 'ok': bool(n.get('ok', True)),
            'testnet': (n.get('network') or net) != 'mainnet', 'chain_id': None,
            'symbol': 'SOL', 'decimals': 9, 'height': n.get('slot') or n.get('absolute_slot'),
            'rpc': n.get('rpc'), 'explorer': 'https://solscan.io', 'via': spec['url']}


def asset(chain, network=None, token=None):
    """What one unit of this pool's money is: symbol, decimals, and the contract
    or mint behind it when it is not the native coin."""
    c = chain_name(chain)
    info = network_info(c, network)
    if not token:
        return {'chain': c, 'network': info['network'], 'token': None,
                'symbol': info['symbol'], 'decimals': info['decimals'],
                'native': True, 'testnet': info['testnet'],
                'explorer': info.get('explorer')}
    token = need_address(c, token, 'token')
    if c == 'ethereum':
        t = _request(c, f'/tokens/{token}', params={'network': info['network']})
        return {'chain': c, 'network': info['network'], 'token': token,
                'symbol': t.get('symbol') or 'TOKEN',
                'decimals': int(t.get('decimals', 18)), 'native': False,
                'name': t.get('name'), 'testnet': info['testnet'],
                'explorer': info.get('explorer')}
    t = _request(c, '/token', params={'mint': token, 'network': info['network']})
    return {'chain': c, 'network': info['network'], 'token': token,
            'symbol': t.get('symbol') or 'TOKEN',
            'decimals': int(t.get('decimals', 9)), 'native': False,
            'name': t.get('name'), 'testnet': info['testnet'],
            'explorer': info.get('explorer')}


def balance(chain, network, address, token=None, decimals=None):
    """What the address actually holds right now, in base units."""
    c = chain_name(chain)
    address = need_address(c, address)
    if c == 'ethereum':
        r = _request(c, '/balance', params={'address': address, 'network': network,
                                            'token': token})
        for k in ('wei', 'raw', 'units', 'base'):
            if isinstance(r.get(k), (int, str)) and str(r.get(k)).isdigit():
                return {'base': int(r[k]), 'symbol': r.get('symbol') or 'ETH'}
        dec = decimals if decimals is not None else int(r.get('decimals', 18))
        val = r.get('balance', r.get('ether', r.get('amount', 0)))
        return {'base': to_base(val, dec), 'symbol': r.get('symbol') or 'ETH'}
    if token:
        r = _request(c, '/portfolio', params={'address': address, 'network': network,
                                              'include_dust': 'true', 'limit': 500})
        for row in (r.get('tokens') or r.get('holdings') or []):
            if same_address(c, row.get('mint'), token):
                dec = decimals if decimals is not None else int(row.get('decimals', 9))
                raw = row.get('raw') or row.get('amount_raw')
                base = int(raw) if raw not in (None, '') else to_base(
                    row.get('amount') or row.get('ui_amount') or 0, dec)
                return {'base': base, 'symbol': row.get('symbol') or 'TOKEN'}
        return {'base': 0, 'symbol': 'TOKEN'}
    r = _request(c, '/balance', params={'address': address, 'network': network})
    return {'base': int(r.get('lamports') or 0), 'symbol': 'SOL'}


# ── receipts ─────────────────────────────────────────────────────

def _eth_height(network):
    try:
        b = _request('ethereum', '/block', params={'network': network, 'tag': 'latest'})
        blk = b.get('block') or b
        return int(blk.get('number') or 0)
    except Exception:
        return None


def verify_transfer(chain, network, txid, to, token=None, decimals=None):
    """Did this transaction actually move money into that address?

    Returns what the chain says, never what the caller claimed: the amount is
    read off the transaction, not off the request. A pool credits the number
    that comes back from here, so an over- or under-payment is recorded as what
    it was rather than as what was owed.
    """
    c = chain_name(chain)
    txid = str(txid or '').strip()
    if not txid:
        raise ChainError(f'which {chain_spec(c)["id_name"]}?')
    to = need_address(c, to, 'to')
    if c == 'ethereum':
        return _verify_eth(network, txid, to, token, decimals)
    return _verify_sol(network, txid, to, token, decimals)


def _verify_eth(network, txid, to, token, decimals):
    if not (txid.startswith('0x') and len(txid) == 66):
        raise ChainError(f'{txid!r} is not an Ethereum transaction hash '
                         '(0x + 64 hex characters)')
    r = _request('ethereum', '/tx', params={'hash': txid, 'network': network})
    tx, receipt = r.get('transaction') or {}, r.get('receipt') or {}
    if not tx:
        raise ChainError(f'no transaction {txid} on {network}', status=404)
    if receipt and int(receipt.get('status', 1)) != 1:
        raise ChainError(f'transaction {txid} reverted — it moved nothing', status=409,
                         txid=txid)
    if not receipt:
        raise ChainError(f'{txid} has not been mined yet — try again once it confirms',
                         status=409, txid=txid)
    block = int(receipt.get('blockNumber') or tx.get('blockNumber') or 0)
    height = _eth_height(network)
    out = {'chain': 'ethereum', 'network': network, 'txid': txid,
           'from': tx.get('from'), 'block': block,
           'at': receipt.get('blockTimestamp') or tx.get('blockTimestamp'),
           'confirmations': (height - block + 1) if height and block else None,
           'explorer': r.get('explorer')}
    if isinstance(out['at'], str):
        try:
            out['at'] = int(out['at'], 16)
        except ValueError:
            out['at'] = None
    if not token:
        if not same_address('ethereum', tx.get('to'), to):
            raise ChainError(
                f'{txid} paid {tx.get("to")}, not the pool vault {to} — a premium is '
                'credited only against a transfer that actually landed in the vault',
                status=409)
        out.update(base=int(tx.get('value') or 0), token=None,
                   decimals=decimals if decimals is not None else 18)
        memo = tx.get('input') or '0x'
        if memo and memo != '0x':
            try:
                out['memo'] = bytes.fromhex(memo[2:]).decode('utf-8', 'replace')
            except ValueError:
                out['memo'] = memo
        return out
    token = need_address('ethereum', token, 'token')
    got = 0
    for log in receipt.get('logs') or []:
        topics = [str(t).lower() for t in (log.get('topics') or [])]
        if len(topics) < 3 or topics[0] != ERC20_TRANSFER:
            continue
        if not same_address('ethereum', log.get('address'), token):
            continue
        if not same_address('ethereum', '0x' + topics[2][-40:], to):
            continue
        raw = str(log.get('data') or '0x0')
        got += int(raw, 16) if raw not in ('0x', '') else 0
    if not got:
        raise ChainError(
            f'{txid} contains no ERC-20 transfer of {token} into {to} — the pool takes '
            'premiums in its own asset only', status=409)
    out.update(base=got, token=token,
               decimals=decimals if decimals is not None else 18)
    return out


def _verify_sol(network, txid, to, token, decimals):
    r = _request('solana', '/tx', params={'signature': txid, 'network': network})
    if r.get('error') and not r.get('slot'):
        raise ChainError(f'no transaction {txid} on solana {network}: {r["error"]}',
                         status=404)
    if r.get('ok') is False:
        raise ChainError(f'transaction {txid} failed on chain — it moved nothing',
                         status=409, txid=txid)
    dec = decimals if decimals is not None else (9 if not token else None)
    got = 0
    if not token:
        for mv in r.get('sol_moves') or []:
            if same_address('solana', mv.get('address'), to) and (mv.get('sol') or 0) > 0:
                got += to_base(mv['sol'], 9)
        if not got:
            raise ChainError(
                f'{txid} moved no SOL into the pool vault {to} — a premium is credited '
                'only against a transfer that actually landed in the vault', status=409)
        dec = 9
    else:
        token = need_address('solana', token, 'mint')
        for mv in r.get('token_moves') or []:
            if not same_address('solana', mv.get('owner'), to):
                continue
            if not same_address('solana', mv.get('mint'), token):
                continue
            if (mv.get('amount') or 0) > 0:
                if dec is None:
                    dec = int(asset('solana', network, token)['decimals'])
                got += to_base(mv['amount'], dec)
        if not got:
            raise ChainError(
                f'{txid} contains no transfer of {token} into {to} — the pool takes '
                'premiums in its own asset only', status=409)
    slot = r.get('slot')
    height = None
    try:
        height = int((_request('solana', '/network',
                               params={'network': network}) or {}).get('slot') or 0)
    except Exception:
        pass
    return {'chain': 'solana', 'network': network, 'txid': txid,
            'from': r.get('fee_payer'), 'block': slot, 'at': r.get('time'),
            'confirmations': (height - slot) if height and slot else None,
            'status': r.get('status'), 'base': got, 'token': token, 'decimals': dec,
            'memo': r.get('memo'),
            'explorer': f'https://solscan.io/tx/{txid}'
                        + ('' if network == 'mainnet' else f'?cluster={network}')}


# ── payouts ──────────────────────────────────────────────────────

def can_sign(chain, account=None):
    """Could this module get a payout signed right now, without asking anyone
    for a password? A pool should be able to say so before it promises money."""
    c = chain_name(chain)
    try:
        if c == 'ethereum':
            me = _request(c, '/me', token=eth_token())
            names = {a['name']: a for a in (me.get('accounts') or [])}
            unlocked = {u.get('name') if isinstance(u, dict) else u
                        for u in (me.get('unlocked') or [])}
            if account and account not in names:
                return {'ok': False, 'reason': f'the eth module has no account named '
                                               f'{account!r} for this identity',
                        'accounts': sorted(names)}
            a = names.get(account) if account else None
            live = bool(a and (a.get('unlocked') or account in unlocked))
            return {'ok': live, 'accounts': sorted(names), 'unlocked': sorted(unlocked),
                    'address': (a or {}).get('address'),
                    'reason': None if live else
                    'the keystore account is locked — pass password= on the payout, or '
                    'unlock it in the eth module'}
        secret = sol_token()
        if not secret:
            return {'ok': False, 'reason': f'no solana write secret at {SOL_SECRET_FILE}'}
        w = _request(c, '/wallet', token=secret)
        names = [x['name'] for x in (w.get('wallets') or [])]
        if account and account not in names:
            return {'ok': False, 'reason': f'the solana module has no wallet named '
                                           f'{account!r}', 'accounts': names}
        addr = next((x['address'] for x in (w.get('wallets') or [])
                     if x['name'] == (account or w.get('default'))), None)
        return {'ok': True, 'accounts': names, 'address': addr, 'reason': None}
    except ChainError as e:
        return {'ok': False, 'reason': e.message}


def account_address(chain, account):
    """The address behind a keystore name — a pool binds to a name, but members
    are owed by an address, so the two must be reconciled once, at binding."""
    c = chain_name(chain)
    if c == 'ethereum':
        me = _request(c, '/me', token=eth_token())
        for a in me.get('accounts') or []:
            if a['name'] == account:
                return a['address']
        raise ChainError(
            f'the eth module has no account named {account!r} for this identity — '
            'create one there first (POST /accounts), then bind the pool to it',
            status=404, accounts=[a['name'] for a in (me.get('accounts') or [])])
    secret = sol_token()
    w = _request(c, '/wallet', token=secret)
    for x in w.get('wallets') or []:
        if x['name'] == account:
            return x['address']
    raise ChainError(f'the solana module has no wallet named {account!r} — create one '
                     'there first', status=404,
                     accounts=[x['name'] for x in (w.get('wallets') or [])])


def send(chain, network, account, to, base, token=None, decimals=18,
         password=None, memo=None, confirm=False):
    """Move base units out of the vault. Returns a receipt with a transaction id
    or raises — there is no third outcome, because a claim marked paid without a
    hash is the failure this whole module exists to avoid."""
    c = chain_name(chain)
    to = need_address(c, to, 'to')
    base = int(base)
    if base <= 0:
        raise ChainError('a payout of zero is not a payout')
    if c == 'ethereum':
        tok = eth_token()
        if token:
            r = _request(c, f'/tokens/{token}/transfer', method='POST', token=tok,
                         body={'account': account, 'to': to, 'amount': base,
                               'network': network, 'password': password,
                               'confirm': bool(confirm)})
        else:
            body = {'account': account, 'to': to, 'value': base, 'network': network,
                    'password': password, 'confirm': bool(confirm), 'wait': True}
            if memo:
                body['data'] = '0x' + str(memo).encode()[:220].hex()
            r = _request(c, '/send', method='POST', token=tok, body=body)
        txid = r.get('hash') or r.get('transaction_hash')
        if not txid:
            raise ChainError(f'the eth module returned no transaction hash: '
                             f'{json.dumps(r)[:300]}', status=502)
        return {'chain': c, 'network': r.get('network') or network, 'txid': txid,
                'status': r.get('status') or 'pending', 'block': r.get('block'),
                'explorer': r.get('explorer'), 'to': to, 'base': base,
                'gas_used': r.get('gas_used')}
    secret = sol_token()
    if not secret:
        raise ChainError(f'the solana module needs its write secret to sign — '
                         f'{SOL_SECRET_FILE} is missing', status=503)
    body = {'to': to, 'amount': to_human(base, decimals), 'network': network,
            'confirm': True, 'wallet': account}
    if token:
        body['mint'] = token
    if memo:
        body['memo'] = str(memo)[:200]
    r = _request(c, '/transfer', method='POST', token=secret, body=body)
    if r.get('needs_confirm') and not r.get('sent', True):
        raise ChainError(f'the solana module held this back: {r.get("reason")}',
                         status=409)
    txid = r.get('signature') or r.get('txid')
    if not txid:
        raise ChainError(f'the solana module returned no signature: '
                         f'{json.dumps(r)[:300]}', status=502)
    return {'chain': c, 'network': r.get('network') or network, 'txid': txid,
            'status': r.get('status') or 'sent', 'block': r.get('slot'),
            'explorer': r.get('explorer') or
            (f'https://solscan.io/tx/{txid}'
             + ('' if network == 'mainnet' else f'?cluster={network}')),
            'to': to, 'base': base}


def explorer_tx(chain, network, txid, explorer=None):
    c = chain_name(chain)
    if c == 'solana':
        return (f'https://solscan.io/tx/{txid}'
                + ('' if network == 'mainnet' else f'?cluster={network}'))
    return f'{explorer.rstrip("/")}/tx/{txid}' if explorer else None


def health():
    """Both bridges, side by side — the first thing to check when a pool says it
    cannot see its own money."""
    out = {}
    for name in CHAINS:
        try:
            info = network_info(name)
            signer = can_sign(name)
            out[name] = {'reachable': True, **info,
                         'accounts': signer.get('accounts') or [],
                         'unlocked': signer.get('unlocked'),
                         'can_sign_unattended': bool(signer.get('ok'))}
        except ChainError as e:
            out[name] = {'reachable': False, 'error': e.message,
                         'via': CHAINS[name]['url']}
    return out
