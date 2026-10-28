"""hermes tests — the AGENT contract, driven against the scripted backend.

The point of these is the contract, not the model: everything orbit/build
probes for and everything it dispatches to has to keep working on a box with
no weights on it, which is most boxes. `fake_backend.py` stands in for
llama-server / ollama so the loop — anchor parsing, tool execution, the finish
that ends a run, the SSE frames — is testable in a second.

    python3 -m pytest tests/test_hermes.py
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src')
sys.path.insert(0, SRC)

import auth      # noqa: E402
import tools     # noqa: E402


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _wait(url, tries=60):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.loads(r.read().decode() or '{}')
        except Exception:
            time.sleep(0.25)
    raise AssertionError(f'{url} never came up')


@pytest.fixture(scope='module')
def server():
    """hermes on a spare port, backed by the scripted stand-in."""
    fake_port, api_port = _free_port(), _free_port()
    fake = subprocess.Popen([sys.executable, os.path.join(HERE, 'fake_backend.py'),
                             str(fake_port)])
    env = {**os.environ,
           'HERMES_API_URL': f'http://127.0.0.1:{fake_port}',
           'PORT': str(api_port)}
    api = subprocess.Popen([sys.executable, os.path.join(SRC, 'api.py')], env=env)
    base = f'http://127.0.0.1:{api_port}'
    try:
        _wait(base + '/health')
        yield base
    finally:
        for p in (api, fake):
            p.terminate()
            p.wait(timeout=5)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read().decode() or '{}')


def _post(base, path, body, stream=False):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    resp = urllib.request.urlopen(req, timeout=60)
    if stream:
        return resp
    return json.loads(resp.read().decode() or '{}')


# ── the compatibility probe ──────────────────────────────────────────

def test_agents_is_the_shape_build_probes_for(server):
    """orbit/build mounts a mod only if GET /agents answers {"agents": [...]}."""
    body = _get(server, '/agents')
    assert isinstance(body.get('agents'), list) and body['agents']
    assert all('id' in a for a in body['agents'])
    assert body['default'] in {a['id'] for a in body['agents']}


def test_the_probe_needs_no_token(server):
    """An authenticated probe is a module nobody can mount."""
    assert '/agents' in auth.OPEN
    assert auth.guard('/agents', headers={}, client_addr='8.8.8.8') == 'public'


def test_running_is_not_open(server):
    with pytest.raises(auth.Denied):
        auth.guard('/run/stream', headers={}, client_addr='8.8.8.8')


# ── the run ──────────────────────────────────────────────────────────

def test_run_stream_emits_the_agent_event_vocabulary(server):
    resp = _post(server, '/run/stream',
                 {'query': 'say hi', 'steps': 3, 'path': '/tmp'}, stream=True)
    events = []
    for raw in resp:
        line = raw.decode().strip()
        if line.startswith('data: '):
            events.append(json.loads(line[6:]))
    kinds = [e['type'] for e in events]
    assert kinds[0] == 'model_start'
    assert 'token' in kinds and 'tool_start' in kinds and 'step' in kinds
    assert kinds[-1] == 'done'
    # the tool actually ran on this box, and its output came back
    ran = next(e for e in events if e['type'] == 'step'
               and e['step']['tool'] == 'bash')
    assert 'hermes-was-here' in ran['step']['result']['stdout']
    # a free run must not draw a cost line in a metering console
    assert 'charged' not in events[-1] and events[-1]['free'] is True


def test_run_blocking_matches_the_stream(server):
    body = _post(server, '/run', {'query': 'say hi', 'steps': 3, 'path': '/tmp'})
    assert body['result'][-1]['tool'] == 'finish'
    assert body['free'] is True


def test_chat_has_no_loop(server):
    body = _post(server, '/chat', {'message': 'hello'})
    assert '<STEP>' in body['text']       # the stand-in always answers a step
    assert body['backend'] == 'server'


def test_models_lists_the_registry_and_what_the_backend_serves(server):
    keys = {row['key'] for row in _get(server, '/models')['models']}
    assert 'hermes-3-8b' in keys
    assert 'hermes-3-8b-fake' in keys     # live, off the backend's own list


# ── the tools ────────────────────────────────────────────────────────

def test_sandbox_drops_the_write_tools():
    assert set(tools.names(sandbox=True)) < set(tools.names())
    for name in ('bash', 'write', 'edit'):
        assert name not in tools.names(sandbox=True)
        with pytest.raises(PermissionError):
            tools.get(name, sandbox=True)


def test_edit_refuses_an_ambiguous_match(tmp_path):
    f = tmp_path / 'x.py'
    f.write_text('a = 1\na = 1\n')
    with pytest.raises(ValueError, match='2 times'):
        tools.edit(str(f), 'a = 1', 'a = 2')
    with pytest.raises(ValueError, match='not found'):
        tools.edit(str(f), 'nope', 'x')


def test_unknown_params_are_dropped_not_fatal(tmp_path):
    """A small model volunteers keys the tool never had."""
    f = tmp_path / 'y.txt'
    out = tools.run('write', file_path=str(f), content='hi', encoding='utf-8')
    assert out['bytes'] == 2 and f.read_text() == 'hi'


# ── the module object ────────────────────────────────────────────────

def test_nothing_installed_refuses_with_the_fix_in_the_message(monkeypatch):
    import backend
    monkeypatch.delenv('HERMES_API_URL', raising=False)
    monkeypatch.setattr(backend, 'PROBES', ('http://127.0.0.1:1',))
    monkeypatch.setattr(backend.LlamaCpp, 'available', staticmethod(lambda: False))
    b = backend.resolve()
    assert b.kind == 'none' and not b.ready()
    for fix in ('llama-cpp-python', 'llama-server', 'ollama'):
        assert fix in b.why()
    with pytest.raises(backend.BackendError):
        b.chat([{'role': 'user', 'content': 'hi'}])


def test_an_explicit_url_is_trusted_but_says_so_when_it_is_down(monkeypatch):
    """HERMES_API_URL is taken on trust — a server loading 5 GB of weights
    refuses for a minute and then works — so it is not probed away. It still
    has to explain itself when a run cannot start."""
    import backend
    monkeypatch.setenv('HERMES_API_URL', 'http://127.0.0.1:1')
    b = backend.resolve()
    assert b.kind == 'server' and not b.ready()
    assert 'not answering' in b.why() and 'HERMES_API_URL' in b.why()
