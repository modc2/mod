"""Tests for orbit/configs — run against a temp fleet so the real configs are
never touched (see claude_pytest_config_mutation: never mutate live config.json
from tests)."""
import importlib.util
import json
import os
import pytest

# load the anchor by path (not `import mod`, which is the SDK's name)
_ANCHOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mod.py')
_spec = importlib.util.spec_from_file_location('configs_anchor', _ANCHOR)
_anchor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_anchor)
Mod = _anchor.Mod


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, indent=4)


@pytest.fixture
def fleet(tmp_path):
    """A tiny fake fleet: two roots, nested module, broken JSON, port clash."""
    core = tmp_path / 'core'
    orbit = tmp_path / 'orbit'
    write(str(core / 'alpha' / 'config.json'),
          {'name': 'alpha', 'version': '1.0.0', 'description': 'first module',
           'app_port': 5001, 'urls': {'app': 'https://x/alpha'}})
    write(str(core / 'server' / 'beta' / 'config.json'),      # nested (depth 2)
          {'name': 'beta', 'description': 'nested module', 'api_port': 5002})
    write(str(orbit / 'gamma' / 'config.json'),
          {'name': 'gamma', 'description': 'port clasher', 'app_port': 5001})
    write(str(orbit / 'delta' / 'config.json'),               # name != dir, no desc,
          {'name': 'omega', 'app_port': 5003,                 # self-alias ≠ collision
           'gateway_port': 5003})
    write(str(orbit / 'broken' / 'config.json'), '{not json')
    return Mod(roots=[str(core), str(orbit)], state_path=str(tmp_path / 'state'))


# --- scan ---------------------------------------------------------------------

def test_configs_scans_fleet(fleet):
    out = fleet.configs()
    assert out['count'] == 5
    assert out['invalid'] == 1
    names = {r['name'] for r in out['configs']}
    assert {'alpha', 'beta', 'gamma', 'omega', 'broken'} <= names


def test_configs_finds_nested(fleet):
    rows = {r['name']: r for r in fleet.configs()['configs']}
    assert rows['beta']['orbit'] == 'core'
    assert rows['beta']['ports'] == {'api_port': 5002}


def test_configs_search(fleet):
    out = fleet.configs(search='nested')
    assert out['count'] == 1
    assert out['configs'][0]['name'] == 'beta'


def test_forward_is_configs(fleet):
    assert fleet.forward()['count'] == fleet.configs()['count']


# --- get / path ------------------------------------------------------------------

def test_get_full_config(fleet):
    out = fleet.get('alpha')
    assert out['config']['version'] == '1.0.0'
    assert out['path'].endswith('alpha/config.json')


def test_get_dot_path_key(fleet):
    assert fleet.get('alpha', 'urls.app')['value'] == 'https://x/alpha'


def test_get_missing_key_raises(fleet):
    with pytest.raises(KeyError):
        fleet.get('alpha', 'urls.nope')


def test_path_falls_back_to_dir_name(fleet):
    # 'delta' dir holds a config named 'omega'; both should resolve
    assert fleet.path('delta') == fleet.path('omega')


def test_path_unknown_raises(fleet):
    with pytest.raises(FileNotFoundError):
        fleet.path('nonexistent')


# --- set / unset / coercion -------------------------------------------------------

def test_set_coerces_json_values(fleet):
    assert fleet.set('alpha', 'app_port', '5099')['new'] == 5099
    assert fleet.set('alpha', 'flag', 'true')['new'] is True
    assert fleet.set('alpha', 'note', 'plain words')['new'] == 'plain words'
    assert fleet.get('alpha')['config']['app_port'] == 5099


def test_set_dot_path_creates_nesting(fleet):
    fleet.set('alpha', 'urls.api', 'https://x/api/alpha')
    cfg = fleet.get('alpha')['config']
    assert cfg['urls'] == {'app': 'https://x/alpha', 'api': 'https://x/api/alpha'}
    fleet.set('alpha', 'brand.new.deep', '1')
    assert fleet.get('alpha')['config']['brand'] == {'new': {'deep': 1}}


