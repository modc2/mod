#!/usr/bin/env python3
"""postquant mcp — the chain as tools.

Twenty-four tools over one state machine. The order below is the order to use
them in: pq_quote before pq_set, because on this chain a write has a price and
the price moves; pq_get and pq_prove after, because a value that is a hash is
only worth what a proof against the state root says it is worth.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 51030 # Streamable HTTP — POST /mcp

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
import consensus as CN                                         # noqa: E402
import keys as K                                               # noqa: E402
import state as S                                              # noqa: E402
from pq import algos as ALGOS                                  # noqa: E402
from pq import pqscore as PQSCORE                              # noqa: E402
from state import StateError, to_pq                            # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'A post-quantum L1 whose entire state machine is a market in key/value '
    'space. Every account picks its key type at creation — ML-DSA (FIPS 204, '
    'lattice) or SLH-DSA (FIPS 205, hash-based), two post-quantum families '
    'chosen to share no assumption, or any key type a plugin registered, '
    'classical curves included — every commitment is SHA3-256 regardless. '
    'The gate is priced, not prohibited: pq_algos lists the key types and '
    'what breaking each costs (brute force assuming a maximal-entropy seed; '
    'polynomial for anything Shor-class), and pq_complexity shows the full '
    'measurement, including the entropy probe that admits or refuses a '
    'plugin scheme. '
    'A key maps to a value, the value is bytes and is usually a 32-byte hash of '
    'something stored elsewhere, and holding that pair costs money continuously: '
    'write gas per byte of KEY and per byte of VALUE (a key byte costs 4x a '
    'value byte, and a value declared kind=hash is cheaper still), witness gas '
    'per byte of signature (an ML-DSA-44 signature is 2420 bytes, an SLH-DSA '
    'one 17088, and the chain bills the difference honestly), and rent per '
    'byte per hour against a prepaid escrow. When '
    'the escrow runs out the entry expires and anyone can pq_sweep it for the '
    'bond the writer put up. '
    'ALWAYS call pq_quote before pq_set: it returns the exact split of write '
    'gas, witness gas and rent deposit at the current base fee, which floats '
    'with how fast the state is growing. Pass data= to pq_set to store the '
    'SHA3-256 of some text and keep the text off-chain — that is the pattern '
    'this chain is priced for; value= with value_kind=raw stores literal bytes '
    'and costs more. A LOCAL caller (shell, stdio MCP, loopback) can pass '
    'file= instead: the node streams that file on its own disk through '
    'SHA3-256 and commits the digest, anchoring a filesystem artifact without '
    'the bytes ever leaving the disk; the public HTTP surface refuses file= '
    'because it reads this node\'s disk. pq_get reads a key, pq_prove returns a Merkle path from it '
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

# file= hashes a file on THIS node's disk, and that must never be offered to
# the open HTTP surface: a public caller who can ask "what is the SHA3-256 of
# /path/i/guessed" has an existence-and-content oracle on the operator's box.
# api.py marks each request's thread remote unless it arrived on loopback with
# no forwarding header; stdio, the shell, tests and direct imports never mark
# anything and stay local, because those callers already own the disk.
_CALLER = threading.local()


def set_remote(flag, owner=None):
    """api.py calls this at the top of every HTTP dispatch. owner = the
    caller proved the server.secret bearer (only meaningful when one exists);
    a local caller is always the owner."""
    _CALLER.remote = bool(flag)
    _CALLER.owner = (not flag) if owner is None else bool(owner)


def _is_owner():
    return getattr(_CALLER, 'owner', True)


def _file_digest(path):
    """Stream a local file through SHA3-256. Local callers only."""
    if getattr(_CALLER, 'remote', False):
        raise StateError(
            'file= reads this node\'s own disk and is local-only (shell, '
            'stdio MCP, loopback) — hash the file where it lives and pass '
            'hash= or value= instead', code='local_only', status=403)
    p = os.path.realpath(os.path.expanduser(str(path)))
    h = hashlib.sha3_256()
    size = 0
    try:
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
                size += len(chunk)
    except OSError as e:
        raise StateError(f'cannot read {p}: {e.strerror or e}',
                         code='bad_file', status=400)
    return {'path': p, 'sha3_256': h.hexdigest(), 'bytes': size}


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
    """The rule is by JSON type: a string is PQ, the display unit — "1.5" and
    "25" both mean PQ — and a bare integer is nq, the base unit. Ambiguity
    here would be a nine-order-of-magnitude mistake, so nothing is guessed
    from magnitude."""
    if value is None:
        return None
    if isinstance(value, str):
        whole, _, frac = value.strip().partition('.')
        frac = (frac + '0' * S.DECIMALS)[:S.DECIMALS]
        return int(whole or 0) * S.PQ + int(frac or 0)
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
    tip = _amount(args.get('tip')) or 0
    max_fee = args.get('max_fee')
    if args.get('dry_run'):
        return {'dry_run': True, 'kind': kind, 'from': wallet['address'],
                'fields': fields, 'base_fee': _money(n.state.base_fee),
                'note': 'nothing was signed or submitted'}
    tx = n.make_tx(wallet, kind, max_fee=_amount(max_fee), tip=tip,
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
            result['block'] = found.get('block')
        except StateError:
            result['status'] = 'dropped'
    else:
        result['status'] = 'pending'
    return result


def _receipt_view(r):
    if not r:
        return None
    out = dict(r)
    for field in ('fee', 'refund', 'bounty', 'amount', 'price', 'deposit', 'escrow'):
        if field in out and isinstance(out[field], int):
            out[field] = _money(out[field])
    return out


# ── read tools ────────────────────────────────────────────────────


def _t_head(a):
    n = node()
    h = n.head()
    try:
        from pq import wasmvm as _wvm
        wasm_ok = bool(_wvm.status()['engine']['ok'])
    except Exception:  # noqa: BLE001
        wasm_ok = False
    return {**h, 'burned': _money(h['burned']), 'supply': _money(h['supply']),
            'base_fee': _money(h['base_fee']),
            'scheme': K.SCHEME, 'hash': h['hash'],
            'schemes': [name for name in ALGOS.names() if ALGOS.allowed(name)],
            'wasm': wasm_ok,
            'signatures': ', '.join(n for n in ALGOS.names()
                                    if ALGOS.allowed(n)) +
                          ' — per account, chosen at key creation (pq_algos)',
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
    _, a = _file_args(a)
    kind = a.get('value_kind') or ('raw' if a.get('value') and not a.get('data')
                                   else 'hash')
    value = _value_for(a, kind)
    seconds = _seconds(a)
    addr = a.get('address')
    w = None
    if not addr:
        w = K.get(a.get('wallet'), required=False)
        addr = w['address'] if w else None
    acct = n.state.accounts.get(addr, {}) if addr else {}
    new_account = bool(addr) and addr not in n.state.accounts
    # Witness gas is per byte and key types differ by an order of magnitude,
    # so the quote prices the scheme that will actually sign: asked for,
    # the wallet's, whatever the chain already recorded, or the default.
    scheme = (a.get('scheme') or (w or {}).get('scheme')
              or acct.get('scheme') or K.SCHEME)
    algo = ALGOS.maybe(scheme)
    if algo is None:
        raise StateError(f'unknown scheme {scheme!r} — pq_algos lists what '
                         'this node knows', code='bad_scheme')
    known = acct.get('pk') is not None
    wb = algo.sizes['sig'] + (0 if known else algo.sizes['pk'])
    q = n.state.quote(key, value, kind, seconds, witness_bytes=wb,
                      new_account=new_account)
    existing = n.state.entry(key, int(time.time()))
    effective_deposit = (0 if (existing and not existing['expired'])
                         else q['rent']['deposit'])
    effective_total = q['total'] - q['rent']['deposit'] + effective_deposit
    return {
        **q,
        'witness': {'scheme': scheme, 'sig_bytes': algo.sizes['sig'],
                    'pk_bytes': 0 if known else algo.sizes['pk'],
                    'pk_note': 'the chain already holds this key' if known
                               else 'a first transaction carries its public '
                                    'key inline and pays for the bytes'},
        'write_cost': _money(q['write_cost']),
        'deposit': _money(effective_deposit),
        'total': _money(effective_total),
        'base_fee': _money(q['base_fee']),
        'rent': {**q['rent'], 'deposit': _money(q['rent']['deposit']),
                 'lease': _money(q['rent']['lease']),
                 'bounty': _money(q['rent']['bounty']),
                 'per_day': _money(q['rent']['per_day'])},
        'value': value,
        **(({'deposit_note': 'key already leased — no additional deposit '
             'charged on re-write (use pq_fund to extend the lease)'}
            ) if effective_deposit == 0 else {}),
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
    items = n.history(a.get('address'), a.get('key'),
                      int(a.get('limit') or 25),
                      since=int(a.get('since') or 0))
    for item in items:
        if item.get('receipt'):
            item['receipt'] = _receipt_view(item['receipt'])
    return {'history': items}


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
    finfo = _file_digest(a['file']) if a.get('file') else None
    data = a.get('data')
    digest = a.get('hash') or (finfo['sha3_256'] if finfo else (
        hashlib.sha3_256(data.encode() if isinstance(data, str) else data)
        .hexdigest() if data is not None else None))
    if digest is None:
        raise StateError('pass file= (a local path, hashed here), data= '
                         '(which is hashed) or hash= (a digest)',
                         code='bad_args')
    return {'key': key, 'committed': e['value'], 'computed': digest.lower(),
            'matches': e['value'] == digest.lower(),
            'value_kind': e['value_kind'],
            'expired': e['expired'],
            **({'file': {'path': finfo['path'], 'bytes': finfo['bytes']}}
               if finfo else {}),
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


def _file_args(a):
    """file= resolves to a hash commitment before the normal shaping runs.
    Returns (finfo, args) — args rewritten so _value_for never sees the path."""
    if not a.get('file'):
        return None, a
    if a.get('data') is not None or a.get('value'):
        raise StateError('pass one of file=, data= or value= — not two',
                         code='bad_args')
    if a.get('value_kind') == 'raw':
        raise StateError('file= always commits the SHA3-256 digest — raw '
                         'would put the file itself on-chain, which the 8KB '
                         'value cap and the per-byte price both exist to '
                         'prevent', code='bad_value')
    finfo = _file_digest(a['file'])
    return finfo, {**a, 'value': finfo['sha3_256'], 'value_kind': 'hash'}


def _t_set(a):
    n = node()
    finfo, a = _file_args(a)
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
    if finfo:
        out['committed'] = {'value': value,
                            'of': f'sha3-256 of {finfo["path"]}',
                            'file_bytes': finfo['bytes'],
                            'stored_on_chain': '32 bytes, not the file'}
    elif a.get('data') is not None:
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


def _t_algos(a):
    """Every key type this node can witness, and the gate that decides it."""
    out = ALGOS.catalog()
    counts = {}
    for acct in node().state.accounts.values():
        if acct.get('scheme'):
            counts[acct['scheme']] = counts.get(acct['scheme'], 0) + 1
    for algo in out['algorithms']:
        algo['accounts_on_chain'] = counts.get(algo['name'], 0)
    out['default'] = K.SCHEME
    try:                       # the wasm enforcement layer, when it is present
        from pq import wasmvm
        out['wasm'] = wasmvm.status()
    except ImportError:
        pass
    return out


def _t_complexity(a):
    """What breaking a key type costs — the analytic bound under maximal
    entropy, the empirical probe of that assumption, and the gate verdict."""
    from pq import complexity as CPLX
    scheme = a.get('scheme')
    if scheme:
        algo = ALGOS.maybe(scheme)
        if algo is None:
            raise StateError(f'unknown scheme {scheme!r} — pq_algos lists '
                             'what this node knows', code='unknown_scheme')
        probe = a.get('probe')
        return CPLX.profile(algo, run_probe=True if probe is None else probe)
    # The all-schemes scan probes only when asked: probing means running
    # each scheme's own keygen/sign, and a full SLH-DSA sign is seconds of
    # pure python. Analytic bounds and cached probes are always included.
    probe_all = bool(a.get('probe'))
    return {
        'assumes': 'maximal entropy — every bound below is against a '
                   'uniformly random seed; the probe is what tests that '
                   'assumption per scheme',
        'floor': f'2^{CPLX.MIN_BITS} best-known classical work, or the '
                 'witness is refused (insufficient_complexity)',
        'algorithms': [CPLX.profile(ALGOS.get(n), run_probe=probe_all)
                       for n in ALGOS.names()],
    }


def _privatize(name, a):
    """Forge-and-hide in one step: private=true on pq_keytype create opens a
    private wallet whose hidden scheme is the key type just forged. The
    composite still exists publicly (that is what the gate and the replay
    audit judge); what never appears on chain is WHICH key type this
    account uses, or its public key. A failure here does not unwind the
    forge — the key type is good, only the wallet is reported missing."""
    from pq import private as PRIV
    wname = a.get('wallet') or ('ghost-' + ''.join(
        c for c in name.lower() if c.isalnum())[:20])
    try:
        w = K.create(wname, scheme=PRIV.NAME, inner=name,
                     overwrite=bool(a.get('overwrite')))
    except Exception as e:                                   # noqa: BLE001
        return {'private_error': str(e)}
    return {'private': {'wallet': w['name'], 'address': w['address'],
                        'commitment': w['pk'], 'hidden': name,
                        'envelope_bytes': PRIV.ENVELOPE,
                        'anonymity_set': len(PRIV.cloak_set()),
                        'note': 'on chain this account is a 32-byte '
                                'commitment and fixed-size envelopes — '
                                'nobody but the proposer learns it signs '
                                f'with {name}'}}


def _t_cairo(a):
    """The Cairo verifier: info, or check = sign with a throwaway CAIRO-MSS
    key and run the witness through lib.cairo in the Cairo VM,
    reporting whether the VM and the chain's Python verifier agree."""
    from pq import cairomss as CM
    action = (a.get('action') or 'info').lower()
    info = {'scheme': CM.NAME, 'cairo': CM.cairo_status(),
            'params': {'n': CM.N, 'w': CM.W, 'chains': CM.LEN,
                       'tree_height': CM.H, 'layers': CM.D,
                       'leaves': 1 << (CM.H * CM.D)},
            'pk_bytes': CM.PK_BYTES, 'sig_bytes': CM.SIG_BYTES,
            'accounts_on_chain': _scheme_accounts(CM.NAME),
            'why': [
                'rests on SHA-256 alone — independent of the lattice under '
                'ML-DSA and the SHAKE under SLH-DSA, so an ALL composite '
                'with it makes a forger break one more unrelated thing',
                'its verifier is a Cairo program — what STARKs (hash-based '
                'proofs, no curve for Shor) prove; the prover is the '
                'upgrade path, this node ships the verifier + parity check',
                'few-time: ~q^2/2^25 odds of one-time-key reuse after q '
                'signatures — a layer, not a solo wallet for heavy use']}
    if action == 'info':
        return info
    if action not in ('check', 'verify'):
        raise StateError(f'unknown action {action!r} — info | check',
                         code='bad_args')
    msg = str(a.get('message') or 'hello from cairo')[:1000].encode()
    ctx = b'postquant/cairo/v1'
    # Always a throwaway key: signing on demand with a real wallet would let
    # any caller spend that wallet's few-time budget (leaf reuse odds grow
    # with every signature, whatever the context).
    pk, sk = CM.keygen(hashlib.sha3_256(b'pq_cairo/demo' + msg).digest())
    who = 'throwaway key (seed = SHA3 of the message)'
    sig = CM.sign(sk, msg, ctx)
    if a.get('tamper'):
        sig = bytes([sig[0], sig[1] ^ 1]) + sig[2:]
    return {**{k: info[k] for k in ('scheme', 'params')}, 'signer': who,
            'message': msg.decode(), 'tampered': bool(a.get('tamper')),
            'vm': CM.cairo_verify(pk, msg, sig, ctx)}


