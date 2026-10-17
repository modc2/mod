"""The Cairo-verified key type, and forging a key type straight into hiding.

CAIRO-MSS-SHA256 is only worth stacking if (a) it is a real signature —
binds message, context and key — (b) the Cairo program and the Python twin
the chain runs agree on the same witness, and (c) it actually raises the
bar inside a composite. The forge-private tests pin the builder's
"make it private" switch: one call forges the key type and opens a private
wallet whose scheme never reaches the chain.
"""

import shutil

import pytest

import keys as K
import mcp as mcpsrv
from pq import algos, cairomss, complexity, compose, private

call = mcpsrv.call_tool
HAS_SCARB = bool(cairomss._scarb())


@pytest.fixture
def cleanup():
    before = set(algos.REGISTRY)
    yield
    for name in set(algos.REGISTRY) - before:
        algos.REGISTRY.pop(name, None)
        complexity.clear_cache(name)
    compose._write_store([s for s in compose._read_store()
                          if s['name'] in algos.REGISTRY])


@pytest.fixture(scope='module')
def kp():
    pk, sk = cairomss.keygen(bytes(range(32)))
    return pk, sk, cairomss.sign(sk, b'm', b'ctx')


def test_registered_accepted_and_sized():
    a = algos.get(cairomss.NAME)
    assert a.quantum_safe and algos.allowed(cairomss.NAME)
    assert a.sizes == {'pk': 64, 'sig': 9376, 'seed': 32}
    assert complexity.summary(a)['ok']
    assert private.fits(cairomss.NAME)          # it can hide in the crowd


def test_signature_binds_message_context_and_key(kp):
    pk, sk, sig = kp
    assert len(sig) == cairomss.SIG_BYTES
    assert cairomss.verify(pk, b'm', sig, b'ctx')
    assert not cairomss.verify(pk, b'n', sig, b'ctx')
    assert not cairomss.verify(pk, b'm', sig, b'xtc')
    pk2, _ = cairomss.keygen(b'\x09' * 32)
    assert not cairomss.verify(pk2, b'm', sig, b'ctx')
    for i in (0, 40, 5000, len(sig) - 1):         # R, a chain, an auth node
        bad = bytearray(sig)
        bad[i] ^= 1
        assert not cairomss.verify(pk, b'm', bytes(bad), b'ctx')
    assert not cairomss.verify(pk, b'm', sig[:-1], b'ctx')
    # deterministic: same key, same message, same witness
    assert cairomss.sign(sk, b'm', b'ctx') == sig


def test_wallet_writes_under_cairo():
    w = call('pq_wallet', {'action': 'create', 'name': 'cairo-t',
                           'scheme': cairomss.NAME, 'overwrite': True})
    assert w['scheme'] == cairomss.NAME
    call('pq_faucet', {'wallet': 'cairo-t', 'amount': '20'})
    r = call('pq_set', {'wallet': 'cairo-t', 'key': 'cairo-t/k',
                        'data': 'x'})
    assert r['status'] == 'included'


def test_cairo_in_all_composite_stacks_assumptions(cleanup):
    t = compose.create('t.cairo3', 'all', ['ML-DSA-44', 'SLH-DSA-SHAKE-128f',
                                           cairomss.NAME])
    assert t.quantum_safe
    r = compose.proofs(t)
    assert r['ok'], r['failed']
    # ablation: drop the Cairo part's signature and the whole thing fails
    assert any(p['proof'].startswith('ablate') or 'part' in p['proof']
               for p in r['proofs'])


def test_forge_private_in_one_call():
    # no cleanup: its witness lands in the shared test chain, and pq_verify
    # replays every witness, so the key type has to stay registered
    r = call('pq_keytype', {'action': 'create', 'name': 't.hidden',
                            'op': 'all', 'parts': ['ML-DSA-44',
                                                   cairomss.NAME],
                            'private': True, 'wallet': 'ghost-forge',
                            'overwrite': True})
    assert r['accepted'] and r['private']['hidden'] == 't.hidden'
    w = K.get('ghost-forge')
    assert w['scheme'] == 'private'
    assert 't.hidden' not in str(call('pq_wallet', {'action': 'list'})[
        'wallets'])
    call('pq_faucet', {'wallet': 'ghost-forge', 'amount': '20'})
    tx = call('pq_set', {'wallet': 'ghost-forge', 'key': 'ghost/f',
                         'data': 'y'})
    assert tx['status'] == 'included'
    assert tx['witness_bytes'] == private.ENVELOPE + 32
    assert 't.hidden' not in str(call('pq_tx', {'hash': tx['hash']}))


def test_forge_without_private_opens_no_wallet(cleanup):
    r = call('pq_keytype', {'action': 'create', 'name': 't.public',
                            'op': 'all', 'parts': ['ML-DSA-44', 'ed25519']})
    assert 'private' not in r and 'private_error' not in r


def test_pq_cairo_info():
    i = call('pq_cairo', {})
    assert i['scheme'] == cairomss.NAME
    assert i['params']['leaves'] == 1 << 24
    assert i['cairo']['program'].endswith('lib.cairo')
    assert i['cairo']['program_sha3']


@pytest.mark.skipif(not HAS_SCARB, reason='scarb not installed')
def test_cairo_vm_agrees_with_python(kp):
    pk, sk, sig = kp
    good = cairomss.cairo_verify(pk, b'm', sig, b'ctx')
    assert good['valid'] and good['python'] and good['agree'], good
    bad = bytearray(sig)
    bad[5000] ^= 1
    r = cairomss.cairo_verify(pk, b'm', bytes(bad), b'ctx')
    assert not r['valid'] and r['agree'], r
    r = cairomss.cairo_verify(pk, b'n', sig, b'ctx')
    assert not r['valid'] and r['agree'], r


@pytest.mark.skipif(not HAS_SCARB, reason='scarb not installed')
def test_pq_cairo_check_tool():
    r = call('pq_cairo', {'action': 'check', 'message': 'x'})
    assert r['vm']['valid'] and r['vm']['agree']
    r = call('pq_cairo', {'action': 'check', 'message': 'x', 'tamper': True})
    assert not r['vm']['valid'] and r['vm']['agree']
