#!/usr/bin/env python3
"""postquant mcp — the chain as tools.

Twenty-two tools over one state machine. The order below is the order to use
them in: pq_quote before pq_set, because on this chain a write has a price and
the price moves; pq_get and pq_prove after, because a value that is a hash is
only worth what a proof against the state root says it is worth.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50760 # Streamable HTTP — POST /mcp

api.py mounts handle() at /mcp and routes its REST paths through call_tool(),
so an agent, a browser and a shell run the same code.
"""

import hashlib
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us afterwards.
    sys.path.append(HERE)

import chain as C                                              # noqa: E402
import keys as K                                               # noqa: E402
import state as S                                              # noqa: E402
from state import StateError, to_pq                            # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'A post-quantum L1 whose entire state machine is a market in key/value '
    'space. Every signature is ML-DSA (FIPS 204, lattice) and every commitment '
    'is SHA3-256 — there is no elliptic curve anywhere, which is the point. '
    'A key maps to a value, the value is bytes and is usually a 32-byte hash of '
    'something stored elsewhere, and holding that pair costs money continuously: '
    'write gas per byte of KEY and per byte of VALUE (a key byte costs 4x a '
    'value byte, and a value declared kind=hash is cheaper still), witness gas '
    'per byte of signature (an ML-DSA signature is 2420 bytes and the chain '
    'bills for it), and rent per byte per hour against a prepaid escrow. When '
    'the escrow runs out the entry expires and anyone can pq_sweep it for the '
    'bond the writer put up. '
    'ALWAYS call pq_quote before pq_set: it returns the exact split of write '
    'gas, witness gas and rent deposit at the current base fee, which floats '
    'with how fast the state is growing. Pass data= to pq_set to store the '
    'SHA3-256 of some text and keep the text off-chain — that is the pattern '
    'this chain is priced for; value= with value_kind=raw stores literal bytes '
    'and costs more. pq_get reads a key, pq_prove returns a Merkle path from it '
    'to the state root (what a light client needs to trust a hash), pq_check '
    'tests whether some data matches what a key committed to. '
    'The market: pq_list offers a key you own at a price, pq_buy takes it with '
    'its remaining lease, pq_market shows the base fee and every listing. '
    'pq_verify replays the whole chain from genesis and re-checks every state '
    'root and every signature. '
    'Writes need a wallet (pq_wallet action=create) and funds (pq_faucet, which '
    'this deployment pays from the genesis treasury). Transactions queue in a '
    'mempool and land in the next block; pq_mine produces one immediately '
    'rather than waiting for the block loop.'
)

_NODE = None
_NODE_LOCK = threading.Lock()


def node():
    """The one node this process runs. Opened on first use — starting the
    chain at import time would mean importing this module writes a genesis
    block, which is not a thing an import should do."""
    global _NODE
    if _NODE is None:
        with _NODE_LOCK:
            if _NODE is None:
                _NODE = C.Node()
    return _NODE


# ── shared shaping ────────────────────────────────────────────────


def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _int(desc, **kw):
    return {'type': 'integer', 'description': desc, **kw}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


def _amount(value):
    """Accept 1.5 or "1.5" as PQ, and 1500000000 as nq. A string with a dot is
    the display unit; a bare integer is the base unit. Ambiguity here would be
    a nine-order-of-magnitude mistake, so the rule is explicit."""
    if value is None:
        return None
    if isinstance(value, str) and '.' in value:
        whole, _, frac = value.strip().partition('.')
        frac = (frac + '0' * S.DECIMALS)[:S.DECIMALS]
        return int(whole or 0) * S.PQ + int(frac)
    if isinstance(value, float):
        return int(round(value * S.PQ))
    return int(value)


def _money(nq):
    return {'nq': int(nq), 'pq': to_pq(nq)}


def _entry_view(e, now=None):
    if e is None:
        return None
    now = now or int(time.time())
    return {
        'key': e['key'], 'owner': e['owner'],
        'value': e['value'], 'value_kind': e['value_kind'],
        'bytes': e['bytes'], 'writes': e['writes'],
        'escrow': _money(e.get('remaining', e['escrow'])),
        'bond': _money(e['bounty']),
        'expires_at': e['expires_at'],
        'expires_in': e.get('expires_in', max(0, e['expires_at'] - now)),
        'expired': e.get('expired', now >= e['expires_at']),
        'rent_per_day': _money(S.rent_for(e['bytes'], 86400)),
        'listed': e['listed'], 'price': _money(e['price']) if e['price'] else None,
        'created': e['created'], 'updated': e['updated'],
    }


