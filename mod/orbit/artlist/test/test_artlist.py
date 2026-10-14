"""Offline tests always run; live tests only when ARTLIST_LIVE=1 (network)."""
import json
import os
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from artlistapi import api, client, mcp_server  # noqa: E402

live = pytest.mark.skipif(os.environ.get('ARTLIST_LIVE') != '1',
                          reason='set ARTLIST_LIVE=1 to hit search-api.artlist.io')


def test_config_fns_exist_on_mod():
    cfg = json.loads((MODULE_DIR / 'config.json').read_text())
    import mod
    m = mod.Mod()
    for fn in cfg['fns']:
        assert callable(getattr(m, fn)), f'config.json fns lists {fn} but Mod has no such method'


def test_song_normalizer():
    s = api._song({'songId': '1', 'songName': 'n', 'artistName': 'a', 'albumName': 'al',
                   'duration': '2:00', 'sitePlayableFilePath': 'https://x', 'tags': [{'name': 't'}]})
    assert s['id'] == '1' and s['tags'] == ['t'] and s['preview_url'] == 'https://x'
    assert api._song({})['preview_url'] is None


def test_preview_guard_refuses_foreign_hosts(tmp_path):
    with pytest.raises(client.ArtlistError):
        client.fetch_preview('https://evil.example.com/x.aac', tmp_path / 'x.aac')
    with pytest.raises(client.ArtlistError):
        client.fetch_preview('https://notartlist.io/x.aac', tmp_path / 'x.aac')


def test_mcp_dispatch_shapes():
    resp = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    names = {t['name'] for t in resp['result']['tools']}
    assert {'artlist_music', 'artlist_song', 'artlist_gq', 'artlist_status'} <= names
    assert mcp_server.handle_message({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    err = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 2, 'method': 'nope'})
    assert err['error']['code'] == -32601


@live
def test_live_music_search():
    r = api.music('epic cinematic', k=2)
    assert r['total'] > 0 and len(r['results']) == 2
    assert r['results'][0]['preview_url'].startswith('https://')


@live
def test_live_songs_by_id():
    r = api.songs(['89458'])
    assert r and r[0]['id'] == '89458'


def test_sfx_rejects_bad_sort():
    with pytest.raises(client.ArtlistError):
        api.sfx('whoosh', sort='RELEVANCE')


@live
def test_live_sfx_search():
    r = api.sfx('whoosh', k=2)
    assert r['results'] and r['results'][0]['preview_url'].startswith('https://')


@live
def test_live_footage_search():
    r = api.footage('ocean waves', k=2)
    assert r['total'] > 0 and r['results'][0]['duration_s'] > 0


@live
def test_live_templates_search():
    r = api.templates('intro', k=1)
    assert r['results'] and r['results'][0]['software']


@live
def test_live_voices():
    r = api.voices(k=2)
    assert r['results'] and r['results'][0]['accents']


# ── server: console + stream relay (offline, ephemeral port) ─────────────────

@pytest.fixture(scope='module')
def srv():
    import threading
    from http.server import ThreadingHTTPServer
    from artlistapi import server
    s = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{s.server_address[1]}'
    s.shutdown()


def _get(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type', ''), e.read()


def test_console_served_bare_and_prefixed(srv):
    for path in ('/', '/artlist/', '/artlist', '/index.html'):
        code, ctype, body = _get(srv + path)
        assert code == 200 and ctype.startswith('text/html'), path
        assert b'<base href="/artlist/">' in body
    code, ctype, _ = _get(srv + '/artlist/app.js')
    assert code == 200 and ctype == 'text/javascript'
    code, ctype, _ = _get(srv + '/app.css')
    assert code == 200 and ctype == 'text/css'
    code, ctype, _ = _get(srv + '/fonts/vt323-latin.woff2')
    assert code == 200 and ctype == 'font/woff2'


def test_console_does_not_shadow_api_or_leak_files(srv):
    code, ctype, body = _get(srv + '/health')
    assert code == 200 and json.loads(body)['ok'] is True
    code, _, body = _get(srv + '/artlist/api/health')
    assert code == 200 and json.loads(body)['ok'] is True
    code, _, _ = _get(srv + '/fonts/../../config.json')
    assert code == 404
    code, _, _ = _get(srv + '/artlistapi/client.py')
    assert code == 404


def test_stream_refuses_foreign_hosts(srv):
    from urllib.parse import quote
    for bad in ('https://evil.example.com/x.aac', 'http://cms-public-artifacts.artlist.io/x', 'https://notartlist.io/x'):
        code, _, body = _get(srv + '/stream?u=' + quote(bad, safe=''))
        assert code == 400 and b'artlist.io' in body, bad


@live
def test_live_stream_relays_audio_with_range(srv):
    import urllib.request
    from urllib.parse import quote
    url = api.music('jazz', k=1)['results'][0]['preview_url']
    req = urllib.request.Request(srv + '/stream?u=' + quote(url, safe=''), headers={'Range': 'bytes=0-99'})
    with urllib.request.urlopen(req, timeout=30) as r:
        assert r.status == 206 and r.headers['Content-Type'].startswith('audio/')
        assert len(r.read()) == 100
