"""The off-chain engine, end to end, against a throwaway store."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


@pytest.fixture()
def P(tmp_path, monkeypatch):
    monkeypatch.setenv('SELFINSURE_DIR', str(tmp_path))
    for m in ('pool', 'onchain', 'chain'):
        sys.modules.pop(m, None)
    if ROOT not in sys.path:
        sys.path.append(ROOT)
    import pool
    pool.STORE = str(tmp_path)
    pool.STATE_FILE = os.path.join(pool.STORE, 'state.json')
    pool.LEDGER_FILE = os.path.join(pool.STORE, 'ledger.jsonl')
    return pool


def _health(P, **kw):
    kw.setdefault('quorum', 1)
    return P.create_pool('Courier health', premium=400, coverage=50000, deductible=250,
                         unit='USD', **kw)


def test_money_stays_in_the_pool(P):
    r = _health(P)
    pid = r['id']
    a = P.join(pid, 'alice')
    P.join(pid, 'bob')
    v = P.pool_info(pid)
    assert v['money']['premiums_in'] == 800
    assert v['money']['balance'] == 800
    assert v['money']['operator_fees'] == 0
    assert v['terms']['fee_pct'] == 0
    assert a['to_pool'] == 400 and a['fee'] == 0


def test_fee_is_capped(P):
    with pytest.raises(P.SelfInsureError):
        _health(P, fee_bps=1001)
    r = _health(P, fee_bps=1000)
    j = P.join(r['id'], 'alice')
    assert j['fee'] == 40 and j['to_pool'] == 360


def test_claim_pays_and_unfunded_is_honest(P):
    r = _health(P)
    pid, ok = r['id'], r['owner_key']
    a = P.join(pid, 'alice')
    P.join(pid, 'bob')
    ag = P.register_agent(pid, 'judge', kind='ai', model='claude-fable-5')
    c = P.file_claim(pid, member='alice', member_key=a['member_key'], amount=1250,
                     title='ER visit')
    assert c['payable_if_accepted'] == 1000       # 1250 - 250 deductible
    assert c['funded_now'] is False               # pot is 800
    v = P.vote(pool=pid, claim=c['claim'], agent='judge', agent_key=ag['agent_key'],
               accept=True, reason='bill matches')
    assert v['state'] == 'unfunded' and v['paid'] == 800 and v['owed'] == 200
    with pytest.raises(P.SelfInsureError) as e:
        P.distribute(pid, owner_key=ok)
    assert 'still unpaid' in str(e.value)
    # the next premium in pays the debt first
    P.join(pid, 'carol')
    cl = P.claim(claim=c['claim'])
    assert cl['state'] == 'paid' and cl['owed'] == 0
    assert P.pool_info(pid)['money']['balance'] == 200


def test_vote_rules(P):
    r = _health(P)
    pid = r['id']
    a = P.join(pid, 'alice')
    ag = P.register_agent(pid, 'alice', kind='human')     # same name as the claimant
    c = P.file_claim(pid, member='alice', member_key=a['member_key'], amount=500, title='x')
    with pytest.raises(P.SelfInsureError):
        P.vote(claim=c['claim'], agent='alice', agent_key=ag['agent_key'], accept=True,
               reason='mine')
    with pytest.raises(P.SelfInsureError):
        P.vote(claim=c['claim'], agent='alice', agent_key=ag['agent_key'], accept=True,
               reason='')


def test_distribute_pro_rata(P):
    r = _health(P)
    pid, ok = r['id'], r['owner_key']
    a = P.join(pid, 'alice')
    P.join(pid, 'bob')
    P.premium(pid, member='alice', member_key=a['member_key'], amount=800)  # alice 1200, bob 400
    d = P.distribute(pid, owner_key=ok, amount=400)
    assert d['confirmed'] is False
    d = P.distribute(pid, owner_key=ok, amount=400, confirm=True)
    parts = {x['name']: x['rebate'] for x in d['to']}
    assert parts == {'alice': 300, 'bob': 100}
    assert P.member(pid, 'alice')['rebated'] == 300


def test_decimals_follow_the_unit(P):
    r = P.create_pool('ETH pool', premium=0.1, coverage=1, unit='ETH', quorum=1)
    p = P._pool(P.load(), r['id'])
    assert p['decimals'] == 18 and p['premium'] == 10 ** 17
    assert P.pool_info(r['id'])['terms']['premium'] == 0.1
    u = P.create_pool('USDC pool', premium=400, coverage=50000, unit='USDC', quorum=1)
    assert P._pool(P.load(), u['id'])['premium'] == 400 * 10 ** 6


def test_split_pro_rata_sums_exactly(P):
    parts = P.split_pro_rata(100, [1, 1, 1])
    assert sum(parts) == 100 and sorted(parts) == [33, 33, 34]
    assert P.split_pro_rata(0, [1, 2]) == [0, 0]


def test_onchain_preset_scales_and_caps():
    if ROOT not in sys.path:
        sys.path.append(ROOT)
    import onchain as O
    h = O.preset('health', decimals=6)
    t = h['terms']
    assert t[0] == 400 * 10 ** 6 and t[2] == 50_000 * 10 ** 6 and t[7] == 0
    assert t[8] == 2 and t[9] == 6600 and t[10] is True
    assert O.terms_from_chain(t, 6)['premium'] == '400'
    with pytest.raises(O.OnchainError):
        O.preset('health', decimals=6, fee_bps=1001)
    cfg = O.config_tuple('Travis County', 'about', oracle_mode='required', terms=t)
    assert cfg[5] == 2 and cfg[6] == t
    src = O.sources()
    assert 'SelfInsure.sol' in src and 'oracles/SignedOracle.sol' in src
    assert 'MAX_FEE_BPS = 1000' in O.source('SelfInsure')
    assert 'operatorShareBps' in O.source('SelfInsure')