def _t_keytype(a):
    """The key type builder's write surface: create / delete / show / list
    key types built on this node — composites of registered schemes, and
    user-supplied wasm blobs (the bring-your-own door). The DAG and the
    proof battery are their own read tools (pq_keytype_dag,
    pq_keytype_test)."""
    from pq import complexity as CPLX
    from pq import compose as CMP
    from pq import wasmtype as WT
    action = (a.get('action') or 'list').lower()
    if action in ('list', 'ls'):
        rows = []
        for kind, spec in ([('composite', s) for s in CMP.specs()]
                           + [('wasm', s) for s in WT.specs()]):
            algo = ALGOS.maybe(spec['name'])
            rows.append({**spec, 'kind': kind, 'registered': algo is not None,
                         'accepted': ALGOS.allowed(spec['name']),
                         **({'security': CPLX.summary(algo)} if algo else {})})
        rows.sort(key=lambda r: r.get('created', 0))
        return {'keytypes': rows, 'count': len(rows), 'ops': list(CMP.OPS),
                'store': CMP.STORE_FILE, 'wasm_store': WT.STORE_FILE,
                'errors': CMP.STORE_ERRORS + WT.STORE_ERRORS}
    name = a.get('name')
    if not name:
        raise StateError('this action needs a name', code='bad_args')
    if action in ('show', 'get'):
        algo = ALGOS.maybe(name)
        if algo is None:
            raise StateError(f'unknown key type {name!r} — pq_algos lists '
                             'what this node knows', code='unknown_scheme',
                             status=404)
        return {**algo.describe(), 'accepted': ALGOS.allowed(name),
                'composite': getattr(algo, 'composite', None),
                'wasmtype': getattr(algo, 'wasmtype', None),
                'dependents': CMP.dependents(name),
                'accounts_on_chain': _scheme_accounts(name)}
    if action == 'create':
        if a.get('wasm'):
            return _create_wasm_keytype(name, a)
        parts = a.get('parts')
        if isinstance(parts, str):
            parts = [p.strip() for p in parts.replace('+', ',').split(',')
                     if p.strip()]
        try:
            author = _author(a)
            algo = CMP.create(name, (a.get('op') or 'all').lower(),
                              parts or [], note=a.get('note'), k=a.get('k'),
                              author=author and author['address'])
        except CMP.ComposeError as e:
            raise StateError(str(e), code='bad_keytype')
        r = ALGOS.refusal(name)
        spec = next((x for x in CMP.specs() if x['name'] == name), {})
        return {'created': name, **algo.describe(),
                'composite': algo.composite, 'author': algo.author,
                'accepted': r is None, **({'refused': r[1]} if r else {}),
                **_maybe_publish(a, name, spec, author),
                **(_privatize(name, a) if a.get('private') and r is None
                   else {}),
                'next': f'pq_keytype_test scheme={name} runs its proofs; '
                        f'pq_wallet action=create scheme={name} uses it'}
    if action in ('delete', 'rm'):
        used_by = _scheme_accounts(name)
        if used_by:
            raise StateError(
                f'{name} witnesses for {used_by} on-chain account(s) — '
                'deleting it would leave history this node could no longer '
                're-judge (pq_verify replays every witness)',
                code='keytype_in_use', status=409)
        algo = ALGOS.maybe(name)
        try:
            if algo is not None and getattr(algo, 'wasmtype', None):
                out = WT.delete(name)
            else:
                out = CMP.delete(name)
        except (CMP.ComposeError, WT.WasmTypeError) as e:
            raise StateError(str(e), code='bad_keytype')
        return {**out, **_unanchor(name)}
    raise StateError(f'unknown action {action!r} — '
                     'create | delete | show | list', code='bad_args')


def _create_wasm_keytype(name, a):
    """The bring-your-own door: one importless wasm blob exporting the pq2
    ABI, admitted only if the proof battery, the determinism double-run and
    the complexity gate all pass — refused otherwise, with the measurement."""
    import base64
    from pq import wasmtype as WT
    try:
        blob = base64.b64decode(a['wasm'], validate=True)
    except Exception:                                       # noqa: BLE001
        raise StateError('wasm must be the blob as standard base64',
                         code='bad_args')
    author = _author(a)
    try:
        algo, card = WT.create(
            name, blob,
            sizes={'pk': a.get('pk_bytes'), 'sig': a.get('sig_bytes'),
                   'sk': a.get('sk_bytes'), 'seed': a.get('seed_bytes', 32)},
            family=a.get('family') or 'wasm',
            basis=a.get('basis') or '',
            quantum_safe=bool(a.get('quantum_safe')),
            classical_bits=a.get('classical_bits'),
            quantum_bits=a.get('quantum_bits'),
            note=a.get('note'), author=author and author['address'])
    except WT.WasmTypeError as e:
        raise StateError(str(e), code='bad_keytype', status=403)
    spec = next((x for x in WT.specs() if x['name'] == name), {})
    return {'created': name, **algo.describe(),
            'wasmtype': algo.wasmtype, 'accepted': True,
            'author': algo.author, **_maybe_publish(a, name, spec, author),
            'admission': {'ok': card['ok'],
                          'determinism': card['determinism'],
                          'budgets': card['budgets'],
                          'proofs_passed': card['battery']['passed']},
            'next': f'pq_wallet action=create scheme={name} uses it; '
                    f'pq_keytype_test scheme={name} re-runs its proofs'}


def _scheme_accounts(name):
    return sum(1 for acct in node().state.accounts.values()
               if acct.get('scheme') == name)


def _t_keytype_test(a):
    """Run the proof battery against one key type, composite or not."""
    from pq import compose as CMP
    scheme = a.get('scheme') or a.get('name')
    algo = ALGOS.maybe(scheme)
    if algo is None:
        raise StateError(f'unknown key type {scheme!r} — pq_algos lists '
                         'what this node knows', code='unknown_scheme',
                         status=404)
    return CMP.proofs(algo)


def _t_keytype_dag(a):
    """The registry as a graph, for mixing and matching."""
    from pq import compose as CMP
    out = CMP.dag()
    for node_ in out['nodes']:
        node_['accounts_on_chain'] = _scheme_accounts(node_['name'])
    return out


def _t_wallet(a):
    action = (a.get('action') or 'list').lower()
    if action in ('list', 'ls'):
        out = K.wallets()
        n = node()
        full = {x['name']: x for x in K._load()['wallets'].values()}
        for w in out['wallets']:
            w['balance'] = _money(n.state.balance(w['address']))
            w['nonce'] = n.state.accounts.get(w['address'], {}).get('nonce', 0)
            w['pq_score'] = PQSCORE.score_wallet(
                full.get(w['name'], w), pk_exposed=w['nonce'] > 0,
                owner=_is_owner())
        return out
    if action == 'create':
        # keys.create runs the complexity gate itself and its refusal names the
        # escape hatch; nothing to pre-check here.
        w = K.create(a.get('name') or 'default', seed=a.get('seed'),
                     scheme=a.get('scheme'), overwrite=bool(a.get('overwrite')),
                     inner=a.get('inner'))
        sz = ALGOS.get(w['scheme']).sizes
        return {**w, 'sig_bytes': sz['sig'],
                'note': 'the seed stays in the keystore and never leaves '
                        'this process; fund it with pq_faucet. Witness gas '
                        f'will bill {sz["sig"]} bytes per signature.'}
    if action in ('use', 'default'):
        return K.use(a['name'])
    if action in ('remove', 'delete'):
        return K.remove(a['name'])
    if action in ('show', 'get'):
        w = K.get(a.get('name'))
        nonce = node().state.accounts.get(w['address'], {}).get('nonce', 0)
        return {**K._public(w), 'pq_score': PQSCORE.score_wallet(
            w, pk_exposed=nonce > 0, owner=_is_owner())}
    if action == 'export':
        return _export_wallet(a.get('name'))
    raise StateError(f'unknown wallet action {action!r} — list, create, use, '
                     'show, export or remove', code='bad_action')


def _export_wallet(name):
    """Hand the owner their private key: the 32-byte seed (plus the hidden
    inner key type for a private wallet) is the whole key — every scheme's
    keygen is deterministic from it, so these fields restore the wallet
    anywhere with pq_wallet action=create. Owner only: local callers, or a
    remote caller holding the server.secret bearer. With no server.secret the
    write gate is open to the internet, so remote export is refused outright
    — an open node must never hand its keys to whoever asks."""
    if not _is_owner():
        raise StateError(
            'exporting a private key is owner-only: open the console on this '
            'node (http://localhost:51030/postquant), or create '
            '~/.mod/postquant/server.secret and send it as the bearer',
            code='owner_only', status=403)
    w = K.get(name)
    restore = {'action': 'create', 'name': w['name'], 'scheme': w['scheme'],
               'seed': w['seed']}
    if w.get('inner'):
        restore['inner'] = w['inner']
    out = {'name': w['name'], 'address': w['address'], 'scheme': w['scheme'],
           'seed': w['seed'], 'restore': restore,
           'warning': 'this seed IS the private key — anyone holding it can '
                      'sign as this address. Store it offline; never paste '
                      'it into a chat or a website.'}
    if w.get('inner'):
        out['inner'] = w['inner']
    return out


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


# ── prims, the key type market, private key types ─────────────────

ANCHOR_PREFIX = 'keytype/'


def _author(a):
    """The wallet credited as a key type's author, if one was named."""
    name = a.get('author')
    return K.get(name) if name else None


def _spec_hash(spec):
    """What a key type's on-chain anchor commits to: the spec that defines
    it (for a wasm prim the spec pins the blob's SHA3, so the bytes are in
    it too), canonical JSON, minus the wall-clock fields."""
    body = {k: v for k, v in spec.items() if k not in ('created', 'author')}
    return hashlib.sha3_256(json.dumps(body, sort_keys=True,
                                       separators=(',', ':')).encode()) \
        .hexdigest()


def _maybe_publish(a, name, spec, author):
    """Anchor a key type on chain: the author's wallet writes
    keytype/<name> = SHA3(spec). Authorship then IS a key in the store —
    provable, transferable, and listable on the existing key market."""
    if not (a.get('publish') and author and spec):
        return {}
    try:
        out = _t_set({'wallet': author['name'], 'key': ANCHOR_PREFIX + name,
                      'value': _spec_hash(spec), 'value_kind': 'hash',
                      'days': a.get('days') or 30})
        return {'anchor': {'key': ANCHOR_PREFIX + name,
                           'value': _spec_hash(spec), 'tx': out.get('hash'),
                           'status': out.get('status'),
                           'owner': author['address']}}
    except StateError as e:
        return {'anchor_error': f'key type created, not anchored: {e}'}


