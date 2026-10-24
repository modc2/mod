"""Offline tests: no network, no Google. The state dir is a tmp path."""
import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from ytmod import api, auth, mcp_server, server, store  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('YOUTUBE_HOME', str(tmp_path))
    yield tmp_path


# ── resolve: every YouTube address shape -> framable embed ──

@pytest.mark.parametrize('inp, kind, ident', [
    ('dQw4w9WgXcQ', 'video', 'dQw4w9WgXcQ'),
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'video', 'dQw4w9WgXcQ'),
    ('https://youtu.be/dQw4w9WgXcQ?t=42', 'video', 'dQw4w9WgXcQ'),
    ('youtube.com/shorts/dQw4w9WgXcQ', 'video', 'dQw4w9WgXcQ'),
    ('https://m.youtube.com/live/dQw4w9WgXcQ', 'video', 'dQw4w9WgXcQ'),
    ('https://www.youtube.com/embed/dQw4w9WgXcQ', 'video', 'dQw4w9WgXcQ'),
    ('https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf', 'playlist',
     'PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf'),
    ('PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf', 'playlist', 'PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf'),
    ('https://www.youtube.com/channel/UCXuqSBlHAE6Xw-yeJA0Tunw', 'channel', 'UCXuqSBlHAE6Xw-yeJA0Tunw'),
    ('UCXuqSBlHAE6Xw-yeJA0Tunw', 'channel', 'UCXuqSBlHAE6Xw-yeJA0Tunw'),
])
def test_resolve_shapes(inp, kind, ident):
    r = api.resolve(inp)
    assert r['kind'] == kind and r['id'] == ident
    assert r['embed'].startswith('https://www.youtube.com/embed/')
    assert 'youtube.com' in r['watch']


def test_resolve_video_start_and_list():
    r = api.resolve('https://youtu.be/dQw4w9WgXcQ?t=1m30s&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf')
    assert r['start'] == 90 and 'start=90' in r['embed'] and 'list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf' in r['embed']


def test_resolve_channel_embed_is_uploads_playlist():
    r = api.resolve('UCXuqSBlHAE6Xw-yeJA0Tunw')
    assert 'list=UUXuqSBlHAE6Xw-yeJA0Tunw' in r['embed']


def test_resolve_words_are_search_and_handles_hit_network(monkeypatch):
    assert api.resolve('lofi hip hop')['kind'] == 'search'
    assert api.resolve('https://www.youtube.com/results?search_query=cats') == {
        'kind': 'search', 'q': 'cats', 'input': 'https://www.youtube.com/results?search_query=cats'}
    monkeypatch.setattr(api, 'channel_id_from_handle', lambda h: 'UCXuqSBlHAE6Xw-yeJA0Tunw')
    r = api.resolve('@LinusTechTips')
    assert r['kind'] == 'channel' and r['handle'] == '@LinusTechTips'
    assert api.resolve('https://www.youtube.com/@LinusTechTips/videos')['id'] == 'UCXuqSBlHAE6Xw-yeJA0Tunw'


def test_resolve_rejects_foreign_hosts():
    with pytest.raises(api.ApiError):
        api.resolve('https://vimeo.com/123')
    with pytest.raises(api.ApiError):
        api.resolve('')


def test_queue_embed():
    u = api.embed_url('queue', '', queue=['dQw4w9WgXcQ', 'aaaaaaaaaaa', 'bbbbbbbbbbb'])
    assert u.startswith('https://www.youtube.com/embed/dQw4w9WgXcQ?') and 'playlist=dQw4w9WgXcQ%2Caaaaaaaaaaa%2Cbbbbbbbbbbb' in u


# ── rss parsing ──

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>Linus Tech Tips</title>
 <entry><yt:videoId>dQw4w9WgXcQ</yt:videoId><title>A &amp; B</title><published>2026-10-15T10:00:00+00:00</published>
  <media:group><media:thumbnail url="https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"/><media:description>desc</media:description>
  <media:community><media:statistics views="1234"/></media:community></media:group></entry>
 <entry><yt:videoId>aaaaaaaaaaa</yt:videoId><title>Second</title><published>2026-10-14T10:00:00+00:00</published></entry>
