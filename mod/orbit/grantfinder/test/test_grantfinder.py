"""grantfinder tests — no network, no agent spend (agent.run is faked)."""
import json
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
os.environ['GRANTFINDER_HOME'] = tempfile.mkdtemp(prefix='gf-test-')
sys.path.append(str(HERE))

import pytest  # noqa: E402
from grantscan import agent, lanes, store, sweep, tags  # noqa: E402

ESP = {'name': 'Ecosystem Support Program', 'funder': 'Ethereum Foundation',
       'url': 'https://www.esp.ethereum.foundation/', 'summary': 'Grants for zero-knowledge research and developer tooling.',
       'status': 'rolling', 'categories': ['crypto'], 'tags': ['Public Goods', 'ETH'], 'amount_max_usd': 30000}
NGI = {'name': 'NGI Zero Commons Fund', 'funder': 'NLnet', 'url': 'https://nlnet.nl/commonsfund/',
       'summary': 'Open source internet commons.', 'status': 'open', 'deadline': '2026-12-01',
       'categories': ['open-source'], 'tags': ['internet'], 'amount_max_usd': 54000}


@pytest.fixture(autouse=True)
def fresh_db():
    if store.DB.exists():
        store.DB.unlink()
    yield


def test_tags_normalise_and_derive():
    t = tags.tag(ESP, 'crypto')
    assert {'crypto', 'ethereum', 'public-goods', 'zk', 'devtools', 'status-rolling', 'amount-10k-50k'} <= set(t)
    assert tags.group('ethereum') == 'ecosystem' and tags.group('status-open') == 'status'
    assert tags.norm('Open Source') == 'open-source' and tags.norm('RetroPGF') == 'retroactive'


def test_upsert_merges_by_url():
    assert store.upsert([ESP], lane='crypto-ethereum', category='crypto') == {'new': 1, 'updated': 0}
    again = {**ESP, 'url': 'https://esp.ethereum.foundation', 'status': 'open', 'tags': ['grants']}
    assert store.upsert([again], lane='crypto-zk-research', category='crypto') == {'new': 0, 'updated': 1}
    [g] = store.grants()
    assert g['status'] == 'open' and 'status-open' in g['tags'] and 'status-rolling' not in g['tags']
    assert g['lanes'] == ['crypto-ethereum', 'crypto-zk-research'] and 'ethereum' in g['tags']


def test_rejects_junk_records():
    assert store.upsert([{'name': '', 'url': 'x'}, {'name': 'a', 'url': 'ftp://a'}]) == {'new': 0, 'updated': 0}


def test_filters_and_sort():
    store.upsert([ESP], category='crypto')
    store.upsert([NGI], category='open-source')
    assert [g['name'] for g in store.grants(tags='crypto')] == [ESP['name']]
    assert [g['name'] for g in store.grants(tags='open-source,internet')] == [NGI['name']]
    assert store.grants(sort='deadline')[0]['name'] == NGI['name']       # open before rolling
    assert store.grants(sort='amount')[0]['name'] == NGI['name']
    assert store.grants(q='nlnet')[0]['funder'] == 'NLnet'
    assert 'category' in store.tag_counts()


def test_command_is_fenced_to_web_tools():
    cmd = agent.command('find grants', 'sys', model='sonnet', budget=1)
    assert cmd[cmd.index('--tools') + 1] == 'WebSearch,WebFetch'
    assert '--strict-mcp-config' in cmd and '--json-schema' in cmd and '--max-budget-usd' in cmd
    assert cmd[-1] == 'find grants'


def test_lane_pick():
    assert all(l['category'] == 'crypto' for l in lanes.pick(category='crypto'))
    assert [l['id'] for l in lanes.pick('oss-ai')] == ['oss-ai']
    with pytest.raises(ValueError):
        lanes.pick('nope')


def test_sweep_with_fake_agent(monkeypatch):
    def fake(prompt, **kw):
        kw['on_step']({'tool': 'WebSearch', 'arg': 'q'})
        return {'grants': [ESP] if 'Ethereum' in prompt else [NGI], 'cost': 0.1, 'turns': 3,
                'steps': [], 'error': None}
    monkeypatch.setattr(agent, 'run', fake)
    r = sweep.sweep(lanes='crypto-ethereum,oss-foundations')
    assert r['new'] == 2 and r['cost'] == 0.2
    runs = store.runs()
    assert len(runs) == 2 and all(x['status'] == 'done' and x['searches'] == 1 for x in runs)


def test_server_routes():
    from http.server import ThreadingHTTPServer
    from grantscan import server
    store.upsert([ESP], category='crypto')
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    get = lambda p, **h: urllib.request.urlopen(urllib.request.Request(base + p, headers=h), timeout=5)
    assert json.loads(get('/health').read())['grants'] == 1
    assert json.loads(get('/grantfinder/api/grants?tags=crypto').read())['n'] == 1
    assert b'grantfinder' in get('/grantfinder', Accept='text/html').read()
    assert b'Ecosystem Support Program' in get('/export.csv').read()
    req = urllib.request.Request(base + '/sweep', data=b'{}', method='POST',
                                 headers={'X-Forwarded-For': '1.2.3.4'})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403
    srv.shutdown()


def test_same_site_twin_merges_and_status_words_dropped():
    store.upsert([{**ESP, 'name': 'Alpha-Omega Grants', 'url': 'https://alpha-omega.dev/grants/how-to-apply/',
                   'tags': ['rolling', 'security']}], lane='a')
    store.upsert([{**ESP, 'name': 'Alpha-Omega', 'url': 'https://alpha-omega.dev/', 'status': 'open'}], lane='b')
    [g] = store.grants()
    assert g['lanes'] == ['a', 'b'] and 'rolling' not in g['tags'] and g['status'] == 'open'
    assert 'climate' not in tags.tag({'name': 'x', 'summary': 'open source sustainability fund'})


def test_retag_keeps_lanes_and_counts_total():
    store.upsert([ESP], lane='crypto-ethereum', category='crypto')
    store.upsert([NGI], lane='oss-foundations', category='open-source')
    assert store.retag() == {'before': 2, 'after': 2}
    assert {tuple(g['lanes']) for g in store.grants()} == {('crypto-ethereum',), ('oss-foundations',)}
    page, n = store.grants(k=1, total=True)
    assert len(page) == 1 and n == 2


def test_past_deadline_is_not_open():
    assert store.clean({**NGI, 'deadline': '2020-01-01'})['status'] == 'closed'
    assert store.clean({**NGI, 'deadline': '2999-01-01'})['status'] == 'open'
    assert tags.norm('Quadratic Funding') == 'quadratic'
