"""Recursive chains: a whole tree inside one slot.

A key on this chain is 32 bytes of value and a rent meter. This module takes
that one slot and puts ANOTHER CHAIN behind it — its own key space, its own
writers, its own Merkle root — so that the parent carries a 32-byte
commitment and nothing else, however many people are writing into the child.

    postquant-1                        the root chain, L1
      store["chain/app"] = C1          32 bytes. One entry. One rent meter.
         |
         +-- app                       a subchain: its own key/value tree
               store["orders/9"]       ...written by anyone the policy allows
               store["chain/eu"] = C2  ...and its own slot holding ANOTHER
                  |                       chain, which may hold another
                  +-- app/eu

C1 is not a pointer. It is SHA3-256 over the child's signed head — which
commits to the child's Merkle root, to the batch of transactions that
produced it and to the previous head — so the parent's 32 bytes commit,
transitively, to every leaf of every descendant. Rewriting one leaf eight
levels down changes C1.

WHAT ONE SLOT COSTS
    A key on L1 costs write gas on its bytes plus rent per byte-hour,
    forever. A subchain of 10,000 keys costs the parent exactly one 32-byte
    hash entry — the same bill as a key holding one hash. The chain's
    scarce resource is state bytes on every node's disk, and a subchain
    spends 32 of them no matter how big it gets. That is the whole trick,
    and it is why this is not a layer-2 bolted on: the L1 was already a
    market in slots, and a slot can hold a tree.

HOW MANY PEOPLE INTERFACE ONE SLOT
    Everybody who writes into the child signs their own transaction with
    their own post-quantum wallet (pqx1, context postquant/subchain-tx/v1,
    per-writer nonces). A sequencer orders a batch of them, applies them to
    the child's state, and signs the resulting head (pqs1). It cannot forge a
    write — every tx in the batch carries its author's signature and the
    batch blob is public and content-addressed, so anyone refetches it and
    re-derives the root. A sequencer can only CENSOR, and censorship is
    visible: the batch that left you out is signed and published.

A PROOF IS A CHAIN OF PROOFS
    proof() returns one bundle walking from a leaf in the deepest child up to
    the L1 state root: a Merkle path inside each tree, and between two trees
    a single hash — the parent slot's value IS the child's head commitment.
    verify_bundle() checks the whole ladder with nothing but SHA3-256: no
    node, no network, no trust in whoever served it. Depth costs
    log2(keys) hashes per level and one hash per hop.

WHY THIS MAKES APPCHAINS CHEAP
    An appchain here is not a new network with a new validator set to
    bootstrap, a new token to list and a bridge to get wrong. It is one key
    on a chain whose validator fits in the footprint pq_node prints, plus a
    sequencer the app already runs. Security of the ANCHOR is the L1's;
    availability and ordering are the app's own problem, stated honestly
    below. That trade is the appchain thesis, and recursion is what makes it
    compose: an appchain can sell slots to its own appchains.

WHAT THIS DOES NOT GIVE YOU
    Not a validity rollup. The L1 does not execute subchain transactions and
    does not know whether a head is a legal successor of the one before it —
    it knows only that the slot's owner wrote it. A verifier gets that
    honestly: replay() refetches every batch blob by hash and re-derives
    every root from genesis, and a head whose batches do not reproduce it is
    simply false. Data availability is the sequencer's promise plus whoever
    else keeps the blobs; a batch nobody kept is a root nobody can expand.
"""

from __future__ import annotations

import json
import os
import re
import time

import keys as K
from pq import algos
from state import (StateError, canonical, check_proof, is_hex, kv_leaf,
                   merkle_path, merkle_root, sha3)

BATCH_V = 'pqs1'
TX_V = 'pqx1'
BATCH_CTX = b'postquant/subchain-batch/v1'
TX_CTX = b'postquant/subchain-tx/v1'
HEAD_DOMAIN = b'pqs1-head\x00'

# A child chain declares itself in its parent's key space under this prefix,
# on the L1 and inside another subchain alike — one rule, every level.
SLOT_PREFIX = 'chain/'

MAX_DEPTH = int(os.environ.get('POSTQUANT_SUBCHAIN_DEPTH', 8))
MAX_KEY_BYTES = 256
MAX_VALUE_BYTES = int(os.environ.get('POSTQUANT_SUBCHAIN_VALUE_MAX', 1024))
MAX_BATCH = int(os.environ.get('POSTQUANT_SUBCHAIN_BATCH', 1024))
MAX_MEMPOOL = 4096
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')