def _unanchor(name):
    """A deleted key type's authorship key goes with it — when this node
    holds the owning wallet. A key someone else bought stays theirs."""
    e = node().state.entry(ANCHOR_PREFIX + name, int(time.time()))
    if not e or e.get('expired'):
        return {}
    owner = K.get(e['owner'], required=False)
    if owner is None:
        return {'anchor_kept': f'{e["key"]} is owned by {e["owner"]}, not a '
                               'wallet on this node — it stays on chain'}
    try:
        out = _t_del({'wallet': owner['name'], 'key': e['key']})
        return {'unanchored': {'key': e['key'], 'tx': out.get('hash'),
                               'status': out.get('status')}}
    except StateError as err:
        return {'anchor_kept': f'{e["key"]} could not be deleted: {err}'}


def _anchor_view(name, now):
    e = node().state.entry(ANCHOR_PREFIX + name, now)
    if not e or e.get('expired'):
        return None
    return {'key': e['key'], 'owner': e['owner'], 'value': e['value'],
            'listed_price': _money(e['price']) if e['listed'] and e['price']
            else None, 'expires_in': e.get('expires_in')}


def _prim_kind(algo):
    if getattr(algo, 'wasmtype', None):
        return 'wasm'
    return 'builtin' if algo.origin == 'builtin' else 'plugin'


def _is_prim(algo):
    return (getattr(algo, 'composite', None) is None
            and not getattr(algo, 'private', False))


def _uses():
    counts = {}
    for acct in node().state.accounts.values():
        if acct.get('scheme'):
            counts[acct['scheme']] = counts.get(acct['scheme'], 0) + 1
    return counts


def _prim_view(algo, uses):
    from pq import compose as CMP
    r = ALGOS.refusal(algo.name)
    d = algo.describe()
    return {**d, 'kind': _prim_kind(algo), 'accepted': r is None,
            **({'refused': r[1]} if r else {}),
            'author': getattr(algo, 'author', None),
            'blob_sha3': (getattr(algo, 'wasmtype', None) or {})
            .get('sha3_256'),
            'used_by': CMP.dependents(algo.name),
            'accounts_on_chain': uses.get(algo.name, 0)}


def _t_prims(a):
    """The atoms: every key type that is not built out of others."""
    uses = _uses()
    if a.get('name'):
        algo = ALGOS.maybe(a['name'])
        if algo is None or not _is_prim(algo):
            raise StateError(f'no prim {a["name"]!r} — pq_prims lists them',
                             code='unknown_scheme', status=404)
        return {**_prim_view(algo, uses),
                'anchor': _anchor_view(algo.name, int(time.time()))}
    rows = [_prim_view(x, uses) for _, x in sorted(ALGOS.REGISTRY.items())
            if _is_prim(x)]
    kinds = {}
    for r in rows:
        kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    return {'prims': rows, 'count': len(rows), 'kinds': kinds,
            'how_to_add': 'pq_prim action=add wasm=<base64> — one importless '
                          'wasm blob exporting the pq2 ABI (keygen/sign/'
                          'verify); admitted only if the proof battery, the '
                          'determinism double-run and the complexity gate '
                          'pass. src/pq/wasm-src/examples/lamport.rs is the '
                          'worked example.'}


def _t_prim(a):
    """Add, show or delete a wasm prim."""
    action = (a.get('action') or 'show').lower()
    name = a.get('name')
    if not name:
        raise StateError('this action needs a name', code='bad_args')
    if action in ('show', 'get'):
        return _t_prims({'name': name})
    if action in ('add', 'create'):
        if not a.get('wasm'):
            raise StateError('a prim is one wasm blob: pass wasm=<base64>',
                             code='bad_args')
        out = _create_wasm_keytype(name, a)
        out['kind'] = 'wasm'
        return out
    if action in ('delete', 'rm'):
        algo = ALGOS.maybe(name)
        if algo is None or not getattr(algo, 'wasmtype', None):
            raise StateError(f'{name} is not a wasm prim registered here — '
                             'builtins and plugins are files',
                             code='bad_keytype')
        return _t_keytype({'action': 'delete', 'name': name})
    raise StateError(f'unknown action {action!r} — add | show | delete',
                     code='bad_args')


_BADGE_ORDER = ('legendary', 'epic', 'rare', 'common')


def _market_item(algo, uses, deps, now):
    from pq import complexity as CPLX
    comp = getattr(algo, 'composite', None)
    priv = getattr(algo, 'private', False)
    s = CPLX.summary(algo)
    r = ALGOS.refusal(algo.name)
    wb = (algo.sizes.get('pk') or 0) + (algo.sizes.get('sig') or 0)
    kind = 'private' if priv else ('keytype' if comp else 'prim')
    sub = 'private' if priv else ('composite' if comp else _prim_kind(algo))
    n_uses, n_forks = uses.get(algo.name, 0), len(deps.get(algo.name, ()))
    cbits = s.get('classical_bits')
    badges = []
    if algo.quantum_safe:
        badges.append('quantum-safe')
    if comp and comp['op'] == 'all' and len(
            {ALGOS.get(p).family for p in comp['parts']}) > 1:
        badges.append('cross-family')
    if comp and comp['op'] == 'threshold':
        badges.append(f'{comp["k"]}-of-{len(comp["parts"])}')
    if priv:
        badges.append('cloaked')
    if sub == 'wasm':
        badges.append('wasm')
    if wb <= 4096:
        badges.append('lean')
    # Rarity: what it would take to break, and how far it is from the
    # stock shelf — a cross-family threshold beats a single lattice set.
    score = (2 if algo.quantum_safe else 0) + (1 if cbits is None or cbits >= 192 else 0) \
        + (2 if 'cross-family' in badges or comp and comp['op'] ==
           'threshold' else 0) + (2 if priv else 0) + (1 if sub == 'wasm'
                                                       else 0)
    rarity = _BADGE_ORDER[0 if score >= 5 else 1 if score >= 4 else
                          2 if score >= 2 else 3]
    badges.insert(0, rarity)
    return {
        'name': algo.name, 'kind': kind, 'sub': sub,
        **({'op': comp['op'], 'parts': comp['parts']} if comp else {}),
        **({'k': comp['k']} if comp and 'k' in comp else {}),
        'author': getattr(algo, 'author', None),
        'anchor': _anchor_view(algo.name, now),
        'uses': n_uses, 'forks': n_forks, 'depth': _depth_of(algo),
        'family': algo.family,
        'pk_bytes': algo.sizes.get('pk'), 'sig_bytes': algo.sizes.get('sig'),
        'witness_bytes': wb, 'witness_gas_nq': wb * S.GAS_WITNESS_BYTE,
        'classical_bits': cbits, 'quantum': s.get('quantum'),
        'quantum_safe': algo.quantum_safe, 'accepted': r is None,
        **({'refused': r[1]} if r else {}),
        'created': getattr(algo, 'created', None),
        'heat': round(n_uses * 3 + n_forks * 5 + (2 if priv else 0)
                      + (1 if r is None else -5), 2),
        'rarity': rarity, 'badges': badges, 'note': algo.note,
    }


def _depth_of(algo, seen=()):
    comp = getattr(algo, 'composite', None)
    if not comp or algo.name in seen:
        return 0
    return 1 + max(_depth_of(ALGOS.get(p), seen + (algo.name,))
                   for p in comp['parts'])


_SORTS = {
    'hot': lambda x: (-x['heat'], x['name']),
    'new': lambda x: (-(x['created'] or 0), x['name']),
    'safe': lambda x: (not x['quantum_safe'], -(x['classical_bits'] or 999),
                       x['name']),
    'cheap': lambda x: (x['witness_bytes'], x['name']),
    'deep': lambda x: (-x['depth'], -x['forks'], x['name']),
    'used': lambda x: (-x['uses'], x['name']),
}


def _t_keymarket(a):
    """The key type market: every prim, composite and the private cloak on
    one shelf, with who built it, who uses it and what breaking it costs."""
    from pq import private as PRIV
    now = int(time.time())
    uses = _uses()
    deps = {}
    for nm, x in ALGOS.REGISTRY.items():
        for p in (getattr(x, 'composite', None) or {}).get('parts', ()):
            deps.setdefault(p, []).append(nm)
    items = [_market_item(x, uses, deps, now)
             for _, x in sorted(ALGOS.REGISTRY.items())]
    kind = (a.get('kind') or 'all').lower()
    if kind != 'all':
        items = [i for i in items if i['kind'] == kind]
    q = (a.get('q') or '').lower().strip()
    if q:
        items = [i for i in items if q in json.dumps(
            [i['name'], i['family'], i['sub'], i.get('parts'), i['badges'],
             i['note']]).lower()]
    sort = (a.get('sort') or 'hot').lower()
    items.sort(key=_SORTS.get(sort, _SORTS['hot']))
    allitems = [_market_item(x, uses, deps, now)
                for x in ALGOS.REGISTRY.values()]
    return {
        'items': items, 'count': len(items), 'sort': sort,
        'sorts': list(_SORTS),
        'stats': {
            'prims': sum(1 for i in allitems if i['kind'] == 'prim'),
            'keytypes': sum(1 for i in allitems if i['kind'] == 'keytype'),
            'wasm': sum(1 for i in allitems if i['sub'] == 'wasm'),
            'composites': sum(1 for i in allitems if i['sub'] == 'composite'),
            'accounts': sum(uses.values()),
            'private_accounts': uses.get(PRIV.NAME, 0),
            'anonymity_set': len(PRIV.cloak_set()),
            'anchored': sum(1 for i in allitems if i['anchor']),
        },
        'authorship': 'publishing a key type (publish=true author=<wallet>) '
                      f'writes {ANCHOR_PREFIX}<name> = SHA3(spec) from the '
                      'author\'s wallet; that key is the authorship — list '
                      'it with pq_list, buy it with pq_buy',
    }


