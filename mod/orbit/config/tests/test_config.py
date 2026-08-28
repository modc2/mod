"""Tests for orbit/config — read-only over the real fleet (this module never
writes a config.json), plus a live-server smoke test on an ephemeral port."""
import importlib.util
import json
import os
import threading
import urllib.request

import pytest

# load the anchor by path (not `import mod`, which is the SDK's name)
_ANCHOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mod.py')
_spec = importlib.util.spec_from_file_location('config_anchor', _ANCHOR)
_anchor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_anchor)
Mod = _anchor.Mod

from mod.core.config.config import Config

SAMPLE = {'mod': '/root/mod/mod', 'lib': '/root/mod', 'n': 3, 'ok': True,
          'nothing': None, 'fns': ['a', 'b'],
          'orbit': {'orbit': '/mod/orbit', 'core': '/mod/core', 'local': '.'}}


@pytest.fixture(scope='module')
def api():
    return Mod()


# --- lines: the structured twin of Config._fmt ---------------------------------

def rebuild(lines):
    out = []
    for l in lines:
        pad = '  ' * l['i']
        out.append(f"{pad}{l['kp']} : {l['v']}" if l['t'] != 'dict' else f"{pad}{l['k']}")
    return '\n'.join(out)


def test_lines_match_repr_exactly(api):
    lines = api._lines(SAMPLE)
    assert rebuild(lines) == str(Config.from_dict(SAMPLE))


def test_lines_align_leaves(api):
    lines = api._lines(SAMPLE)
    leaves = [l for l in lines if l['i'] == 0 and l['t'] != 'dict']
    assert len({len(l['kp']) for l in leaves}) == 1          # padded to one width
    assert [l['kp'].strip() for l in leaves] == [l['k'] for l in leaves]


def test_lines_types_and_paths(api):
    lines = {l['p']: l for l in api._lines(SAMPLE)}
    assert lines['n']['t'] == 'int'
    assert lines['ok']['t'] == 'bool'
    assert lines['nothing']['t'] == 'none'
    assert lines['fns']['t'] == 'list'
    assert lines['orbit']['t'] == 'dict' and lines['orbit']['n'] == 3
    assert lines['orbit.core']['v'] == '/mod/core' and lines['orbit.core']['i'] == 1


# --- render ----------------------------------------------------------------------

def test_render_dict_and_json_string(api):
    a = api.render(SAMPLE)
    b = api.render(json.dumps(SAMPLE))
    assert a['text'] == b['text'] == str(Config.from_dict(SAMPLE))
    assert a['lines'] == b['lines']


def test_render_rejects_non_dict(api):
    with pytest.raises(ValueError):
        api.render('[1, 2, 3]')


# --- fleet ------------------------------------------------------------------------

def test_modules_have_ids(api):
    rows = api.modules()['configs']
    ids = {r['id'] for r in rows}
    assert 'orbit/config' in ids and 'orbit/configs' in ids and 'core/config' in ids


def test_get_by_id_disambiguates_shared_name(api):
    mine = api.get('orbit/config')
    lib = api.get('core/config')
    assert mine['path'].endswith('orbit/config/config.json')
    assert lib['path'].endswith('core/config/config.json')
    assert mine['config']['app_port'] == 50240
    assert mine['text'] == str(Config.from_dict(mine['config']))


def test_get_by_name_and_dot_path(api):
    out = api.get('configs', key='urls.app')
    assert out['config'] == {'app': 'https://modc2.com/configs'}
    with pytest.raises(KeyError):
        api.get('configs', key='urls.nope')


def test_get_unknown_module(api):
    with pytest.raises(FileNotFoundError):
        api.get('no-such-module-xyz')


def test_source_has_doc_and_class(api):
    s = api.source()
    assert 'class Config(dict)' in s['source']
    assert 'Munch' in s['doc']


# --- live server smoke -------------------------------------------------------------

@pytest.fixture(scope='module')
def server(api):
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), api._make_handler())
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        body = r.read()
        return r.status, r.headers.get('Content-Type', ''), body


def test_server_index_and_gateway_prefix(server):
    for path in ('/', '/config', '/config/'):
        status, ctype, body = get(server + path)
        assert status == 200 and 'text/html' in ctype
        assert b'every config, pretty' in body


def test_server_api(server):
    status, _, body = get(server + '/api/info')
    assert status == 200 and json.loads(body)['name'] == 'config'
    status, _, body = get(server + '/config/api/config?id=orbit/config')
    out = json.loads(body)
    assert status == 200 and out['id'] == 'orbit/config' and out['lines']


def test_server_render_post(server):
    req = urllib.request.Request(
        server + '/api/render', method='POST',
        data=json.dumps({'data': SAMPLE}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        out = json.loads(r.read())
    assert out['text'] == str(Config.from_dict(SAMPLE))


def test_server_404(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(server + '/api/nope')
    assert e.value.code == 404