DATA_DIR = os.path.expanduser(os.environ.get('POSTQUANT_DATA_DIR',
                                             '~/.mod/postquant'))

ZERO = '00' * 32


def _dir():
    return os.path.join(DATA_DIR, 'subchains')


def _slug(path: str) -> str:
    return sha3(path.encode()).hex()[:24]


def _file(path: str) -> str:
    return os.path.join(_dir(), _slug(path) + '.json')


# ── the record ────────────────────────────────────────────────────


def exists(path: str) -> bool:
    return os.path.exists(_file(path))


def load(path: str) -> dict:
    try:
        with open(_file(path)) as f:
            return json.load(f)
    except FileNotFoundError:
        raise StateError(f'no subchain {path!r} — pq_subchain action=list',
                         code='no_subchain', status=404) from None
    except ValueError as e:
        raise StateError(f'subchain {path!r} is corrupt on this node: {e}',
                         code='bad_state', status=500) from None


def save(sc: dict):
    os.makedirs(_dir(), mode=0o700, exist_ok=True)
    tmp = _file(sc['path']) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(sc, f, indent=1, sort_keys=True)
    os.replace(tmp, _file(sc['path']))


def registry() -> list:
    """Every subchain this node holds, parents before children."""
    out = []
    try:
        names = sorted(os.listdir(_dir()))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith('.json'):
            continue
        try:
            with open(os.path.join(_dir(), fn)) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda s: (s['path'].count('/'), s['path']))


def depth(path: str) -> int:
    """0 = the L1 itself, 1 = a subchain anchored in an L1 slot."""
    return path.count('/')


def parent_path(path: str) -> str:
    return path.rsplit('/', 1)[0]


def name_of(path: str) -> str:
    return path.rsplit('/', 1)[1]


def slot_key(path: str) -> str:
    """The key in the PARENT's store that holds this chain."""
    return SLOT_PREFIX + name_of(path)


# ── creating one ──────────────────────────────────────────────────


def create(name, *, parent, sequencer, writers='open', allow=(), note=''):
    """Declare a subchain under `parent` (an L1 chain_id or another
    subchain's path). Nothing is anchored yet — a chain with no batch has no
    head to commit to, so the first seal is what reaches the parent."""
    name = str(name or '').strip().lower()
    if not NAME_RE.match(name):
        raise StateError('a subchain name is 1-32 chars of a-z, 0-9, - or _ '
                         '— it becomes the key chain/<name> in the parent',
                         code='bad_name')
    parent = str(parent).strip().strip('/')
    if not parent:
        raise StateError('parent is the L1 chain_id or a subchain path',
                         code='missing_arg')
    path = f'{parent}/{name}'
    if depth(path) > MAX_DEPTH:
        raise StateError(f'{path} would be {depth(path)} levels deep, cap is '
                         f'{MAX_DEPTH} — each level is a hash hop in every '
                         'proof', code='too_deep')
    if depth(parent) >= 1 and not exists(parent):
        raise StateError(f'no parent subchain {parent!r} on this node',
                         code='no_subchain', status=404)
    if exists(path):
        raise StateError(f'{path} already exists here', code='exists')
    if writers not in ('open', 'allowlist', 'owner'):
        raise StateError('writers is open | allowlist | owner',
                         code='bad_args')
    allow = [a for a in allow if a]
    for a in allow:
        if not K.valid_address(a):
            raise StateError(f'{a!r} is not an address', code='bad_address')
    sc = {
        'v': BATCH_V, 'path': path, 'name': name,
        'parent': parent, 'depth': depth(path),
        'slot': slot_key(path),
        'sequencer': sequencer,
        'policy': {'writers': writers, 'allow': allow},
        'note': str(note or '')[:200],
        'created': int(time.time()),
        'n': 0, 'head': None, 'root': merkle_root([]).hex(),
        'batches': [], 'mempool': [], 'store': {}, 'nonces': {},
        'anchored': None,
    }
    save(sc)
    return sc


def drop(path: str):
    sc = load(path)
    kids = children(path)
    if kids:
        raise StateError(f'{path} still holds {len(kids)} subchain(s): '
                         f'{", ".join(k["path"] for k in kids)}',
                         code='has_children')
    os.remove(_file(path))
    return {'dropped': path, 'batches': sc['n'], 'keys': len(sc['store'])}