</feed>"""


class _Resp:
    def __init__(self, data): self.data = data
    def read(self, *a): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_rss_and_home(monkeypatch):
    monkeypatch.setattr(api.urllib.request, 'urlopen', lambda req, timeout=0: _Resp(FEED))
    r = api.rss('UCXuqSBlHAE6Xw-yeJA0Tunw', 5)
    assert r['title'] == 'Linus Tech Tips' and len(r['items']) == 2
    it = r['items'][0]
    assert it['title'] == 'A & B' and it['views'] == 1234 and it['embed'].endswith('dQw4w9WgXcQ?rel=0&modestbranding=1&enablejsapi=1')
    h = api.home(['UCXuqSBlHAE6Xw-yeJA0Tunw', 'UCsXVk37bltHxD1rDPwtNM8Q'], n=3)
    assert len(h['items']) == 3 and h['channels'] == 2 and h['embed'].startswith('https://www.youtube.com/embed/')
    assert h['items'][0]['published'] >= h['items'][1]['published']


def test_video_falls_back_to_oembed_without_auth(monkeypatch):
    body = json.dumps({'title': 'Never Gonna Give You Up', 'author_name': 'Rick Astley',
                       'author_url': 'https://www.youtube.com/@RickAstleyYT',
                       'thumbnail_url': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'}).encode()
    monkeypatch.setattr(api.urllib.request, 'urlopen', lambda req, timeout=0: _Resp(body))
    v = api.Session(owner=False).video('dQw4w9WgXcQ')
    assert v['source'] == 'oembed' and v['channel_id'] == '@RickAstleyYT' and v['title'].startswith('Never')


# ── store ──

def test_store_accounts_roundtrip(home):
    assert store.accounts() == [] and store.active_id() is None
    store.upsert({'id': 'UC1', 'title': 'One', 'email': 'a@x', 'refresh_token': 'r1', 'access_token': 't'})
    store.upsert({'id': 'UC2', 'title': 'Two', 'handle': '@two', 'refresh_token': 'r2'})
    assert store.active_id() == 'UC2'
    assert [a['id'] for a in store.accounts()] == ['UC2', 'UC1']
    assert 'refresh_token' not in store.accounts()[0]
    assert store.set_active('a@x')['active'] == 'UC1'
    assert store.get('@two')['id'] == 'UC2'
    # re-consent without a refresh token keeps the old one
    store.upsert({'id': 'UC1', 'access_token': 't2'}, make_active=False)
    assert store.get('UC1')['refresh_token'] == 'r1'
    assert oct(os.stat(home / 'accounts.json').st_mode)[-3:] == '600'
    assert store.remove('UC1')['active'] == 'UC2'
    with pytest.raises(KeyError):
        store.set_active('nope')


def test_client_status_hides_secret():
    st = store.set_client('abc.apps.googleusercontent.com', 'shh', 'KEY')
    assert st['oauth_client'] and st['api_key'] and 'shh' not in json.dumps(st)
    assert store.api_key() == 'KEY'


# ── auth url ──

def test_auth_start_needs_client_then_builds_url():
    with pytest.raises(auth.AuthError):
        auth.start('http://127.0.0.1:1/auth/callback')
    store.set_client('cid', 'sec')
    st = auth.start('http://127.0.0.1:1/auth/callback')
    assert st['url'].startswith(auth.AUTH_URL) and 'access_type=offline' in st['url'] and st['state'] in st['url']
    assert 'youtube.force-ssl' in st['url'] and 'youtube.upload' in st['url']
    with pytest.raises(auth.AuthError):
        auth.finish('code', 'bogus-state')


# ── session rails ──

def test_session_rails(monkeypatch):
    # no key, no account: reads say so
    with pytest.raises(api.ApiError) as e:
        api.Session(owner=True).search('x')
    assert e.value.reason == 'noauth'
    # a non-owner never reaches an account
    store.upsert({'id': 'UC1', 'title': 'One', 'refresh_token': 'r', 'access_token': 't', 'expires_at': 9e9})
    with pytest.raises(api.Forbidden):
        api.Session(owner=False).subscriptions()
    # the owner's call carries the bearer
    seen = {}

    def fake_open(req, timeout=0):
        seen['auth'] = req.get_header('Authorization'); seen['url'] = req.full_url
        return _Resp(b'{"items": []}')
    monkeypatch.setattr(api.urllib.request, 'urlopen', fake_open)
    assert api.Session(owner=True).subscriptions() == {'items': []}
    assert seen['auth'] == 'Bearer t' and 'mine=true' in seen['url']
    # public caller with an api key uses the key, never the token
    store.set_client(api_key='KEY')
    api.Session(owner=False).search('cats')
    assert seen['auth'] is None and 'key=KEY' in seen['url']


# ── mcp ──

def test_mcp_tool_list_and_rings():
    tools = mcp_server.tool_list()
    names = {t['name'] for t in tools}
    assert {'yt_open', 'yt_search', 'yt_home', 'yt_accounts', 'yt_upload', 'yt_analytics', 'yt_moderate'} <= names
    rings = {t['_meta']['ring'] for t in tools}
    assert rings == {'open', 'user', 'creator'}
    for t in tools:
        assert t['inputSchema']['type'] == 'object'
        assert 'readOnlyHint' in t['annotations']
    assert next(t for t in tools if t['name'] == 'yt_delete_video')['annotations']['destructiveHint'] is True


def test_mcp_dispatch_and_gating():
    init = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
    assert init['result']['serverInfo']['name'] == 'youtube'
    assert mcp_server.handle_message({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    r = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                   'params': {'name': 'yt_open', 'arguments': {'target': 'dQw4w9WgXcQ'}}})
    assert r['result']['isError'] is False and r['result']['structuredContent']['kind'] == 'video'
    # account tools are closed to the public and open to the owner
    pub = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                     'params': {'name': 'yt_accounts'}}, {'owner': False})
    assert pub['result']['isError'] and 'owner' in pub['result']['content'][0]['text']
    own = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                                     'params': {'name': 'yt_accounts'}}, {'owner': True})
    assert own['result']['isError'] is False and own['result']['structuredContent']['accounts'] == []
    st = mcp_server.call_tool('yt_status', {}, {'owner': True})['structuredContent']
    assert st['rails'] == {'keyless': True, 'api_key': False, 'account': False}
    bad = mcp_server.call_tool('yt_video', {}, {'owner': True})
    assert bad['isError'] and 'missing argument' in bad['content'][0]['text']


# ── http server ──

@pytest.fixture
def srv():
    s = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{s.server_port}'
    s.shutdown()


def _get(url, headers=None, method='GET', body=None):
    req = urllib.request.Request(url, headers=headers or {}, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_server_routes(srv, monkeypatch):
    code, h, body = _get(srv + '/health')
    assert code == 200 and json.loads(body)['ok'] and json.loads(body)['tools'] == len(mcp_server.TOOLS)
    code, h, body = _get(srv + '/', {'Accept': 'text/html'})
    assert code == 200 and b'<iframe' in body and 'text/html' in h['Content-Type']
    code, h, body = _get(srv + '/youtube/api/open?target=https://youtu.be/dQw4w9WgXcQ')
    assert code == 200 and json.loads(body)['embed'].startswith('https://www.youtube.com/embed/dQw4w9WgXcQ')
    # /go is a redirect to the embed; follow nothing
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(srv + '/go?target=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf')
    except urllib.error.HTTPError as e:
        assert e.code == 302 and 'list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf' in e.headers['Location']
    # loopback without a proxy header IS the box: account tools open
    code, h, body = _get(srv + '/accounts')
    assert code == 200 and json.loads(body)['accounts'] == []
    # the same call forwarded through a proxy is public: closed
    code, h, body = _get(srv + '/accounts', {'X-Forwarded-For': '1.2.3.4'})
    assert code == 401
    code, h, body = _get(srv + '/auth/local', {'X-Forwarded-For': '1.2.3.4'})
    assert code == 403
    # mcp over http, public ctx
    code, h, body = _get(srv + '/mcp', method='POST', body={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert code == 200 and len(json.loads(body)['result']['tools']) == len(mcp_server.TOOLS)
    code, h, body = _get(srv + '/mcp', {'X-Forwarded-For': '1.2.3.4'}, 'POST',
                         {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'yt_feed'}})
    assert json.loads(body)['result']['isError'] is True
    code, h, body = _get(srv + '/tools')
    assert code == 200 and '/search' in json.loads(body)['rest']
    code, h, body = _get(srv + '/nope')
    assert code == 404
