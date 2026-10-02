"""Offline tests: sources are stubbed, so these never touch the network."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from newsagg import engine, mcp_server, sources, store  # noqa: E402

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Bittensor launches dTAO upgrade</title><link>https://a.com/x?utm_source=tw</link>
<pubDate>Thu, 01 Oct 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Subnets get tokens&lt;/p&gt;</description></item>
<item><title>Weather is nice</title><link>https://b.com/w</link></item>
</channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom story</title>
<link href="https://c.com/s"/><updated>2026-10-01T09:00:00Z</updated><summary>s</summary></entry></feed>"""


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'FEEDS', tmp_path / 'feeds.json')


def stub(monkeypatch, **fns):
    table = {n: {'fn': f, 'default': True, 'docs': n} for n, f in fns.items()}
    monkeypatch.setattr(sources, 'SOURCES', table)


def test_parse_rss_and_atom():
    rss = sources.parse_feed(RSS, 't')
    assert rss[0]['title'] == 'Bittensor launches dTAO upgrade'
    assert rss[0]['summary'] == 'Subnets get tokens'
    assert rss[0]['ts'] == 1790848800
    atom = sources.parse_feed(ATOM, 't')
    assert atom[0]['url'] == 'https://c.com/s' and atom[0]['ts'] > 0


def test_dates():
    assert sources.parse_date('20261001T100000Z') == 1790848800
    assert sources.parse_date('2026-10-01T10:00:00Z') == 1790848800
    assert sources.parse_date('garbage') == 0


def test_canonical_strips_tracking():
    assert engine.canonical('https://www.a.com/x/?utm_source=tw&id=3') == 'a.com/x?id=3'


def test_dedupe_ranks_and_survives_dead_source(monkeypatch):
    now = time.time()
    a = sources.item('Bittensor launches dTAO upgrade', 'https://a.com/x?utm_source=1', 'one', now - 3600)
    b = sources.item('Bittensor launches dTAO upgrade today', 'https://b.com/y', 'two', now - 7200)
    c = sources.item('Unrelated cooking story', 'https://c.com/z', 'two', now)

    def boom(q, n):
        raise RuntimeError('down')

    stub(monkeypatch, one=lambda q, n: [a], two=lambda q, n: [b, c], dead=boom)
    out = engine.search('bittensor dtao', k=5)
    assert out['sources']['dead']['ok'] is False
    assert out['unique'] == 2
    top = out['results'][0]
    assert 'bittensor' in top['title'].lower() and len(top['found_by']) == 2
    assert all('cooking' not in r['title'].lower() for r in out['results'])


def test_window_drops_old(monkeypatch):
    old = sources.item('Bittensor old', 'https://a.com/o', 'one', time.time() - 30 * 86400)
    stub(monkeypatch, one=lambda q, n: [old])
    assert engine.search('bittensor', hours=24)['results'] == []
    assert len(engine.search('bittensor', hours=0)['results']) == 1


def test_guard_blocks_private():
    for bad in ('http://127.0.0.1/', 'file:///etc/passwd', 'http://169.254.169.254/'):
        with pytest.raises(ValueError):
            sources.guard(bad)


def test_read_extracts_paragraphs(monkeypatch):
    page = (b'<html><head><title>T</title></head><body><nav><p>menu menu menu menu menu menu menu menu</p></nav>'
            b'<p>' + b'Real paragraph text here. ' * 4 + b'</p><script>x()</script></body></html>')
    monkeypatch.setattr(sources, 'fetch', lambda *a, **k: page)
    r = engine.read('https://a.com/x')
    assert r['title'] == 'T' and r['text'].startswith('Real paragraph') and 'menu' not in r['text']


def test_mcp_protocol(monkeypatch):
    stub(monkeypatch, one=lambda q, n: [sources.item('Bittensor news', 'https://a.com/1', 'one', time.time())])
    init = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                      'params': {'protocolVersion': '2025-03-26'}})
    assert init['result']['protocolVersion'] == '2025-03-26'
    assert mcp_server.handle_message({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    names = {t['name'] for t in mcp_server.handle_message(
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']}
    assert {'news_search', 'news_read', 'news_add_feed'} <= names
    r = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                   'params': {'name': 'news_search', 'arguments': {'query': 'bittensor'}}})
    assert r['result']['isError'] is False
    assert json.loads(r['result']['content'][0]['text'])['results'][0]['url'] == 'https://a.com/1'
    bad = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                                     'params': {'name': 'news_search', 'arguments': {}}})
    assert bad['result']['isError'] is True


def test_feed_store_roundtrip(monkeypatch):
    monkeypatch.setattr(sources, 'fetch', lambda *a, **k: RSS)
    monkeypatch.setattr(sources, 'guard', lambda u: u)
    n0 = len(store.feeds())
    assert store.add('https://feeds.example.org/rss', 'Ex')['items'] == 2
    assert len(store.feeds()) == n0 + 1
    assert store.remove('Ex')['removed'] == 1