def test_unset(fleet):
    fleet.unset('alpha', 'urls.app')
    assert 'app' not in fleet.get('alpha')['config']['urls']
    with pytest.raises(KeyError):
        fleet.unset('alpha', 'urls.app')


def test_set_refuses_broken_json(fleet):
    with pytest.raises(ValueError):
        fleet.set('broken', 'name', 'fixed')


# --- backups / restore -------------------------------------------------------------

def test_set_backs_up_and_restore_undoes(fleet):
    before = fleet.get('alpha')['config']
    res = fleet.set('alpha', 'version', '"9.9.9"')
    assert os.path.exists(res['backup'])
    assert fleet.get('alpha')['config']['version'] == '9.9.9'
    fleet.restore('alpha')
    assert fleet.get('alpha')['config'] == before


def test_restore_without_backups_raises(fleet):
    with pytest.raises(FileNotFoundError):
        fleet.restore('gamma')


def test_backups_newest_first(fleet):
    fleet.set('alpha', 'a', '1')
    fleet.set('alpha', 'b', '2')
    backs = fleet.backups('alpha')
    assert len(backs) == 2
    assert backs == sorted(backs, reverse=True)


def test_restore_rejects_foreign_path(fleet, tmp_path):
    fleet.set('alpha', 'a', '1')
    evil = tmp_path / 'evil.json'
    write(str(evil), {'name': 'evil'})
    with pytest.raises(FileNotFoundError):
        fleet.restore('alpha', backup=str(evil))


# --- lint / ports ---------------------------------------------------------------

def test_lint_finds_everything(fleet):
    out = fleet.lint()
    assert out['checked'] == 5
    issues = ' | '.join(i['issue'] for i in out['issues'])
    assert 'invalid JSON' in issues
    assert 'port 5001 claimed by 2 modules' in issues
    assert "name 'omega' != directory 'delta'" in issues
    assert 'missing "description"' in issues
    assert out['errors'] >= 2                       # broken JSON + port clash
    # errors sort before warnings
    levels = [i['level'] for i in out['issues']]
    assert levels == sorted(levels, key=lambda l: l != 'error')


def test_lint_single_module(fleet):
    out = fleet.lint('alpha')
    assert out['checked'] == 1
    assert out['issues'] == []


def test_ports_map_and_collisions(fleet):
    out = fleet.ports()
    by_port = {t['port']: t for t in out['ports']}
    assert by_port[5001]['collision'] is True
    assert sorted(by_port[5001]['owners']) == ['alpha.app_port', 'gamma.app_port']
    assert by_port[5002]['collision'] is False
    assert by_port[5003]['collision'] is False     # one module aliasing its own port
    assert out['collisions'] == 1


# --- web API ----------------------------------------------------------------------

def test_serve_api_endpoints(fleet):
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), fleet._make_handler())
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        def get(p):
            return urllib.request.urlopen(f'http://127.0.0.1:{port}{p}', timeout=5)
        assert b'configs' in get('/').read()                       # UI
        assert b'configs' in get('/configs/').read()               # gateway prefix
        data = _json.loads(get('/api/configs').read())
        assert data['count'] == 5
        one = _json.loads(get('/api/config?mod=alpha').read())
        assert one['config']['name'] == 'alpha'
        lint = _json.loads(get('/api/lint').read())
        assert lint['errors'] >= 2
        ports = _json.loads(get('/api/ports').read())
        assert ports['collisions'] == 1
        with pytest.raises(urllib.error.HTTPError):               # unknown route → 404
            get('/api/nope')
    finally:
        httpd.shutdown()


def test_info(fleet):
    out = fleet.info()
    assert out['name'] == 'configs'
    assert out['modules'] == 5
    assert out['invalid'] == 1
