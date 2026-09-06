"""Offline tests: URI parsing, normalizers, PKCE, routing, MCP wire format.

Nothing here touches the network or the operator's ~/.mod/spotify — the
adapter is pointed at a tmp keystore and its HTTP layer is stubbed.
"""

import base64
import hashlib
import json
import os
import sys
import urllib.parse

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import api                                          # noqa: E402
import mcp as mcp_server                            # noqa: E402
import spotify as S                                 # noqa: E402


@pytest.fixture
def sp(tmp_path, monkeypatch):
    """An adapter with tmp credential files and a recording HTTP layer."""
    for var in ('SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET',
                'SPOTIFY_REDIRECT_URI', 'SPOTIFY_ACCESS_TOKEN'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(S, 'PKCE_PATH', str(tmp_path / 'pkce.json'))
    client = S.Spotify(client_id='cid', client_secret='hunter2-do-not-log',
                       keys_path=str(tmp_path / 'keys.json'),
                       auth_path=str(tmp_path / 'auth.json'))
    client.calls = []
    client.responses = {}

    def fake_request(method, path, params=None, body=None, user=True, _retry=True):
        client.calls.append({'method': method, 'path': path,
                             'params': params or {}, 'body': body})
        return client.responses.get(path, {})

    client.request = fake_request
    return client


# ── uri parsing ──

@pytest.mark.parametrize('value,expected', [
    ('spotify:track:4cOdK2wGLETKBW3PvgPWqT', ('track', '4cOdK2wGLETKBW3PvgPWqT')),
    ('https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3', ('album', '1DFixLWuPkv3KT3TnV35m3')),
    ('https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT?si=x',
     ('track', '4cOdK2wGLETKBW3PvgPWqT')),
    ('4cOdK2wGLETKBW3PvgPWqT', ('track', '4cOdK2wGLETKBW3PvgPWqT')),
    ('boards of canada roygbiv', (None, None)),
    ('', (None, None)),
])
def test_parse_uri(value, expected):
    assert S.parse_uri(value) == expected


def test_bare_id_takes_the_caller_s_kind():
    assert S.to_uri('1DFixLWuPkv3KT3TnV35m3', 'playlist') == \
        'spotify:playlist:1DFixLWuPkv3KT3TnV35m3'


# ── normalizers ──

def test_track_is_flattened():
    t = S.track({'name': 'Roygbiv', 'duration_ms': 152000, 'type': 'track',
                 'id': 'x', 'uri': 'spotify:track:x',
                 'artists': [{'name': 'Boards of Canada'}, {'name': 'Guest'}],
                 'album': {'name': 'Music Has the Right to Children'},
                 'external_urls': {'spotify': 'https://open.spotify.com/track/x'}})
    assert t['artists'] == 'Boards of Canada, Guest'
    assert t['album'] == 'Music Has the Right to Children'
    assert t['duration'] == '2:32'
    assert t['url'].endswith('/track/x')


def test_item_dispatches_on_type():
    assert S.item({'type': 'artist', 'name': 'Sade'})['type'] == 'artist'
    assert S.item({'type': 'playlist', 'name': 'dinner'})['name'] == 'dinner'


# ── auth ──

def test_missing_client_id_is_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.delenv('SPOTIFY_CLIENT_ID', raising=False)
    bare = S.Spotify(keys_path=str(tmp_path / 'k.json'), auth_path=str(tmp_path / 'a.json'))
    with pytest.raises(S.SpotifyError) as e:
        bare.authorize_url()
    assert 'client_id' in e.value.message
    assert 'dashboard' in e.value.hint


def test_authorize_url_is_pkce_s256(sp):
    got = sp.authorize_url()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(got['url']).query)
    assert q['code_challenge_method'] == ['S256']
    assert q['client_id'] == ['cid'] and q['response_type'] == ['code']
    verifier = json.load(open(S.PKCE_PATH))['verifier']
    expect = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    assert q['code_challenge'] == [expect]
    assert 'user-modify-playback-state' in q['scope'][0]


def test_exchange_rejects_a_state_mismatch(sp):
    sp.authorize_url()
    with pytest.raises(S.SpotifyError):
        sp.exchange('somecode', state='not-the-one')


def test_set_key_writes_0600(sp):
    sp.set_key(client_id='new', client_secret='shh')
    assert oct(os.stat(sp.keys_path).st_mode)[-3:] == '600'
    assert json.load(open(sp.keys_path))['client_id'] == 'new'


def test_status_never_leaks_a_secret(sp):
    out = sp.status()
    assert out['client_secret'] == 'set'
    assert 'hunter2' not in json.dumps(out)
    assert out['logged_in'] is False


def test_not_logged_in_says_how_to_log_in(tmp_path):
    bare = S.Spotify(client_id='cid', keys_path=str(tmp_path / 'k.json'),
                     auth_path=str(tmp_path / 'a.json'))
    with pytest.raises(S.SpotifyError) as e:
        bare.me()
    assert e.value.status == 401 and 'login' in e.value.hint


# ── verbs ──

def test_play_a_phrase_searches_then_plays(sp):
    sp.responses['/search'] = {'tracks': {'items': [
        {'type': 'track', 'name': 'Roygbiv', 'uri': 'spotify:track:abc',
         'artists': [{'name': 'Boards of Canada'}]}]}}
    sp.play(query='roygbiv')
    searched, played = sp.calls[0], sp.calls[1]
    assert searched['path'] == '/search' and searched['params']['type'] == 'track'
    assert played['path'] == '/me/player/play'
    assert played['body'] == {'uris': ['spotify:track:abc']}


