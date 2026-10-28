"""server.py: URL forms, public/loopback gates, the console shell."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope='module')
def base():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'server.py'),
                             str(port)], cwd=HERE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    for _ in range(50):
        try:
            urllib.request.urlopen(url + '/health', timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    yield url
    proc.terminate()


def _req(url, body=None, headers=None):
    req = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.parametrize('path', ['/health', '/ztensor/api/health',
                                  '/ztensor/_api/health', '/api/ztensor/health'])
def test_every_url_form_reaches_the_api(base, path):
    code, body = _req(base + path)
    assert code == 200 and json.loads(body)['module'] == 'ztensor'


def test_console_shell(base):
    code, body = _req(base + '/ztensor/', headers={'Accept': 'text/html'})
    assert code == 200 and b'Zuma consensus' in body


def test_whoami_local(base):
    assert json.loads(_req(base + '/ztensor/_api/whoami')[1])['local'] is True


@pytest.mark.parametrize('fn', ['keygen', 'sign', 'submit', 'score', 'settle', 'claim',
                                'register', 'deploy', 'devnet', 'mytag'])
def test_key_ops_refused_through_a_proxy(base, fn):
    code, body = _req(base + f'/ztensor/api/{fn}', {'name': 'x'},
                      {'X-Forwarded-For': '203.0.113.9'})
    assert code == 403 and 'anchor host' in json.loads(body)['error']


def test_unknown_fn_404(base):
    assert _req(base + '/ztensor/api/nope', {})[0] == 404


def test_board_never_carries_secrets(base):
    code, body = _req(base + '/ztensor/api/board')
    assert code == 200 and b'secret' not in body
    for fn in ('info', 'status'):
        assert 'keys' not in json.loads(_req(base + f'/ztensor/api/{fn}')[1])
    proxied = {'X-Forwarded-For': '203.0.113.9'}
    assert _req(base + '/ztensor/api/keys', headers=proxied)[0] == 403
    assert _req(base + '/ztensor/api/keys', {}, proxied)[0] == 403
    assert _req(base + '/ztensor/api/keys')[0] == 200  # loopback still can


def test_sim_state_shape(base):
    s = json.loads(_req(base + '/ztensor/_api/sim?since=0')[1])
    assert {'id', 'running', 'events', 'next'} <= set(s)
