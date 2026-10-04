"""Offline tests for market.py: agent/panel judge kinds and the market.

The agent-protocol judge is tested against a stub HTTP server standing in
for orbit/agent's POST /run — no network, no model.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market  # noqa: E402
import panel  # noqa: E402

RULE = {'name': 'clean', 'kind': 'rule', 'forbid': ['TODO']}
LONG = {'name': 'long', 'kind': 'rule', 'min_len': 20}


@pytest.fixture
def book(tmp_path):
    return panel.Panels(str(tmp_path / 'judge.db'))


@pytest.fixture
def shop(book):
    return market.Market(book)


# ── panel-kind judges: judges of judges ─────────────────────────


def test_panel_judge_composes(book):
    book.create('sub', 'alice', [RULE, LONG], threshold=60)
    book.create('top', 'alice',
                [RULE, {'name': 'bench', 'kind': 'panel', 'panel': 'sub'}],
                threshold=60)
    v = book.judge('top', 'a perfectly reasonable sentence with no faults')
    assert v['approved'] and v['average'] == 100
    bench = next(s for s in v['scores'] if s['name'] == 'bench')
    assert bench['kind'] == 'panel' and bench['score'] == 100
    assert 'verdict #' in bench['reason'] and 'PASS' in bench['reason']
    # The sub-panel's own deliberation is on record too.
    assert any(x['panel'] == 'sub' for x in book.verdicts())


def test_panel_judge_carries_sub_average(book):
    # sub: clean=50 (TODO), long=100 → avg 75; top: that 75 is one vote
    # beside a clean 50 → (75+50)/2 = 62.5.
    book.create('sub', 'alice', [RULE, LONG], threshold=60)
    book.create('top', 'alice',
                [RULE, {'name': 'bench', 'kind': 'panel', 'panel': 'sub'}],
                threshold=60)
    v = book.judge('top', 'this is long enough but contains a TODO marker')
    assert v['average'] == 62.5 and v['approved']


def test_panel_cycle_fails_closed(book):
    book.create('b', 'alice', [RULE], threshold=10)
    book.create('a', 'alice',
                [{'name': 'del', 'kind': 'panel', 'panel': 'b'}], threshold=10)
    # Close the loop after creation: b now delegates back to a, and its
    # quorum requires both votes — so the dead cycle leg is fatal.
    up = book.update('b', 'alice',
                     judges=[RULE, {'name': 'loop', 'kind': 'panel', 'panel': 'a'}],
                     min_votes=2)
    assert 'error' not in up
    v = book.judge('a', 'a clean input that would otherwise sail through')
    assert not v['approved'] and v['average'] is None  # quorum fails closed
    # The cycle surfaced as a judge error somewhere down the chain.
    sub = next(x for x in book.verdicts() if x['panel'] == 'b')
    assert any('cycle' in (s['reason'] or '') for s in sub['scores']
               if s['score'] is None)


def test_panel_judge_validation(book):
    book.create('sub', 'alice', [RULE])
    no_ref = book.create('x', 'a', [{'name': 'j', 'kind': 'panel'}])
    assert 'error' in no_ref
    missing = book.create('x', 'a',
                          [{'name': 'j', 'kind': 'panel', 'panel': 'nope'}])
    assert 'error' in missing
    own = book.create('x', 'a', [{'name': 'j', 'kind': 'panel', 'panel': 'x'}])
    assert 'own bench' in own['error']


# ── agent-kind judges: the agent protocol, stubbed ──────────────


class _StubAgent(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        self.server.last_body = json.loads(self.rfile.read(n) or b'{}')
        body = json.dumps(self.server.payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def agent_api():
    srv = HTTPServer(('127.0.0.1', 0), _StubAgent)
    srv.payload, srv.last_body = {}, None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def _agent_judge(srv, **extra):
    return dict({'name': 'bot', 'kind': 'agent', 'agent_type': 'reviewer',
                 'prompt': 'is it good?',
                 'url': f'http://127.0.0.1:{srv.server_address[1]}/run'}, **extra)


def test_agent_judge_scores_from_finish_summary(book, agent_api):
    agent_api.payload = {'result': [
        {'tool': 'think', 'result': 'hmm'},
        {'tool': 'finish', 'params': {'summary': '{"score": 88, "reason": "solid"}'}}]}
    book.create('p', 'alice', [_agent_judge(agent_api)], threshold=60)
    v = book.judge('p', 'judge me')
    assert v['approved'] and v['average'] == 88
    assert v['scores'][0]['reason'] == 'solid'
    sent = agent_api.last_body
    assert sent['agent_type'] == 'reviewer' and 'judge me' in sent['query']
    assert sent['free'] is True


def test_agent_judge_bare_number_fallback(book, agent_api):
    agent_api.payload = {'result': [
        {'tool': 'response', 'result': 'I would say 42 out of 100.'}]}
    book.create('p', 'alice', [_agent_judge(agent_api)], threshold=60)
    v = book.judge('p', 'judge me')
    assert not v['approved'] and v['average'] == 42


def test_agent_judge_error_means_no_vote(book, agent_api):
    agent_api.payload = {'error': 'No API key configured'}
    book.create('p', 'alice', [_agent_judge(agent_api)], threshold=10)
    v = book.judge('p', 'judge me')
    assert not v['approved'] and v['average'] is None
    assert v['scores'][0]['score'] is None
    assert 'agent run failed' in v['scores'][0]['reason']


def test_agent_judge_validation(book):
    assert 'error' in book.create('x', 'a', [
        {'name': 'j', 'kind': 'agent', 'prompt': 'p'}])          # no agent_type
    assert 'error' in book.create('x', 'a', [
        {'name': 'j', 'kind': 'agent', 'agent_type': 'default'}])  # no prompt


# ── the judge market ────────────────────────────────────────────


def test_market_seeds(shop):
    names = {d['name'] for d in shop.list()}
    assert {'concise', 'clarity', 'reviewer'} <= names
    kinds = {d['name']: d['kind'] for d in shop.list()}
    assert kinds['reviewer'] == 'agent'


def test_publish_browse_update(shop):
    d = shop.publish('strict', 'bob', {'kind': 'rule', 'max_len': 100},
                     'short or nothing', tags='style, rules')
    assert d['kind'] == 'rule' and d['tags'] == ['style', 'rules']
    assert shop.list(q='nothing')[0]['name'] == 'strict'
    assert [x['name'] for x in shop.list(kind='rule') if x['author'] == 'bob'] \
        == ['strict']
    # Re-publish under the same (name, author) updates in place.
    again = shop.publish('strict', 'bob', {'kind': 'rule', 'max_len': 50})
    assert again['id'] == d['id'] and again['spec']['max_len'] == 50
    # Someone else's listing with the same name is a separate row.
    other = shop.publish('strict', 'carol', {'kind': 'rule', 'max_len': 10})
    assert other['id'] != d['id']
    # Bad specs never list.
    assert 'error' in shop.publish('bad', 'bob', {'kind': 'agent'})
    assert 'error' in shop.publish('Bad Name', 'bob', {'kind': 'rule'})
    assert 'error' in shop.publish('bad', '', {'kind': 'rule'})


def test_unpublish_author_only(shop):
    d = shop.publish('mine', 'bob', {'kind': 'rule', 'max_len': 100})
    assert 'error' in shop.unpublish(d['id'], 'mallory')
    assert shop.unpublish(d['id'], 'bob')['unpublished'] == d['id']
    assert 'error' in shop.get(d['id'])


def test_install_onto_panel(book, shop):
    book.create('pr', 'alice', [RULE])
    d = shop.publish('strict', 'bob', {'kind': 'rule', 'max_len': 100})
    out = shop.install(d['id'], 'pr', 'alice')
    assert out['installed'] == 'strict'
    assert [j['name'] for j in book.get('pr')['judges']] == ['clean', 'strict']
    assert shop.get(d['id'])['installs'] == 1
    # A second install auto-renames instead of clashing.
    again = shop.install(d['id'], 'pr', 'alice', weight=2)
    assert again['installed'] == 'strict-2'
    j2 = next(j for j in book.get('pr')['judges'] if j['name'] == 'strict-2')
    assert j2['weight'] == 2
    # Only the panel's creator can seat a judge.
    assert 'error' in shop.install(d['id'], 'pr', 'mallory')
    assert 'error' in shop.install(9999, 'pr', 'alice')
    assert 'error' in shop.install(d['id'], 'nope', 'alice')


def test_installed_judge_actually_votes(book, shop):
    book.create('pr', 'alice', [LONG], threshold=75)
    d = shop.publish('noban', 'bob', {'kind': 'rule', 'forbid': ['banned']})
    shop.install(d['id'], 'pr', 'alice')
    v = book.judge('pr', 'a long enough sentence containing a banned word')
    assert v['average'] == 75 and v['approved']
