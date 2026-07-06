"""govmod tests — multi-option staked disputes, both verdict modes, no live chain.

Weights and the block clock are injected (vote(weight=...), resolve(now=...)) so
the dispute logic is tested deterministically without BlocTime / a chain RPC.
"""
import importlib.util
import json
import os
import sys
import tempfile

import pytest

import mod as m  # the framework package

# Load the local govmod mod.py by path (its filename `mod.py` would otherwise
# shadow the framework `mod` package above).
_spec = importlib.util.spec_from_file_location(
    'govmod_mod', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mod.py'))
_govmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_govmod)
Mod = _govmod.Mod

A = '0x' + 'a' * 40
B = '0x' + 'b' * 40
C = '0x' + 'c' * 40
D = '0x' + 'd' * 40
E = '0x' + 'e' * 40
F = '0x' + 'f' * 40


@pytest.fixture
def gov():
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    os.remove(path)
    yield Mod(index_path=path)
    if os.path.exists(path):
        os.remove(path)


def _three_way(gov, threshold=0, time_limit=10):
    """A backs cats, B backs dogs, C backs birds; activate."""
    c = gov.open('best pet?', options=['cats', 'dogs', 'birds'],
                 stake=1000, option='cats', threshold=threshold, time_limit=time_limit, key=A)
    gov.join(c['id'], option='dogs', stake=1000, key=B)
    gov.join(c['id'], option='birds', stake=500, key=C)
    return gov.activate(c['id'], key=A)


def test_multi_option_token_vote(gov):
    c = _three_way(gov, threshold=100)
    assert c['status'] == 'active' and c['deadline'] == c['activated_block'] + 10
    gov.vote(c['id'], 'cats', key=D, weight=300)
    gov.vote(c['id'], 'dogs', key=E, weight=200)
    gov.vote(c['id'], 'birds', key=F, weight=100)

    t = gov.tally(c['id'])
    assert t['tally'] == {'cats': 300, 'dogs': 200, 'birds': 100}
    assert t['leader'] == 'cats' and not t['tie']

    r = gov.resolve(c['id'], now=c['deadline'] + 1)
    assert r['status'] == 'resolved' and r['winning_option'] == 'cats'
    assert r['winners'] == [A]
    # pot = 1000+1000+500 = 2500, sole winner A takes all
    assert r['payout'] == {A: 2500}


def test_pro_rata_split_among_winning_backers(gov):
    # two players both back "cats", one backs "dogs"; cats wins -> split pot 2:1
    c = gov.open('q', options=['cats', 'dogs'], stake=2000, option='cats', time_limit=5, key=A)
    gov.join(c['id'], option='cats', stake=1000, key=B)
    gov.join(c['id'], option='dogs', stake=900, key=C)
    c = gov.activate(c['id'], key=A)
    gov.vote(c['id'], 'cats', key=D, weight=10)
    r = gov.resolve(c['id'], now=c['deadline'] + 1)
    pot = 2000 + 1000 + 900
    assert r['winning_option'] == 'cats' and set(r['winners']) == {A, B}
    # A staked 2000, B staked 1000 -> 2:1 of 3900
    assert r['payout'][A] == pot * 2000 // 3000
    assert r['payout'][B] == pot * 1000 // 3000
    assert sum(r['payout'].values()) == pot   # full pot distributed, no dust lost


def test_below_quorum_refunds_all(gov):
    c = _three_way(gov, threshold=10_000)
    gov.vote(c['id'], 'cats', key=D, weight=50)
    r = gov.resolve(c['id'], now=c['deadline'] + 1)
    assert r['status'] == 'no_verdict' and 'quorum' in r['reason']
    assert r['payout'] == {A: 1000, B: 1000, C: 500}


def test_tie_for_first_is_no_verdict(gov):
    c = _three_way(gov, threshold=0)
    gov.vote(c['id'], 'cats', key=D, weight=100)
    gov.vote(c['id'], 'dogs', key=E, weight=100)
    r = gov.resolve(c['id'], now=c['deadline'] + 1)
    assert r['status'] == 'no_verdict' and 'tie' in r['reason']


def test_activation_requires_real_contest(gov):
    c = gov.open('q', options=['a', 'b'], stake=10, option='a', key=A)
    with pytest.raises(AssertionError):       # only one player
        gov.activate(c['id'], key=A)
    gov.join(c['id'], option='a', stake=10, key=B)  # both on same option
    with pytest.raises(AssertionError):       # <2 distinct options backed
        gov.activate(c['id'], key=A)
    gov.join(c['id'], option='b', stake=10, key=B)  # B switches to b
    assert gov.activate(c['id'], key=A)['status'] == 'active'