def children(path: str) -> list:
    return [s for s in registry() if s['parent'] == path]


# ── transactions ──────────────────────────────────────────────────


def _check_key(key):
    if not isinstance(key, str) or not key:
        raise StateError('key must be a non-empty string', code='bad_key')
    if len(key.encode()) > MAX_KEY_BYTES:
        raise StateError(f'key is over {MAX_KEY_BYTES} bytes', code='bad_key')
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in key):
        raise StateError('key must not contain control characters',
                         code='bad_key')


def _check_value(value, kind):
    if kind not in ('hash', 'raw'):
        raise StateError("value_kind is 'hash' or 'raw'", code='bad_value')
    if not is_hex(value):
        raise StateError('value must be hex', code='bad_value')
    n = len(value) // 2
    if kind == 'hash' and n != 32:
        raise StateError(f'a hash value is exactly 32 bytes, got {n}',
                         code='bad_value')
    if n > MAX_VALUE_BYTES:
        raise StateError(f'value is {n} bytes, cap {MAX_VALUE_BYTES} — a '
                         'subchain stores commitments too', code='bad_value')


def next_nonce(sc, address) -> int:
    pending = [t['body']['nonce'] for t in sc['mempool']
               if t['body']['from'] == address]
    return max([sc['nonces'].get(address, 0)] + pending) + 1


def make_tx(wallet, path, *, key, value, value_kind='hash', nonce=None,
            sc=None):
    """Sign one write into a subchain. The signature covers the chain PATH,
    so a witness minted for app/eu can never be replayed into app or into an
    L1 transaction — different context string, different message. An empty
    value deletes the key (its owner only)."""
    _check_key(key)
    value = '' if value is None else str(value)
    if value == '':
        value_kind = 'raw'
    _check_value(value, value_kind)
    sc = sc or load(path)
    body = {'v': TX_V, 'chain': path, 'key': key, 'value': value,
            'value_kind': value_kind, 'from': wallet['address'],
            'nonce': int(nonce if nonce is not None
                         else next_nonce(sc, wallet['address'])),
            'ts': int(time.time())}
    algo = algos.get(wallet['scheme'])
    sig = algo.sign(K.secret_key(wallet), canonical(body), TX_CTX)
    tx = {'body': body, 'sig': sig.hex(), 'scheme': wallet['scheme'],
          'pk': wallet['pk']}
    tx['hash'] = tx_hash(tx)
    return tx


def tx_hash(tx) -> str:
    return sha3(b'pqx\x00', canonical(tx['body']),
                bytes.fromhex(tx.get('sig', ''))).hex()


def verify_tx(tx) -> bool:
    """Same four checks as an L1 witness: accepted key type, signature over
    the exact body, address derived from the key, key type gate."""
    try:
        body = tx['body']
        if body.get('v') != TX_V:
            return False
        scheme = tx['scheme']
        if not algos.allowed(scheme):
            return False
        pk = bytes.fromhex(tx['pk'])
        if K.address(pk, scheme) != body['from']:
            return False
        return bool(algos.get(scheme).verify(pk, canonical(body),
                                             bytes.fromhex(tx['sig']), TX_CTX))
    except Exception:                                      # noqa: BLE001
        return False


def may_write(sc, address) -> bool:
    p = sc['policy']
    if p['writers'] == 'open':
        return True
    if p['writers'] == 'owner':
        return address == sc['sequencer']
    return address in p['allow'] or address == sc['sequencer']


def submit(path, tx):
    """Queue a signed write. Verified here AND again when the batch is
    applied, because the second check is the one a stranger replays."""
    sc = load(path)
    if tx['body']['chain'] != path:
        raise StateError(f'this witness is for {tx["body"]["chain"]!r}, not '
                         f'{path!r}', code='wrong_chain')
    if not verify_tx(tx):
        raise StateError('signature does not verify over this transaction',
                         code='bad_witness')
    if not may_write(sc, tx['body']['from']):
        raise StateError(f'{tx["body"]["from"]} may not write to {path} '
                         f'(writers={sc["policy"]["writers"]})',
                         code='not_allowed', status=403)
    if tx['body']['key'].startswith(SLOT_PREFIX):
        raise StateError(f'{SLOT_PREFIX}* is the slot namespace — a child '
                         'chain writes it by sealing, not by hand',
                         code='reserved_key')
    if len(sc['mempool']) >= MAX_MEMPOOL:
        raise StateError('subchain mempool is full — seal a batch',
                         code='mempool_full', status=429)
    if any(t['hash'] == tx['hash'] for t in sc['mempool']):
        return {'queued': False, 'hash': tx['hash'], 'note': 'already queued'}
    sc['mempool'].append(tx)
    save(sc)
    return {'queued': True, 'hash': tx['hash'], 'chain': path,
            'pending': len(sc['mempool'])}


