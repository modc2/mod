"""Tests for the x mod: config/protocol shape, the MCP tool layer, and the
one live path that needs no credentials.

The Rust backend is spawned on a throwaway port with a scrubbed credential
environment, so nothing here can post as a real account. Signing itself is
covered by the Rust unit tests (`cargo test -p x-rs`).
"""
import json
import os
import socket
import subprocess
import sys
import time

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from x.mod import CRED_FIELDS, Mod  # noqa: E402

BINARY = os.path.join(ROOT, 'x-rs', 'target', 'release', 'x-api')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def server():
    """The Rust MCP backend, credential-free so writes can never fire."""
    if not os.path.exists(BINARY):
        pytest.skip(f'{BINARY} not built — run `m x/build`')
    port = free_port()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('X_', 'TWITTER_'))}
    env.update(PORT=str(port), HOME='/tmp/x-test-home')  # no ~/.mod/x/credentials.json
    proc = subprocess.Popen([BINARY], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    for _ in range(50):
        try:
            if requests.get(f'{url}/health', timeout=1).ok:
                break
        except requests.ConnectionError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail('backend did not come up')
    yield url
    proc.kill()


@pytest.fixture(scope='module')
def mod(server):
    return Mod(server_url=server)


# ----------------------------------------------------------------- protocol

def test_config_matches_mod_protocol():
    with open(os.path.join(ROOT, 'config.json')) as f:
        cfg = json.load(f)
    assert cfg['name'] == 'x'
    assert isinstance(cfg['port'], int)
    assert cfg['urls']['api'].endswith(str(cfg['port']))
    for fn in cfg['fns']:
        assert callable(getattr(Mod, fn, None)), f'config lists missing fn: {fn}'


def test_forward_is_the_default_entry(mod):
    """No query → auth state, not an exception; a mod always answers."""
    assert mod.forward()['writes'] is False


# ----------------------------------------------------------------- mcp layer

def test_mcp_handshake(server):
    r = requests.post(f'{server}/mcp', json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-06-18'}}).json()
    assert r['result']['serverInfo']['name'] == 'x'
    assert r['result']['capabilities']['tools'] == {}


def test_notifications_get_no_reply(server):
    r = requests.post(f'{server}/mcp',
                      json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    assert r.status_code == 202


def test_tool_registry_shape(mod):
    tools = mod.tools()
    names = [t['name'] for t in tools]
    assert len(names) == len(set(names)), 'duplicate tool names'
    for t in tools:
        assert t['description']
        assert t['inputSchema']['type'] == 'object'
    # Every wrapper on the Mod maps to a real tool.
    for fn in ('search', 'get_post', 'user', 'timeline', 'post', 'like', 'follow'):
        assert fn in names


def test_unknown_tool_is_an_error(mod):
    with pytest.raises(RuntimeError, match='unknown tool'):
        mod.mcp_call('nope')


def test_rest_dispatches_through_the_tool_layer(server):
    """/forward is the generic adapter — same result as tools/call."""
    rest = requests.post(f'{server}/forward', json={'action': 'auth_status'}).json()
    rpc = requests.post(f'{server}/mcp', json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'auth_status', 'arguments': {}}}).json()
    assert rest == rpc['result']['structuredContent']


# ----------------------------------------------------------------- auth

def test_no_credentials_reported_honestly(mod):
    status = mod.auth_status()
    assert status == {
        'bearer': False, 'user_context': False, 'reads': False, 'writes': False,
        'keyless_fallback': ['get_post'],
        'sources': status['sources'],
    }


def test_reads_without_a_bearer_say_so(mod):
    with pytest.raises(RuntimeError, match='no X credentials'):
        mod.search('anything')


def test_writes_demand_user_context(mod):
    with pytest.raises(RuntimeError, match='OAuth 1.0a user credentials'):
        mod.post('this must never reach X')


def test_upstream_status_survives_the_rest_hop(server):
    assert requests.get(f'{server}/search?q=x').status_code == 401


def test_set_keys_rejects_unknown_fields(mod):
    with pytest.raises(ValueError, match='unknown credential'):
        mod.set_keys(oauth_token='nope')
    assert set(CRED_FIELDS) == {'bearer_token', 'api_key', 'api_secret',
                                'access_token', 'access_token_secret'}


# ----------------------------------------------------------------- live, keyless

def test_keyless_get_post_reads_the_first_tweet(mod):
    """@jack's post 20 — public, immutable, and needs no credentials."""
    data = mod.get_post('20')['data']
    assert data['text'] == 'just setting up my twttr'
    assert data['id'] == '20'


def test_post_urls_resolve_to_ids(mod):
    assert mod.get_post('https://x.com/jack/status/20')['data']['id'] == '20'
