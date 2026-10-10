"""Offline tests for bt.news — feed parsing, relevance, the local index.

Nothing here touches the network: `news.fetch` is replaced with canned feeds.
"""
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['BT_NO_SNAPSHOT'] = '1'
os.environ.setdefault('BT_DATA_DIR', '/tmp/bt-test-default')

from bt import history, news, tools  # noqa: E402

NOW = int(time.time())
RSS = f'''<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Chutes ships TEE inference - CoinDesk</title><link>https://ex.com/a</link>
  <description>&lt;p&gt;The Bittensor subnet &lt;b&gt;Chutes&lt;/b&gt; ...&lt;/p&gt;</description>
  <pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime(NOW - 3600))}</pubDate>
  <source url="https://coindesk.com">CoinDesk</source></item>
<item><title>Apex Legends season 30 patch notes</title><link>https://ex.com/b</link>
  <description>Game news, nothing about crypto</description></item>
<item><title>Bittensor SN1 Apex hits new high</title><link>https://ex.com/c</link>
  <description>TAO subnets rally</description></item>
<item><title>Remittix presale final price, Bittensor watch</title><link>https://ex.com/spam</link></item>
</channel></rss>'''.encode()
ATOM = f'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>v2.0.1</title><link rel="alternate" href="https://github.com/o/r/releases/tag/v2.0.1"/>
  <updated>{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(NOW - 7200))}</updated>
  <author><name>dev</name></author><content type="html">fixes</content></entry>