# ── the tree ──────────────────────────────────────────────────────


def sub_leaf(path, key, e) -> bytes:
    """One leaf of a subchain's tree. Domain-separated by the chain path, so
    the same key/value in two chains is two different leaves and a path
    lifted from one tree proves nothing in another."""
    return sha3(b'sub\x00', path.encode(), key.encode(),
                sha3(bytes.fromhex(e['value'] or '')),
                e['owner'].encode(),
                int(e['nonce']).to_bytes(8, 'big'),
                (e.get('value_kind') or '').encode())


def leaves(sc) -> list:
    return [(k, sub_leaf(sc['path'], k, sc['store'][k]))
            for k in sorted(sc['store'])]


def root_of(sc) -> str:
    return merkle_root([lf for _, lf in leaves(sc)]).hex()


def proof_in(sc, key) -> dict:
    lvs = leaves(sc)
    idx = next((i for i, (k, _) in enumerate(lvs) if k == key), None)
    if idx is None:
        raise StateError(f'nothing at {key!r} in {sc["path"]}',
                         code='no_entry', status=404)
    flat = [lf for _, lf in lvs]
    return {'key': key, 'leaf': flat[idx].hex(), 'index': idx,
            'leaves': len(flat), 'path': merkle_path(flat, idx),
            'root': merkle_root(flat).hex()}


# ── batches ───────────────────────────────────────────────────────


def head_commit(header) -> str:
    """What the parent slot holds: 32 bytes over the child's signed head.
    The header carries the root, the batch and the previous head, so this one
    hash commits to the child's entire history."""
    return sha3(HEAD_DOMAIN, canonical(header)).hex()


def _apply(sc, txs, ts):
    """Apply an ordered batch. Pure in sc: returns (store, nonces, applied,
    rejected) and touches nothing until the caller commits."""
    store = {k: dict(v) for k, v in sc['store'].items()}
    nonces = dict(sc['nonces'])
    applied, rejected = [], []
    for tx in txs:
        body = tx['body']
        who = body['from']
        why = None
        if not verify_tx(tx):
            why = 'bad witness'
        elif body['chain'] != sc['path']:
            why = 'wrong chain'
        elif not may_write(sc, who):
            why = 'not allowed to write here'
        elif int(body['nonce']) <= nonces.get(who, 0):
            why = (f'nonce {body["nonce"]} is not above '
                   f'{nonces.get(who, 0)} for {who}')
        elif (body['key'] in store
              and store[body['key']]['owner'] != who
              and who != sc['sequencer']):
            # open means anyone may TAKE a free key, not take yours
            why = f'{body["key"]} belongs to {store[body["key"]]["owner"]}'
        if why:
            rejected.append({'hash': tx.get('hash'), 'why': why})
            continue
        nonces[who] = int(body['nonce'])
        if body['value'] == '':                 # an empty value is a delete
            store.pop(body['key'], None)
        else:
            store[body['key']] = {
                'value': body['value'],
                'value_kind': body.get('value_kind') or 'hash',
                'owner': who, 'nonce': int(body['nonce']),
                'updated': ts, 'batch': sc['n'] + 1}
        applied.append(tx)
    return store, nonces, applied, rejected


