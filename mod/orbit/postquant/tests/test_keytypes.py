"""The key type builder: composites, the store, the DAG and the proofs.

Composites are real key types — so the decisive tests are the ones that put
one on the wire: a wallet created under a hybrid, a transaction it signed,
a tampered copy refused. The arithmetic tests pin the two security stories
('all' = break every part, 'any' = break the weakest) because the catalog
card is a safety claim, and a wrong claim is worse than none.
"""

import hashlib

import pytest

import keys as K
import mcp as mcpsrv
from pq import algos, complexity, compose


@pytest.fixture
def cleanup():
    """Every composite a test creates dies with the test — names can never
    be rebound, so a leaked registration would poison the next test."""
    before = set(algos.REGISTRY)
    yield
    for name in set(algos.REGISTRY) - before:
        algos.REGISTRY.pop(name, None)
        complexity.clear_cache(name)
    compose._write_store([s for s in compose._read_store()
                          if s['name'] in algos.REGISTRY])


def test_all_composite_signs_and_verifies_a_tx(cleanup):
    compose.create('t.hybrid', 'all', ['ML-DSA-44', 'ed25519'])
    a = algos.get('t.hybrid')
    assert a.sizes['pk'] == algos.get('ML-DSA-44').sizes['pk'] + 32
    assert a.sizes['sig'] == algos.get('ML-DSA-44').sizes['sig'] + 64
    assert a.quantum_safe          # any part quantum-safe suffices for 'all'
    assert algos.refusal('t.hybrid') is None

    K.create('hyb', scheme='t.hybrid')
    w = K.get('hyb')
    tx = K.sign_tx(w, {'kind': 'xfer', 'to': w['address'],
                       'amount': 1, 'nonce': 0})
    assert K.verify_tx(tx)
    assert not K.verify_tx({**tx, 'body': {**tx['body'], 'amount': 2}})
    K.remove('hyb')