def _t_private(a):
    """Private key types: sign without ever revealing which key type."""
    import base64
    from pq import private as PRIV
    action = (a.get('action') or 'info').lower()
    n = node()
    if action == 'info':
        cloak = PRIV.cloak_set()
        return {
            'scheme': PRIV.NAME, 'seal': {k: v for k, v in
                                          PRIV.seal_info().items()
                                          if k != 'ek'} if not a.get('ek')
            else PRIV.seal_info(),
            'envelope_bytes': PRIV.ENVELOPE,
            'witness_gas_nq': (PRIV.ENVELOPE + 32) * S.GAS_WITNESS_BYTE,
            'cloak_set': cloak, 'anonymity_set': len(cloak),
            'private_accounts': _uses().get(PRIV.NAME, 0),
            'default_inner': PRIV.DEFAULT_INNER,
            'honesty': [
                'on chain: a 32-byte commitment and fixed-size envelopes — '
                'the key type and the public key never appear',
                'every envelope is the same size, so ML-DSA, SLH-DSA, '
                'ed25519 and wasm prims are indistinguishable',
                'envelopes are sealed with ML-KEM-768 to the proposer: '
                'harvest-now-decrypt-later does not open them',
                'the proposer can open them — it already orders and signs '
                'every block; everyone else trusts its attestation',
                'if the seal key leaks, each account falls back to its '
                'hidden scheme\'s own security card',
                'a zero-knowledge proof would remove the proposer from the '
                'trust path; that is the upgrade, not a claim made here',
            ],
        }
    if action == 'create':
        inner = a.get('inner') or PRIV.DEFAULT_INNER
        w = K.create(a.get('name') or 'ghost', seed=a.get('seed'),
                     scheme=PRIV.NAME, overwrite=bool(a.get('overwrite')),
                     inner=inner)
        return {**w, 'commitment': w['pk'], 'envelope_bytes': PRIV.ENVELOPE,
                'anonymity_set': len(PRIV.cloak_set()),
                'note': 'the address commits to SHA3(key type, salt, public '
                        'key); fund it with pq_faucet and use it in any '
                        'write — the chain only ever sees envelopes'}
    if action == 'prove':
        w = K.get(a.get('wallet'))
        if w['scheme'] != PRIV.NAME:
            raise StateError(f'{w["name"]} is a {w["scheme"]} wallet — '
                             'private proofs need pq_private action=create',
                             code='not_private')
        msg = str(a.get('message') or '').encode()
        env = PRIV.prove(K.secret_key(w), msg)
        return {'address': w['address'], 'commitment': w['pk'],
                'message_sha3': hashlib.sha3_256(msg).hexdigest(),
                'proof': env.hex(), 'proof_bytes': len(env),
                'scheme': PRIV.NAME, 'scheme_revealed': False}
    if action == 'verify':
        commit = a.get('commitment')
        addr = a.get('address')
        if not commit and addr:
            acct = n.state.accounts.get(addr) or {}
            commit = acct.get('pk')
            if not commit:
                w = K.get(addr, required=False)
                commit = w and w['scheme'] == PRIV.NAME and w['pk']
            if not commit:
                raise StateError(f'{addr} has no commitment on chain yet — '
                                 'pass commitment= instead',
                                 code='no_commitment', status=404)
        if not commit or not S.is_hex(commit, 32):
            raise StateError('pass address= or commitment= (32 bytes hex)',
                             code='bad_args')
        if addr and K.address(bytes.fromhex(commit), PRIV.NAME) != addr:
            raise StateError('that commitment does not hash to that address',
                             code='bad_commitment')
        proof = a.get('proof') or ''
        try:
            env = bytes.fromhex(proof)
        except ValueError:
            try:
                env = base64.b64decode(proof, validate=True)
            except Exception:                               # noqa: BLE001
                raise StateError('proof must be hex (or base64)',
                                 code='bad_args')
        msg = str(a.get('message') or '').encode()
        ok, att = PRIV.attest(n.validator, K, bytes.fromhex(commit), msg,
                              env, n.chain_id)
        return {'valid': ok, 'scheme_revealed': False,
                'address': K.address(bytes.fromhex(commit), PRIV.NAME),
                'commitment': commit, 'attestation': att,
                'anonymity_set': len(PRIV.cloak_set())}
    if action == 'open':
        w = K.get(a.get('wallet'))
        if w['scheme'] != PRIV.NAME:
            raise StateError(f'{w["name"]} is not a private wallet',
                             code='not_private')
        sk = K.secret_key(w)
        return {'address': w['address'], 'commitment': w['pk'],
                'inner': sk['inner'], 'salt': sk['salt'].hex(),
                'pk': sk['pk'].hex(),
                'note': 'selective disclosure: anyone can now recompute the '
                        'commitment with pq_private action=check_open — '
                        'and anyone can now attack the revealed scheme'}
    if action == 'check_open':
        ok = PRIV.check_open(a.get('commitment') or '', a.get('inner') or '',
                             a.get('salt') or '', a.get('pk') or '')
        return {'match': ok, 'commitment': a.get('commitment'),
                'inner': a.get('inner')}
    raise StateError(f'unknown action {action!r} — info | create | prove | '
                     'verify | open | check_open', code='bad_args')


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
    'pq_algos': {
        'description': 'The key types this chain can witness: ML-DSA (FIPS '
                       '204, lattice) and SLH-DSA (FIPS 205, hash-based) '
                       'built in, plus anything a plugin registered — with '
                       'per-scheme signature and public key sizes (what '
                       'witness gas bills), whether each is quantum-safe, and '
                       'whether the node accepts it. Every wallet picks one '
                       'at creation; new key types are a .py file dropped '
                       'into a plugin directory, no rebuild.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_algos,
    },
    'pq_complexity': {
        'description': 'What breaking a key type costs. The brute-force '
                       'bound assuming a maximal-entropy seed (2^k keygen '
                       'trials classical, Grover 2^(k/2) — or POLYNOMIAL '
                       'for anything that declared quantum_safe=false, '
                       'because Shor is not a search), the best declared '
                       'structural attack, and the empirical entropy probe '
                       'that tests the assumption: deterministic keygen, '
                       'distinct keys, every seed region load-bearing, '
                       'avalanche, sign/verify that binds the message. The '
                       'gate verdict (accepted / insufficient_complexity) '
                       'is decided from exactly this card. One scheme= for '
                       'the full probed profile; no args for every scheme '
                       'at once (analytic only unless probe=true — probing '
                       'runs each scheme\'s own keygen and sign).',
        'inputSchema': {'type': 'object', 'properties': {
            'scheme': _str('one key type by name (pq_algos lists them); '
                           'omit for all'),
            'probe': _bool('run the empirical entropy probe (default: yes '
                           'for a single scheme, no for the all-scheme '
                           'scan)')}},
        'handler': _t_complexity,
    },
    'pq_keytype': {
        'description': 'The key type builder: add a NEW key type to this '
                       'node. Two doors. COMPOSE registered schemes: op=all '
                       'is the hybrid AND (every part signs, verify needs '
                       'every part — falls only if every part falls, '
                       'quantum-safe if any part is); op=any is the 1-of-n '
                       'OR (one part\'s signature suffices — falls at the '
                       'weakest part); op=threshold k=2 is k-of-n (any k '
                       'parts sign — falls when the k weakest fall); parts '
                       'may themselves be composites, '
                       'so key types form a DAG (pq_keytype_dag draws it). '
                       'Or BRING YOUR OWN as wasm=<base64 blob>: one '
                       'importless wasm module exporting the pq2 ABI '
                       '(pq_keygen/pq_sign/pq_verify — importless means no '
                       'clock, no randomness, no host call, so it is '
                       'deterministic and every node can re-judge every '
                       'witness forever). A bring-your-own blob is admitted '
                       'only if the proof battery, a determinism double-run, '
                       'the runtime budgets and the complexity gate ALL '
                       'pass — refused otherwise with the measurement '
                       'attached. Either way the new scheme registers like '
                       'any other: wallets pick it by name, witness gas '
                       'bills its real bytes. Actions: create, delete '
                       '(refused while on-chain accounts or composites '
                       'depend on it), show, list.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('create | delete | show | list (default list)',
                           enum=['create', 'delete', 'show', 'list']),
            'name': _str('the key type\'s name, 3-48 chars of '
                         '[A-Za-z0-9_.+-] — hashed into every address under '
                         'it, never rebindable'),
            'op': _str('composites: how the parts combine — all (hybrid '
                       'AND), any (1-of-n OR) or threshold (k-of-n, pass '
                       'k); default all',
                       enum=['all', 'any', 'threshold']),
            'k': _int('threshold: how many parts must sign (1..parts)'),
            'author': _str('local wallet credited as the key type\'s author'),
            'private': _bool('create: also open a PRIVATE wallet hiding '
                             'this new key type — the chain sees only a '
                             'commitment and fixed-size envelopes'),
            'wallet': _str('create + private: name for that private wallet '
                           '(default ghost-<name>)'),
            'publish': _bool('anchor keytype/<name> = SHA3(spec) on chain '
                             'from the author wallet — authorship becomes a '
                             'key you can list and sell'),
            'parts': {'type': 'array', 'items': {'type': 'string'},
                      'description': 'composites: 2-6 registered key types '
                                     'to combine (pq_algos lists them); also '
                                     'accepted as one string, comma- or '
                                     '+-separated'},
            'wasm': _str('bring-your-own: the wasm blob, standard base64. '
                         'Must export the pq2 ABI and declare zero imports; '
                         'pq/wasm-src/examples/lamport.rs is the worked '
                         'example'),
            'pk_bytes': _int('bring-your-own: exact public key size the '
                             'blob produces'),
            'sig_bytes': _int('bring-your-own: exact signature size'),
            'sk_bytes': _int('bring-your-own: exact secret key size'),
            'family': _str('bring-your-own: scheme family for the catalog '
                           'card (e.g. Lamport, EdDSA)'),
            'basis': _str('bring-your-own: what the security rests on, '
                          'one honest line'),
            'quantum_safe': _bool('bring-your-own: does the hard problem '
                                  'survive a quantum computer? false means '
                                  'POLYNOMIAL (Shor-class) on the catalog '
                                  'card'),
            'classical_bits': _int('bring-your-own: best-known classical '
                                   'attack in bits, if cheaper than brute '
                                   'force over the 256-bit seed — the '
                                   'complexity floor judges this'),
            'quantum_bits': _int('bring-your-own: best-known quantum attack '
                                 'in bits, when quantum_safe'),
            'note': _str('a line about why this key type exists (say so '
                         'here if it is one-time or experimental)'),
        }},
        'handler': _t_keytype,
    },
    'pq_keytype_test': {
        'description': 'Run the proof battery against one key type, built-in '
                       'or composite: sign/verify roundtrip, tampered '
                       'message, tampered and truncated signature, context '
                       'binding, wrong-key rejection, per-part ablation for '
                       'composites (corrupt one part\'s signature bytes — an '
                       '"all" that still verifies is lying about a part), '
                       'and a fresh entropy probe. Refutation-only: a green '
                       'board means no failure was found, never "secure". '
                       'Also returns keygen/sign/verify timings and real '
                       'witness sizes.',
        'inputSchema': {'type': 'object', 'properties': {
            'scheme': _str('the key type to test (pq_algos lists them)')},
            'required': ['scheme']},
        'handler': _t_keytype_test,
    },
    'pq_keytype_dag': {
        'description': 'Every key type this node knows, as the graph it is: '
                       'one node per key type (family, acceptance, security '
                       'bits, witness sizes, on-chain accounts) and one edge '
                       'from each part into the composite built from it. '
                       'Primitives sit at depth 0; a composite\'s depth is '
                       'one past its deepest part. What the console\'s '
                       'BUILDER tab draws.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_keytype_dag,
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
            'file': _str('price committing to a local file — the digest is '
                         '32 bytes whatever the file weighs (local callers '
                         'only)'),
            'value': _str('hex bytes to store literally, instead of data='),
            'value_kind': _str('hash (32-byte commitment, cheaper per byte) or '
                               'raw', enum=['hash', 'raw']),
            'days': _int('lease length in days'),
            'hours': _int('lease length in hours'),
            'seconds': _int('lease length in seconds (minimum 3600)'),
            'wallet': _WALLET,
            'address': _str('price it for this address instead of a wallet'),
            'scheme': _str('price the witness for this key type instead of '
                           'the wallet\'s (pq_algos lists them) — an SLH-DSA '
                           'signature is ~7x an ML-DSA-44 one and witness gas '
                           'notices'),
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
                       'the chain is priced for), file= to commit to a file '
                       'on this node\'s disk the same way (local callers '
                       'only), or value= with '
                       'value_kind=raw for literal hex bytes. Claims the key if '
                       'it is free, and only its owner can overwrite it. The '
                       'deposit is sized from days=/hours= automatically; it '
                       'buys a lease, and when the lease runs out the entry '
                       'expires and can be swept by anyone.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the key to write'),
            'data': _str('text to commit to — hashed with SHA3-256'),
            'file': _str('path to a file on this node\'s disk — streamed '
                         'through SHA3-256, only the 32-byte digest lands '
                         'on-chain. Local callers only (shell, stdio MCP, '
                         'loopback); the public API refuses it'),
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
        'description': 'The local keystore: list, create, show, use or remove '
                       'a wallet. Each wallet picks its key type at creation — '
                       'ML-DSA by default, SLH-DSA for the hash-based hedge '
                       '(pq_algos compares them). Whatever the scheme, what '
                       'is stored is a 32-byte seed, mode 0600, off the '
                       'source tree.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('list, create, use, show, export or remove — '
                           'export returns the seed (the private key) and '
                           'is owner-only (local or server.secret bearer)',
                           enum=['list', 'create', 'use', 'show', 'export',
                                 'remove']),
            'inner': _str('private wallets only: the hidden key type (as '
                          'returned by export) to restore it'),
            'name': _str('wallet name'),
            'scheme': _str('key type for a new wallet (default '
                           'ML-DSA-44; pq_algos lists the choices and what '
                           'each costs in witness gas)'),
            'seed': _str('32 bytes of hex for a deterministic wallet — the '
                         'same seed under two schemes is two unrelated keys'),
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
            'file': _str('or a path on this node\'s disk — hashed here and '
                         'compared (local callers only)'),
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
            'limit': _int('how many (default 25)'),
            'since': _int('only scan from this block height (default 0 — full chain)')}},
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
            'signatures': _bool('also re-verify every transaction witness, '
                                'whatever its key type (slow)')}},
        'handler': _t_verify,
    },

    'pq_prims': {
        'description': 'The prims: every key type that is not built from '
                       'others — builtin ML-DSA/SLH-DSA, plugin schemes '
                       '(ed25519) and user wasm blobs — with what they rest '
                       'on, what breaking them costs, which key types use '
                       'them and how many accounts sign with them. name= '
                       'shows one.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('one prim to show')}},
        'handler': _t_prims,
    },
    'pq_prim': {
        'description': 'Add a prim: one importless wasm blob exporting the '
                       'pq2 ABI (pq_keygen/pq_sign/pq_verify), admitted only '
                       'if the proof battery, the determinism double-run and '
                       'the complexity gate pass — refused with the '
                       'measurement otherwise. author= credits a wallet and '
                       'publish=true anchors the spec hash on chain at '
                       'keytype/<name>. Also show / delete.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('add | show | delete (default show)'),
            'name': _str('the prim name — hashed into every address, never '
                         'rebound'),
            'wasm': _str('the blob, standard base64'),
            'pk_bytes': _int('exact public key size the blob produces'),
            'sig_bytes': _int('exact signature size'),
            'sk_bytes': _int('exact secret key size'),
            'family': _str('family label, e.g. hash-OTS'),
            'basis': _str('what the security rests on'),
            'quantum_safe': _bool('declared quantum-safe'),
            'classical_bits': _int('best-known classical attack, bits'),
            'quantum_bits': _int('best-known quantum attack, bits'),
            'note': _str('a line for the market card'),
            'author': _str('local wallet credited as author'),
            'publish': _bool('anchor keytype/<name> = SHA3(spec) on chain '
                             'from the author wallet'),
        }, 'required': ['name']},
        'handler': _t_prim,
    },
    'pq_keymarket': {
        'description': 'The key type market: every prim, composite and the '
                       'private cloak on one shelf — author, on-chain anchor '
                       '(authorship is the key keytype/<name>, listable and '
                       'buyable), accounts using it, key types forked from '
                       'it, witness bytes and gas, classical/quantum attack '
                       'cost, rarity. sort=hot|new|safe|cheap|deep|used, '
                       'q= filters, kind=all|prim|keytype|private.',
        'inputSchema': {'type': 'object', 'properties': {
            'sort': _str('hot | new | safe | cheap | deep | used'),
            'q': _str('text filter over name, family, parts, badges'),
            'kind': _str('all | prim | keytype | private')}},
        'handler': _t_keymarket,
    },
    'pq_cairo': {
        'description': 'The Cairo-verified key type CAIRO-MSS-SHA256: a '
                       'hash-based hypertree signature resting on SHA-256 '
                       'alone (independent of ML-DSA\'s lattices and '
                       'SLH-DSA\'s SHAKE), whose verifier is a Cairo '
                       'program (src/pq/cairo/src/lib.cairo). Layer it into '
                       'an ALL composite and a quantum forger must break '
                       'one more unrelated assumption. action=info (params, '
                       'program hash, toolchain) | check (sign a message '
                       'with a throwaway key and run the witness '
                       'through the Cairo VM; returns whether the VM and the '
                       'chain\'s verifier agree; tamper=true flips a bit and '
                       'must come back invalid). Read-only.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('info | check (default info)',
                           enum=['info', 'check']),
            'message': _str('check: the message (default "hello from '
                            'cairo")'),
            'tamper': _bool('check: flip one signature bit first — the VM '
                            'must say invalid'),
        }},
        'handler': _t_cairo,
    },
    'pq_private': {
        'description': 'Private key types: an account whose key type and '
                       'public key never appear on chain. Its address '
                       'commits to SHA3(key type, salt, pk); its witnesses '
                       'are fixed-size ML-KEM-768 envelopes that only the '
                       'proposer opens, so every hidden scheme looks the '
                       'same. action=info (cloak set, seal key, threat '
                       'model) | create (name, inner=<hidden scheme>) | '
                       'prove (wallet, message -> sealed proof) | verify '
                       '(address|commitment, message, proof -> valid + the '
                       'proposer\'s signed attestation, scheme never '
                       'revealed) | open (owner reveals inner/salt/pk) | '
                       'check_open. Private wallets work in every write '
                       'tool.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('info | create | prove | verify | open | '
                           'check_open (default info)'),
            'name': _str('create: wallet name (default ghost)'),
            'inner': _str('create: the hidden key type (default ML-DSA-44; '
                          'info lists the cloak set)'),
            'seed': _str('create: optional 32-byte hex seed'),
            'overwrite': _bool('create: replace an existing wallet'),
            'wallet': _str('prove/open: the private wallet'),
            'message': _str('prove/verify: the message'),
            'proof': _str('verify: the sealed proof, hex'),
            'address': _str('verify: the private address'),
            'commitment': _str('verify/check_open: the 32-byte commitment'),
            'salt': _str('check_open: revealed salt hex'),
            'pk': _str('check_open: revealed inner public key hex'),
            'ek': _bool('info: include the full seal encapsulation key'),
        }},
        'handler': _t_private,
    },
}