def seal(path, wallet, *, txs=None, now=None):
    """Order the mempool into a batch, apply it, sign the new head.

    Returns the batch blob (public, content-addressed — store it in the
    vault) and the commitment the parent slot has to be set to. Anchoring
    into another SUBCHAIN happens here, because that is just a transaction;
    anchoring into the L1 is a priced write and the caller does it.
    """
    sc = load(path)
    if wallet['address'] != sc['sequencer']:
        raise StateError(f'{path} is sequenced by {sc["sequencer"]} — only '
                         'that wallet signs its heads', code='not_sequencer',
                         status=403)
    now = int(now or time.time())
    pool = list(txs if txs is not None else sc['mempool'])[:MAX_BATCH]
    store, nonces, applied, rejected = _apply(sc, pool, now)

    # A child chain's slot is written by the child itself, never by hand:
    # every chain under this one folds its CURRENT head into this batch. The
    # values go in the header, so replaying this batch reproduces the root
    # without holding the children.
    kids = [k for k in children(path) if k['head']]
    slots = {k['slot']: {'value': k['head'], 'owner': k['sequencer'],
                         'nonce': k['n'], 'chain': k['path']} for k in kids}
    for name, s in slots.items():
        store[name] = {'value': s['value'], 'value_kind': 'hash',
                       'owner': s['owner'], 'nonce': s['nonce'],
                       'updated': now, 'batch': sc['n'] + 1,
                       'subchain': s['chain']}

    flat = [sub_leaf(path, k, store[k]) for k in sorted(store)]
    root = merkle_root(flat).hex()
    tx_hashes = [t['hash'] for t in applied]
    header = {
        'v': BATCH_V, 'chain': path, 'parent': sc['parent'],
        'slot': sc['slot'], 'n': sc['n'] + 1,
        'prev': sc['head'] or ZERO, 'root': root,
        'txs_root': merkle_root([bytes.fromhex(h) for h in tx_hashes]).hex(),
        'count': len(applied), 'keys': len(store),
        'slots': slots, 'writers': sc['policy']['writers'],
        'sequencer': sc['sequencer'], 'ts': now,
    }
    algo = algos.get(wallet['scheme'])
    sig = algo.sign(K.secret_key(wallet), canonical(header), BATCH_CTX)
    batch = {'over': header, 'txs': applied, 'by': wallet['address'],
             'scheme': wallet['scheme'], 'pk': wallet['pk'],
             'sig': sig.hex(), 'context': BATCH_CTX.decode()}
    commit = head_commit(header)
    blob = canonical(batch)

    sc['store'], sc['nonces'] = store, nonces
    sc['n'] = header['n']
    sc['root'], sc['head'] = root, commit
    sc['mempool'] = [t for t in sc['mempool']
                     if t['hash'] not in set(tx_hashes)
                     and t['hash'] not in {r['hash'] for r in rejected}]
    sc['batches'].append({'n': header['n'], 'commit': commit, 'root': root,
                          'count': len(applied), 'keys': len(store),
                          'blob_sha3': sha3(blob).hex(), 'bytes': len(blob),
                          'ts': now, 'header': header})
    save(sc)

    out = {'chain': path, 'n': header['n'], 'root': root, 'commit': commit,
           'applied': len(applied), 'rejected': rejected,
           'keys': len(store), 'header': header, 'batch': batch,
           'blob_sha3': sha3(blob).hex(), 'blob_bytes': len(blob),
           'slot': sc['slot'], 'parent': sc['parent']}

    # Every child this batch folded in is now committed by this chain's
    # root: that is what "anchored" means one level down.
    for kid in kids:
        kid['anchored'] = kid['head']
        kid['anchored_at'] = now
        save(kid)
    out['folded'] = [{'chain': k['path'], 'slot': k['slot'],
                      'head': k['head']} for k in kids]

    if sc['depth'] > 1:
        # Recursion: the parent is a subchain, so anchoring is one more
        # level of exactly this — the parent folds this head into its own
        # tree when IT seals. Nothing reaches the L1 until the ancestor at
        # depth 1 seals and its owner pays for 32 bytes.
        out['anchor'] = {'scope': 'subchain', 'chain': sc['parent'],
                         'key': sc['slot'], 'value': commit,
                         'pending': True,
                         'note': f'seal {sc["parent"]} to fold this head '
                                 'into it; it reaches the L1 when the '
                                 'ancestor at depth 1 anchors'}
    else:
        out['anchor'] = {'scope': 'l1', 'key': sc['slot'], 'value': commit,
                         'note': 'set this L1 key to this value — 32 bytes, '
                                 'whatever the child weighs'}
    return out


def mark_anchored(path, commit, tx=None):
    sc = load(path)
    sc['anchored'] = commit
    sc['anchor_tx'] = tx
    sc['anchored_at'] = int(time.time())
    save(sc)
    return sc


def parse_batch(blob: bytes) -> dict:
    try:
        b = json.loads(blob.decode())
        assert b['over']['v'] == BATCH_V
        for k in ('chain', 'n', 'root', 'prev', 'txs_root'):
            assert k in b['over']
        for k in ('by', 'scheme', 'pk', 'sig', 'txs'):
            assert k in b
        return b
    except (ValueError, KeyError, AssertionError, AttributeError, TypeError):
        raise StateError('not a pqs1 batch', code='bad_batch') from None


