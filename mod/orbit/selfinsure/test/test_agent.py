"""The guide and the agent: explanations, drafting from a sentence, and that
nothing is created without an explicit confirm."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), 'src')


@pytest.fixture()
def A(tmp_path, monkeypatch):
    monkeypatch.setenv('SELFINSURE_DIR', str(tmp_path))
    monkeypatch.delenv('SELFINSURE_AGENT_LLM', raising=False)
    for m in ('pool', 'onchain', 'chain', 'mcp', 'guide', 'agent'):
        sys.modules.pop(m, None)
    if ROOT not in sys.path:
        sys.path.append(ROOT)
    import pool
    pool.STORE = str(tmp_path)
    pool.STATE_FILE = os.path.join(pool.STORE, 'state.json')
    pool.LEDGER_FILE = os.path.join(pool.STORE, 'ledger.jsonl')
    import agent
    return agent


@pytest.mark.parametrize('q,topic', [
    ('What does selfinsure mean?', 'what'),
    ('what is this?', 'what'),
    ("what's this site about", 'what'),
    ('who are you', 'what'),
    ('How do I create my own pool?', 'create'),
    ('what is the operator fee', 'fee'),
    ('what happens if the pool runs out of money', 'unfunded'),
    ('who decides claims', 'adjudicator'),
    ('what is a deductible', 'deductible'),
])
def test_explain(A, q, topic):
    assert A.G.explain(q)['topic']['id'] == topic


def test_parse_terms(A):
    t = A.G.parse_terms('a pool for 20 couriers covering bike theft, $8 a month, '
                        'up to $600, $50 deductible, 2 votes, 14 day wait, 3% fee')
    assert t == {'premium': 8, 'period_days': 30, 'coverage': 600, 'deductible': 50,
                 'quorum': 2, 'waiting_days': 14, 'fee_bps': 300, 'members': 20}
    assert A.G.parse_terms('50 dog owners, 1.5k max payout')['members'] == 50
    assert A.G.parse_terms('pays up to 1.5k')['coverage'] == 1500


def test_draft_uses_template_and_checks(A):
    d = A.G.draft('pet vet fund for 10 owners, 15 a month, up to 2000')
    assert d['template'] == 'pet'
    assert d['create_args']['coverage'] == 2000
    assert d['check']['level'] == 'bad'           # 10 x 180/yr < one 2000 claim


def test_check_flags_uncapped_and_fee(A):
    c = A.G.check({'premium': 10, 'coverage': 0, 'fee_bps': 500}, 10)
    assert c['level'] == 'bad'
    assert any('5%' in n for n in c['notes'])


def test_fee_is_capped(A):
    assert A.G.draft('a pool for bikes, 50% fee')['create_args']['fee_bps'] == 1000


def test_draft_does_not_create(A):
    r = A.run('Draft a pool for 20 couriers covering bike theft, $8 a month, up to $600')
    assert r['draft'] and 'created' not in r
    assert A.M.call_tool('si_pools', {})['count'] == 0


def test_confirm_creates_once_with_key(A):
    r = A.run('make a pool for 5 friends covering phone screens, 4 a month, up to 200, confirm')
    assert r['created']['owner_key'].startswith('si_owner_')
    assert A.M.call_tool('si_pools', {})['count'] == 1
    pid = r['created']['id']
    assert pid in A.run(f'tell me about {pid}')['result']


def test_reader_never_creates(A):
    r = A.run('make a pool for phones, 4 a month, up to 200, confirm', agent='selfinsure-reader')
    assert 'created' not in r and 'read-only' in r['result']
    assert A.M.call_tool('si_pools', {})['count'] == 0


def test_howto_is_not_a_draft(A):
    r = A.run('how do I start a bike theft pool?')
    assert 'draft' not in r and 'create' in r['topics']


def test_what_is_this_gets_the_overview(A):
    r = A.run('what is this?')
    assert 'not sure' not in r['result'] and 'what' in r['topics']


def test_unknown_is_honest(A):
    r = A.run('what is the capital of france')
    assert 'not sure' in r['result'] and r['brain'] == 'rules'


def test_llm_falls_back_visibly(A, monkeypatch):
    monkeypatch.setattr(A, 'LLM_URL', 'http://127.0.0.1:9')     # nothing listens
    r = A.run('what is a deductible', brain='llm')
    assert r['brain'] == 'rules' and r['fallback']


def test_contract_shape(A):
    roster = A.agents()
    assert {a['id'] for a in roster['agents']} == {'selfinsure', 'selfinsure-reader'}
    assert A.card('http://x')['endpoints']['stream'] == 'http://x/run/stream'


def test_mcp_tools(A):
    assert len(A.M.call_tool('si_explain', {'topic': 'all'})['topics']) == len(A.G.TOPICS)
    assert A.M.call_tool('si_draft', {'template': 'bike'})['create_args']['coverage'] == 600
    assert 'created' not in A.M.call_tool('si_ask', {'query': 'bike pool, 8 a month, up to 600, confirm'})
