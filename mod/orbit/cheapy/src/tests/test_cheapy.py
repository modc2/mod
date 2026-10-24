"""Offline tests: a fake price list, a throwaway cache dir, no network."""

import json
import os
import sys
import threading
import urllib.request

import pytest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(SRC)

import cheapy  # noqa: E402

RAW = [
    {'id': 'a/cheap', 'name': 'Cheap', 'context_length': 8000,
     'pricing': {'prompt': '0.0000001', 'completion': '0.0000002'},
     'architecture': {'input_modalities': ['text']}, 'supported_parameters': []},
    {'id': 'b/vision', 'name': 'Vision', 'context_length': 200000,
     'pricing': {'prompt': '0.000001', 'completion': '0.000004'},
     'architecture': {'input_modalities': ['text', 'image']},
     'supported_parameters': ['tools', 'reasoning']},
    {'id': 'c/free', 'name': 'Free', 'context_length': 32000,
     'pricing': {'prompt': '0', 'completion': '0'},
     'architecture': {'input_modalities': ['text']}, 'supported_parameters': ['tools']},
    {'id': 'openrouter/auto', 'name': 'Router', 'context_length': 1000000,
     'pricing': {'prompt': '-1', 'completion': '-1'}},
]


@pytest.fixture
def c(tmp_path):
    return cheapy.Cheapy(dir=str(tmp_path), fetch=lambda: RAW)


def ids(res):
    return [m['id'] for m in res['models']]


def test_normalize_drops_routers():
    rows = cheapy.normalize(RAW)
    assert [r['id'] for r in rows] == ['a/cheap', 'b/vision', 'c/free']
    v = rows[1]
    assert v['in_m'] == 1.0 and v['out_m'] == 4.0
    assert v['vision'] and v['tools'] and v['reasoning'] and not v['free']
    assert rows[2]['free']


def test_cheapest_orders_by_job_cost(c):
    r = c.cheapest(input_tokens=1000, output_tokens=1000, calls=10)
    assert ids(r) == ['c/free', 'a/cheap', 'b/vision']
    cheap = r['models'][1]
    assert cheap['per_call'] == pytest.approx((0.1 * 1000 + 0.2 * 1000) / 1e6)
    assert cheap['total'] == pytest.approx(cheap['per_call'] * 10)
    assert r['source'] == 'live'


def test_filters(c):
    assert ids(c.cheapest(free=False)) == ['a/cheap', 'b/vision']
    assert ids(c.cheapest(free='1')) == ['c/free']
    assert ids(c.cheapest(tools='1', free='0')) == ['b/vision']
    assert ids(c.cheapest(vision=True)) == ['b/vision']
    assert ids(c.cheapest(q='vis')) == ['b/vision']
    assert ids(c.cheapest(provider='a')) == ['a/cheap']
    # context must hold input + output
    assert ids(c.cheapest(input_tokens=32000, output_tokens=1000)) == ['b/vision']


def test_compare(c):
    r = c.compare('b/vision,a/cheap,nope/x', 1000, 1000)
    assert [m['id'] for m in r['models']] == ['a/cheap', 'b/vision']
    assert r['models'][0]['x'] == 1 and r['models'][1]['x'] > 1
    assert r['missing'] == ['nope/x']


def test_cache_then_stale(tmp_path):
    a = cheapy.Cheapy(dir=str(tmp_path), fetch=lambda: RAW)
    assert a.load()[1] == 'live'
    b = cheapy.Cheapy(dir=str(tmp_path), fetch=lambda: 1 / 0)
    assert b.load()[1] == 'cache'
    assert b.load(refresh=True)[1] == 'stale'
    assert len(b.load(refresh=True)[0]) == 3


def test_snapshot_floor(tmp_path):
    c = cheapy.Cheapy(dir=str(tmp_path), fetch=lambda: 1 / 0)
    rows, source, _ = c.load()
    assert source == 'snapshot' and len(rows) > 50


def test_estimate():
    assert cheapy.estimate('x' * 400)['tokens'] == 100
    assert cheapy.estimate(chars=8)['tokens'] == 2


def test_server_routes(tmp_path):
    import serve
    serve._c = cheapy.Cheapy(dir=str(tmp_path), fetch=lambda: RAW)
    httpd = serve.ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    try:
        def get(p):
            with urllib.request.urlopen(base + p) as r:
                return r.read().decode()
        for p in ('/cheapy/api/cheapest?free=0', '/api/cheapy/cheapest?free=0',
                  '/cheapest?free=0'):
            assert [m['id'] for m in json.loads(get(p))['models']] == ['a/cheap', 'b/vision']
        assert 'cheapy' in get('/cheapy/')
        assert json.loads(get('/health'))['ok']
    finally:
        httpd.shutdown()
