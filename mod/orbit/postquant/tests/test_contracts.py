"""Running wasm contracts off chain, proving it with receipts.

What must hold: a pqc1 blob runs through the hash-enforced host and its
output is a pure function of (code, state, input); a run leaves a signed
receipt whose chain anyone can replay; a receipt that lies — tampered state,
tampered input, forged signer, code swapped — is refuted; only the owner of
contract/<name> may anchor a run; reverts are receipts too; a blob with
imports is not a contract.
"""

import base64
import hashlib
import json

import pytest

import contracts as C
import mcp as mcpsrv
import vault as V
from state import StateError

pytestmark = pytest.mark.skipif(not (C.wasmvm.ENABLED and C.wasmvm.NODE),
                                reason='no wasm host on this box')


def call(tool, **kw):
    return mcpsrv.call_tool(tool, kw)


def u64(n):
    return n.to_bytes(8, 'little').hex()


@pytest.fixture(scope='module')
def owner():
    call('pq_wallet', action='create', name='runner')
    call('pq_faucet', wallet='runner', amount='500')
    return 'runner'


@pytest.fixture(scope='module')
def stranger():
    call('pq_wallet', action='create', name='stranger')
    call('pq_faucet', wallet='stranger', amount='100')
    return 'stranger'


@pytest.fixture(scope='module')
def counter(owner):
    r = call('pq_contract', action='deploy', example='counter', name='ctr',
             wallet=owner, hours=4)
    assert r['pqc1']['abi'] == 'pqc1' and r['pqc1']['runnable']
    return 'ctr'


def test_examples_are_listed_and_inspectable():
    ex = call('pq_contract', action='examples')
    names = {e['name'] for e in ex['examples']}
    assert {'counter', 'ledger'} <= names
    for e in ex['examples']:
        assert e['abi'] == 'pqc1' and e['imports'] == 0
        assert 'pqc_run' in e['exports'] and e['cid']


def test_counter_runs_and_receipts_chain(owner, counter):
    r1 = call('pq_contract', action='run', name=counter, input='00',
              wallet=owner)
    assert r1['ret'] == 8 and not r1['reverted'] and r1['anchored']
    assert r1['n'] == 0 and r1['prev'] is None
    assert r1['state_out_hex'] == u64(1)
    assert r1['state_in'] == hashlib.sha3_256(b'').hexdigest()
    r2 = call('pq_contract', action='run', name=counter,
              input='02' + u64(40), wallet=owner)
    assert r2['n'] == 1 and r2['prev'] == r1['receipt']
    assert r2['state_out_hex'] == u64(41)
    assert r2['state_in'] == r1['state_out']
    st = call('pq_contract', action='state', name=counter)
    assert st['state_hex'] == u64(41) and st['n'] == 1
    log = call('pq_contract', action='receipts', name=counter)
    assert [x['n'] for x in log['receipts']] == [1, 0]
    assert log['receipts'][0]['receipt'] == r2['receipt']
    show = call('pq_contract', action='show', name=counter)
    assert show['runs'] == 2 and show['receipt'] == r2['receipt']


def test_verify_replays_the_anchored_receipt(owner, counter):
    v = call('pq_contract', action='verify', name=counter)
    assert v['valid'], v
    assert v['checks'] == {k: True for k in v['checks']}
    assert v['replay']['replayed'] and v['replay']['matches']
    assert v['signature']['sig_valid']
    # an older receipt in the chain is still valid, just not the latest
    log = call('pq_contract', action='receipts', name=counter)
    old = log['receipts'][-1]['receipt']
    v0 = call('pq_contract', action='verify', receipt=old)
    assert v0['valid'] and v0['n'] == 0
    assert v0['checks']['anchored_latest'] is False


def test_revert_is_a_receipt_that_keeps_the_state(owner, counter):
    before = call('pq_contract', action='state', name=counter)['state_hex']
    r = call('pq_contract', action='run', name=counter, input='03',
             wallet=owner)                                    # reset -> 0
    assert r['state_out_hex'] == u64(0)
    r = call('pq_contract', action='run', name=counter, input='01',
             wallet=owner)                                    # dec below 0
    assert r['reverted'] and r['ret'] == -4
    assert r['state_out'] == r['state_in'] and r['state_out_hex'] == u64(0)
    v = call('pq_contract', action='verify', name=counter)
    assert v['valid'] and v['replay']['reverted']
    assert before == u64(41)