</feed>'''.encode()


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv('BT_DATA_DIR', str(tmp_path))
    c = history._db()
    c.executemany('INSERT INTO subnet_meta (netuid, name, symbol, github, url) VALUES (?,?,?,?,?)',
                  [(0, 'root', 'T', None, None),
                   (1, 'Apex', 'a', 'https://github.com/macrocosm-os/apex', None),
                   (64, 'Chutes', 'c', 'https://github.com/chutesai/chutes.git', 'https://chutes.ai')])
    c.commit()
    c.close()
    news._ready.clear()
    monkeypatch.setattr(news, 'HOST_GAP', {})
    return tmp_path


def test_parse_rss_and_atom():
    items = news.parse_feed(RSS)
    assert len(items) == 4
    a = items[0]
    assert a['url'] == 'https://ex.com/a' and a['publisher'] == 'CoinDesk'
    assert '<' not in a['summary'] and 'Chutes' in a['summary']
    assert abs(a['ts'] - (NOW - 3600)) < 2
    e = news.parse_feed(ATOM)[0]
    assert e['url'].endswith('v2.0.1') and e['author'] == 'dev'
    assert abs(e['ts'] - (NOW - 7200)) < 2
    assert news.parse_feed(b'not xml') == []


def test_discover_feed():
    page = '<html><head><link rel="alternate" type="application/rss+xml" href="/blog/rss.xml"></head>'
    assert news.discover_feed('https://chutes.ai/', page) == 'https://chutes.ai/blog/rss.xml'
    assert news.discover_feed('https://x.io', '<link rel="stylesheet" href="a.css">') is None


def test_relevance():
    apex = {'netuid': 1, 'name': 'Apex'}
    assert not news.mentions(apex, 'Apex Legends season 30 patch notes', need_context=True)
    assert news.mentions(apex, 'Bittensor Apex hits new high', need_context=True)
    assert news.mentions(apex, 'SN1 is up', need_context=True)          # SN<n> alone is enough
    assert not news.mentions(apex, 'SN12 is up', need_context=True)
    assert news.mentions({'netuid': 17, 'name': '404—GEN'}, '404 GEN on bittensor', True)
    assert not news.mentions({'netuid': 2, 'name': 'ai'}, 'ai on bittensor', True)  # too generic
    subs = [{'netuid': 1, 'name': 'Apex'}, {'netuid': 64, 'name': 'Chutes'}]
    assert news.match_subnets('Chutes and Apex on Bittensor', subs) == [1, 64]
    assert news.match_subnets('Chutes and Apex', subs) == []           # outlet needs context


def test_store_dedupes_mutes_and_flags_focus(fresh):
    items = [dict(i, kind='news') for i in news.parse_feed(RSS)]
    added = news.store(1, 'gnews', items)
    assert added == 3                       # the presale promo is muted
    assert news.store(1, 'gnews', items) == 0                       # same urls
    dup = dict(items[2], url='https://mirror.com/c')                # same headline, new url
    assert news.store(1, 'reddit', [dup]) == 0
    got = news.news(netuid=1, days=0)
    assert got['total'] == 3
    focus = {i['title']: i['focus'] for i in got['items']}
    assert focus['Bittensor SN1 Apex hits new high'] == 1
    assert focus['Chutes ships TEE inference - CoinDesk'] == 0
    assert news.news(netuid=1, days=0, focused=True)['total'] == 2
    assert news.news(netuid=1, days=0, search='TEE')['total'] == 1
    assert news.news(netuid=64, days=0)['total'] == 0


def test_refresh_runs_sources_and_logs(fresh, monkeypatch):
    seen = []

    def fake_fetch(url, accept='*/*'):
        seen.append(url)
        if 'github.com' in url:
            return ATOM
        if 'reddit' in url:
            raise RuntimeError('429')
        return RSS
    monkeypatch.setattr(news, 'fetch', fake_fetch)
    r = news.refresh(64, ['github', 'gnews', 'reddit'])
    runs = {x['source']: x for x in r['runs']}
    assert runs['github']['ok'] and runs['github']['added'] == 1       # .git suffix stripped, atom deduped
    assert any('github.com/chutesai/chutes/releases.atom' in u for u in seen)
    assert runs['gnews']['ok'] and runs['gnews']['found'] == 1         # headline must name Chutes
    assert not runs['reddit']['ok'] and '429' in runs['reddit']['error']
    st = news.status()
    assert st['items'] == 2 and st['subnets_covered'] == 1
    assert st['recent_errors'][0]['source'] == 'reddit'
    # root (netuid 0) never scrapes github/site — it has none
    r0 = news.refresh(0, ['github', 'gnews'])
    assert [x['source'] for x in r0['runs']] == ['gnews']
    b = news.buzz(days=7)['subnets']
    assert {x['netuid'] for x in b} == {0, 64}
    assert next(x for x in b if x['netuid'] == 64)['releases'] == 1


def test_feeds_match_and_config(fresh, monkeypatch):
    monkeypatch.setattr(news, 'fetch', lambda url, accept='*/*': RSS)
    assert news.feeds() == news.DEFAULT_FEEDS
    news._save_feeds([{'url': 'https://outlet.com/rss', 'label': 'Outlet'}])
    r = news.refresh_feeds()
    assert r['runs'][0]['ok']
    by = {}
    for i in news.news(days=0)['items']:
        by.setdefault(i['netuid'], []).append(i['title'])
    assert 'Bittensor SN1 Apex hits new high' in by[1]
    assert 'Chutes ships TEE inference - CoinDesk' in by[64]
    assert not any('Legends' in t for ts in by.values() for t in ts)
    out = news.add_feed('https://another.com/feed', netuid=64)
    assert out['feeds'] == 2 and json.load(open(news.feeds_path()))[1]['netuid'] == 64
    news.remove_feed('https://another.com/feed')
    assert len(news.feeds()) == 1
    with pytest.raises(ValueError):
        news.add_feed('ftp://nope')


def test_news_tools_registered(fresh):
    names = {t.name for t in tools.TOOLS}
    assert {'bt_news', 'bt_news_buzz', 'bt_news_refresh', 'bt_news_sources',
            'bt_news_add_feed', 'bt_news_remove_feed'} <= names
    for n in names:
        if n.startswith('bt_news'):
            t = tools.TOOL_MAP[n]
            assert t.local and not t.mutates and t.group == 'News'
    assert tools.call_tool('bt_news', {'days': 0})['total'] == 0
