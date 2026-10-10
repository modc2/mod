"""Offline tests: a throwaway studio in a temp dir, no network, no ffmpeg."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.append(HERE)

import studio as studiomod  # noqa: E402

PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)  # bytes are bytes to the store
MP3 = (b'ID3' + b'\x00' * 64)


@pytest.fixture()
def st(tmp_path):
    return studiomod.Studio(str(tmp_path))


def test_add_asset_and_dedupe(st):
    a = st.add_asset('clip.mp4', b'fake video bytes')
    assert a['kind'] == 'video' and a['ext'] == 'mp4'
    again = st.add_asset('renamed.mp4', b'fake video bytes')
    assert again['id'] == a['id']            # same bytes, same id
    assert len(st.assets()) == 1


def test_kinds_and_rejects(st):
    assert st.add_asset('song.mp3', MP3)['kind'] == 'audio'
    assert st.add_asset('still.png', PNG)['kind'] == 'image'
    assert 'error' in st.add_asset('notes.txt', b'hi')
    assert 'error' in st.add_asset('empty.mp4', b'')
    assert [a['kind'] for a in st.assets('audio')] == ['audio']


def test_media_path_exists(st):
    a = st.add_asset('clip.webm', b'xyz')
    p = st.asset_path(a['id'])
    assert os.path.exists(p) and p.endswith('.webm')


def test_remove_asset(st):
    a = st.add_asset('clip.mp4', b'bye')
    p = st.asset_path(a['id'])
    assert st.remove_asset(a['id']) == {'removed': a['id']}
    assert not os.path.exists(p)
    assert 'error' in st.remove_asset('nope')


def test_project_roundtrip(st):
    v = st.add_asset('a.mp4', b'v1')
    s = st.add_asset('b.png', PNG)
    m = st.add_asset('c.mp3', MP3)
    tl = {'video': [{'asset': v['id']}, {'asset': s['id'], 'dur': 4}],
          'audio': [{'asset': m['id']}]}
    p = st.save_project('my cut', tl)
    got = st.get_project(p['id'])
    assert got['name'] == 'my cut'
    assert got['timeline']['video'][1]['dur'] == 4
    # update keeps the id and created stamp
    p2 = st.save_project('my cut v2', {'video': [], 'audio': []}, id=p['id'])
    assert p2['id'] == p['id'] and p2['created'] == p['created']
    assert len(st.projects()) == 1
    assert st.remove_project(p['id'])['removed'] == p['id']
    assert 'error' in st.remove_project(p['id'])


def test_render_without_ffmpeg_reports_cleanly(st, monkeypatch):
    monkeypatch.setattr(studiomod.shutil, 'which', lambda _: None)
    v = st.add_asset('a.mp4', b'v1')
    p = st.save_project('cut', {'video': [{'asset': v['id']}], 'audio': []})
    out = st.render(p['id'])
    assert 'error' in out and 'ffmpeg' in out['error']


def test_pack_roundtrip(st, tmp_path):
    v = st.add_asset('a.mp4', b'videobytes')
    m = st.add_asset('c.mp3', MP3)
    p = st.save_project('shared cut',
                        {'video': [{'asset': v['id']}],
                         'audio': [{'asset': m['id']}]})
    pack = st.export_pack(p['id'])
    assert pack['artist_pack'] == 1 and len(pack['assets']) == 2

    other = studiomod.Studio(str(tmp_path / 'other'))
    r = other.import_pack(pack)
    assert r['ok'] and r['assets'] == 2
    got = other.get_project(r['project'])
    assert got['timeline']['video'][0]['asset'] == v['id']  # ids travel intact
    assert other.get_asset(v['id'])['size'] == len(b'videobytes')
    assert 'error' in other.import_pack({'nope': 1})


def test_stats(st):
    st.add_asset('a.mp4', b'1234')
    s = st.stats()
    assert s['assets'] == 1 and s['bytes'] == 4 and s['projects'] == 0
    assert isinstance(s['ffmpeg'], bool)