def check_batch(batch: dict) -> dict:
    """Is this head signed by whom it says, over what it says? Hashes only —
    no node, no state."""
    over = batch['over']
    out = {'chain': over['chain'], 'n': over['n'], 'root': over['root'],
           'commit': head_commit(over), 'by': batch['by'],
           'scheme': batch['scheme']}
    algo = algos.get(batch['scheme'])
    if algo is None:
        return {**out, 'sig_valid': False,
                'reason': f'unknown key type {batch["scheme"]}'}
    try:
        pk = bytes.fromhex(batch['pk'])
        addr_ok = K.address(pk, batch['scheme']) == batch['by']
        sig_ok = bool(algo.verify(pk, canonical(over),
                                  bytes.fromhex(batch['sig']), BATCH_CTX))
    except Exception as e:                                 # noqa: BLE001
        return {**out, 'sig_valid': False, 'reason': f'malformed: {e}'}
    txs = batch.get('txs') or []
    hashes = [tx_hash(t) for t in txs]
    txs_ok = merkle_root([bytes.fromhex(h) for h in hashes]).hex() \
        == over['txs_root']
    bad = [h for t, h in zip(txs, hashes) if not verify_tx(t)]
    out.update(sig_valid=sig_ok and addr_ok, address_matches_pk=addr_ok,
               sequencer_is_signer=batch['by'] == over.get('sequencer'),
               txs=len(txs), txs_root_matches=txs_ok,
               witnesses_valid=not bad, bad_witnesses=bad)
    if not sig_ok:
        out['reason'] = 'head signature does not verify'
    elif not addr_ok:
        out['reason'] = 'pk does not derive by='
    elif not txs_ok:
        out['reason'] = 'the batch does not hash to txs_root'
    elif bad:
        out['reason'] = f'{len(bad)} transaction(s) in the batch do not verify'
    return out


def replay(path, fetch) -> dict:
    """Rebuild this chain from its published batches and nothing else.

    `fetch(sha3) -> bytes` gets a batch blob from anywhere — this node's
    vault, another node, a file someone emailed you. Every root is
    re-derived, every witness re-checked, every head re-linked to the one
    before it. A sequencer that rewrote history fails here, loudly.
    """
    sc = load(path)
    store, nonces, prev = {}, {}, ZERO
    steps, ok = [], True
    for b in sc['batches']:
        step = {'n': b['n'], 'blob_sha3': b['blob_sha3']}
        try:
            blob = fetch(b['blob_sha3'])
        except Exception as e:                             # noqa: BLE001
            step.update(ok=False, reason=f'blob unavailable: {e}')
            steps.append(step)
            ok = False
            continue
        if sha3(blob).hex() != b['blob_sha3']:
            step.update(ok=False, reason='blob does not hash to its cid')
            steps.append(step)
            ok = False
            continue
        batch = parse_batch(blob)
        over = batch['over']
        sig = check_batch(batch)
        shadow = {'path': path, 'policy': sc['policy'],
                  'sequencer': over.get('sequencer', sc['sequencer']),
                  'store': store, 'nonces': nonces, 'n': over['n'] - 1}
        store, nonces, applied, rejected = _apply(shadow, batch['txs'],
                                                  over['ts'])
        for name, s in (over.get('slots') or {}).items():
            # A slot holds a child chain's head. The header carries it, so
            # this root re-derives without the child — and the child's own
            # replay is what proves the head it published was honest.
            store[name] = {'value': s['value'], 'value_kind': 'hash',
                           'owner': s['owner'], 'nonce': s['nonce'],
                           'updated': over['ts'], 'batch': over['n'],
                           'subchain': s.get('chain')}
        root = merkle_root([sub_leaf(path, k, store[k])
                            for k in sorted(store)]).hex()
        step.update(ok=(sig['sig_valid'] and sig.get('txs_root_matches')
                        and root == over['root'] and over['prev'] == prev
                        and not rejected),
                    sig_valid=sig['sig_valid'], root=root,
                    root_claimed=over['root'], root_matches=root == over['root'],
                    prev_matches=over['prev'] == prev, applied=len(applied),
                    rejected=rejected, commit=head_commit(over))
        if not step['ok'] and 'reason' not in step:
            step['reason'] = sig.get('reason') or (
                'root does not reproduce' if root != over['root']
                else 'head does not link to the previous one')
        prev = head_commit(over)
        ok = ok and step['ok']
        steps.append(step)
    return {'chain': path, 'batches': len(sc['batches']), 'ok': ok,
            'head': prev if sc['batches'] else None,
            'head_matches': (prev == sc['head']) if sc['batches'] else True,
            'steps': steps,
            'note': 'every root re-derived from the published batches; a '
                    'head that does not reproduce is false, whoever signed it'}


