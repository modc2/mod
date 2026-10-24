"""Customizable off-chain proofs (pqp1): replay, wasm verifiers, quorums.

What must hold: a wasm verifier is admitted only if importless and pqp1; a
non-wasm machine advances only by a proof its owner's policy accepts; a bad
proof (wrong nonce, wrong state, forged/duplicate/foreign attestation) is
refused and leaves no receipt; the receipt re-checks without the policy and
without the prover; a tampered proof blob is refuted; policy is owner-only
and binds submissions; replay-era receipts verify unchanged.
"""

import base64
import hashlib
import json

import pytest

import contracts as C
import mcp as mcpsrv
import proofs as P
import vault as V
from state import StateError

pytestmark = pytest.mark.skipif(not (C.wasmvm.ENABLED and C.wasmvm.NODE),
                                reason='no wasm host on this box')


def call(tool, **kw):
    return mcpsrv.call_tool(tool, kw)


def b64(b):
    return base64.b64encode(b).decode()


def u64(n):
    return n.to_bytes(8, 'little').hex()


def mkwallet(name, **kw):
    call('pq_wallet', action='create', name=name, **kw)
    call('pq_faucet', wallet=name, amount='500')
    return name


@pytest.fixture(scope='module')
def owner():
    return mkwallet('prover-owner')


@pytest.fixture(scope='module')
def stranger():
    return mkwallet('prover-stranger')


def test_verifier_examples_are_pqp1():
    ex = {e['name']: e for e in P.verifier_examples()}
    assert set(ex) == {'counter-spec', 'pow-ladder'}
    for e in ex.values():
        assert e['pqp1']['abi'] == 'pqp1' and e['pqp1']['imports'] == 0
    # a pqc1 contract is not a verifier, and vice versa
    assert P.inspect(C.example_blob('counter'))['abi'] is None
    assert C.inspect(P.example_verifier('counter-spec'))['abi'] is None


def test_verifier_with_imports_is_refused(owner):
    imp = (b'\x00asm\x01\x00\x00\x00\x01\x04\x01\x60\x00\x00'
           b'\x02\x07\x01\x01a\x01b\x00\x00')
    with pytest.raises(StateError) as e:
        call('pq_contract', action='verifier_deploy', name='bad',
             code_b64=b64(imp), wallet=owner, hours=1)
    assert e.value.code == 'bad_code'


def test_pow_ladder_wasm_proof_end_to_end(owner, stranger):
    d = call('pq_contract', action='verifier_deploy', example='pow-ladder',
             wallet=owner, hours=4)
    assert d['pqp1']['abi'] == 'pqp1' and d['key'] == 'verifier/pow-ladder'
    call('pq_contract', action='deploy', example='ladder', name='ladder',
         wallet=owner, hours=4)
    # no policy, no code to run: nothing to check against yet
    with pytest.raises(StateError) as e:
        call('pq_contract', action='solve', name='ladder', input='01')
    assert e.value.code == 'bad_policy'
    with pytest.raises(StateError) as e:                 # owner only
        call('pq_contract', action='set_policy', name='ladder',
             system='wasm', verifier='pow-ladder', wallet=stranger)
    assert e.value.code == 'not_owner'
    pol = call('pq_contract', action='set_policy', name='ladder',
               system='wasm', verifier='pow-ladder', wallet=owner, hours=4)
    assert pol['policy']['system'] == 'wasm'
    assert call('pq_contract', action='policy',
                name='ladder')['policy']['verifier'] == d['cid']
    with pytest.raises(StateError):                      # read can't set
        call('pq_contract', action='policy', name='ladder', system='replay')

    chain = []
    for label in (b'first', b'second', b'third'):
        s = call('pq_contract', action='solve', name='ladder',
                 input=label.hex())
        assert s['system'] == 'wasm'
        chk = call('pq_contract', action='check', name='ladder',
                   input=label.hex(), state_out_b64=s['state_out_b64'],
                   proof_b64=s['proof_b64'])
        assert chk['valid'], chk
        r = call('pq_contract', action='submit', name='ladder',
                 input=label.hex(), state_out_b64=s['state_out_b64'],
                 proof_b64=s['proof_b64'], wallet=owner)
        assert r['anchored'] and r['proof']['system'] == 'wasm'
        # the work was real: 16 leading zero bits in the new state
        assert r['state_out_hex'].startswith('0000') or \
            int(r['state_out_hex'], 16) >> 240 == 0
        chain.append(r)
    assert [r['n'] for r in chain] == [0, 1, 2]
    assert chain[2]['state_in'] == chain[1]['state_out']
    v = call('pq_contract', action='verify', name='ladder')
    assert v['valid'], v
    assert v['proof']['system'] == 'wasm' and v['proof']['matches']
    assert v['checks']['proof_policy'] is True
    log = call('pq_contract', action='receipts', name='ladder')
    assert {x['proof'] for x in log['receipts']} == {'wasm'}


