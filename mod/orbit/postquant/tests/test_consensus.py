"""Modular consensus: engines are swappable, migrations ride in headers and
replay from the log, and every seal and vote is a post-quantum signature.

Each test runs its own chain in its own directory, so a migration in one test
never changes the rules another test's chain is produced under.
"""

import itertools
import json
import os
import tempfile

import pytest

import chain as C
import consensus as CN
import keys as K
import mcp as mcpsrv
from state import StateError

_n = itertools.count()


def fresh():
    d = tempfile.mkdtemp(prefix='pq-consensus-')
    return C.Node(chain_id=f'pq-cn-{next(_n)}', data_dir=d)


def wallet(name, scheme='ML-DSA-44'):
    w = K.get(name, required=False)
    if w is None or w['scheme'] != scheme:
        K.create(name, scheme=scheme, overwrite=w is not None)
    return K.get(name)


def mine(n, k=1, now=None):
    out = []
    for _ in range(k):
        out.append(n.produce(now=now, force=True))
    return out


def test_legacy_chain_is_authority_epoch_and_unchanged():
    n = fresh()
    mine(n, 2)
    assert n.epoch()['engine'] == 'authority'
    assert n.epoch().get('legacy')
    tip = n.block()
    assert 'consensus' not in tip['header']          # hashes as they always were
    assert n.verify()['ok']


def test_roundrobin_with_mixed_pq_key_types_rotates():
    n = fresh()
    vals = [wallet('rr-a'), wallet('rr-b', 'ML-DSA-65'),
            wallet('rr-c', 'SLH-DSA-SHAKE-128f')]
    ep = n.migrate('roundrobin', [w['name'] for w in vals])
    r = n.produce(force=True)                        # carries the migration
    assert r['block']['migrate']['engine'] == 'roundrobin'
    assert n.epoch()['engine'] == 'roundrobin'
    assert ep['from_height'] == r['block']['height'] + 1
    seen = []
    for _ in range(3):
        b = n.produce(force=True)['block']
        assert b['consensus'] == 'roundrobin'
        seen.append(b['proposer'])
    assert set(seen) == {w['address'] for w in vals}
    v = n.verify()
    assert v['ok'], v['problems']
    assert v['epochs'] == 2


def test_bft_local_quorum_commits_and_tampered_commit_fails_audit():
    n = fresh()
    names = [wallet(f'bft-{i}')['name'] for i in range(4)]
    n.migrate('bft', names)
    n.produce(force=True)
    b = n.produce(force=True)
    assert not b.get('proposed')
    blk = n.block()
    assert len(blk['commit']['votes']) == 4
    assert n.verify()['ok']
    # Strip votes below 2/3 in the log: the audit must refuse the block.
    lines = open(n.blocks_file).read().splitlines()
    last = json.loads(lines[-1])
    last['commit']['votes'] = last['commit']['votes'][:2]
    lines[-1] = json.dumps(last, separators=(',', ':'))
    open(n.blocks_file, 'w').write('\n'.join(lines) + '\n')
    v = n.verify()
    assert not v['ok']
    assert any('voting power' in p for p in v['problems'])


def test_bft_waits_for_remote_votes_then_commits():
    n = fresh()
    local = wallet('bft-local')
    remote = [wallet(f'bft-remote-{i}') for i in range(2)]
    entries = [CN.validator_entry(w['name']) for w in remote]
    n.migrate('bft', ['bft-local'] + entries)
    n.produce(force=True)                            # migration block
    # The remote keys leave this node: only their signatures come back.
    held = [dict(w) for w in remote]
    for w in remote:
        K.remove(w['name'])
    try:
        # A silent remote proposer is skipped once its round times out.
        later = n.blocks[-1]['timestamp'] + 3 * CN.ROUND_SECONDS
        r = n.produce(now=later, force=True)
        assert r['block']['proposer'] == local['address']
        assert r['proposed'] and r['power'] < r['needed']
        view = n.proposal_view()['proposal']
        sig = CN.sign_vote(held[0], n.chain_id, view['height'],
                           view['round'], view['hash'])
        with pytest.raises(StateError):
            n.vote(validator=held[1]['address'], sig=sig)   # wrong signer
        out = n.vote(validator=held[0]['address'], sig=sig,
                     block_hash=view['hash'])
        assert not out['committed']                       # 2 of 3 is not > 2/3
        sig2 = CN.sign_vote(held[1], n.chain_id, view['height'],
                            view['round'], view['hash'])
        out = n.vote(validator=held[1]['address'], sig=sig2)
        assert out['committed']
        assert n.verify()['ok']
    finally:
        for w in held:
            K.create(w['name'], seed=w['seed'], scheme=w['scheme'],
                     overwrite=True)
    assert local