# ── the recursive proof ───────────────────────────────────────────


def ancestry(path) -> list:
    """[deepest ... depth-1] — the subchains between a leaf and the L1."""
    out, p = [], path
    while depth(p) >= 1 and exists(p):
        sc = load(p)
        out.append(sc)
        p = sc['parent']
    return out


def proof(path, key, *, l1_proof=None) -> dict:
    """One bundle from a leaf in `path` up to the L1 state root.

    Each hop is a Merkle path inside one tree plus the single hash that is
    that tree's head — and the hop above holds exactly that hash in a slot.
    verify_bundle() checks the ladder without this node.
    """
    line = ancestry(path)
    if not line:
        raise StateError(f'no subchain {path!r}', code='no_subchain',
                         status=404)
    hops, want_key, want_value = [], key, None
    for i, sc in enumerate(line):
        e = sc['store'].get(want_key)
        if e is None:
            raise StateError(f'nothing at {want_key!r} in {sc["path"]}',
                             code='no_entry', status=404)
        if want_value is not None and e['value'] != want_value:
            raise StateError(f'{sc["path"]} slot {want_key} holds '
                             f'{e["value"][:16]}…, child head is '
                             f'{want_value[:16]}… — this node is mid-seal; '
                             'seal the parent', code='stale_anchor',
                             status=409)
        p = proof_in(sc, want_key)
        if p['root'] != sc['root']:
            raise StateError(f'{sc["path"]} root moved mid-proof',
                             code='race', status=409)
        hops.append({
            'level': 'subchain', 'chain': sc['path'], 'depth': sc['depth'],
            'key': want_key, 'entry': {k: e[k] for k in
                                       ('value', 'value_kind', 'owner',
                                        'nonce')},
            'leaf': p['leaf'], 'index': p['index'], 'leaves': p['leaves'],
            'path': p['path'], 'root': p['root'],
            'head': sc['head'], 'batch': sc['n'],
            # the signed head, so a verifier can check for itself that this
            # 32-byte commitment is the one that covers this root
            'header': (sc['batches'][-1].get('header')
                       if sc['batches'] else None),
            'sequencer': sc['sequencer'],
            'hashes': len(p['path']),
        })
        want_key, want_value = sc['slot'], sc['head']
        if sc['anchored'] and sc['anchored'] != sc['head']:
            hops[-1]['warning'] = ('this chain has sealed past what its '
                                   'parent holds — anchor it')

    top = line[-1]
    if l1_proof is None:
        return {'chain': path, 'key': key, 'hops': hops, 'depth': len(hops),
                'root': None, 'complete': False,
                'note': f'stops at the L1 slot {top["slot"]} — pass the L1 '
                        'Merkle proof to close the ladder'}
    lp = l1_proof(top['slot'])
    entry = lp['entry']
    if entry['value'] != top['head']:
        raise StateError(f'the L1 slot {top["slot"]} holds '
                         f'{entry["value"][:16]}…, this chain\'s head is '
                         f'{top["head"][:16]}… — anchor the chain first',
                         code='stale_anchor', status=409)
    hops.append({
        'level': 'l1', 'chain': top['parent'], 'depth': 0,
        'key': top['slot'], 'entry': entry, 'leaf': lp['leaf'],
        'index': lp['index'], 'leaves': lp['leaves'], 'path': lp['path'],
        'root': lp['root'], 'hashes': len(lp['path']),
    })
    return {
        'chain': path, 'key': key, 'hops': hops, 'depth': len(hops),
        'root': lp['root'], 'complete': True,
        'hashes': sum(h['hashes'] for h in hops) + len(hops) - 1,
        'bytes': len(canonical(hops)),
        'note': 'a light client with the L1 state root and nothing else '
                'checks this with SHA3-256 alone',
    }