def test_bad_proofs_are_refused_and_leave_no_receipt(owner, stranger):
    before = call('pq_contract', action='receipts', name='ladder')
    s = call('pq_contract', action='solve', name='ladder', input='aa')
    proof = base64.b64decode(s['proof_b64'])
    bad_nonce = bytearray(proof)
    bad_nonce[0] ^= 1
    for kw in (
        dict(proof_b64=b64(bytes(bad_nonce))),                # wrong nonce
        dict(proof_b64=''),                                   # no proof
        dict(proof_b64=s['proof_b64'], state_out_b64=b64(b'\0' * 32)),
    ):
        args = dict(name='ladder', input='aa',
                    state_out_b64=s['state_out_b64'], wallet=owner)
        args.update(kw)
        with pytest.raises(StateError) as e:
            call('pq_contract', action='submit', **args)
        assert e.value.code == 'proof_rejected' and e.value.status == 422
    with pytest.raises(StateError) as e:                      # not owner
        call('pq_contract', action='submit', name='ladder', input='aa',
             state_out_b64=s['state_out_b64'], proof_b64=s['proof_b64'],
             wallet=stranger)
    assert e.value.code == 'not_owner'
    with pytest.raises(StateError) as e:                      # policy binds
        call('pq_contract', action='submit', name='ladder', input='aa',
             state_out_b64=s['state_out_b64'], proof_b64=s['proof_b64'],
             system='quorum', signers='x', wallet=owner)
    assert e.value.code == 'policy_mismatch'
    after = call('pq_contract', action='receipts', name='ladder')
    assert len(after['receipts']) == len(before['receipts'])


def test_tampered_proof_blob_is_refuted(owner):
    st = V.Store(mcpsrv.node().dir)
    cid, r = mcpsrv._latest_receipt('ladder', st)
    # swap the proof for a different one: hash mismatch -> refuted
    forged = json.loads(json.dumps(r))
    forged['over']['proof']['proof_sha3'] = hashlib.sha3_256(b'x').hexdigest()
    rep = P.check_receipt(forged, st)
    assert rep['matches'] in (False, None)
    # a stored proof that hashes right but is not a valid nonce
    junk = b'\x01' * 8
    st.put(junk, kind='state')
    forged = json.loads(json.dumps(r))
    forged['over']['proof'].update(proof_sha3=C.cid_of(junk), proof_bytes=8)
    rep = P.check_receipt(forged, st)
    assert rep['matches'] is False


def test_counter_spec_checks_a_run_it_never_executed(owner):
    call('pq_contract', action='verifier_deploy', example='counter-spec',
         wallet=owner, hours=4)
    call('pq_contract', action='deploy', example='counter', name='spec-ctr',
         wallet=owner, hours=4)
    call('pq_contract', action='set_policy', name='spec-ctr', system='wasm',
         verifier='counter-spec', wallet=owner, hours=4)
    # node refuses to run it itself: the policy wants a proof
    with pytest.raises(StateError) as e:
        call('pq_contract', action='run', name='spec-ctr', input='00',
             wallet=owner)
    assert e.value.code == 'policy_requires_proof'
    # a foreign implementation computed the step; the spec accepts it
    r = call('pq_contract', action='submit', name='spec-ctr', input='00',
             state_out_b64=b64(bytes.fromhex(u64(1))), wallet=owner)
    assert r['proof']['system'] == 'wasm' and r['state_out_hex'] == u64(1)
    with pytest.raises(StateError) as e:                     # wrong result
        call('pq_contract', action='submit', name='spec-ctr', input='00',
             state_out_b64=b64(bytes.fromhex(u64(5))), wallet=owner)
    assert e.value.code == 'proof_rejected'
    assert call('pq_contract', action='verify', name='spec-ctr')['valid']
    # the bundled solver agrees
    s = call('pq_contract', action='solve', name='spec-ctr',
             input='02' + u64(9))
    assert bytes.fromhex(s['state_out_hex']) == bytes.fromhex(u64(10))