def test_min_stake_enforced(gov):
    c = gov.open('q', options=['a', 'b'], min_stake=100, key=A)
    with pytest.raises(AssertionError):
        gov.join(c['id'], option='a', stake=50, key=A)
    assert gov.join(c['id'], option='a', stake=100, key=A)['stakes'][A]['amount'] == 100


def test_multi_option_multisig(gov):
    c = gov.open('which?', options=['x', 'y', 'z'], stake=500, option='x',
                 verdict_mode='multisig', signers=[D, E, F], required_sigs=2, key=A)
    gov.join(c['id'], option='y', stake=500, key=B)
    c = gov.activate(c['id'], key=A)

    with pytest.raises(AssertionError):       # non-signer
        gov.sign_verdict(c['id'], 'x', key=A)
    gov.sign_verdict(c['id'], 'x', key=D)
    with pytest.raises(ValueError):           # 1 sig, deadline not passed
        gov.resolve(c['id'], now=c['deadline'] - 1)
    gov.sign_verdict(c['id'], 'x', key=E)
    r = gov.resolve(c['id'], now=c['deadline'] - 1)   # 2-of-3 reached early
    assert r['status'] == 'resolved' and r['winning_option'] == 'x' and r['winners'] == [A]


def test_leave_and_cancel_refund(gov):
    c = gov.open('q', options=['a', 'b'], stake=10, option='a', key=A)
    gov.join(c['id'], option='b', stake=20, key=B)
    out = gov.leave(c['id'], key=B)
    assert out['refund'] == {B: 20}
    out = gov.cancel(c['id'], key=A)
    assert out['status'] == 'cancelled' and out['refund'] == {A: 10}


def test_amend_invalidates_terms_hash(gov):
    c = gov.open('q', options=['a', 'b'], threshold=0, key=A)
    h0 = c['terms_hash']
    c2 = gov.amend(c['id'], threshold=999, options=['a', 'b', 'c'], key=A)
    assert c2['threshold'] == 999 and c2['options'] == ['a', 'b', 'c'] and c2['terms_hash'] != h0


def test_mutual_agreement_via_hash_and_sig():
    fd, path = tempfile.mkstemp(suffix='.json'); os.close(fd); os.remove(path)
    try:
        gov = Mod(index_path=path)
        bob = m.key('govmod_test_bob')
        c = gov.open('q', options=['a', 'b'], stake=1, option='a', key=A)
        with pytest.raises(ValueError):                  # stale terms hash
            gov.join(c['id'], option='b', stake=1, key=B, accept_hash='0xdead')
        sig = bob.sign(c['terms_hash'])
        out = gov.join(c['id'], option='b', stake=1, key=bob.address, sig=sig,
                       accept_hash=c['terms_hash'])
        assert bob.address.lower() in out['stakes']
    finally:
        if os.path.exists(path):
            os.remove(path)


# --- gas-minimal settlement -------------------------------------------------

def test_settle_plan_single_winner_uses_transfer(gov):
    c = _three_way(gov)
    gov.vote(c['id'], 'cats', key=D, weight=10)
    gov.resolve(c['id'], now=c['deadline'] + 1)
    plan = gov.settle_plan(c['id'])
    assert plan['method'] == 'transfer' and plan['recipients'] == 1
    assert plan['merkle_root'] is None
    # net flow: A only needs +1500 (gets 2500, staked 1000); losers -1000/-500
    assert plan['net_flows'][A] == 1500


def test_settle_plan_merkle_for_many_winners_and_proofs_verify(gov):
    c = gov.open('q', options=['cats', 'dogs'], stake=2000, option='cats', time_limit=5, key=A)
    gov.join(c['id'], option='cats', stake=1000, key=B)
    gov.join(c['id'], option='dogs', stake=900, key=C)
    c = gov.activate(c['id'], key=A)
    gov.vote(c['id'], 'cats', key=D, weight=10)
    r = gov.resolve(c['id'], now=c['deadline'] + 1)

    plan = gov.settle_plan(c['id'])
    assert plan['method'] == 'merkle-claim' and plan['recipients'] == 2
    root = plan['merkle_root']
    assert root and root != '0x' + '00' * 32

    # every winner's proof reconstructs the posted root (OZ sorted-pair verify)
    for addr in r['winners']:
        pf = gov.merkle_proof(r['payout'], addr)
        leaf = gov._leaf(addr, pf['amount'])
        for sib in pf['proof']:
            sib_b = bytes.fromhex(sib[2:])
            leaf = gov._hash_pair(leaf, sib_b)
        assert '0x' + leaf.hex() == root


# --- sealed (private / commit-reveal) voting --------------------------------

class _Clock:
    """commit()/reveal() read the live block; this gives tests a movable one."""
    def __init__(self, h=1000):
        self.h = h
    def __call__(self, now=None):
        return int(now) if now is not None else self.h


