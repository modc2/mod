"""Private key types, threshold composites, prims and the key type market.

The private tests put a hidden-scheme account on the wire and check the two
claims that matter: every envelope is the same size whatever it hides (so
the size leaks nothing), and a proof verifies without the answer ever
naming the scheme. Threshold tests pin the k-of-n arithmetic, because the
market's rarity and safety columns are read straight off it.
"""

import pytest

import keys as K
import mcp as mcpsrv
from pq import algos, complexity, compose, private

call = mcpsrv.call_tool


@pytest.fixture
def cleanup():
    before = set(algos.REGISTRY)
    yield
    for name in set(algos.REGISTRY) - before:
        algos.REGISTRY.pop(name, None)
        complexity.clear_cache(name)
    compose._write_store([s for s in compose._read_store()
                          if s['name'] in algos.REGISTRY])


def test_envelopes_are_one_size_whatever_they_hide():
    sizes = set()
    for inner in ('ML-DSA-44', 'ed25519', 'SLH-DSA-SHAKE-128f'):
        c, sk = private.keygen_inner(b'\x07' * 32, inner)
        sig = private.sign(sk, b'm', b'ctx')
        assert private.verify(c, b'm', sig, b'ctx')
        assert not private.verify(c, b'n', sig, b'ctx')
        sizes.add(len(sig))
    assert sizes == {private.ENVELOPE}


def test_envelope_rejects_tamper_and_foreign_commitment():
    c, sk = private.keygen_inner(b'\x01' * 32, 'ML-DSA-44')
    c2, _ = private.keygen_inner(b'\x02' * 32, 'ML-DSA-44')
    sig = bytearray(private.sign(sk, b'm'))
    assert not private.verify(c2, b'm', bytes(sig))
    sig[-100] ^= 1                    # padding region, inside the tag
    assert not private.verify(c, b'm', bytes(sig))


def test_private_cannot_be_hidden_or_composed(cleanup):
    with pytest.raises(ValueError):
        private.keygen_inner(b'\x00' * 32, 'private')
    with pytest.raises(compose.ComposeError):
        compose.create('t.hide', 'all', ['private', 'ed25519'])


def test_private_wallet_writes_and_hides_its_scheme():
    w = call('pq_private', {'action': 'create', 'name': 'ghost-t',
                            'inner': 'ed25519', 'overwrite': True})
    assert w['scheme'] == 'private' and 'inner' not in w
    call('pq_faucet', {'wallet': 'ghost-t', 'amount': '20'})
    r = call('pq_set', {'wallet': 'ghost-t', 'key': 'ghost-t/k',
                        'data': 'x'})
    assert r['status'] == 'included'
    assert r['witness_bytes'] == private.ENVELOPE + 32
    listed = call('pq_wallet', {'action': 'list'})['wallets']
    assert all('inner' not in x for x in listed)
    tx = call('pq_tx', {'hash': r['hash']})
    assert 'ed25519' not in str(tx)


def test_prove_verify_attest_and_open():
    call('pq_private', {'action': 'create', 'name': 'ghost-p',
                        'inner': 'ML-DSA-44', 'overwrite': True})
    p = call('pq_private', {'action': 'prove', 'wallet': 'ghost-p',
                            'message': 'hi'})
    v = call('pq_private', {'action': 'verify', 'commitment': p['commitment'],
                            'message': 'hi', 'proof': p['proof']})
    assert v['valid'] and v['scheme_revealed'] is False
    assert 'ML-DSA-44' not in str(v['attestation']['over'])
    assert not call('pq_private', {'action': 'verify',
                                   'commitment': p['commitment'],
                                   'message': 'ho', 'proof': p['proof']})[
        'valid']
    o = call('pq_private', {'action': 'open', 'wallet': 'ghost-p'})
    assert o['inner'] == 'ML-DSA-44'
    assert call('pq_private', {'action': 'check_open', **{
        k: o[k] for k in ('commitment', 'inner', 'salt', 'pk')}})['match']
    # A proof is bound to the attest context: it is not a tx witness.
    w = K.get('ghost-p')
    assert not algos.get('private').verify(
        bytes.fromhex(w['pk']), b'hi', bytes.fromhex(p['proof']),
        K.TX_CONTEXT)