# ── the vault: off-chain bytes, on-chain pointers ─────────────────


def _vault_store():
    import vault as V
    return V.Store(node().dir)          # per chain: its anchors keep it


def _vault_err(fn):
    """VaultError → StateError, so REST answers with its status code."""
    import vault as V

    def wrapped(a):
        try:
            return fn(a)
        except V.VaultError as e:
            raise StateError(str(e), code=e.code, status=e.status) from None
    return wrapped


def _live_anchors():
    """cid -> [keys] for every unexpired hash entry: what keeps a blob.
    A live RECEIPT keeps everything it points at (code, states, the receipt
    before it, and so on back) — an anchored run must stay replayable."""
    n = node()
    now = int(time.time())
    out = {}
    for key in n.state.store:
        e = n.state.entry(key, now)
        if e and not e['expired'] and e.get('value_kind') == 'hash':
            out.setdefault(e['value'], []).append(key)
    import vault as V
    st = _vault_store()
    todo = [c for c in list(out) if (st.meta(c) or {}).get('kind') == 'receipt']
    for c in list(out):
        if (st.meta(c) or {}).get('kind') == 'state' and \
                (st.meta(c) or {}).get('bytes', 1 << 20) < 4096:
            try:
                v = json.loads(st.get(c).decode()).get('verifier')
            except (V.VaultError, ValueError, AttributeError, TypeError):
                continue
            if isinstance(v, str) and len(v) == 64:
                out.setdefault(v, []).append('policy:' + c[:16])
    seen = set()
    while todo:
        cid = todo.pop()
        if cid in seen:
            continue
        seen.add(cid)
        try:
            over = json.loads(st.get(cid).decode())['over']
        except (V.VaultError, ValueError, KeyError, TypeError):
            continue
        pf = over.get('proof') or {}
        for ref in (over.get('code'), over.get('state_in'),
                    over.get('state_out'), over.get('prev'),
                    pf.get('proof_sha3'), pf.get('verifier')):
            if ref:
                out.setdefault(ref, []).append('receipt:' + cid[:16])
                if (st.meta(ref) or {}).get('kind') == 'receipt':
                    todo.append(ref)
    return out