def test_pos_lottery_weights_by_balance():
    n = fresh()
    a, b = wallet('pos-a'), wallet('pos-b')
    funder = n.validator
    for w, amt in ((a, 500), (b, 100)):
        n.send(funder, 'xfer', to=w['address'], amount=amt * 10**9)
    n.produce()
    n.migrate('pos', ['pos-a', 'pos-b'])
    n.produce(force=True)
    for _ in range(4):
        r = n.produce(force=True)
        assert not r.get('proposed')
        assert r['block']['proposer'] in (a['address'], b['address'])
    assert n.verify()['ok']
    picks = [CN.lottery(bytes([i]) * 8, {'x': 5, 'y': 1}) for i in range(200)]
    assert picks.count('x') > picks.count('y') * 2


def test_pow_then_back_to_authority_replays():
    n = fresh()
    n.migrate('pow', params={'difficulty': 8})
    n.produce(force=True)
    b = n.produce(force=True)['block']
    assert b['consensus'] == 'pow' and b['difficulty'] == 8
    assert CN.leading_zero_bits(bytes.fromhex(n.block()['hash'])) >= 8
    n.migrate('authority', [n.validator['name']])
    n.produce(force=True)
    n.produce(force=True)
    assert n.epoch()['engine'] == 'authority'
    v = n.verify()
    assert v['ok'], v['problems']
    assert v['epochs'] == 3
    # Reopening the node rebuilds the schedule from the headers.
    again = C.Node(chain_id=n.chain_id, data_dir=n.dir)
    assert [e['engine'] for e in again.schedule] == \
        ['authority', 'pow', 'authority']


def test_validator_key_rule():
    with pytest.raises(StateError) as e:
        CN.validator_entry(wallet('val-ed', 'ed25519')['name'])
    assert e.value.code == 'bad_validator_key'
    assert CN.key_refusal('private')
    assert 'few/one-time' in (CN.key_refusal('CAIRO-MSS-SHA256') or '')
    assert CN.key_refusal('ML-DSA-87') is None
    # require_pq off opens classical keys (devnet choice, on the record).
    assert CN.validator_entry('val-ed', require_pq=False)['scheme'] == 'ed25519'


def test_migration_guards():
    n = fresh()
    with pytest.raises(StateError) as e:
        n.migrate('bft', [wallet('g1')['name']],
                  at=n.blocks[-1]['height'] + 1)
    assert e.value.code == 'bad_height'
    stranger = CN.validator_entry(wallet('g-stranger')['name'])
    K.remove('g-stranger')
    try:
        with pytest.raises(StateError) as e:
            n.migrate('roundrobin', [stranger])
        assert e.value.code == 'no_local_validator'
    finally:
        pass
    with pytest.raises(StateError) as e:
        n.migrate('authority', [wallet('g1')['name'], wallet('g2')['name']])
    with pytest.raises(StateError):
        n.migrate('nope', [])


def test_tool_surface():
    out = mcpsrv.call_tool('pq_consensus', {'action': 'engines'})
    assert {'authority', 'roundrobin', 'bft', 'pos', 'pow'} <= \
        {e['name'] for e in out['engines']}
    info = mcpsrv.call_tool('pq_consensus', {})
    assert info['active']['engine'] and 'how' in info
    plan = mcpsrv.call_tool('pq_consensus', {'action': 'plan',
                                             'engine': 'pow',
                                             'difficulty': 10})
    assert plan['dry_run'] and plan['epoch']['params']['difficulty'] == 10
    assert os.path.exists(CN.HERE)


def test_tool_migrate_to_pos_with_stake_on_a_fresh_chain(monkeypatch):
    n = fresh()
    monkeypatch.setattr(mcpsrv, '_NODE', n)
    out = mcpsrv.call_tool('pq_consensus', {
        'action': 'migrate', 'engine': 'pos', 'create': 3,
        'schemes': 'ML-DSA-44,SLH-DSA-SHAKE-128f', 'stake': '50'})
    assert out['queued'] and out['mined']
    assert n.epoch()['engine'] == 'pos'
    assert {v['scheme'] for v in out['epoch']['validators']} == \
        {'ML-DSA-44', 'SLH-DSA-SHAKE-128f'}
    r = n.produce(force=True)
    assert r and not r.get('proposed')
    sched = mcpsrv.call_tool('pq_consensus', {'action': 'schedule'})
    assert [e['engine'] for e in sched['epochs']] == ['authority', 'pos']
    assert n.verify()['ok']