def test_play_an_album_uri_uses_context_not_uris(sp):
    sp.play(uri='spotify:album:1DFixLWuPkv3KT3TnV35m3')
    assert sp.calls[0]['body'] == {'context_uri': 'spotify:album:1DFixLWuPkv3KT3TnV35m3'}


def test_play_with_no_target_resumes(sp):
    sp.play()
    assert sp.calls[0]['path'] == '/me/player/play' and sp.calls[0]['body'] is None


def test_device_is_resolved_by_name(sp):
    sp.responses['/me/player/devices'] = {'devices': [
        {'id': 'dev1', 'name': 'Kitchen speaker', 'type': 'Speaker', 'is_active': False}]}
    sp.pause(device='kitchen')
    paused = next(c for c in sp.calls if c['path'] == '/me/player/pause')
    assert paused['params']['device_id'] == 'dev1'


def test_unknown_device_lists_the_real_ones(sp):
    sp.responses['/me/player/devices'] = {'devices': [
        {'id': 'dev1', 'name': 'Kitchen speaker', 'is_active': True}]}
    with pytest.raises(S.SpotifyError) as e:
        sp.transfer('bathroom')
    assert 'Kitchen speaker' in e.value.hint


def test_volume_is_clamped(sp):
    sp.volume(500)
    assert sp.calls[0]['params']['volume_percent'] == 100


def test_repeat_rejects_nonsense(sp):
    with pytest.raises(S.SpotifyError):
        sp.repeat('sometimes')


def test_nothing_playing_is_not_an_error(sp):
    sp.responses['/me/player'] = {}
    assert sp.now_playing() == {'playing': False,
                                'note': 'nothing is playing on any device'}


def test_top_validates_time_range(sp):
    with pytest.raises(S.SpotifyError):
        sp.top(time_range='last_tuesday')


def test_playlist_add_resolves_phrases_and_batches(sp):
    sp.responses['/search'] = {'tracks': {'items': [
        {'type': 'track', 'name': 'X', 'uri': 'spotify:track:zzz', 'artists': []}]}}
    out = sp.playlist_add('spotify:playlist:pid', ['khruangbin', 'spotify:track:kept'])
    assert out['added'] == ['spotify:track:zzz', 'spotify:track:kept']
    posts = [c for c in sp.calls if c['path'] == '/playlists/pid/tracks']
    assert len(posts) == 1 and posts[0]['method'] == 'POST'


def test_search_needs_a_query(sp):
    with pytest.raises(S.SpotifyError):
        sp.search('')


def test_search_singularizes_the_type(sp):
    sp.search('sade', type='tracks,artists')
    assert sp.calls[0]['params']['type'] == 'track,artist'


# ── mcp wire format ──

def test_every_tool_has_a_schema_and_a_handler():
    assert len(mcp_server.TOOLS) == 22
    for name, tool in mcp_server.TOOLS.items():
        assert name.startswith('spotify_')
        assert tool['description'] and callable(tool['handler'])
        assert tool['inputSchema']['type'] == 'object'
        for req in tool['inputSchema'].get('required', []):
            assert req in tool['inputSchema']['properties']


def test_initialize_echoes_a_supported_protocol_version():
    r = mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                           'params': {'protocolVersion': '2025-06-18'}})
    assert r['result']['protocolVersion'] == '2025-06-18'
    assert r['result']['serverInfo']['name'] == 'spotify'


def test_unknown_protocol_version_falls_back():
    r = mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                           'params': {'protocolVersion': '1999-01-01'}})
    assert r['result']['protocolVersion'] == mcp_server.DEFAULT_PROTOCOL_VERSION


def test_notifications_get_no_response():
    assert mcp_server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_tools_list_matches_the_registry():
    r = mcp_server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    assert len(r['result']['tools']) == len(mcp_server.TOOLS)


def test_unknown_tool_is_a_jsonrpc_error():
    r = mcp_server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                           'params': {'name': 'spotify_teleport'}})
    assert r['error']['code'] == -32602


def test_a_failing_tool_is_iserror_not_a_crash(monkeypatch, tmp_path):
    """Logged out is the common case — it must come back as a readable hint."""
    monkeypatch.delenv('SPOTIFY_ACCESS_TOKEN', raising=False)
    monkeypatch.setattr(S, 'AUTH_PATH', str(tmp_path / 'nope.json'))
    monkeypatch.setattr(S, 'KEYS_PATH', str(tmp_path / 'nokeys.json'))
    r = mcp_server.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                           'params': {'name': 'spotify_now_playing', 'arguments': {}}})
    assert r['result']['isError'] is True
    assert r['result']['structuredContent']['status'] == 401


def test_tool_names_match_config():
    cfg = json.load(open(os.path.join(HERE, 'config.json')))
    assert cfg['tools'] == list(mcp_server.TOOLS)


def test_config_fns_exist_on_the_mod():
    import mod as spotify_mod
    cfg = json.load(open(os.path.join(HERE, 'config.json')))
    m = spotify_mod.Mod()
    for fn in cfg['fns']:
        assert callable(getattr(m, fn)), fn


# ── rest surface ──

def test_info_lists_the_mcp_endpoint():
    got = api.info()
    assert got['mcp']['endpoint'] == 'POST /mcp'
    assert got['mcp']['tools'] == len(mcp_server.TOOLS)


def test_unknown_route_404s():
    with pytest.raises(S.SpotifyError) as e:
        api.route('GET', '/nope', '', {})
    assert e.value.status == 404


def test_health_needs_no_auth():
    assert api.route('GET', '/health', '', {})['ok'] is True
