"""replit — bridge tests.

Everything here runs against a real generated Repl: the suite bundles a tiny
module, boots the bundle's own main.py on a loopback port, and drives the
bridge at it. That is the only honest way to test discovery — the shape of the
null call is a contract between the template and the bridge, and a mocked one
would let the two drift apart.

    python3 -m pytest orbit/replit/tests -q
"""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


anchor = _load('replit_anchor_test', os.path.join(MODULE, 'mod.py'))
mcp = _load('replit_mcp_test', os.path.join(MODULE, 'mcp.py'))


GREETER = '''
class Mod:
    description = 'a tiny mod that lives on a Repl'

    def hello(self, name: str = 'world', loud: bool = False):
        """Greet someone by name."""
        return {'text': ('hello ' + name).upper() if loud else 'hello ' + name}

    def add(self, a, b=1):
        """Add two numbers."""
        return {'sum': a + b}
'''


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope='module')
def api(tmp_path_factory):
    """A bridge with its own state dir — never ~/.mod/replit."""
    return anchor.Mod(state_path=str(tmp_path_factory.mktemp('state')))


@pytest.fixture(scope='module')
def source(tmp_path_factory):
    """A stdlib-only module to bundle (the fleet's own all import the SDK)."""
    d = tmp_path_factory.mktemp('greeter')
    (d / 'mod.py').write_text(GREETER)
    (d / 'config.json').write_text(json.dumps(
        {'name': 'greeter', 'description': 'a tiny mod that lives on a Repl',
         'version': '0.1.0', 'anchor': 'mod.py'}))
    return str(d)


