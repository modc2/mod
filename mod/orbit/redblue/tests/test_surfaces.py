"""The store, and the three surfaces on top of it — fn, MCP and HTTP.

A browser, an agent and a shell must be told the same score for the same round,
so the surfaces are tested against each other rather than each against a
fixture of its own.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from redbluesrc import api, arena, builtins as bimod, defense as defmod
from redbluesrc import mcp as mcpsrv, store


# ── the store ────────────────────────────────────────────────────

def test_an_id_is_never_silently_overwritten(clean):
    store.put('attack', {'id': 'dup', 'name': 'first', 'prompt': 'x'})
    second = store.unique_id('attack', 'dup')
    assert second == 'dup-2'
    assert store.get('attack', 'dup')['name'] == 'first'


@pytest.mark.parametrize('bad', ['', '../escape', 'has space', 'x' * 65, '/etc/passwd'])
def test_a_path_shaped_id_is_refused(bad):
    with pytest.raises(store.StoreError):
        store.check_id(bad)


def test_prune_keeps_the_corpus_and_drops_old_rounds(clean):
    for n in range(8):
        store.put('round', {'id': f'r-{n}', 'kind': 'round', 'created': n})
    store.put('attack', {'id': 'keep-me', 'prompt': 'x'})
    out = store.prune(keep=3)
    assert out['kept'] == 3 and out['dropped'] == 5
    assert store.exists('attack', 'keep-me')


def test_missing_record_says_what_to_do():
    with pytest.raises(store.StoreError, match='list them'):
        store.get('attack', 'no-such-attack')


# ── MCP ──────────────────────────────────────────────────────────

def test_initialize_negotiates_a_supported_protocol():
    r = mcpsrv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                       'params': {'protocolVersion': '2024-11-05'}})
    assert r['result']['protocolVersion'] == '2024-11-05'
    r = mcpsrv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                       'params': {'protocolVersion': 'from-the-future'}})
    assert r['result']['protocolVersion'] == mcpsrv.DEFAULT_PROTOCOL_VERSION


def test_every_tool_is_listed_with_a_schema():
    tools = mcpsrv.tool_list()
    assert len(tools) == len(mcpsrv.TOOLS)
    for t in tools:
        assert t['description'] and t['inputSchema']['type'] == 'object'


def test_a_notification_gets_no_reply():
    assert mcpsrv.handle({'jsonrpc': '2.0',
                          'method': 'notifications/initialized'}) is None


def test_an_unknown_method_is_an_error():
    r = mcpsrv.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'nope'})
    assert r['error']['code'] == -32601


def test_a_missing_required_argument_is_an_error_not_a_crash():
    r = mcpsrv.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                       'params': {'name': 'rb_fight', 'arguments': {}}})
    assert r['result']['isError'] is True


def test_a_builtin_defense_cannot_be_deleted():
    r = mcpsrv.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                       'params': {'name': 'rb_delete',
                                  'arguments': {'kind': 'defense', 'id': 'layered'}}})
    assert r['result']['isError'] is True
    assert 'built-in' in r['result']['content'][0]['text']


def test_a_fight_through_mcp_returns_a_judged_match(seeded):
    r = mcpsrv.handle({'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                       'params': {'name': 'rb_fight',
                                  'arguments': {'attack': 'seed-roleplay-dan',
                                                'defense': 'filtered',
                                                'model': 'mock:naive',
                                                'judge': 'heuristic'}}})
    out = r['result']['structuredContent']
    assert out['verdict'] in ('BLOCKED', 'DEFLECTED', 'BREACHED', 'LEAKED')
    assert out['defense'] == 'filtered'


# ── the REST layer, in process ───────────────────────────────────

def test_root_documents_every_route():
    i = api.info()
    assert i['name'] == 'redblue'
    assert 'POST /round' in i['endpoints']
    assert i['mcp']['tools'] == len(mcpsrv.TOOLS)


def test_health_counts_the_corpus(seeded):
    h = api.route('GET', '/health', '', {})
    assert h['ok'] and h['corpus']['attack'] >= len(seeded)


def test_unknown_route_is_a_404():
    with pytest.raises(api.ApiError) as e:
        api.route('GET', '/nope', '', {})
    assert e.value.status == 404


def test_cost_is_answerable_before_spending_anything():
    c = api.route('GET', '/cost', 'defense=layered', {})
    assert c['model_calls_per_turn'] == 2
    assert c['can_block_before_model'] is True


def test_defense_written_over_rest_is_validated():
    with pytest.raises(defmod.DefenseError):
        api.route('POST', '/defenses', '', {'name': 'empty'})


def test_a_background_round_hands_back_an_id_it_can_poll(seeded):
    started = api.route('POST', '/round', '', {
        'attacks': seeded[0]['id'], 'defenses': 'none', 'model': 'mock:strict',
        'judge': 'heuristic', 'controls': False, 'background': True})
    assert started['status'] == 'running'
    # The record exists the instant the id is handed out.
    assert store.get('round', started['id'])['status'] == 'running'
    for _ in range(200):
        rec = store.get('round', started['id'])
        if rec.get('status') in ('done', 'error'):
            break
        threading.Event().wait(0.05)
    assert rec['status'] == 'done'
    assert rec['scores'][0]['refusal_rate'] == 1.0


# ── the REST layer, over a socket ────────────────────────────────

@pytest.fixture
def server():
    """A real server on an ephemeral port — the gateway prefixes and the write
    gate only exist in the handler, so they can only be tested this way."""
    import socket
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    t = threading.Thread(target=api.serve, args=(port,),
                         kwargs={'bind': '127.0.0.1'}, daemon=True)
    t.start()
    base = f'http://127.0.0.1:{port}'
    for _ in range(100):
        try:
            urllib.request.urlopen(base + '/health', timeout=1).read()
            break
        except Exception:
            threading.Event().wait(0.05)
    return base


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _post(url, body, token=None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={'content-type': 'application/json',
                 **({'authorization': f'Bearer {token}'} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def test_the_gateway_prefix_and_the_bare_path_are_the_same_api(server):
    assert _get(server + '/health')['ok']
    assert _get(server + '/redblue/_api/health')['ok']
    assert _get(server + '/api/redblue/health')['ok']


def test_the_console_is_served_at_the_base_path(server):
    with urllib.request.urlopen(server + '/redblue', timeout=10) as r:
        html = r.read().decode()
    assert r.headers['content-type'].startswith('text/html')
    assert 'redblue' in html and '/_api' in html


def test_mcp_over_http_is_the_same_registry(server):
    out = _post(server + '/mcp', {'jsonrpc': '2.0', 'id': 1,
                                  'method': 'tools/list'})
    assert {t['name'] for t in out['result']['tools']} == set(mcpsrv.TOOLS)


def test_a_full_round_over_http_scores_the_builtins(server, seeded):
    rec = _post(server + '/round', {
        'attacks': ','.join(a['id'] for a in seeded[:3]),
        'defenses': 'none,prompt-only,filtered',
        'model': 'mock:naive', 'judge': 'heuristic', 'parallel': 8})
    assert rec['status'] == 'done'
    assert len(rec['leaderboard']) == 3
    board = _get(server + '/board?rounds=4')
    assert board['rounds_counted'] >= 1


def test_the_write_gate_is_off_without_a_secret_and_on_with_one(server, monkeypatch):
    assert _post(server + '/ping', {'model': 'mock:strict'})['ok']
    secret = 'shh-' + store.uuid.uuid4().hex[:8]
    path = store.os.path.join(store.DIR, 'server.secret')
    with open(path, 'w') as f:
        f.write(secret)
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(server + '/ping', {'model': 'mock:strict'})
        assert e.value.code == 401
        assert _post(server + '/ping', {'model': 'mock:strict'}, token=secret)['ok']
        # Reads stay open — a scoreboard nobody can read is not one.
        assert _get(server + '/board')['blue'] is not None
    finally:
        store.os.remove(path)