def _sealed_case(gov, threshold=0, time_limit=10, base=1000):
    clk = _Clock(base)
    gov.block = clk
    c = gov.open('best pet?', options=['cats', 'dogs', 'birds'], stake=1000, option='cats',
                 threshold=threshold, time_limit=time_limit, privacy='sealed', key=A)
    gov.join(c['id'], option='dogs', stake=1000, key=B)
    c = gov.activate(c['id'], key=A)         # activated at `base`; deadline = base + time_limit
    return c, clk


def test_sealed_hides_votes_until_resolve(gov):
    c, clk = _sealed_case(gov, threshold=100)
    cid = c['id']

    # voters commit opaque hashes (the option is never sent to the coordinator)
    salts = {D: gov.gen_salt(), E: gov.gen_salt(), F: gov.gen_salt()}
    gov.commit(cid, gov.seal('cats', salts[D]), weight=300, key=D)
    gov.commit(cid, gov.seal('dogs', salts[E]), weight=200, key=E)
    r = gov.commit(cid, gov.seal('cats', salts[F]), weight=100, key=F)
    assert r['ballots'] == 3 and r['committed_weight'] == 600
    assert 'option' not in r                       # never echoes the choice

    # during voting: only aggregates — no per-option tally, no voter identities
    t = gov.tally(cid)
    assert t['phase'] == 'commit' and t['ballots'] == 3 and t['committed_weight'] == 600
    assert 'tally' not in t and 'leader' not in t
    view = gov.case(cid)
    assert view['ballots']['sealed'] and view['ballots']['count'] == 3
    assert 'commitment' not in json.dumps(view)    # raw ballots not exposed
    assert gov._tally(gov.case(cid, raw=True)) == {'cats': 0, 'dogs': 0, 'birds': 0}

    with pytest.raises(AssertionError):            # sealed case rejects the open vote()
        gov.vote(cid, 'cats', key=D, weight=1)

    # after the deadline the commit phase is closed; voters reveal openings
    clk.h = c['deadline'] + 1
    with pytest.raises(ValueError):
        gov.commit(cid, gov.seal('cats', salts[D]), weight=1, key=D)
    gov.reveal(cid, 'cats', salts[D], key=D)
    gov.reveal(cid, 'dogs', salts[E], key=E)
    gov.reveal(cid, 'cats', salts[F], key=F)
    assert 'tally' not in gov.tally(cid)           # still no per-option leak pre-resolve

    r = gov.resolve(cid, now=clk.h)
    assert r['status'] == 'resolved' and r['winning_option'] == 'cats'   # 400 > 200
    # settlement done -> full tally exposed for verification
    assert gov.case(cid)['result_tally'] == {'cats': 400, 'dogs': 200, 'birds': 0}


def test_sealed_reveal_must_match_commitment(gov):
    c, clk = _sealed_case(gov)
    salt = gov.gen_salt()
    gov.commit(c['id'], gov.seal('cats', salt), weight=10, key=D)
    clk.h = c['deadline'] + 1
    with pytest.raises(ValueError):                # lying about the option fails
        gov.reveal(c['id'], 'dogs', salt, key=D)
    assert gov.reveal(c['id'], 'cats', salt, key=D)['revealed']


def test_sealed_unrevealed_votes_do_not_count(gov):
    c, clk = _sealed_case(gov, threshold=0)
    s1, s2 = gov.gen_salt(), gov.gen_salt()
    gov.commit(c['id'], gov.seal('cats', s1), weight=100, key=D)
    gov.commit(c['id'], gov.seal('dogs', s2), weight=999, key=E)  # never revealed
    clk.h = c['deadline'] + 1
    gov.reveal(c['id'], 'cats', s1, key=D)
    r = gov.resolve(c['id'], now=clk.h)
    assert r['winning_option'] == 'cats' and gov.case(c['id'])['result_tally']['dogs'] == 0


def test_sealed_nullifier_is_anonymous_and_one_per_voter(gov):
    c, _ = _sealed_case(gov)
    r1 = gov.commit(c['id'], gov.seal('cats', gov.gen_salt()), weight=5, key=D)
    nf = r1['nullifier']
    assert nf != D and len(nf) == 66              # not the address; opaque 32-byte tag
    r2 = gov.commit(c['id'], gov.seal('dogs', gov.gen_salt()), weight=5, key=D)
    assert r2['nullifier'] == nf and r2['ballots'] == 1   # same voter -> one ballot (replaced)


def test_sealed_requires_token_mode(gov):
    with pytest.raises(AssertionError):
        gov.open('q', options=['a', 'b'], verdict_mode='multisig', signers=[D, E],
                 privacy='sealed', key=A)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