@pytest.fixture(scope='module')
def repl(api, source):
    """The bundle, actually running — this is the 'Repl'."""
    man = api.bundle(source, name='greeter')
    port = _free_port()
    proc = subprocess.Popen([sys.executable, 'main.py'], cwd=man['path'],
                            env=dict(os.environ, PORT=str(port)),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f'http://127.0.0.1:{port}'
    for _ in range(60):
        try:
            urllib.request.urlopen(url + '/health', timeout=1).read()
            break
        except Exception:                            # noqa: BLE001
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail('generated main.py never came up: ' + proc.stdout.read().decode()[:2000])
    yield url
    proc.kill()


# ── bundle ──────────────────────────────────────────────────────────────────

def test_bundle_writes_a_runnable_project(api, source):
    man = api.bundle(source, name='b1')
    names = {f['file'] for f in api.bundle_files('b1')['files']}
    assert {'.replit', 'replit.nix', 'main.py', 'mod.py', 'config.json'} <= names
    assert man['warnings'] == []                     # stdlib only, nothing to warn about
    assert 'run = "python3 main.py"' in api.bundle_file('b1', '.replit')['text']


def test_bundle_flags_the_sdk_import(api, tmp_path):
    d = tmp_path / 'sdkmod'
    d.mkdir()
    (d / 'mod.py').write_text('import mod\n\n\nclass Mod:\n    pass\n')
    (d / 'config.json').write_text('{"name": "sdkmod", "anchor": "mod.py"}')
    man = api.bundle(str(d), name='sdkmod')
    assert any('not on PyPI' in w for w in man['warnings'])


def test_bundle_file_cannot_escape(api, source):
    api.bundle(source, name='b2')
    with pytest.raises(ValueError):
        api.bundle_file('b2', '../../secrets.json')


def test_zip_is_written(api, source):
    api.bundle(source, name='b3')
    z = api.zip('b3')
    assert os.path.exists(z['zip']) and z['bytes'] > 0


@pytest.mark.parametrize('repo', [
    'owner/name', 'https://github.com/owner/name', 'https://github.com/owner/name.git',
    'github.com/owner/name/', 'https://www.github.com/owner/name'])
def test_run_url_normalises(api, repo):
    assert api.run_url(repo)['url'] == 'https://replit.com/github/owner/name'


def test_run_url_rejects_garbage(api):
    with pytest.raises(ValueError):
        api.run_url('nameonly')


def test_import_refuses_a_bare_repl_url(api):
    with pytest.raises(ValueError, match='403'):
        api.import_repl('https://replit.com/@someone/someslug')


# ── the generated Repl answers the protocol ─────────────────────────────────

def test_repl_reports_typed_signatures(repl):
    info = json.load(urllib.request.urlopen(repl + '/'))
    assert info['loaded'] and set(info['fns']) == {'add', 'hello'}
    hello = {p['name']: p for p in info['params']['hello']}
    assert hello['name']['default'] == 'world' and not hello['name']['required']
    assert {p['name']: p['required'] for p in info['params']['add']}['a'] is True
    assert info['docs']['hello'].startswith('Greet')


# ── link / discover / catalog ───────────────────────────────────────────────

def test_link_discovers(api, repl):
    rec = api.link('greeter', repl)
    assert rec['ok'] and rec['mod'] == 'greeter'
    assert rec['fns'] == ['add', 'hello']
    assert rec['params']['hello'][0]['name'] == 'name'


def test_link_survives_a_dead_repl(api):
    rec = api.link('dead', 'http://127.0.0.1:%d' % _free_port())
    assert rec['ok'] is False and rec['error']
    assert 'dead' in api._read('remotes.json', {})   # linked anyway, discoverable later
    api.unlink('dead')


def test_link_validates(api):
    with pytest.raises(ValueError):
        api.link('bad name', 'https://x.replit.dev')
    with pytest.raises(ValueError):
        api.link('ok', 'ftp://x.replit.dev')


def test_catalog_indexes_the_mods(api, repl):
    api.link('greeter', repl)
    cat = api.catalog()
    assert cat['n'] == 1 and cat['fns'] == 2 and cat['undiscovered'] == []
    mod = cat['mods'][0]
    assert mod['mod'] == 'greeter' and mod['docs']['add'] == 'Add two numbers.'


def test_catalog_reads_cache_not_network(api, repl):
    """The catalog must be free to call — the MCP tool list rebuilds from it."""
    api.link('greeter', repl)
    t0 = time.time()
    for _ in range(20):
        api.catalog()
    assert time.time() - t0 < 1.0


def test_ping_reports_a_dead_remote_without_raising(api):
    api.link('dead', 'http://127.0.0.1:%d' % _free_port(), discover=False)
    p = api.ping('dead', timeout=2)
    assert p['ok'] is False and p['error']
    api.unlink('dead')


def test_unlink_is_gone(api, repl):
    api.link('tmp', repl)
    api.unlink('tmp')
    with pytest.raises(FileNotFoundError):
        api.repl('tmp')


# ── calling a mod that lives on the Repl ────────────────────────────────────

def test_call(api, repl):
    api.link('greeter', repl)
    assert api.call('greeter', 'hello', args={'name': 'x'})['result'] == {'text': 'hello x'}
    assert api.call('greeter', 'add', args={'a': 2, 'b': 3})['result'] == {'sum': 5}


def test_call_does_not_eat_a_remote_arg_named_name(api, repl):
    """`name` is this function's own first parameter — a Repl-hosted mod is
    still entitled to a parameter called that."""
    api.link('greeter', repl)
    out = api.call('greeter', 'hello', args={'name': 'collision', 'loud': True})
    assert out['result'] == {'text': 'HELLO COLLISION'}


def test_null_call_returns_info(api, repl):
    api.link('greeter', repl)
    out = api.call('greeter')
    assert out['info']['name'] == 'greeter'


def test_unknown_fn_says_what_exists(api, repl):
    api.link('greeter', repl)
    out = api.call('greeter', 'nope')
    assert out['code'] == 404 and out['fns'] == ['add', 'hello']


def test_call_accepts_a_raw_url(api, repl):
    assert api.call(repl, 'hello')['result'] == {'text': 'hello world'}


# ── MCP ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def rpc(api, repl):
    api.link('greeter', repl)
    mcp.bind(api)

    def call(method, params=None, local=True, id_=1):
        return mcp.handle({'jsonrpc': '2.0', 'id': id_, 'method': method,
                           'params': params or {}}, local=local)
    return call


def test_initialize_negotiates_the_protocol(rpc):
    r = rpc('initialize', {'protocolVersion': '2025-06-18'})['result']
    assert r['protocolVersion'] == '2025-06-18'
    assert r['serverInfo']['name'] == 'replit'
    r = rpc('initialize', {'protocolVersion': '1999-01-01'})['result']
    assert r['protocolVersion'] == mcp.DEFAULT_PROTOCOL_VERSION


def test_notifications_get_no_response(rpc):
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_bad_message_is_a_jsonrpc_error(rpc):
    assert mcp.handle('nope')['error']['code'] == -32600
    assert rpc('does/not/exist')['error']['code'] == -32601


def test_tools_list_lifts_every_repl_fn(rpc):
    tools = {t['name']: t for t in rpc('tools/list')['result']['tools']}
    assert 'replit_catalog' in tools
    assert 'repl_greeter_hello' in tools and 'repl_greeter_add' in tools
    schema = tools['repl_greeter_hello']['inputSchema']
    assert schema['properties']['loud']['type'] == 'boolean'
    assert tools['repl_greeter_add']['inputSchema']['required'] == ['a']


def test_tools_call_reaches_the_repl(rpc):
    out = rpc('tools/call', {'name': 'repl_greeter_hello',
                             'arguments': {'name': 'mcp', 'loud': True}})['result']
    assert out['isError'] is False
    assert out['structuredContent']['result'] == {'text': 'HELLO MCP'}


def test_tools_call_reports_failure_as_iserror(rpc):
    out = rpc('tools/call', {'name': 'replit_repl', 'arguments': {'name': 'nosuch'}})['result']
    assert out['isError'] is True and 'nosuch' in out['content'][0]['text']


def test_unknown_tool_is_an_rpc_error(rpc):
    assert rpc('tools/call', {'name': 'nope'})['error']['code'] == -32602


def test_a_proxied_caller_reads_but_cannot_write(rpc):
    ok = rpc('tools/call', {'name': 'replit_catalog', 'arguments': {}}, local=False)['result']
    assert ok['isError'] is False
    blocked = rpc('tools/call', {'name': 'repl_greeter_hello', 'arguments': {}},
                  local=False)['result']
    assert blocked['isError'] is True and 'loopback-only' in blocked['content'][0]['text']


def test_every_tool_declares_a_schema(rpc):
    for t in rpc('tools/list')['result']['tools']:
        assert t['inputSchema']['type'] == 'object'
        assert t['description'] and len(t['name']) <= 64


def test_config_lists_the_new_fns():
    cfg = json.load(open(os.path.join(MODULE, 'config.json')))
    inst = anchor.Mod
    for fn in cfg['fns']:
        assert callable(getattr(inst, fn, None)), f'config.json names a missing fn: {fn}'
    for fn in ('catalog', 'discover', 'repl', 'mcp', 'mcp_tools'):
        assert fn in cfg['fns']


# ── HTTP surface ────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def server(api, repl):
    """The console + MCP on one port, in its own process with its own HOME."""
    api.link('greeter', repl)
    home = os.path.join(os.path.dirname(api.state_dir), 'home')
    os.makedirs(os.path.join(home, '.mod'), exist_ok=True)
    shutil.copytree(api.state_dir, os.path.join(home, '.mod', 'replit'),
                    dirs_exist_ok=True)
    port = _free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(MODULE, 'mod.py'),
                             'serve', str(port)],
                            env=dict(os.environ, HOME=home),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f'http://127.0.0.1:{port}'
    for _ in range(80):
        try:
            urllib.request.urlopen(url + '/api/status', timeout=1).read()
            break
        except Exception:                            # noqa: BLE001
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail('console never came up: ' + proc.stdout.read().decode()[:2000])
    yield url
    proc.kill()