def _unb64(s, what):
    import base64
    import binascii
    s = (s or '').strip().replace('-', '+').replace('_', '/')
    try:
        return base64.b64decode(s + '=' * (-len(s) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise StateError(f'{what} must be base64', code='bad_args') from None


def _anchor(a, key, cid):
    """Write key = cid as a hash commitment, from the caller's wallet."""
    return _t_set({**{k: a[k] for k in ('wallet', 'days', 'hours', 'seconds',
                                        'deposit', 'dry_run') if k in a},
                   'key': key, 'value': cid, 'value_kind': 'hash'})


def _local_bytes(path):
    """file= for contracts: local callers only, like pq_set file=."""
    if getattr(_CALLER, 'remote', False):
        raise StateError('file= reads this node\'s disk and is local-only — '
                         'send code= instead', code='local_only', status=403)
    p = os.path.realpath(os.path.expanduser(str(path)))
    try:
        with open(p, 'rb') as f:
            return f.read()
    except OSError as e:
        raise StateError(f'cannot read {p}: {e.strerror or e}',
                         code='bad_file') from None


@_vault_err
def _t_vault(a):
    import vault as V
    st = _vault_store()
    action = a.get('action') or 'info'
    if action == 'info':
        items = st.list()
        return {
            'what': 'off-chain storage the node holds but cannot read: the '
                    'client seals with AES-256-GCM before upload, the node '
                    'stores ciphertext under cid = SHA3-256(blob), and the '
                    'chain holds the 32-byte cid as a rent-paying hash entry',
            'envelope': {'magic': 'PQV1', 'header_bytes': V.HEADER,
                         'cipher': 'AES-256-GCM, aad = header',
                         'modes': {'key': 'random 32-byte key, returned to '
                                          'the client only',
                                   'passphrase': f'PBKDF2-HMAC-SHA256, '
                                                 f'{V.PBKDF2_ITERS} rounds'},
                         'inside': 'u32 meta length | {name,type,size} | '
                                   'data | zero pad to a power of two '
                                   f'(min {V.MIN_BUCKET} B)'},
            'limits': {'max_blob': V.MAX_BLOB, 'quota': V.QUOTA,
                       'used': st.used(), 'blobs': len(items),
                       'grace_seconds': V.GRACE},
            'retention': 'kept while any live chain entry commits to its '
                         'cid; unanchored blobs are pruned after the grace '
                         'window',
            'node_sees': ['ciphertext length bucket', 'arrival time',
                          'which key anchors it'],
            'node_never_sees': ['plaintext', 'file name', 'exact size',
                                'the key or passphrase'],
            'how': 'seal on the client (console VAULT tab, or mod.py '
                   'vault_put), then pq_vault action=put blob=<base64> '
                   'key=vault/<name> wallet=<w>',
        }
    if action == 'put':
        if not a.get('blob'):
            raise StateError('put needs blob= (base64 of a PQV1 envelope '
                             'sealed on the client)', code='missing_arg')
        blob = _unb64(a['blob'], 'blob')
        live = _live_anchors()
        out = st.put(blob, kind='sealed', live=live)
        out['anchors'] = live.get(out['cid'], [])
        if a.get('key'):
            out['anchor'] = _anchor(a, a['key'], out['cid'])
            out['anchors'] = sorted(set(out['anchors']) | {a['key']})
        elif not out['anchors']:
            out['warning'] = (f'unanchored: pruned after {V.GRACE}s unless a '
                              'chain key commits to this cid')
        return out
    if action == 'get':
        cid = a.get('cid')
        if not cid and a.get('key'):
            e = node().state.entry(a['key'], int(time.time()))
            if e is None:
                raise StateError(f'no entry at {a["key"]!r}', code='no_entry',
                                 status=404)
            cid = e['value']
        if not cid:
            raise StateError('get needs cid= or key=', code='missing_arg')
        import base64
        blob = st.get(cid)
        return {**st.listing(cid), 'blob': base64.b64encode(blob).decode(),
                'verified': V.cid_of(blob) == cid,
                'anchors': _live_anchors().get(cid, []),
                'note': 'ciphertext — open it on the client with its key or '
                        'passphrase'}
    if action == 'list':
        live = _live_anchors()
        return {'blobs': [{**i, 'anchors': live.get(i['cid'], [])}
                          for i in st.list()]}
    raise StateError(f'unknown vault action {action!r} — info, put, get, '
                     'list', code='bad_action')


def _contract_names():
    n = node()
    now = int(time.time())
    out = []
    for key in sorted(n.state.store):
        if not key.startswith('contract/') or key.count('/') != 1:
            continue
        e = n.state.entry(key, now)
        out.append({'name': key[9:], 'key': key, 'cid': e['value'],
                    'owner': e['owner'], 'expired': e['expired'],
                    'expires_in': e['expires_in']})
    return out


@_vault_err
def _t_contract(a):
    import base64
    import contracts as C
    import vault as V
    st = _vault_store()
    action = a.get('action') or 'info'
    if action == 'info':
        return {
            'model': 'store, don\'t execute: a contract is content-addressed '
                     'code in the vault (PUBLIC — code nobody can read cannot '
                     'be audited) plus one chain key contract/<name> = '
                     'SHA3-256(code). The anchor key is owned, rented, '
                     'listed and sold like any key; rewriting it is an '
                     'upgrade, and the block log keeps every version.',
            'state': 'contract/<name>/<k> keys, written by the anchor\'s '
                     'owner — the chain enforces ownership, not semantics',
            'execution': 'off consensus: anyone fetches the code by hash and '
                         're-runs it. Cairo/sierra code is STARK-provable, '
                         'so a run can be proven and its proof hash anchored '
                         'next to the code',
            'langs': list(V.LANGS),
            'contracts': len(_contract_names()),
        }
    if action == 'deploy':
        name = a.get('name')
        if not name or '/' in name:
            raise StateError('deploy needs name= (no slashes)',
                             code='missing_arg')
        lang = a.get('lang') or 'source'
        if a.get('example'):
            code = C.example_blob(a['example'])
            lang = 'source' if C.EXAMPLES[a['example']].get('source_only') \
                else 'wasm'
        elif a.get('file'):
            code = _local_bytes(a['file'])
        elif a.get('code_b64'):
            code = _unb64(a['code_b64'], 'code_b64')
        elif a.get('code') is not None:
            code = str(a['code']).encode()
        else:
            raise StateError('deploy needs code=, code_b64= or file=',
                             code='missing_arg')
        live = _live_anchors()
        out = st.put(code, kind='code', lang=lang, live=live)
        if lang == 'wasm':
            out['pqc1'] = C.inspect(code)
        out['anchor'] = _anchor(a, 'contract/' + name, out['cid'])
        out['key'] = 'contract/' + name
        return out
    if action == 'show':
        name = a.get('name') or ''
        key = name if name.startswith('contract/') else 'contract/' + name
        e = node().state.entry(key, int(time.time()))
        if e is None:
            raise StateError(f'no contract at {key!r}', code='no_entry',
                             status=404)
        cid = e['value']
        out = {'name': key[9:], 'key': key, 'cid': cid, 'owner': e['owner'],
               'expired': e['expired'], 'expires_in': e['expires_in']}
        try:
            code = st.get(cid)
        except V.VaultError:
            out['code'] = None
            out['verified'] = False
            out['note'] = 'the chain commits to this code but this node does ' \
                          'not hold the bytes — fetch them from any node by cid'
            return out
        meta = st.meta(cid) or {}
        out['lang'] = meta.get('lang')
        out['bytes'] = len(code)
        out['verified'] = V.cid_of(code) == cid
        if meta.get('lang') == 'wasm':
            out['code_b64'] = base64.b64encode(code).decode()
            out['pqc1'] = C.inspect(code)
            rcid, r = _latest_receipt(key[9:], st)
            out['receipt'] = rcid
            out['runs'] = (r['over']['n'] + 1) if r else 0
            out['state'] = r['over']['state_out'] if r else None
        else:
            out['code'] = code.decode('utf-8', 'replace')
        proof = node().state.proof(key)
        out['proof_valid'] = S.check_proof(proof['leaf'], proof['path'],
                                           proof['root'])
        out['state_root'] = proof['root']
        return out
    if action == 'list':
        return {'contracts': _contract_names()}
    if action == 'examples':
        return {'abi': C.ABI,
                'abi_spec': 'exports memory, pq_reset(), pq_alloc(n)->ptr, '
                            'pqc_run(state,state_len,input,input_len,out,'
                            'out_cap)->i32 = next-state length, <0 = revert; '
                            'NO imports',
                'receipt': 'pqr1 — {over:{v,chain_id,contract,abi,code,n,'
                           'prev,state_in,input,input_sha3,state_out,'
                           'out_bytes,ret,reverted,out_cap,ts}, by, scheme, '
                           'pk, sig} '
                           'signed under ' + C.RECEIPT_CTX.decode(),
                'examples': C.examples(),
                'flow': ['pq_contract action=deploy example=counter '
                         'name=counter wallet=<w> days=30',
                         'pq_contract action=run name=counter input=00 '
                         'wallet=<w>',
                         'pq_contract action=verify name=counter',
                         'pq_contract action=receipts name=counter',
                         'pq_contract action=state name=counter']}
    if action in ('run', 'verify', 'receipts', 'state'):
        return _contract_exec(a, action, st)
    if action in _PROVE_ACTIONS:
        return _contract_prove(a, action, st)
    raise StateError(f'unknown contract action {action!r} — info, deploy, '
                     'show, list, run, verify, receipts, state, examples, '
                     + ', '.join(_PROVE_ACTIONS),
                     code='bad_action')


def _receipt_key(name):
    return 'contract/' + name + '/receipt'


def _latest_receipt(name, st):
    """(cid, receipt dict|None) anchored at contract/<name>/receipt."""
    import contracts as C
    import vault as V
    e = node().state.entry(_receipt_key(name), int(time.time()))
    if e is None or e['expired']:
        return None, None
    cid = e['value']
    try:
        return cid, C.parse_receipt(st.get(cid))
    except (V.VaultError, StateError):
        return cid, None


def _contract_exec(a, action, st):
    import contracts as C
    import vault as V
    name = (a.get('name') or '').strip()
    if name.startswith('contract/'):
        name = name[9:]
    if action == 'verify' and a.get('receipt'):
        name = None
    elif not name:
        raise StateError(f'{action} needs name=', code='missing_arg')
    n = node()
    now = int(time.time())
    anchor = n.state.entry('contract/' + name, now) if name else None
    if name and anchor is None:
        raise StateError(f'no contract at contract/{name}', code='no_entry',
                         status=404)

    if action == 'run':
        w = _wallet(a.get('wallet'))
        if anchor['expired']:
            raise StateError(f'contract/{name} has expired — re-deploy it',
                             code='expired')
        if w['address'] != anchor['owner']:
            raise StateError(f'contract/{name} is owned by {anchor["owner"]}; '
                             f'only the owner may run and anchor it (wallet '
                             f'{w["name"]} is {w["address"]})',
                             code='not_owner', status=403)
        code_cid = anchor['value']
        _pcid, _pol = _policy_of(name, st)
        if _pol and _pol['system'] != 'replay':
            raise StateError(f'contract/{name} requires {_pol["system"]} '
                             'proofs — use action=submit with a proof '
                             '(or set_policy system=replay)',
                             code='policy_requires_proof', status=409)
        if not st.has(code_cid):
            raise StateError('this node does not hold the code blob — '
                             'fetch it from any node by cid', code='no_blob',
                             status=404)
        info = C.inspect(st.get(code_cid))
        if not info['abi']:
            raise StateError(f'contract/{name} is not a pqc1 wasm contract: '
                             f'{info["reason"]}', code='bad_code')
        prev_cid, prev = _latest_receipt(name, st)
        rk = n.state.entry(_receipt_key(name), now)
        if rk and not rk['expired'] and rk['owner'] != w['address']:
            raise StateError(f'{_receipt_key(name)} is held by {rk["owner"]} '
                             '— the receipt key must belong to the contract '
                             'owner', code='key_taken', status=409)
        if prev_cid and prev is None:
            raise StateError(f'the anchored receipt {prev_cid[:16]}… is not '
                             'on this node — fetch it by cid before running '
                             'on top of it', code='no_blob', status=404)
        if a.get('state_b64') is not None:
            state_in = _unb64(a['state_b64'], 'state_b64')
        elif prev:
            so = prev['over']['state_out']
            state_in = b'' if so == C.cid_of(b'') else st.get(so)
        else:
            state_in = b''
        inp = _unb64(a['input_b64'], 'input_b64') if a.get('input_b64')             else _hexbytes(a.get('input') or '', 'input')
        res = C.execute(st._p(code_cid), code_cid, state_in, inp)
        seq = (prev['over']['n'] + 1) if prev else 0
        receipt = C.make_receipt(
            w, chain_id=n.chain_id, contract=name, code_cid=code_cid,
            n=seq, prev=prev_cid, state_in=state_in, inp=inp, result=res)
        blob = C.receipt_blob(receipt)
        live = _live_anchors()
        if state_in:
            st.put(state_in, kind='state', live=live)
        if not res['reverted'] and res['out']:
            st.put(res['out'], kind='state', live=live)
        listing = st.put(blob, kind='receipt', live=live)
        out = {'contract': name, 'code': code_cid, 'receipt': listing['cid'],
               'n': seq, 'prev': prev_cid, 'ret': res['ret'],
               'reverted': res['reverted'], 'trap': res['trap'],
               'ms': res['ms'], 'state_in': receipt['over']['state_in'],
               'state_out': receipt['over']['state_out'],
               'state_out_hex': (res['out'] if not res['reverted']
                                 else state_in).hex(),
               'out_bytes': receipt['over']['out_bytes'],
               'signed_by': w['address'], 'scheme': w['scheme'],
               'receipt_blob': receipt}
        if a.get('anchor', True) in (False, 'false', '0', 0, 'no'):
            out['anchored'] = False
            out['note'] = 'not anchored — the receipt is signed and stored '                           'but the chain does not point at it'
            return out
        out['anchor'] = _anchor(a, _receipt_key(name), listing['cid'])
        out['anchored'] = not a.get('dry_run')
        out['key'] = _receipt_key(name)
        return out

    if action == 'state':
        cid, r = _latest_receipt(name, st)
        out = {'contract': name, 'receipt': cid, 'n': None, 'state': None,
               'state_hex': None}
        if r is None:
            out['note'] = 'no receipt anchored' if cid is None else                 'anchored receipt not held on this node'
            return out
        so = r['over']['state_out']
        out.update(n=r['over']['n'], state=so)
        try:
            b = b'' if so == C.cid_of(b'') else st.get(so)
            out['state_hex'] = b.hex()
            out['bytes'] = len(b)
        except V.VaultError:
            out['note'] = 'state blob not held on this node'
        return out

    if action == 'receipts':
        cid, r = _latest_receipt(name, st)
        chain = []
        limit = int(a.get('limit') or 50)
        while cid and len(chain) < limit:
            if r is None:
                chain.append({'receipt': cid, 'missing': True})
                break
            o = r['over']
            chain.append({'receipt': cid, 'n': o['n'], 'input': o['input'],
                          'ret': o['ret'], 'reverted': o['reverted'],
                          'state_in': o['state_in'], 'state_out': o['state_out'],
                          'code': o['code'], 'by': r['by'], 'ts': o['ts'],
                          'proof': (o.get('proof') or {}).get('system',
                                                              'replay')})
            cid = o['prev']
            if cid:
                try:
                    r = C.parse_receipt(st.get(cid))
                except (V.VaultError, StateError):
                    r = None
        return {'contract': name, 'key': _receipt_key(name),
                'owner': anchor['owner'], 'receipts': chain,
                'latest_first': True}

    # verify
    if a.get('receipt'):
        cid = str(a['receipt']).strip()
        try:
            r = C.parse_receipt(st.get(cid))
        except V.VaultError as e:
            raise StateError(str(e), code='no_blob', status=404) from None
        name = r['over']['contract']
        anchor = n.state.entry('contract/' + name, now)
    else:
        cid, r = _latest_receipt(name, st)
        if cid is None:
            raise StateError(f'nothing anchored at {_receipt_key(name)}',
                             code='no_entry', status=404)
        if r is None:
            raise StateError(f'receipt {cid[:16]}… is not held on this node',
                             code='no_blob', status=404)
    import proofs as P
    sig = C.check_signature(r)
    rep = P.check_receipt(r, st)
    rk = n.state.entry(_receipt_key(name), now)
    _pc, pol = _policy_of(name, st)
    rproof = r['over'].get('proof') or {}
    rsys = rproof.get('system', 'replay')
    checks = {
        'signature': sig['sig_valid'],
        'replays': rep.get('matches'),
        'chain_id': r['over']['chain_id'] == n.chain_id,
        'signer_is_owner': bool(anchor) and anchor['owner'] == r['by'],
        'anchored_latest': bool(rk) and not rk['expired']
        and rk['value'] == cid,
        'anchor_owner_is_owner': bool(rk and anchor)
        and rk['owner'] == anchor['owner'],
        'code_is_current': bool(anchor) and anchor['value'] == r['over']['code'],
        'proof_policy': (pol is None and rsys == 'replay') or bool(
            pol and pol['system'] == rsys
            and pol.get('verifier') == rproof.get('verifier')),
    }
    hard = ('signature', 'replays', 'chain_id', 'signer_is_owner')
    valid = all(checks[k] is True for k in hard)
    means = ('this run really happened: the owner signed it and '
             're-executing the code on the same state and input '
             'reproduces it byte for byte' if rsys == 'replay' else
             f'the owner signed this step and its {rsys} proof checks out '
             'against the state, input and result — nothing was re-run')
    return {'receipt': cid, 'contract': name, 'n': r['over']['n'],
            'valid': valid, 'checks': checks, 'signature': sig,
            'replay': rep, 'proof': {'system': rsys, **rep,
                                     'policy_now': pol},
            'means': means if valid else 'do not trust this receipt',
            'soft': 'anchored_latest / anchor_owner_is_owner / '
                    'code_is_current / proof_policy describe the chain NOW; '
                    'an old receipt in the chain is still valid when they '
                    'are false'}


_PROVE_ACTIONS = ('proofs', 'verifiers', 'verifier_deploy', 'policy',
                  'set_policy', 'solve', 'attest', 'check', 'submit')


def _verifier_names():
    n = node()
    now = int(time.time())
    out = []
    for key in sorted(n.state.store):
        if not key.startswith('verifier/') or key.count('/') != 1:
            continue
        e = n.state.entry(key, now)
        out.append({'name': key[9:], 'key': key, 'cid': e['value'],
                    'owner': e['owner'], 'expired': e['expired'],
                    'expires_in': e['expires_in']})
    return out


def _policy_of(name, st):
    """(cid, policy|None) anchored at contract/<name>/proof."""
    import proofs as P
    import vault as V
    e = node().state.entry(P.policy_key(name), int(time.time()))
    if e is None or e['expired']:
        return None, None
    try:
        return e['value'], P.parse_policy(st.get(e['value']))
    except (V.VaultError, StateError):
        return e['value'], None


def _resolve_verifier(ref, st):
    """name | cid | example name -> cid (example blobs are stored on use)."""
    import proofs as P
    ref = (ref or '').strip()
    if ref.startswith('verifier/'):
        ref = ref[9:]
    if len(ref) == 64 and all(c in '0123456789abcdef' for c in ref):
        if not st.has(ref):
            raise StateError(f'this node does not hold verifier {ref[:16]}… '
                             '— verifier_deploy it or fetch it by cid',
                             code='no_blob', status=404)
        return ref
    e = node().state.entry('verifier/' + ref, int(time.time()))
    if e and not e['expired']:
        return e['value']
    if ref in P.VERIFIER_EXAMPLES:
        blob = P.example_verifier(ref)
        return st.put(blob, kind='code', lang='wasm',
                      live=_live_anchors())['cid']
    raise StateError(f'no verifier {ref!r} — deploy one with '
                     'action=verifier_deploy', code='no_entry', status=404)


def _machine(name, st):
    """(anchor entry, code cid, abi) for contract/<name>."""
    import contracts as C
    name = (name or '').strip()
    if name.startswith('contract/'):
        name = name[9:]
    if not name:
        raise StateError('name= is required', code='missing_arg')
    anchor = node().state.entry('contract/' + name, int(time.time()))
    if anchor is None:
        raise StateError(f'no contract at contract/{name}', code='no_entry',
                         status=404)
    abi = C.EXT_ABI
    if st.has(anchor['value']) and (st.meta(anchor['value']) or {}).get(
            'lang') == 'wasm':
        if C.inspect(st.get(anchor['value']))['abi']:
            abi = C.ABI
    return name, anchor, anchor['value'], abi


def _bytes_arg(a, b64, hexk, what):
    if a.get(b64) not in (None, ''):
        return _unb64(a[b64], b64)
    if a.get(hexk) not in (None, ''):
        return _hexbytes(a[hexk], hexk)
    return None


def _contract_prove(a, action, st):
    import json as _json
    import contracts as C
    import proofs as P
    import vault as V
    n = node()
    now = int(time.time())

    if action == 'verifiers':
        return {'abi': P.ABI, 'verifiers': _verifier_names(),
                'examples': P.verifier_examples()}

    if action == 'proofs':
        dep = {v['cid']: v for v in _verifier_names()}
        ver = []
        for e in P.verifier_examples():
            d = dep.pop(e.get('cid'), None)
            ver.append({**e, 'deployed': bool(d),
                        'key': d['key'] if d else None,
                        'owner': d['owner'] if d else None})
        for cid, d in dep.items():
            info = {'abi': None}
            try:
                info = P.inspect(st.get(cid))
            except V.VaultError:
                info['reason'] = 'blob not held on this node'
            ver.append({'name': d['name'], 'cid': cid, 'example': False,
                        'deployed': True, 'key': d['key'],
                        'owner': d['owner'], 'pqp1': info,
                        'what': 'deployed by ' + d['owner'][:12] + '…'})
        machines = []
        for c in _contract_names():
            _cid, pol = _policy_of(c['name'], st)
            machines.append({'name': c['name'], 'owner': c['owner'],
                             'system': pol['system'] if pol else None,
                             'verifier': (pol or {}).get('verifier'),
                             'k': (pol or {}).get('k'),
                             'signers': (pol or {}).get('signers')})
        return {
            'abi': P.ABI,
            'abi_spec': 'exports memory, pq_reset(), pq_alloc(n)->ptr, '
                        'pqp_verify(state_in,sl,input,il,state_out,ol,'
                        'proof,pl)->i32 (1 = valid); NO imports',
            'systems': [{'id': k, **v} for k, v in P.SYSTEM_INFO.items()],
            'verifiers': ver, 'machines': machines,
            'attest': 'quorum signers sign pqa1 {chain_id, contract, code, '
                      'prev, state_in, input_sha3, state_out} under '
                      + P.ATTEST_CTX.decode(),
            'flow': ['pq_contract action=verifier_deploy example=pow-ladder '
                     'wallet=<w>',
                     'pq_contract action=deploy example=ladder name=ladder '
                     'wallet=<w>',
                     'pq_contract action=set_policy name=ladder system=wasm '
                     'verifier=pow-ladder wallet=<w>',
                     'pq_contract action=solve name=ladder input=<hex>',
                     'pq_contract action=submit name=ladder input=<hex> '
                     'state_out_b64=… proof_b64=… wallet=<w>',
                     'pq_contract action=verify name=ladder']}

    if action == 'verifier_deploy':
        ex = a.get('example')
        name = (a.get('name') or ex or '').strip()
        if not name or '/' in name:
            raise StateError('verifier_deploy needs name= (no slashes)',
                             code='missing_arg')
        if ex:
            blob = P.example_verifier(ex)
        elif a.get('code_b64'):
            blob = _unb64(a['code_b64'], 'code_b64')
        elif a.get('file'):
            blob = _local_bytes(a['file'])
        else:
            raise StateError('verifier_deploy needs example=, code_b64= or '
                             'file=', code='missing_arg')
        info = P.inspect(blob)
        if not info['abi']:
            raise StateError(f'not a pqp1 verifier: {info["reason"]}',
                             code='bad_code')
        out = st.put(blob, kind='code', lang='wasm', live=_live_anchors())
        out['pqp1'] = info
        out['key'] = 'verifier/' + name
        out['anchor'] = _anchor(a, out['key'], out['cid'])
        return out

    name, anchor, code_cid, abi = _machine(a.get('name'), st)
    pcid, pol = _policy_of(name, st)

    if action == 'policy':
        if a.get('system'):
            raise StateError('policy only reads — use action=set_policy to '
                             'change it', code='bad_action')
        return {'name': name, 'policy': pol, 'cid': pcid,
                'key': P.policy_key(name), 'owner': anchor['owner'],
                'abi': abi}

    if action == 'set_policy':
        w = _wallet(a.get('wallet'))
        if w['address'] != anchor['owner']:
            raise StateError(f'contract/{name} is owned by {anchor["owner"]}'
                             f'; only the owner sets its proof policy',
                             code='not_owner', status=403)
        system = a.get('system')
        if system == 'replay' and abi != C.ABI:
            raise StateError('replay needs a pqc1 wasm contract — this '
                             'machine has none', code='bad_policy')
        verifier = None
        if system == 'wasm':
            verifier = _resolve_verifier(a.get('verifier'), st)
        newp = P.make_policy(system, verifier=verifier, k=a.get('k'),
                             signers=_list_arg(a.get('signers')))
        blob = P.canonical(newp)
        listing = st.put(blob, kind='state', live=_live_anchors())
        out = {'name': name, 'policy': newp, 'cid': listing['cid'],
               'key': P.policy_key(name)}
        out['anchor'] = _anchor(a, P.policy_key(name), listing['cid'])
        return out

    # transition actions ------------------------------------------------
    prev_cid, prev = _latest_receipt(name, st)
    if prev_cid and prev is None:
        raise StateError(f'the anchored receipt {prev_cid[:16]}… is not on '
                         'this node — fetch it by cid first', code='no_blob',
                         status=404)
    state_in = _bytes_arg(a, 'state_b64', 'state_hex', 'state')
    if state_in is None:
        so = prev['over']['state_out'] if prev else None
        state_in = b'' if (so is None or so == C.cid_of(b'')) else st.get(so)
    inp = _bytes_arg(a, 'input_b64', 'input', 'input') or b''
    claim = {'chain_id': n.chain_id, 'contract': name, 'code': code_cid,
             'prev': prev_cid}
    code_path = None
    if abi == C.ABI and st.has(code_cid):
        code_path = st._p(code_cid)

    def effective_policy():
        sysname = a.get('system')
        if pol and sysname and sysname != pol['system']:
            raise StateError(f'contract/{name} has a {pol["system"]} policy '
                             f'— a {sysname} proof is not accepted',
                             code='policy_mismatch', status=409)
        if pol:
            return pol
        if not sysname:
            return P.make_policy('replay') if abi == C.ABI else None
        return P.make_policy(
            sysname, verifier=(_resolve_verifier(a.get('verifier'), st)
                               if sysname == 'wasm' else None),
            k=a.get('k'), signers=_list_arg(a.get('signers')))

    if action == 'solve':
        policy = effective_policy()
        if policy is None:
            raise StateError('this machine has no policy and no code to '
                             'run — set_policy first', code='bad_policy')
        sysname, got, note = policy['system'], None, None
        if sysname == 'wasm':
            ex = P.known_example(policy['verifier'])
            if ex:
                got = P.solve_example(ex, state_in, inp)
                note = f'bundled prover for {ex}'
        if got is None and code_path:
            res = C.execute(code_path, code_cid, state_in, inp)
            if res['reverted']:
                raise StateError('the contract reverts on this input '
                                 f'(ret {res["ret"]}) — nothing to prove',
                                 code='reverted', status=422)
            got = (res['out'], b'')
            note = 'ran the contract (replay); for quorum, have signers ' \
                   'attest this state_out'
        if got is None:
            raise StateError('no bundled prover for this verifier — build '
                             'state_out and proof yourself and use check',
                             code='no_solver', status=404)
        state_out, proof = got
        import base64
        return {'system': sysname, 'state_in': C.cid_of(state_in),
                'state_out': C.cid_of(state_out),
                'state_out_b64': base64.b64encode(state_out).decode(),
                'state_out_hex': state_out.hex(),
                'proof_b64': base64.b64encode(proof).decode(),
                'proof_hex': proof.hex(), 'note': note}

    state_out = _bytes_arg(a, 'state_out_b64', 'state_out_hex', 'state_out')
    if state_out is None:
        raise StateError(f'{action} needs state_out_b64= (the claimed next '
                         'state)', code='missing_arg')

    if action == 'attest':
        w = _wallet(a.get('wallet'))
        msg = P.claim_message(state_in=state_in, inp=inp,
                              state_out=state_out, **claim)
        return {'attestation': P.make_attestation(w, msg),
                'message_sha3': C.cid_of(msg), 'contract': name,
                'prev': prev_cid, 'state_in': C.cid_of(state_in),
                'state_out': C.cid_of(state_out)}

    # check / submit
    policy = effective_policy()
    if policy is None:
        raise StateError('this machine is not wasm and has no proof policy '
                         '— pass system= (wasm + verifier=, or quorum + '
                         'signers=, k=) or set_policy', code='bad_policy')
    attestations = None
    if policy['system'] == 'quorum':
        raw = a.get('attestations')
        if raw is None and a.get('proof_b64'):
            raw = _unb64(a['proof_b64'], 'proof_b64')
        elif isinstance(raw, str):
            raw = raw.encode()
        attestations = P.parse_attestations(raw if raw is not None else [])
        proof = P.canonical(attestations)
    else:
        proof = _bytes_arg(a, 'proof_b64', 'proof_hex', 'proof') or b''
    res = P.check_transition(
        policy['system'], store=st, policy=policy, claim=claim,
        state_in=state_in, inp=inp, state_out=state_out, proof=proof,
        attestations=attestations, code_path=code_path)
    if action == 'check':
        res['contract'] = name
        res['policy'] = policy
        return res
    if not res['valid']:
        raise StateError(f'{policy["system"]} proof rejected: '
                         f'{res.get("reason") or "invalid"}',
                         code='proof_rejected', status=422)

    w = _wallet(a.get('wallet'))
    if anchor['expired']:
        raise StateError(f'contract/{name} has expired — re-deploy it',
                         code='expired')
    if w['address'] != anchor['owner']:
        raise StateError(f'contract/{name} is owned by {anchor["owner"]}; '
                         f'only the owner may submit and anchor transitions '
                         f'(wallet {w["name"]} is {w["address"]})',
                         code='not_owner', status=403)
    rk = n.state.entry(_receipt_key(name), now)
    if rk and not rk['expired'] and rk['owner'] != w['address']:
        raise StateError(f'{_receipt_key(name)} is held by {rk["owner"]} — '
                         'the receipt key must belong to the contract owner',
                         code='key_taken', status=409)
    seq = (prev['over']['n'] + 1) if prev else 0
    receipt = C.make_transition_receipt(
        w, chain_id=n.chain_id, contract=name, code_cid=code_cid, abi=abi,
        n=seq, prev=prev_cid, state_in=state_in, inp=inp,
        state_out=state_out,
        proof=P.descriptor(policy['system'], policy, proof))
    live = _live_anchors()
    for blob in (state_in, state_out):
        if blob:
            st.put(blob, kind='state', live=live)
    if receipt['over'].get('proof'):
        st.put(proof, kind='state', live=live)
    listing = st.put(C.receipt_blob(receipt), kind='receipt', live=live)
    out = {'contract': name, 'code': code_cid, 'receipt': listing['cid'],
           'n': seq, 'prev': prev_cid, 'ret': receipt['over']['ret'],
           'reverted': False, 'state_in': receipt['over']['state_in'],
           'state_out': receipt['over']['state_out'],
           'state_out_hex': state_out.hex(),
           'out_bytes': len(state_out), 'signed_by': w['address'],
           'scheme': w['scheme'], 'receipt_blob': receipt,
           'proof': {'system': policy['system'],
                     'verifier': policy.get('verifier'),
                     'proof_sha3': C.cid_of(proof),
                     'proof_bytes': len(proof), 'ms': res.get('ms')}}
    if a.get('anchor', True) in (False, 'false', '0', 0, 'no'):
        out['anchored'] = False
        return out
    out['anchor'] = _anchor(a, _receipt_key(name), listing['cid'])
    out['anchored'] = not a.get('dry_run')
    out['key'] = _receipt_key(name)
    return out


def _hexbytes(s, what):
    s = str(s).strip().replace(' ', '')
    if s.startswith('0x'):
        s = s[2:]
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise StateError(f'{what} must be hex bytes', code='bad_args')             from None


TOOLS.update({
    'pq_vault': {
        'description': 'Off-chain encrypted storage. The CLIENT seals a file '
                       '(AES-256-GCM, PQV1 envelope — the console VAULT tab '
                       'or mod.py vault_put do it); the node stores only the '
                       'ciphertext under cid = SHA3-256(blob) and refuses '
                       'anything that does not look sealed; key= anchors the '
                       'cid on chain as a rent-paying 32-byte hash entry, and '
                       'the blob is kept as long as an anchor lives. '
                       'action=info | put (blob base64, key, wallet, days) | '
                       'get (cid or key -> ciphertext) | list.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('info | put | get | list (default info)',
                           enum=['info', 'put', 'get', 'list']),
            'blob': _str('put: base64 of the sealed PQV1 envelope'),
            'cid': _str('get: SHA3-256 of the blob, hex'),
            'key': _str('put: chain key to anchor the cid at (e.g. '
                        'vault/notes); get: resolve the cid from this key'),
            'wallet': _str('put: wallet that signs the anchor'),
            'days': _str('put: anchor lease in days'),
            'hours': _str('put: anchor lease in hours'),
            'dry_run': _bool('put: quote the anchor without signing'),
        }},
        'handler': _t_vault,
    },
    'pq_contract': {
        'description': 'Store AND run smart contracts. Code (wasm, sierra, '
                       'cairo or any source) goes into the vault PUBLIC and '
                       'content-addressed, and the chain key '
                       'contract/<name> commits to SHA3-256(code) — owned, '
                       'rented and sellable like any key; rewriting it is an '
                       'upgrade with the old versions in the block log. '
                       'Execution is OFF chain with on-chain proof: a wasm '
                       'contract exporting the pqc1 ABI (importless pure '
                       'state transition pqc_run(state,input)->state) is run '
                       'on this node by its owner, who signs a pqr1 receipt '
                       '{code, state_in, input, state_out} with their '
                       'post-quantum wallet; the receipt is anchored at '
                       'contract/<name>/receipt and ANYONE verifies it by '
                       'fetching the blobs by hash and re-running. '
                       'action=info | examples (bundled counter + ledger) | '
                       'deploy (name, code|code_b64|file|example, lang, '
                       'wallet, days) | show | list | run (name, input hex, '
                       'wallet, anchor) | verify (name or receipt cid) | '
                       'receipts (name -> the run log) | state (name -> '
                       'current state bytes). PROOF SYSTEMS (v0.15): how a '
                       'transition is accepted off chain is the owner\'s '
                       'choice — replay | wasm (a custom importless pqp1 '
                       'verifier blob) | quorum (k-of-n post-quantum '
                       'attestations). proofs (list systems, verifiers, '
                       'machines) | verifier_deploy (name, example|code_b64) '
                       '| policy (read) | set_policy (name, system, '
                       'verifier, k, signers) | solve (bundled prover) | '
                       'attest (a signer vouches) | check (dry) | submit '
                       '(state_out_b64 + proof_b64 or attestations -> a '
                       'signed, anchored receipt).',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('info | examples | deploy | show | list | run | '
                           'verify | receipts | state (default info)',
                           enum=['info', 'examples', 'deploy', 'show', 'list',
                                 'run', 'verify', 'receipts', 'state',
                                 'proofs', 'verifiers', 'verifier_deploy',
                                 'policy', 'set_policy', 'solve', 'attest',
                                 'check', 'submit']),
            'name': _str('contract name (key contract/<name>)'),
            'example': _str('deploy: bundled example to deploy instead of '
                            'code — counter | ledger'),
            'input': _str('run: input bytes as hex (the contract\'s own '
                          'encoding; see examples)'),
            'input_b64': _str('run: input bytes as base64'),
            'state_b64': _str('run: override the input state (default: the '
                              'anchored latest receipt\'s state_out, or '
                              'empty)'),
            'anchor': _bool('run: write contract/<name>/receipt (default '
                            'true; false = sign + store only)'),
            'receipt': _str('verify: receipt cid (default: the anchored '
                            'latest for name=)'),
            'limit': _str('receipts: how many back from the latest (50)'),
            'system': _str('policy/check/submit: replay | wasm | quorum',
                           enum=['replay', 'wasm', 'quorum']),
            'verifier': _str('wasm: verifier name or cid (verifier/<name>)'),
            'k': _str('quorum: signatures required'),
            'signers': _str('quorum: allowed signer addresses (list or '
                            'comma-separated)'),
            'state_out_b64': _str('check/submit/attest: the claimed next '
                                  'state, base64'),
            'proof_b64': _str('check/submit: the proof bytes, base64 '
                              '(wasm); quorum accepts the attestations '
                              'list here as JSON'),
            'attestations': {'type': 'array', 'items': {'type': 'object'},
                             'description': 'quorum: attestations from '
                                            'action=attest'},
            'lang': _str('deploy: wasm | sierra | cairo | source (default '
                         'source)', enum=['wasm', 'sierra', 'cairo',
                                          'source']),
            'code': _str('deploy: code as text'),
            'code_b64': _str('deploy: code as base64 (wasm)'),
            'file': _str('deploy: local path (local callers only)'),
            'wallet': _str('deploy/run: wallet that signs the anchor / '
                           'the receipt (must own contract/<name> to run)'),
            'days': _str('deploy/run: anchor lease in days'),
            'hours': _str('deploy/run: anchor lease in hours'),
            'dry_run': _bool('deploy/run: quote the anchor without signing'),
        }},
        'handler': _t_contract,
    },
})


