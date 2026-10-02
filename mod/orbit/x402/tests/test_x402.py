"""Offline tests: normalization, the store's merge/retire rules, the crawler's
paging, and the HTTP surface. No network — every fetch is stubbed."""

import base64
import importlib.util
import json
import os
import sys
import threading
import urllib.request

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(HERE)

import x402db as store  # noqa: E402
import x402src as src   # noqa: E402

V2 = {
    'resource': 'https://API.Example.com/search/', 'type': 'http', 'x402Version': 2,
    'description': 'web search', 'serviceName': 'Example', 'tags': ['search'],
    'quality': {'l30DaysTotalCalls': 70, 'l30DaysUniquePayers': 5},
    'accepts': [
        {'amount': '3000', 'asset': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
         'network': 'eip155:8453', 'payTo': '0xabc', 'scheme': 'exact',
         'extra': {'name': 'USD Coin'}},
        {'amount': '1000', 'asset': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
         'network': 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'payTo': 'So1', 'scheme': 'exact'},
    ],
    'extensions': {'bazaar': {'info': {'input': {'method': 'post'}}}},
}
V1 = {'resource': 'https://other.io/x', 'x402Version': 1,
      'accepts': [{'maxAmountRequired': '5', 'asset': '0xdead', 'network': 'base',
                   'extra': {'name': 'Mystery'}, 'description': 'v1 desc',
                   'outputSchema': {'input': {'method': 'GET'}}}]}


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    store.reset(str(tmp_path / 'x.db'))
    yield


def test_normalize_v2():
    r = src.normalize(V2)
    assert r['url'] == 'https://api.example.com/search'
    assert r['host'] == 'api.example.com'
    assert r['method'] == 'POST'
    assert r['networks'] == ['base', 'solana']
    assert r['price_usd'] == pytest.approx(0.001)
    assert r['calls_30d'] == 70


def test_normalize_v1_unknown_asset_has_no_price():
    r = src.normalize(V1)
    assert r['price_usd'] is None and r['offers'][0]['amount'] == 5
    assert r['description'] == 'v1 desc' and r['networks'] == ['base']


def test_normalize_goplausible_shape():
    r = src.normalize({'resourceUrl': 'https://g.io/a', 'method': 'post', 'settleCount': 9,
                       'accepts': [{'network': 'algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=',
                                    'amount': '200000', 'asset': '31566704'}]})
    assert r['url'] == 'https://g.io/a' and r['method'] == 'POST'
    assert r['networks'] == ['algorand'] and r['price_usd'] == pytest.approx(0.2)
    assert r['calls_30d'] == 9


def test_normalize_rejects_junk():
    assert src.normalize({'resource': 'ftp://x'}) is None
    assert src.normalize({}) is None


def test_network_families():
    assert src.network_name('algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73k') == 'algorand'
    assert src.network_name('eip155:999999') == 'eip155:999999'


def test_merge_keeps_richer_fields_and_counts_sources():
    a = src.normalize(V2)
    b = dict(a, name='', calls_30d=3)
    store.upsert([a], 'cdp')
    store.upsert([b], 'payai')
    s = store.get(a['url'])
    assert s['name'] == 'Example' and s['calls_30d'] == 70
    assert sorted(s['sources']) == ['cdp', 'payai']
    assert store.stats()['overlap'] == 1


def test_retire_drops_unlisted_but_keeps_pinned():
    a, b = src.normalize(V2), src.normalize(V1)
    store.upsert([a, b], 'cdp', now=100)
    store.upsert([b], 'probe', now=100, pinned=True)
    store.upsert([], 'cdp', now=200)
    assert store.retire('cdp', 150) == 1
    assert store.get(a['url']) is None
    assert store.get(b['url'])['pinned'] is True


def test_search_fts_and_filters():
    store.upsert([src.normalize(V2), src.normalize(V1)], 'cdp')
    assert store.search(q='sear')['total'] == 1
    assert store.search(q='"; DROP')['total'] == 0
    assert store.search(network='solana')['total'] == 1
    assert store.search(max_price=0.01)['total'] == 1
    assert store.search(host='OTHER.IO')['total'] == 1
    assert store.hosts()['total'] == 2


def test_crawl_pages_until_total(monkeypatch):
    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        off = int(url.split('offset=')[1])
        items = [dict(V2, resource=f'https://a.io/{i}') for i in range(off, min(off + 2, 5))]
        return {'items': items, 'pagination': {'limit': 2, 'total': 5}}
    monkeypatch.setattr(src, 'fetch_json', fake)
    rows = list(src.crawl('https://f.io', page=2, pause=0))
    assert len(rows) == 5 and len(calls) == 3


def test_crawl_steps_down_on_400(monkeypatch):
    def fake(url, timeout=None):
        if 'limit=1000' in url:
            raise src.SourceError(f'{url}: HTTP 400')
        return {'items': [V2], 'pagination': {'total': 1}}
    monkeypatch.setattr(src, 'fetch_json', fake)
    assert len(list(src.crawl('https://f.io', pause=0))) == 1


def test_probe_reads_v2_header(monkeypatch):
    req = {'x402Version': 2, 'resource': {'url': 'https://p.io/x', 'description': 'probed'},
           'accepts': V2['accepts']}
    hdr = base64.b64encode(json.dumps(req).encode()).decode()
    monkeypatch.setattr(src, 'fetch', lambda u, t=15, method='GET': (402, {'PAYMENT-REQUIRED': hdr}, b''))
    r = src.probe('https://p.io/x')
    assert r['x402'] and r['service']['description'] == 'probed'
    monkeypatch.setattr(src, 'fetch', lambda u, t=15, method='GET': (200, {}, b'ok'))
    assert src.probe('https://p.io/x')['x402'] is False


def _anchor():
    spec = importlib.util.spec_from_file_location('x402_anchor_t', os.path.join(HERE, 'mod.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_mod_sync_one_source(monkeypatch):
    m = _anchor()
    M = m.Mod()
    monkeypatch.setattr(src, 'fetch_json',
                        lambda url, timeout=None: {'items': [V2, V1], 'pagination': {'total': 2}})
    r = M.sync(source='payai')
    assert r['sources']['payai']['indexed'] == 2
    assert M.services(q='search')['total'] == 1
    assert M.info()['index']['services'] == 2


def test_http_surface(monkeypatch):
    os.environ['X402_AUTOSYNC'] = '0'
    spec = importlib.util.spec_from_file_location('x402_serve_t', os.path.join(HERE, 'serve.py'))
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    store.upsert([src.normalize(V2)], 'cdp')
    httpd = srv.ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    try:
        j = json.load(urllib.request.urlopen(f'{base}/x402/api/services?q=search'))
        assert j['total'] == 1
        j = json.load(urllib.request.urlopen(f'{base}/api/x402/stats'))
        assert j['services'] == 1
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f'{base}/x402/api/sync')
        assert e.value.code == 405
        html = urllib.request.urlopen(f'{base}/x402/').read()
        assert b'X402' in html
    finally:
        httpd.shutdown()


def test_reads_do_not_wait_for_a_writer():
    store.upsert([src.normalize(V2)], 'cdp')
    out = []
    with store._lock:                      # a crawl batch mid-write
        t = threading.Thread(target=lambda: out.append(store.search(q='search')['total']))
        t.start()
        t.join(5)
    assert out == [1]