def test_tampered_receipt_is_refuted(owner, stranger, counter):
    log = call('pq_contract', action='receipts', name=counter)
    cid = log['receipts'][0]['receipt']
    st = V.Store(mcpsrv.node().dir)
    r = json.loads(st.get(cid))
    # 1. claim a different output: signature breaks
    forged = json.loads(json.dumps(r))
    forged['over']['state_out'] = hashlib.sha3_256(b'lie').hexdigest()
    assert not C.check_signature(forged)['sig_valid']
    # 2. keep the signature, re-sign nothing: replay must still refute a
    #    receipt whose input does not hash to input_sha3
    forged = json.loads(json.dumps(r))
    forged['over']['input'] = '00'
    rep = C.replay(forged, st)
    assert rep['matches'] is False
    # 3. a stranger signing a correct run is not the owner
    w = mcpsrv._wallet(stranger)
    inp = bytes.fromhex(r['over']['input'])
    state_in = b'' if r['over']['state_in'] == C.cid_of(b'') \
        else st.get(r['over']['state_in'])
    res = C.execute(st._p(r['over']['code']), r['over']['code'], state_in, inp)
    rr = C.make_receipt(w, chain_id=r['over']['chain_id'], contract=counter,
                        code_cid=r['over']['code'], n=r['over']['n'],
                        prev=r['over']['prev'], state_in=state_in, inp=inp,
                        result=res)
    blob = C.receipt_blob(rr)
    st.put(blob, kind='receipt')
    v = call('pq_contract', action='verify', receipt=C.cid_of(blob))
    assert v['checks']['signature'] and v['checks']['replays']
    assert v['checks']['signer_is_owner'] is False and not v['valid']


def test_only_the_owner_runs(owner, stranger, counter):
    with pytest.raises(StateError) as e:
        call('pq_contract', action='run', name=counter, input='00',
             wallet=stranger)
    assert e.value.code == 'not_owner'


def test_ledger_mint_transfer_burn(owner):
    call('pq_contract', action='deploy', example='ledger', name='bank',
         wallet=owner, hours=4)
    a, b = 'aa' * 32, 'bb' * 32
    r = call('pq_contract', action='run', name='bank',
             input='00' + a + u64(100), wallet=owner)
    assert r['state_out_hex'] == a + u64(100)
    r = call('pq_contract', action='run', name='bank',
             input='01' + a + b + u64(40), wallet=owner)
    assert r['state_out_hex'] == a + u64(60) + b + u64(40)
    r = call('pq_contract', action='run', name='bank',
             input='01' + b + a + u64(41), wallet=owner)
    assert r['reverted'] and r['ret'] == -4
    r = call('pq_contract', action='run', name='bank',
             input='02' + b + u64(40), wallet=owner)
    assert r['state_out_hex'] == a + u64(60)
    v = call('pq_contract', action='verify', name='bank')
    assert v['valid']
    log = call('pq_contract', action='receipts', name='bank')
    assert len(log['receipts']) == 4 and log['receipts'][1]['reverted']


def test_unanchored_run_is_signed_but_not_pointed_at(owner, counter):
    before = call('pq_contract', action='state', name=counter)
    r = call('pq_contract', action='run', name=counter, input='00',
             wallet=owner, anchor=False)
    assert r['anchored'] is False and r['receipt']
    after = call('pq_contract', action='state', name=counter)
    assert after['receipt'] == before['receipt']
    v = call('pq_contract', action='verify', receipt=r['receipt'])
    assert v['valid'] and v['checks']['anchored_latest'] is False


def test_blob_with_imports_or_without_abi_is_not_a_contract(owner):
    imp = (b'\x00asm\x01\x00\x00\x00'
           b'\x01\x04\x01\x60\x00\x00'                    # type () -> ()
           b'\x02\x07\x01\x01a\x01b\x00\x00')             # import a.b func
    info = C.inspect(imp)
    assert info['abi'] is None and 'import' in info['reason']
    call('pq_contract', action='deploy', name='imp', lang='wasm',
         code_b64=base64.b64encode(imp).decode(), wallet=owner, hours=1)
    with pytest.raises(StateError) as e:
        call('pq_contract', action='run', name='imp', input='00',
             wallet=owner)
    assert e.value.code == 'bad_code'
    # a key-type blob (pq2 ABI) is not a contract either
    with open(C.os.path.join(C.EXAMPLES_DIR, '..', 'lamport.wasm'),
              'rb') as f:
        info = C.inspect(f.read())
    assert info['abi'] is None and 'pqc_run' in info['reason']


def test_receipts_keep_their_blobs_alive(owner, counter, monkeypatch):
    monkeypatch.setattr(V, 'GRACE', 0)
    live = mcpsrv._live_anchors()
    log = call('pq_contract', action='receipts', name=counter)
    for x in log['receipts']:
        assert x['receipt'] in live and x['code'] in live
        assert x['state_out'] in live
    st = V.Store(mcpsrv.node().dir)
    dropped = st.prune(live, now=C.time.time() + 10)
    assert not any(x['receipt'] in dropped for x in log['receipts'])