def test_threshold_arithmetic_and_proofs(cleanup):
    t = compose.create('t.2of3', 'threshold',
                       ['ML-DSA-44', 'ed25519', 'SLH-DSA-SHAKE-128f'], k=2)
    assert t.composite == {'op': 'threshold', 'k': 2,
                           'parts': ['ML-DSA-44', 'ed25519',
                                     'SLH-DSA-SHAKE-128f']}
    assert t.quantum_safe              # only 1 part falls to Shor, k=2
    r = compose.proofs(t)
    assert r['ok'], r['failed']
    assert 'below_threshold_rejected' in [x['proof'] for x in r['proofs']]
    # 2-of-2 where one part is classical: Shor breaks one, still need two.
    t2 = compose.create('t.2of2c', 'threshold', ['ML-DSA-44', 'ed25519'], k=1)
    assert not t2.quantum_safe         # k=1: the curve alone suffices
    with pytest.raises(compose.ComposeError):
        compose.create('t.bad', 'threshold', ['ML-DSA-44', 'ed25519'], k=3)


def test_threshold_any_k_subset_verifies(cleanup):
    t = compose.create('t.sub', 'threshold',
                       ['ML-DSA-44', 'ed25519', 'ML-DSA-65'], k=2)
    pk, sk = t.keygen(b'\x05' * 32)
    parts = [algos.get(n) for n in t.composite['parts']]
    sig = bytes([0b110]) + parts[1].sign(sk[1], b'm') + \
        parts[2].sign(sk[2], b'm')
    assert t.verify(pk, b'm', sig)
    assert not t.verify(pk, b'm', bytes([0b111]) + sig[1:])


def test_market_prims_and_publish(cleanup):
    call('pq_wallet', {'action': 'create', 'name': 'author-t',
                       'overwrite': True})
    call('pq_faucet', {'wallet': 'author-t', 'amount': '20'})
    out = call('pq_keytype', {'action': 'create', 'name': 't.pub',
                              'op': 'all', 'parts': ['ML-DSA-44', 'ed25519'],
                              'author': 'author-t', 'publish': True})
    assert out['anchor']['key'] == 'keytype/t.pub'
    assert out['author'] == K.get('author-t')['address']
    m = call('pq_keymarket', {'sort': 'new', 'q': 't.pub'})
    item = next(i for i in m['items'] if i['name'] == 't.pub')
    assert item['anchor']['owner'] == out['author']
    assert 'cross-family' in item['badges']
    assert m['stats']['anonymity_set'] == len(private.cloak_set())
    prims = call('pq_prims', {})
    names = [p['name'] for p in prims['prims']]
    assert 'ML-DSA-44' in names and 't.pub' not in names
    assert 'private' not in names
    assert call('pq_prim', {'action': 'show', 'name': 'ed25519'})[
        'kind'] == 'plugin'


def test_delete_takes_its_anchor_with_it(cleanup):
    call('pq_wallet', {'action': 'create', 'name': 'author-d',
                       'overwrite': True})
    call('pq_faucet', {'wallet': 'author-d', 'amount': '20'})
    call('pq_keytype', {'action': 'create', 'name': 't.gone', 'op': 'any',
                        'parts': ['ML-DSA-44', 'ed25519'],
                        'author': 'author-d', 'publish': True})
    out = call('pq_keytype', {'action': 'delete', 'name': 't.gone'})
    assert out['unanchored']['key'] == 'keytype/t.gone'
    assert mcpsrv.node().state.entry('keytype/t.gone') is None