def _wallet(name=None):
    return K.get(name)


def _mine_if(auto):
    """Most callers want the transaction to have happened by the time they get
    an answer back, so writes mine by default. Pass mine=false to leave it in
    the mempool and watch it land."""
    if not auto:
        return None
    b = node().produce()
    return b and {'height': b['block']['height'], 'hash': b['hash'],
                  'included': b['included'], 'dropped': b['dropped']}


def _submit(wallet, kind, args, **fields):
    """Sign, quote, submit and (by default) mine — the shape every write tool
    shares. dry_run stops after the quote and returns the unsigned plan."""
    n = node()
    tip = int(args.get('tip') or 0)
    max_fee = args.get('max_fee')
    if args.get('dry_run'):
        return {'dry_run': True, 'kind': kind, 'from': wallet['address'],
                'fields': fields, 'base_fee': _money(n.state.base_fee),
                'note': 'nothing was signed or submitted'}
    tx = n.make_tx(wallet, kind, max_fee=_amount(max_fee), tip=_amount(tip),
                   **fields)
    out = n.submit(tx)
    mined = _mine_if(args.get('mine', True))
    result = {'hash': tx['hash'], 'kind': kind, 'from': wallet['address'],
              'queued': out.get('queued', False), 'mined': mined,
              'witness_bytes': len(tx['sig']) // 2 +
                               (len(tx.get('pk', '')) // 2)}
    if mined:
        try:
            found = n.transaction(tx['hash'])
            result['receipt'] = _receipt_view(found.get('receipt'))
            result['status'] = found['status']
            result['height'] = found.get('height')
        except StateError:
            result['status'] = 'dropped'
    else:
        result['status'] = 'pending'
    return result


def _receipt_view(r):
    if not r:
        return None
    out = dict(r)
    for field in ('fee', 'refund', 'bounty', 'amount', 'price', 'escrow'):
        if field in out and isinstance(out[field], int):
            out[field] = _money(out[field])
    return out


# ── read tools ────────────────────────────────────────────────────


def _t_head(a):
    n = node()
    h = n.head()
    return {**h, 'burned': _money(h['burned']), 'supply': _money(h['supply']),
            'base_fee': _money(h['base_fee']),
            'scheme': K.SCHEME, 'hash': h['hash'],
            'signatures': f'{K.SCHEME} (FIPS 204)',
            'state_target_bytes': S.STATE_TARGET_BYTES,
            'pending': len(n.mempool)}


def _t_get(a):
    n = node()
    key = a['key']
    e = n.state.entry(key, int(time.time()))
    if e is None:
        raise StateError(f'no entry at {key!r}', code='no_entry', status=404)
    view = _entry_view(e)
    if e['value_kind'] == 'hash':
        view['commitment'] = (
            'this value is a SHA3-256 digest — the data it commits to is not on '
            'this chain. Use pq_check with the data to test it against this key.')
    return view


def _t_keys(a):
    n = node()
    now = int(time.time())
    prefix, owner = a.get('prefix'), a.get('owner')
    out = []
    for key in sorted(n.state.store):
        e = n.state.entry(key, now)
        if prefix and not key.startswith(prefix):
            continue
        if owner and e['owner'] != owner:
            continue
        if a.get('listed') and not e['listed']:
            continue
        if not a.get('include_expired') and e['expired']:
            continue
        out.append(_entry_view(e, now))
    limit = int(a.get('limit') or 100)
    return {'count': len(out), 'keys': out[:limit],
            'state_bytes': n.state.state_bytes(),
            'truncated': len(out) > limit}


def _t_quote(a):
    """The tool to call before writing anything."""
    n = node()
    key = a['key']
    kind = a.get('value_kind') or ('raw' if a.get('value') and not a.get('data')
                                   else 'hash')
    value = _value_for(a, kind)
    seconds = _seconds(a)
    addr = a.get('address')
    if not addr:
        w = K.get(a.get('wallet'), required=False)
        addr = w['address'] if w else None
    new_account = bool(addr) and addr not in n.state.accounts
    q = n.state.quote(key, value, kind, seconds, new_account=new_account)
    existing = n.state.entry(key, int(time.time()))
    return {
        **q,
        'write_cost': _money(q['write_cost']),
        'deposit': _money(q['rent']['deposit']),
        'total': _money(q['total']),
        'base_fee': _money(q['base_fee']),
        'rent': {**q['rent'], 'deposit': _money(q['rent']['deposit']),
                 'lease': _money(q['rent']['lease']),
                 'bounty': _money(q['rent']['bounty']),
                 'per_day': _money(q['rent']['per_day'])},
        'value': value,
        'occupied': _entry_view(existing) if existing else None,
        'affordable': (_money(n.state.balance(addr)) if addr else None),
    }


def _t_account(a):
    n = node()
    addr = a.get('address')
    if not addr:
        addr = K.get(a.get('wallet'))['address']
    acct = n.state.accounts.get(addr, {'balance': 0, 'nonce': 0, 'pk': None})
    now = int(time.time())
    owned = [_entry_view(n.state.entry(k, now), now)
             for k in sorted(n.state.store)
             if n.state.store[k]['owner'] == addr]
    return {'address': addr, 'balance': _money(acct['balance']),
            'nonce': acct['nonce'],
            'known_to_chain': acct.get('pk') is not None,
            'scheme': acct.get('scheme'),
            'keys_owned': len(owned),
            'bytes_occupied': sum(e['bytes'] for e in owned),
            'rent_per_day': _money(sum(S.rent_for(e['bytes'], 86400)
                                       for e in owned)),
            'keys': owned[:int(a.get('limit') or 50)],
            'pending': [t['hash'] for t in n.mempool.values()
                        if t['body']['from'] == addr]}


def _t_market(a):
    """The market as one card: what a byte costs right now, what is for sale,
    and what is about to expire."""
    n = node()
    now = int(time.time())
    listings, expiring = [], []
    for key in sorted(n.state.store):
        e = n.state.entry(key, now)
        if e['listed'] and not e['expired']:
            listings.append(_entry_view(e, now))
        if 0 < e['expires_in'] < int(a.get('expiring_within') or 86400):
            expiring.append(_entry_view(e, now))
    sample = n.state.quote('example/key', '00' * 32, 'hash', 86400)
    recent = [b['header'] for b in n.read_blocks_tail(10)]
    return {
        'base_fee': _money(n.state.base_fee),
        'base_fee_is': ('nq per gas, floating with state growth: '
                        f'{S.STATE_TARGET_BYTES} bytes per block is the target, '
                        f'and the fee moves at most '
                        f'{100 // S.BASE_FEE_MAX_CHANGE_DEN}% a block'),
        'state_bytes': n.state.state_bytes(),
        'keys': len(n.state.store),
        'recent_growth': [b['state_bytes'] for b in recent],
        'gas_schedule': {'tx_base': S.GAS_TX_BASE, 'key_byte': S.GAS_KEY_BYTE,
                         'value_byte_raw': S.GAS_VALUE_BYTE,
                         'value_byte_hash': S.GAS_VALUE_BYTE_HASH,
                         'witness_byte': S.GAS_WITNESS_BYTE,
                         'new_account': S.GAS_ACCOUNT_NEW,
                         'market_op': S.GAS_MARKET_OP},
        'rent': {'per_byte_hour': _money(S.RENT_PER_BYTE_HOUR),
                 'entry_overhead_bytes': S.ENTRY_OVERHEAD,
                 'min_lease_seconds': S.MIN_LEASE_SECONDS},
        'example': {'what': 'a 32-byte hash under an 11-byte key for one day',
                    'bytes': sample['billable_bytes'],
                    'write': _money(sample['write_cost']),
                    'deposit': _money(sample['rent']['deposit']),
                    'total': _money(sample['total'])},
        'listings': listings,
        'expiring_soon': sorted(expiring, key=lambda e: e['expires_in'])[:20],
        'burned': _money(n.state.burned),
        'rent_collected': _money(n.state.rent_collected),
        'supply': _money(n.state.supply),
    }


def _t_block(a):
    b = node().block(a.get('block'))
    full = bool(a.get('full'))
    return {**b['header'], 'hash': b['hash'],
            'proposer_signature_bytes': len(b.get('sig', '')) // 2,
            'txs': b['txs'] if full else [t['hash'] for t in b['txs']],
            'receipts': [_receipt_view(r) for r in b['receipts']]}


def _t_tx(a):
    found = node().transaction(a['hash'])
    if 'receipt' in found:
        found['receipt'] = _receipt_view(found['receipt'])
    return found


def _t_history(a):
    n = node()
    return {'history': n.history(a.get('address'), a.get('key'),
                                 int(a.get('limit') or 25))}


def _t_prove(a):
    n = node()
    proof = n.state.proof(a['key'])
    proof['entry'] = _entry_view(proof['entry'])
    proof['valid'] = S.check_proof(proof['leaf'], proof['path'], proof['root'])
    proof['head_root'] = n.head()['state_root']
    proof['note'] = ('the root here is the live state root; it equals the '
                     'header root only at a block boundary, so quote height '
                     f'{n.head()["height"]} with it')
    return proof


def _t_check(a):
    """Does some data match what a key committed to?"""
    n = node()
    key = a['key']
    e = n.state.entry(key, int(time.time()))
    if e is None:
        raise StateError(f'no entry at {key!r}', code='no_entry', status=404)
    data = a.get('data')
    digest = a.get('hash') or (
        hashlib.sha3_256(data.encode() if isinstance(data, str) else data)
        .hexdigest() if data is not None else None)
    if digest is None:
        raise StateError('pass data= (which is hashed) or hash= (a digest)',
                         code='bad_args')
    return {'key': key, 'committed': e['value'], 'computed': digest.lower(),
            'matches': e['value'] == digest.lower(),
            'value_kind': e['value_kind'],
            'expired': e['expired'],
            'note': ('a match means this data is what the key committed to at '
                     'the time it was written — the chain stores the digest, '
                     'never the data')}


def _t_verify(a):
    return node().verify(signatures=bool(a.get('signatures')))


def _t_mempool(a):
    n = node()
    return {'pending': [{'hash': t['hash'], **t['body']}
                        for t in n.pending()],
            'count': len(n.mempool),
            'recently_dropped': n.rejected[-10:]}


# ── write tools ───────────────────────────────────────────────────


def _seconds(a):
    if a.get('seconds'):
        return int(a['seconds'])
    if a.get('days'):
        return int(float(a['days']) * 86400)
    if a.get('hours'):
        return int(float(a['hours']) * 3600)
    return S.MIN_LEASE_SECONDS


def _value_for(a, kind):
    """data= is hashed, value= is taken as bytes. The chain never sees data."""
    if a.get('data') is not None:
        data = a['data']
        return hashlib.sha3_256(data.encode() if isinstance(data, str)
                                else data).hexdigest()
    value = (a.get('value') or '')
    if kind == 'hash' and value and not S.is_hex(value, 32):
        # A caller who passed text to value= with kind=hash meant data=.
        raise StateError('value= must be 32 bytes of hex when value_kind=hash '
                         '— pass data= instead and the chain will hash it',
                         code='bad_value')
    return value.lower()


def _t_set(a):
    n = node()
    w = _wallet(a.get('wallet'))
    key = a['key']
    kind = a.get('value_kind') or ('raw' if a.get('value') and not a.get('data')
                                   else 'hash')
    value = _value_for(a, kind)
    seconds = _seconds(a)
    q = n.state.quote(key, value, kind, seconds,
                      new_account=w['address'] not in n.state.accounts)
    deposit = _amount(a.get('deposit')) if a.get('deposit') is not None \
        else q['rent']['deposit']
    existing = n.state.entry(key, int(time.time()))
    if existing and not existing['expired'] and a.get('deposit') is None:
        deposit = 0            # already leased; extend with pq_fund, not here
    balance = n.state.balance(w['address'])
    if not a.get('dry_run') and balance < q['write_cost'] + deposit:
        raise StateError(
            f'{w["address"]} holds {to_pq(balance)} {S.SYMBOL} and this write '
            f'needs {to_pq(q["write_cost"] + deposit)} '
            f'({to_pq(q["write_cost"])} gas + {to_pq(deposit)} deposit). '
            'Call pq_faucet.', code='insufficient_funds')
    out = _submit(w, 'set', a, key=key, value=value, value_kind=kind,
                  deposit=deposit)
    out['quote'] = {'gas': q['gas'], 'gas_total': q['gas_total'],
                    'write_cost': _money(q['write_cost']),
                    'deposit': _money(deposit),
                    'lease_seconds': seconds,
                    'billable_bytes': q['billable_bytes']}
    if a.get('data') is not None:
        out['committed'] = {'value': value, 'of': 'sha3-256 of your data',
                            'stored_on_chain': '32 bytes, not the data'}
    return out


def _t_del(a):
    return _submit(_wallet(a.get('wallet')), 'del', a, key=a['key'])


def _t_fund(a):
    deposit = _amount(a.get('deposit'))
    if not deposit and (a.get('days') or a.get('hours') or a.get('seconds')):
        n = node()
        e = n.state.entry(a['key'], int(time.time()))
        if e:
            deposit = S.rent_for(e['bytes'], _seconds(a))
    if not deposit:
        raise StateError('pass deposit= or days=/hours= to size one',
                         code='bad_deposit')
    return _submit(_wallet(a.get('wallet')), 'fund', a, key=a['key'],
                   deposit=deposit)


def _t_transfer(a):
    to = a['to']
    if not K.valid_address(to):
        w = K.get(to, required=False)
        if w is None:
            raise StateError(f'{to!r} is not an address or a local wallet name',
                             code='bad_address')
        to = w['address']
    return _submit(_wallet(a.get('wallet')), 'xfer', a, to=to,
                   amount=_amount(a['amount']))


def _t_list(a):
    return _submit(_wallet(a.get('wallet')), 'list', a, key=a['key'],
                   price=_amount(a.get('price') or 0))


def _t_buy(a):
    return _submit(_wallet(a.get('wallet')), 'buy', a, key=a['key'],
                   max_price=_amount(a.get('max_price') or 0))


def _t_sweep(a):
    return _submit(_wallet(a.get('wallet')), 'sweep', a, key=a['key'])


def _t_wallet(a):
    action = (a.get('action') or 'list').lower()
    if action in ('list', 'ls'):
        out = K.wallets()
        n = node()
        for w in out['wallets']:
            w['balance'] = _money(n.state.balance(w['address']))
            w['nonce'] = n.state.accounts.get(w['address'], {}).get('nonce', 0)
        return out
    if action == 'create':
        w = K.create(a.get('name') or 'default', seed=a.get('seed'),
                     overwrite=bool(a.get('overwrite')))
        return {**w, 'note': 'the seed stays in the keystore and never leaves '
                             'this process; fund it with pq_faucet'}
    if action in ('use', 'default'):
        return K.use(a['name'])
    if action in ('remove', 'delete'):
        return K.remove(a['name'])
    if action in ('show', 'get'):
        w = K.get(a.get('name'))
        return {k: v for k, v in w.items() if k != 'seed'}
    raise StateError(f'unknown wallet action {action!r} — list, create, use, '
                     'show or remove', code='bad_action')


def _t_faucet(a):
    """Genesis treasury → a local wallet. This is a devnet with one proposer;
    the treasury is the genesis allocation and the faucet is a real signed
    transfer out of it, not a mint."""
    n = node()
    treasury = K.get(n.genesis['validator_name'], required=False)
    if treasury is None:
        raise StateError('the genesis treasury key is not in this keystore — '
                         'this node cannot pay a faucet', code='no_treasury',
                         status=403)
    to = a.get('address') or K.get(a.get('wallet'))['address']
    if not K.valid_address(to):
        w = K.get(to, required=False)
        to = w['address'] if w else to
    if not K.valid_address(to):
        raise StateError(f'{to!r} is not an address', code='bad_address')
    amount = _amount(a.get('amount')) or 100 * S.PQ
    cap = _amount(os.environ.get('POSTQUANT_FAUCET_MAX') or 0) or 10_000 * S.PQ
    if amount > cap:
        raise StateError(f'faucet cap is {to_pq(cap)} {S.SYMBOL} per call',
                         code='faucet_cap')
    return _submit(treasury, 'xfer', a, to=to, amount=amount)


def _t_mine(a):
    n = node()
    out = n.produce(force=bool(a.get('force')))
    if out is None:
        return {'mined': False, 'reason': 'nothing pending — pass force=true '
                                          'for an empty block', **n.head()}
    return {'mined': True, **out['block'], 'hash': out['hash'],
            'dropped': out['dropped'],
            'next_base_fee': _money(out['next_base_fee'])}


# ── registry ──────────────────────────────────────────────────────

_WALLET = _str('local wallet name or address to sign with (default wallet if '
               'omitted)')
_MINE = _bool('produce a block immediately so the transaction is final when '
              'this returns (default true)')
_DRY = _bool('price and describe the transaction without signing or sending it')
_FEE = {'tip': _str('tip per gas above the base fee, as PQ ("0.5") or nq'),
        'max_fee': _str('the most you will pay per gas; defaults to twice the '
                        'base fee so a moving market does not strand the tx')}

TOOLS = {
    'pq_head': {
        'description': 'The chain tip: height, block hash, state root, the '
                       'current base fee, how many bytes the store occupies, '
                       'supply and total burned. Start here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_head,
    },
    'pq_quote': {
        'description': 'What a write will cost, before you sign it. Returns the '
                       'three prices separately — write gas split into key '
                       'bytes, value bytes and witness bytes; the rent deposit '
                       'for the lease you asked for; and the refundable sweep '
                       'bond — at the current base fee. Call this before every '
                       'pq_set: the base fee floats with state growth and a '
                       'signed transaction cannot be repriced.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key you intend to write'),
            'data': _str('text to commit to — it is hashed with SHA3-256 and '
                         'only the 32-byte digest is priced and stored'),
            'value': _str('hex bytes to store literally, instead of data='),
            'value_kind': _str('hash (32-byte commitment, cheaper per byte) or '
                               'raw', enum=['hash', 'raw']),
            'days': _int('lease length in days'),
            'hours': _int('lease length in hours'),
            'seconds': _int('lease length in seconds (minimum 3600)'),
            'wallet': _WALLET,
            'address': _str('price it for this address instead of a wallet'),
        }, 'required': ['key']},
        'handler': _t_quote,
    },
    'pq_get': {
        'description': 'Read one key: its value, whether that value is a hash '
                       'or raw bytes, who owns it, how many bytes it occupies, '
                       'what rent it is paying, when the lease runs out and '
                       'whether it is for sale.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key to read')}, 'required': ['key']},
        'handler': _t_get,
    },
    'pq_keys': {
        'description': 'Browse the store: every live key, or those under a '
                       'prefix, owned by an address, or currently listed for '
                       'sale. Expired entries are hidden unless you ask for '
                       'them — they are still on disk until somebody sweeps.',
        'inputSchema': {'type': 'object', 'properties': {
            'prefix': _str('only keys starting with this'),
            'owner': _str('only keys held by this address'),
            'listed': _bool('only keys for sale'),
            'include_expired': _bool('include entries whose lease has run out'),
            'limit': _int('how many to return (default 100)')}},
        'handler': _t_keys,
    },
    'pq_set': {
        'description': 'Write a key. Pass data= to store the SHA3-256 of some '
                       'text (the text stays off-chain — this is the pattern '
                       'the chain is priced for), or value= with '
                       'value_kind=raw for literal hex bytes. Claims the key if '
                       'it is free, and only its owner can overwrite it. The '
                       'deposit is sized from days=/hours= automatically; it '
                       'buys a lease, and when the lease runs out the entry '
                       'expires and can be swept by anyone.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key to write'),
            'data': _str('text to commit to — hashed with SHA3-256'),
            'value': _str('hex bytes to store literally'),
            'value_kind': _str('hash or raw', enum=['hash', 'raw']),
            'days': _int('how long to lease it for (default 1 hour)'),
            'hours': _int('lease length in hours'),
            'seconds': _int('lease length in seconds'),
            'deposit': _str('override the rent deposit, as PQ ("2.5") or nq'),
            'wallet': _WALLET, 'mine': _MINE, 'dry_run': _DRY, **_FEE},
            'required': ['key']},
        'handler': _t_set,
    },
    'pq_del': {
        'description': 'Delete a key you own and get the unused rent and the '
                       'full sweep bond back. This is the only way the store '
                       'shrinks on purpose, which is why it pays.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key to delete'), 'wallet': _WALLET, 'mine': _MINE,
            'dry_run': _DRY, **_FEE}, 'required': ['key']},
        'handler': _t_del,
    },
    'pq_fund': {
        'description': 'Add rent to any key, yours or not — public data stays '
                       'up because somebody keeps paying for it. Size the top-up '
                       'with days=/hours= or give deposit= directly.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key to extend'),
            'deposit': _str('amount to add, as PQ or nq'),
            'days': _int('buy this many more days of lease'),
            'hours': _int('buy this many more hours'),
            'wallet': _WALLET, 'mine': _MINE, 'dry_run': _DRY, **_FEE},
            'required': ['key']},
        'handler': _t_fund,
    },
    'pq_sweep': {
        'description': 'Delete an entry whose lease has run out and collect the '
                       'bond its writer put up. Anyone can call it on any '
                       'expired key; the bond is sized to more than cover the '
                       'gas, which is what makes expiry real rather than '
                       'advisory. pq_keys include_expired=true finds targets.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the expired key to clear'), 'wallet': _WALLET,
            'mine': _MINE, 'dry_run': _DRY, **_FEE}, 'required': ['key']},
        'handler': _t_sweep,
    },
    'pq_list': {
        'description': 'Offer a key you own at a price. price=0 delists. The '
                       'buyer gets the entry with its remaining lease, so a '
                       'well-funded key is worth more than an empty one.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key to sell'),
            'price': _str('asking price as PQ ("25") or nq; 0 delists'),
            'wallet': _WALLET, 'mine': _MINE, 'dry_run': _DRY, **_FEE},
            'required': ['key', 'price']},
        'handler': _t_list,
    },
    'pq_buy': {
        'description': 'Buy a listed key at its asking price, taking ownership '
                       'along with the remaining lease and bond. Pass max_price '
                       'to refuse if the seller repriced first.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key to buy'),
            'max_price': _str('refuse above this, as PQ or nq'),
            'wallet': _WALLET, 'mine': _MINE, 'dry_run': _DRY, **_FEE},
            'required': ['key']},
        'handler': _t_buy,
    },
    'pq_market': {
        'description': 'The market in one call: the current base fee and what '
                       'moves it, the full gas schedule, the rent rate, a worked '
                       'example price, every key for sale, and everything about '
                       'to expire.',
        'inputSchema': {'type': 'object', 'properties': {
            'expiring_within': _int('seconds ahead to look for expiries '
                                    '(default 86400)')}},
        'handler': _t_market,
    },
    'pq_transfer': {
        'description': 'Send PQ to an address or to another local wallet by '
                       'name.',
        'inputSchema': {'type': 'object', 'properties': {
            'to': _str('a pq… address or a local wallet name'),
            'amount': _str('amount as PQ ("1.5") or nq (1500000000)'),
            'wallet': _WALLET, 'mine': _MINE, 'dry_run': _DRY, **_FEE},
            'required': ['to', 'amount']},
        'handler': _t_transfer,
    },
    'pq_account': {
        'description': 'One account: balance, nonce, whether the chain has seen '
                       'its public key yet, every key it owns, the bytes it '
                       'occupies and what that costs per day.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('a pq… address'), 'wallet': _WALLET,
            'limit': _int('how many owned keys to list')}},
        'handler': _t_account,
    },
    'pq_wallet': {
        'description': 'The local keystore: list, create, show, use or remove a '
                       'wallet. Keys are ML-DSA; what is stored is the 32-byte '
                       'seed, mode 0600, off the source tree.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('list, create, use, show or remove',
                           enum=['list', 'create', 'use', 'show', 'remove']),
            'name': _str('wallet name'),
            'seed': _str('32 bytes of hex for a deterministic wallet'),
            'overwrite': _bool('replace an existing wallet of that name')}},
        'handler': _t_wallet,
    },
    'pq_faucet': {
        'description': 'Fund a wallet from the genesis treasury. A real signed '
                       'transfer, not a mint — this deployment is a single '
                       'proposer devnet and the treasury is its genesis '
                       'allocation.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('who to fund; defaults to the default wallet'),
            'wallet': _str('local wallet name to fund'),
            'amount': _str('how much, as PQ (default 100)'),
            'mine': _MINE}},
        'handler': _t_faucet,
    },
    'pq_prove': {
        'description': 'A Merkle path from one key to the state root, with the '
                       'leaf and the root. This is what a light client needs: '
                       'given only the root from a block header, the path '
                       'proves this key commits to this exact hash. Verify it '
                       'anywhere with SHA3-256.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key to prove')}, 'required': ['key']},
        'handler': _t_prove,
    },
    'pq_check': {
        'description': 'Does this data match what a key committed to? Hashes '
                       'what you pass with SHA3-256 and compares it to the '
                       'stored value. The chain never saw the data — this is '
                       'how a hash value gets used.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key holding the commitment'),
            'data': _str('the data to test'),
            'hash': _str('or a digest you computed yourself')},
            'required': ['key']},
        'handler': _t_check,
    },
    'pq_block': {
        'description': 'One block by height or hash, or the tip. Headers carry '
                       'the state root, the base fee that applied, gas used and '
                       'the bytes of state the block added.',
        'inputSchema': {'type': 'object', 'properties': {
            'block': _str('a height, a block hash, or "head"'),
            'full': _bool('include whole transactions rather than hashes')}},
        'handler': _t_block,
    },
    'pq_tx': {
        'description': 'One transaction by hash: its body, its witness, the '
                       'receipt it produced and the block it landed in — or '
                       'that it is still pending, or why it was dropped.',
        'inputSchema': {'type': 'object', 'properties': {
            'hash': _str('the transaction hash')}, 'required': ['hash']},
        'handler': _t_tx,
    },
    'pq_history': {
        'description': 'What has happened, newest first, filtered to an address '
                       'or a key.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': _str('only transactions touching this address'),
            'key': _str('only transactions touching this key'),
            'limit': _int('how many (default 25)')}},
        'handler': _t_history,
    },
    'pq_mempool': {
        'description': 'What is queued but not yet in a block, and what was '
                       'recently dropped and why.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_mempool,
    },
    'pq_mine': {
        'description': 'Produce a block now instead of waiting for the block '
                       'loop. force=true seals an empty one.',
        'inputSchema': {'type': 'object', 'properties': {
            'force': _bool('produce a block even with an empty mempool')}},
        'handler': _t_mine,
    },
    'pq_verify': {
        'description': 'Replay the chain from genesis and re-check everything: '
                       'parent links, header hashes, transaction roots, every '
                       'state root, and the proposer signature on every block. '
                       'signatures=true also re-verifies every transaction '
                       'witness, which is slow and is the real audit.',
        'inputSchema': {'type': 'object', 'properties': {
            'signatures': _bool('also re-verify every ML-DSA transaction '
                                'witness (slow)')}},
        'handler': _t_verify,
    },
}

WRITE_TOOLS = {'pq_set', 'pq_del', 'pq_fund', 'pq_sweep', 'pq_list', 'pq_buy',
               'pq_transfer', 'pq_wallet', 'pq_faucet', 'pq_mine'}


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer, so a route and an MCP
    tools/call cannot diverge."""
    tool = TOOLS.get(name)
    if not tool:
        raise StateError(f'no tool named {name!r} — {", ".join(TOOLS)}',
                         code='no_tool', status=404)
    args = dict(args or {})
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise StateError(f'{name} needs {required}', code='missing_arg')
    return tool['handler'](args)


# ── JSON-RPC ──────────────────────────────────────────────────────


def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code,
                                                   'message': message}}


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        return _result(id_, {
            'content': [{'type': 'text',
                         'text': json.dumps(out, default=str, indent=2)}],
            'structuredContent': out if isinstance(out, dict) else None,
            'isError': False})
    except StateError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict())}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'bad arguments for {name}: {e}'}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600,
                      'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'postquant', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def tool_list():
    return [{'name': n, 'description': t['description'],
             'inputSchema': t['inputSchema']} for n, t in TOOLS.items()]


def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50760)))
    else:
        serve_stdio()