def _post(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=dict({'Content-Type': 'application/json'},
                                              **(headers or {})), method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b'{}'), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{}'), e.code


def test_console_serves_the_app(server):
    html = urllib.request.urlopen(server + '/').read().decode()
    assert '<title>replit' in html and 'MCP' in html


def test_null_call_over_http(server):
    body, code = _post(server + '/', {})
    assert code == 200 and body['name'] == 'replit' and 'catalog' in body['fns']


def test_catalog_endpoint(server):
    j = json.load(urllib.request.urlopen(server + '/api/catalog'))
    assert j['n'] == 1 and j['mods'][0]['fns'] == ['add', 'hello']


def test_mcp_over_http_on_both_gateway_shapes(server):
    for path in ('/mcp', '/replit/mcp', '/api/replit/mcp'):
        body, code = _post(server + path, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        assert code == 200, path
        assert any(t['name'] == 'repl_greeter_hello' for t in body['result']['tools']), path


def test_mcp_notification_over_http_answers_202(server):
    req = urllib.request.Request(
        server + '/mcp', data=json.dumps({'jsonrpc': '2.0',
                                          'method': 'notifications/initialized'}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 202


def test_proxied_writes_are_refused(server):
    body, code = _post(server + '/api/link', {'name': 'x', 'url': 'https://y.replit.dev'},
                       headers={'X-Forwarded-For': '1.2.3.4'})
    assert code == 403 and 'read-only' in body['error']
    body, _ = _post(server + '/mcp',
                    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 'replit_link',
                                'arguments': {'name': 'x', 'url': 'https://y.replit.dev'}}},
                    headers={'X-Forwarded-For': '1.2.3.4'})
    assert body['result']['isError'] is True