def verify_bundle(bundle) -> dict:
    """Check a recursive proof. PURE: hashes and the bundle, nothing else.

    Run this anywhere — another node, a browser, a verifier that has never
    heard of this module's state directory. What it proves: the value at
    `key` in the deepest chain is committed, hop by hop, by the L1 state
    root you already trust.
    """
    hops = (bundle or {}).get('hops') or []
    if not hops:
        return {'ok': False, 'reason': 'no hops in this bundle'}
    checks, ok, child_head = [], True, None
    for h in hops:
        c = {'chain': h.get('chain'), 'key': h.get('key'),
             'level': h.get('level')}
        e = h.get('entry') or {}
        try:
            if h['level'] == 'l1':
                leaf = kv_leaf(h['key'], e).hex()
            else:
                leaf = sub_leaf(h['chain'], h['key'], e).hex()
        except Exception as ex:                            # noqa: BLE001
            c.update(ok=False, reason=f'cannot rebuild the leaf: {ex}')
            checks.append(c)
            ok = False
            break
        c['leaf_matches'] = leaf == h.get('leaf')
        c['path_matches'] = check_proof(leaf, h.get('path') or [],
                                        h.get('root'))
        # the hop below handed us a head; this hop's slot must hold it
        c['carries_child'] = (child_head is None
                              or e.get('value') == child_head)
        # ...and that head must be the commitment over THIS tree's root,
        # which is the hop from one chain to the next
        head_ok = True
        if h.get('level') != 'l1':
            hdr = h.get('header')
            head_ok = bool(hdr) and head_commit(hdr) == h.get('head') \
                and hdr.get('root') == h.get('root')
            c['head_commits_root'] = head_ok
        c['hashes'] = len(h.get('path') or [])
        c['ok'] = bool(c['leaf_matches'] and c['path_matches']
                       and c['carries_child'] and head_ok)
        if not c['ok']:
            c['reason'] = ('the leaf is not this key/value'
                           if not c['leaf_matches'] else
                           'the Merkle path does not reach the root'
                           if not c['path_matches'] else
                           f'this slot does not hold the child head '
                           f'{str(child_head)[:16]}…'
                           if not c['carries_child'] else
                           'the head does not commit to this root')
        ok = ok and c['ok']
        checks.append(c)
        # what the hop above has to be holding: this tree's head, which
        # commits to the root we just proved against
        child_head = h.get('head') if h['level'] != 'l1' else None
    top = hops[-1]
    return {
        'ok': bool(ok),
        'key': hops[0].get('key'), 'chain': hops[0].get('chain'),
        'value': (hops[0].get('entry') or {}).get('value'),
        'depth': len(hops),
        'root': top.get('root'),
        'anchored_on': top.get('chain') if top.get('level') == 'l1' else None,
        'complete': top.get('level') == 'l1',
        'hashes': sum(c.get('hashes', 0) for c in checks),
        'hops': checks,
        'note': 'checked with SHA3-256 only — if ok and the root is the L1 '
                'state root you trust, that value is committed by this chain'
        if ok else 'this bundle does not hold up',
    }


# ── the picture ───────────────────────────────────────────────────


def tree(l1_chain_id, l1_entry=None) -> dict:
    """The whole recursive structure this node knows, as a tree."""
    by_parent = {}
    for sc in registry():
        by_parent.setdefault(sc['parent'], []).append(sc)

    def node_for(sc):
        anchored = sc.get('anchored')
        return {
            'path': sc['path'], 'name': sc['name'], 'depth': sc['depth'],
            'slot': sc['slot'], 'parent': sc['parent'],
            'keys': len(sc['store']), 'batches': sc['n'],
            'root': sc['root'], 'head': sc['head'],
            'anchored': anchored, 'in_sync': anchored == sc['head'],
            'pending': len(sc['mempool']),
            'writers': sc['policy']['writers'],
            'allow': sc['policy']['allow'],
            'sequencer': sc['sequencer'],
            'parent_bytes': 32,
            'children': [node_for(k) for k in by_parent.get(sc['path'], [])],
        }

    kids = [node_for(s) for s in by_parent.get(l1_chain_id, [])]

    def count(ns):
        return sum(1 + count(n['children']) for n in ns)

    def keys(ns):
        return sum(n['keys'] + keys(n['children']) for n in ns)

    def deepest(ns):
        return max([0] + [1 + deepest(n['children']) for n in ns])

    return {
        'chain': l1_chain_id, 'root': True, 'children': kids,
        'subchains': count(kids), 'keys_below': keys(kids),
        'depth': deepest(kids),
        'l1_slots': len(kids), 'l1_bytes': 32 * len(kids),
        'entries': l1_entry or {},
    }