def test_quorum_attestations(owner, stranger):
    signers = [mkwallet('q1'), mkwallet('q2'), mkwallet('q3')]
    addrs = [mcpsrv._wallet(w)['address'] for w in signers]
    # a source machine: nothing to run, the quorum decides what a step is
    call('pq_contract', action='deploy', name='oracle',
         code='price feed: state = latest price, steps vouched by 2 of 3',
         lang='source', wallet=owner, hours=4)
    call('pq_contract', action='set_policy', name='oracle', system='quorum',
         k=2, signers=addrs, wallet=owner, hours=4)
    out = b'42'
    args = dict(name='oracle', input=b'tick'.hex(), state_out_b64=b64(out))
    ats = [call('pq_contract', action='attest', wallet=w, **args)
           ['attestation'] for w in signers]
    # one vote is not a quorum; the same vote twice is still one
    for bad in ([ats[0]], [ats[0], ats[0]], []):
        with pytest.raises(StateError) as e:
            call('pq_contract', action='submit', wallet=owner,
                 attestations=bad, **args)
        assert e.value.code == 'proof_rejected'
    # an attestation from a non-signer does not count
    out_w = call('pq_contract', action='attest', wallet=stranger, **args)
    with pytest.raises(StateError):
        call('pq_contract', action='submit', wallet=owner,
             attestations=[ats[0], out_w['attestation']], **args)
    # a signature over a different result does not count
    other = call('pq_contract', action='attest', wallet=signers[1],
                 name='oracle', input=b'tick'.hex(),
                 state_out_b64=b64(b'43'))['attestation']
    with pytest.raises(StateError):
        call('pq_contract', action='submit', wallet=owner,
             attestations=[ats[0], other], **args)
    chk = call('pq_contract', action='check', attestations=ats[:2], **args)
    assert chk['valid'] and chk['k'] == 2 and len(chk['good']) == 2
    r = call('pq_contract', action='submit', wallet=owner,
             attestations=[ats[0], ats[2]], **args)
    assert r['proof']['system'] == 'quorum' and r['anchored']
    v = call('pq_contract', action='verify', name='oracle')
    assert v['valid'] and v['proof']['good']
    # the attestation is bound to the chain position: replaying it for the
    # next step (different prev) is refused
    with pytest.raises(StateError):
        call('pq_contract', action='submit', wallet=owner,
             attestations=[ats[0], ats[2]], **args)


def test_replay_era_receipts_and_replay_policy_still_work(owner):
    call('pq_contract', action='deploy', example='counter', name='old-ctr',
         wallet=owner, hours=4)
    r = call('pq_contract', action='run', name='old-ctr', input='00',
             wallet=owner)
    assert 'proof' not in r['receipt_blob']['over']
    v = call('pq_contract', action='verify', name='old-ctr')
    assert v['valid'] and v['proof']['system'] == 'replay'
    assert v['checks']['proof_policy'] is True
    # replay can also be a submitted claim: the node re-executes to accept
    ok = call('pq_contract', action='check', name='old-ctr', input='00',
              state_out_b64=b64(bytes.fromhex(u64(2))))
    assert ok['valid'] and ok['system'] == 'replay'
    bad = call('pq_contract', action='check', name='old-ctr', input='00',
               state_out_b64=b64(bytes.fromhex(u64(9))))
    assert not bad['valid']


def test_proofs_listing(owner):
    pr = call('pq_contract', action='proofs')
    assert pr['abi'] == 'pqp1'
    assert {s['id'] for s in pr['systems']} == {'replay', 'wasm', 'quorum'}
    names = {v['name'] for v in pr['verifiers']}
    assert {'counter-spec', 'pow-ladder'} <= names
    m = {x['name']: x for x in pr['machines']}
    assert m['ladder']['system'] == 'wasm' and m['oracle']['k'] == 2
    ex = call('pq_contract', action='examples')
    assert 'ladder' in {e['name'] for e in ex['examples']}