def test_all_needs_every_part(cleanup):
    """Corrupting either part's signature region must kill the composite —
    the proof battery's ablation, asserted directly."""
    compose.create('t.abl', 'all', ['ML-DSA-44', 'ed25519'])
    a = algos.get('t.abl')
    pk, sk = a.keygen(b'\x07' * 32)
    sig = a.sign(sk, b'm', b'c')
    assert a.verify(pk, b'm', sig, b'c')
    mldsa_sig = algos.get('ML-DSA-44').sizes['sig']
    for pos in (mldsa_sig // 2, mldsa_sig + 10):    # one byte in each part
        cut = bytearray(sig)
        cut[pos] ^= 1
        assert not a.verify(pk, b'm', bytes(cut), b'c')


def test_any_is_weakest_link(cleanup):
    c = compose.create('t.weak', 'any', ['ed25519', 'ML-DSA-44'])
    assert not c.quantum_safe       # 'any' is quantum-safe only if ALL are
    assert c.classical_bits == 126  # ed25519's Pollard rho, the weakest part
    assert algos.refusal('t.weak') is None   # 126 still clears the 2^100 floor
    pk, sk = c.keygen(b'\x01' * 32)
    sig = c.sign(sk, b'm', b'c')
    assert sig[0] == 0 and len(sig) == 65    # index byte + ed25519 sig
    assert c.verify(pk, b'm', sig, b'c')
    # part 0's signature relabelled as part 1 must not verify
    assert not c.verify(pk, b'm', bytes([1]) + sig[1:], b'c')


def test_nesting_makes_a_dag(cleanup):
    compose.create('t.inner', 'all', ['ML-DSA-44', 'ed25519'])
    compose.create('t.outer', 'any', ['t.inner', 'SLH-DSA-SHAKE-128f'])
    d = compose.dag()
    nodes = {n['name']: n for n in d['nodes']}
    assert nodes['ML-DSA-44']['depth'] == 0
    assert nodes['t.inner']['depth'] == 1
    assert nodes['t.outer']['depth'] == 2
    assert {'from': 't.inner', 'to': 't.outer', 'op': 'any'} in d['edges']
    # and the nested composite still signs end to end
    a = algos.get('t.outer')
    pk, sk = a.keygen(b'\x02' * 32)
    assert a.verify(pk, b'msg', a.sign(sk, b'msg', b''), b'')


def test_validation_refuses_the_nonsense(cleanup):
    with pytest.raises(compose.ComposeError):      # unknown part
        compose.create('t.bad1', 'all', ['ML-DSA-44', 'nope'])
    with pytest.raises(compose.ComposeError):      # one part is not a mix
        compose.create('t.bad2', 'all', ['ML-DSA-44'])
    with pytest.raises(compose.ComposeError):      # duplicate parts
        compose.create('t.bad3', 'all', ['ed25519', 'ed25519'])
    with pytest.raises(compose.ComposeError):      # bad op
        compose.create('t.bad4', 'xor', ['ML-DSA-44', 'ed25519'])
    with pytest.raises(compose.ComposeError):      # bad name
        compose.create('no spaces', 'all', ['ML-DSA-44', 'ed25519'])
    compose.create('t.ok', 'all', ['ML-DSA-44', 'ed25519'])
    with pytest.raises(compose.ComposeError):      # names cannot rebind
        compose.create('t.ok', 'any', ['ML-DSA-44', 'ed25519'])


def test_store_persists_and_reloads(cleanup):
    compose.create('t.kept', 'all', ['ML-DSA-44', 'ed25519'])
    assert any(s['name'] == 't.kept' for s in compose.specs())
    # a reload must re-register exactly what was stored, once
    algos.REGISTRY.pop('t.kept')
    complexity.clear_cache('t.kept')
    compose.load_store()
    assert algos.maybe('t.kept') is not None
    compose.load_store()                       # idempotent — no duplicate error
    assert not any(e['keytype'] == 't.kept' for e in compose.STORE_ERRORS)


def test_delete_protects_dependents_and_history(cleanup):
    compose.create('t.base', 'all', ['ML-DSA-44', 'ed25519'])
    compose.create('t.top', 'any', ['t.base', 'ML-DSA-44'])
    with pytest.raises(compose.ComposeError):      # t.top depends on it
        compose.delete('t.base')
    with pytest.raises(compose.ComposeError):      # builtins are not deletable
        compose.delete('ML-DSA-44')
    compose.delete('t.top')
    compose.delete('t.base')
    assert algos.maybe('t.base') is None
    assert not any(s['name'] == 't.base' for s in compose.specs())


def test_proofs_pass_honest_fail_dishonest(cleanup):
    compose.create('t.proven', 'all', ['ML-DSA-44', 'ed25519'])
    r = compose.proofs(algos.get('t.proven'))
    assert r['ok'] and not r['failed']
    names = [p['proof'] for p in r['proofs']]
    assert 'part_load_bearing:ML-DSA-44' in names
    assert 'part_load_bearing:ed25519' in names

    liar = algos.SigAlgo(
        't.liar', keygen=lambda s: (hashlib.sha3_256(s).digest(), s),
        sign=lambda sk, m, ctx=b'': b'x' * 64,
        verify=lambda pk, m, sig, ctx=b'': True,
        sizes={'pk': 32, 'sig': 64, 'seed': 32},
        family='fake', quantum_safe=False)
    algos.register(liar)
    liar.origin = 'test'
    r = compose.proofs(liar)
    assert not r['ok'] and 'tampered_message' in r['failed']
    assert algos.refusal('t.liar') is not None     # and the gate agrees


def test_the_tool_surface(cleanup):
    created = mcpsrv.call_tool('pq_keytype', {
        'action': 'create', 'name': 't.tool',
        'op': 'all', 'parts': 'ML-DSA-44 + ed25519'})   # string form parses
    assert created['accepted'] and created['composite']['op'] == 'all'

    listed = mcpsrv.call_tool('pq_keytype', {})
    assert any(k['name'] == 't.tool' for k in listed['keytypes'])

    shown = mcpsrv.call_tool('pq_keytype', {'action': 'show', 'name': 't.tool'})
    assert shown['accepted'] and shown['accounts_on_chain'] == 0

    d = mcpsrv.call_tool('pq_keytype_dag', {})
    assert any(e == {'from': 'ed25519', 'to': 't.tool', 'op': 'all'}
               for e in d['edges'])

    tested = mcpsrv.call_tool('pq_keytype_test', {'scheme': 't.tool'})
    assert tested['ok'] and tested['sizes']['sig'] == \
        algos.get('t.tool').sizes['sig']

    gone = mcpsrv.call_tool('pq_keytype', {'action': 'delete',
                                           'name': 't.tool'})
    assert gone['deleted'] == 't.tool'


def test_delete_refused_while_an_account_witnesses():
    """Once a composite has witnessed on chain, deleting it would leave
    history this node could no longer re-judge — the tool must refuse.
    No cleanup fixture on purpose: t.used's witness is in a block now, so
    the key type must stay registered for any later replay in this run."""
    from state import StateError
    mcpsrv.call_tool('pq_keytype', {'action': 'create', 'name': 't.used',
                                    'op': 'all',
                                    'parts': ['ML-DSA-44', 'ed25519']})
    mcpsrv.call_tool('pq_wallet', {'action': 'create', 'name': 'usedw',
                                   'scheme': 't.used'})
    mcpsrv.call_tool('pq_faucet', {'wallet': 'usedw', 'amount': '50',
                                   'mine': True})
    mcpsrv.call_tool('pq_set', {'key': 'test/used', 'data': 'hi',
                                'wallet': 'usedw', 'days': 1, 'mine': True})
    with pytest.raises(StateError) as e:
        mcpsrv.call_tool('pq_keytype', {'action': 'delete', 'name': 't.used'})
    assert e.value.code == 'keytype_in_use'
    mcpsrv.call_tool('pq_wallet', {'action': 'remove', 'name': 'usedw'})