# ── modular consensus ─────────────────────────────────────────────


def _list_arg(v):
    if v in (None, ''):
        return []
    if isinstance(v, str):
        v = v.strip()
        if v.startswith('['):
            return json.loads(v)
        return [x.strip() for x in v.split(',') if x.strip()]
    return list(v)


def _validator_view(v, n, epoch, stake):
    eng = CN.get(epoch['engine'])
    powers = eng.powers(epoch, stake)
    w = n._local(v['address'])
    return {'address': v['address'], 'name': v.get('name'),
            'scheme': v['scheme'], 'power': powers.get(v['address'], 0),
            'local': bool(w),
            'pq_score': PQSCORE.score_algo(v['scheme']).get('score')}


def _epoch_view(epoch, n):
    eng = CN.get(epoch['engine'])
    stake = n.state.balance
    out = {'engine': eng.name, 'title': eng.title, 'family': eng.family,
           'from_height': epoch['from_height'],
           'legacy': bool(epoch.get('legacy')),
           'require_pq': epoch.get('require_pq', True),
           'params': epoch.get('params', {}),
           'id': CN.epoch_id(epoch),
           'validators': [_validator_view(v, n, epoch, stake)
                          for v in epoch['validators']]}
    try:
        out['quorum'] = eng.quorum(epoch, stake)
    except StateError as e:
        out['quorum_error'] = e.message
    return out


