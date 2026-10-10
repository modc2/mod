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