def _make_validators(a, engine):
    """validators= names/objects, plus create=N fresh local wallets cycling
    through schemes= (mixing key types is the point)."""
    vals = _list_arg(a.get('validators'))
    count = int(a.get('create') or 0)
    if count:
        if count > CN.MAX_VALIDATORS:
            raise StateError(f'create at most {CN.MAX_VALIDATORS}',
                             code='bad_consensus')
        schemes = _list_arg(a.get('schemes')) or [K.SCHEME]
        for i in range(count):
            name = f'{engine}-v{i + 1}'
            scheme = schemes[i % len(schemes)]
            w = K.get(name, required=False)
            if w is None or w['scheme'] != scheme:
                K.create(name, scheme=scheme, overwrite=w is not None)
            vals.append(name)
    powers = a.get('powers')
    if powers:
        pw = [int(x) for x in _list_arg(powers)]
        vals = [dict(CN.validator_entry(v, a.get('require_pq', True)
                                        not in (False, 'false', '0')),
                     power=pw[i] if i < len(pw) else 1)
                for i, v in enumerate(vals)]
    return vals


def _t_consensus(a):
    n = node()
    action = (a.get('action') or 'info').lower()
    if action in ('engines', 'catalog'):
        return CN.catalog()
    if action == 'schedule':
        return {'epochs': [_epoch_view(e, n) for e in n.schedule],
                'pending': (_epoch_view(n.pending_migration, n)
                            if n.pending_migration else None)}
    if action == 'proposal':
        return n.proposal_view()
    if action == 'vote':
        if a.get('wallet') and not _is_owner():
            raise StateError('signing a vote with a wallet on this node is '
                             'owner-only — remote validators sign the '
                             'vote_message themselves and pass validator= '
                             'sig=', code='owner_only', status=403)
        return n.vote(validator=a.get('validator'), sig=a.get('sig'),
                      block_hash=a.get('hash'), wallet=a.get('wallet'))
    if action in ('migrate', 'plan'):
        engine = a.get('engine')
        if not engine:
            raise StateError('migrate needs engine= (pq_consensus '
                             'action=engines lists them)',
                             code='missing_arg')
        require_pq = a.get('require_pq', True) not in (False, 'false', '0',
                                                       0)
        params = a.get('params') or {}
        if isinstance(params, str):
            params = json.loads(params)
        for k in ('difficulty', 'min_stake'):
            if a.get(k) not in (None, ''):
                params[k] = int(a[k])
        if action == 'plan' or a.get('dry_run'):
            vals = _list_arg(a.get('validators'))
            ep = CN.make_epoch(engine, vals, params,
                               a.get('at') or n.blocks[-1]['height'] + 2,
                               require_pq)
            return {'dry_run': True, 'epoch': _epoch_view(ep, n)}
        vals = _make_validators(a, engine)
        if a.get('stake') not in (None, '', 0, '0'):
            amount = _amount(a['stake'])
            for v in vals:
                w = K.get(v if isinstance(v, str) else v.get('name') or
                          v['address'], required=False)
                if w:
                    n.send(n.validator, 'xfer', to=w['address'],
                           amount=amount)
            n.produce()
        ep = n.migrate(engine, vals, params, a.get('at'), require_pq)
        out = {'queued': True, 'epoch': _epoch_view(ep, n),
               'note': f'rides in the next block under '
                       f'{n.epoch()["engine"]}; {engine} rules from height '
                       f'{ep["from_height"]}'}
        if a.get('mine', True) not in (False, 'false', '0', 0):
            r = n.produce(force=True)
            out['mined'] = bool(r) and not r.get('proposed')
            if r:
                out['block'] = r['block']['height']
                out['proposed'] = bool(r.get('proposed'))
        return out
    if action == 'cancel':
        was = n.cancel_migration()
        return {'cancelled': bool(was), 'epoch': was}
    if action != 'info':
        raise StateError(f'unknown action {action!r} — info | engines | '
                         'schedule | migrate | plan | cancel | proposal | '
                         'vote', code='bad_action')
    cur = n.epoch()
    return {'active': _epoch_view(cur, n),
            'epochs': len(n.schedule),
            'pending': (_epoch_view(n.pending_migration, n)
                        if n.pending_migration else None),
            **n.proposal_view(),
            'note': n.consensus_note,
            'engines': [e.name for e in CN.REGISTRY.values()],
            'how': 'migrate engine=<name> validators=<wallets> (or create=N '
                   'schemes=ML-DSA-44,SLH-DSA-SHAKE-128f): the switch rides '
                   'in the next block, sealed by the CURRENT engine, and '
                   'replays from the log in pq_verify'}


TOOLS['pq_consensus'] = {
    'description': 'Modular consensus. The rule for who may extend the '
                   'chain is a pluggable engine — authority (one key), '
                   'roundrobin (rotating PoA), bft (> 2/3 voting power '
                   'signs, instant finality), pos (balance-weighted BFT, '
                   'SHA3 stake lottery), pow (SHA3 hashcash) or a plugin '
                   'in src/consensus.d/ — and every seal and vote is a '
                   'signature under one of the chain\'s post-quantum key '
                   'types, mixable per validator. Switching engines is a '
                   'migration written into a block header and authorised '
                   'by the engine it replaces; pq_verify rebuilds the '
                   'schedule from the log. action=info | engines | '
                   'schedule | plan (dry run) | migrate (engine, '
                   'validators or create=N + schemes, powers, difficulty, '
                   'min_stake, at, require_pq) | cancel | proposal (a '
                   'block waiting for votes + the message to sign) | vote '
                   '(validator + sig, or wallet= for a key held here).',
    'inputSchema': {'type': 'object', 'properties': {
        'action': _str('info | engines | schedule | plan | migrate | cancel '
                       '| proposal | vote (default info)',
                       enum=['info', 'engines', 'schedule', 'plan',
                             'migrate', 'cancel', 'proposal', 'vote']),
        'engine': _str('migrate/plan: authority | roundrobin | bft | pos | '
                       'pow | a plugin'),
        'validators': _str('migrate/plan: wallet names (comma list) or a '
                           'JSON list of {address, pk, scheme, power}'),
        'create': _int('migrate: create N local validator wallets '
                       '<engine>-v1..N'),
        'schemes': _str('migrate: key types to cycle through for create= '
                        '(comma list)'),
        'powers': _str('migrate: voting power per validator (comma list)'),
        'difficulty': _int('pow: leading zero bits (1-24, default 12)'),
        'min_stake': _int('pos: nq a validator needs to count'),
        'stake': _str('migrate: send each local validator this much PQ '
                      'from the node treasury first (pos)'),
        'at': _int('migrate: activation height (default next block + 1)'),
        'require_pq': _bool('validator keys must be quantum-safe and '
                            'many-time (default true)'),
        'mine': _bool('migrate: produce the carrying block now (default '
                      'true)'),
        'validator': _str('vote: validator address'),
        'sig': _str('vote: hex signature over vote_message'),
        'hash': _str('vote: the proposal hash being voted on'),
        'wallet': _str('vote: sign with this local wallet (owner)'),
    }},
    'handler': _t_consensus,
}

WRITE_TOOLS = {'pq_set', 'pq_del', 'pq_fund', 'pq_sweep', 'pq_list', 'pq_buy',
               'pq_transfer', 'pq_wallet', 'pq_faucet', 'pq_mine',
               'pq_keytype', 'pq_prim', 'pq_private'}

WRITE_TOOLS |= {'pq_vault', 'pq_contract', 'pq_consensus'}


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
        with open(os.path.join(os.path.dirname(HERE), 'config.json')) as f:
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
        api.serve(int(argv[i]) if i > 0 else
                  int(os.environ.get('PORT', os.environ.get('POSTQUANT_PORT',
                                                            51030))))
    else:
        serve_stdio()
