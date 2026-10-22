"""grokbot tests — everything that must be right before a key is spent.

Nothing here talks to xAI: `payload` builds the request without sending it,
and the two routing tests run against a stub upstream. The state directory is
redirected per-test, so no test can read or write a real user's key.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Point every module at a throwaway ~/.mod/grokbot."""
    monkeypatch.setenv('GROKBOT_DIR', str(tmp_path))
    monkeypatch.delenv('XAI_API_KEY', raising=False)
    monkeypatch.delenv('GROK_API_KEY', raising=False)
    import client
    import identity
    import runs
    monkeypatch.setattr(client, 'STATE', str(tmp_path))
    monkeypatch.setattr(client, 'USERS', str(tmp_path / 'users'))
    monkeypatch.setattr(client, 'KEY_FILE', str(tmp_path / 'key.json'))
    monkeypatch.setattr(client, '_MODELS_CACHE', {})
    monkeypatch.setattr(identity, 'OWNER_PATH', tmp_path / 'owner.json')
    monkeypatch.setattr(identity, 'STATE', tmp_path)
    monkeypatch.setattr(runs, 'STATE', str(tmp_path))
    monkeypatch.setattr(runs, 'DIR', str(tmp_path / 'runs'))
    yield tmp_path


ADDR = '0xabc0000000000000000000000000000000000001'


# ── the key store ────────────────────────────────────────────────────

def test_key_is_stored_per_address_and_never_world_readable():
    import client
    client.set_user_key(ADDR, 'xai-secret')
    assert client.load_user(ADDR)['key'] == 'xai-secret'
    mode = os.stat(client._user_path(ADDR)).st_mode & 0o777
    assert mode == 0o600
    # somebody else's address sees nothing
    assert client.load_user('0xdead') == {}


def test_key_can_be_forgotten():
    import client
    client.set_user_key(ADDR, 'xai-secret')
    client.set_user_key(ADDR, '')
    assert 'key' not in client.load_user(ADDR)


def test_a_key_that_is_not_an_xai_key_is_refused():
    import client
    with pytest.raises(client.GrokError):
        client.set_user_key(ADDR, 'sk-openai-nope')


def test_key_resolution_order():
    import client
    client.set_key('xai-operator')
    client.set_user_key(ADDR, 'xai-account')
    assert client.Client(address=ADDR).key == 'xai-account'
    assert client.Client(key='xai-request', address=ADDR).key == 'xai-request'
    assert client.Client().key == 'xai-operator'          # falls back
    assert client.Client(address='0xnobody').key == 'xai-operator'


def test_key_state_never_echoes_the_key():
    import client
    client.set_user_key(ADDR, 'xai-abcdefghijklmnop')
    state = client.Client(address=ADDR).key_state()
    assert state['key'] is True and state['source'] == 'account'
    assert 'xai-abcdefghijklmnop' not in json.dumps(state)


def test_no_key_is_a_401_that_says_what_to_do():
    import client
    with pytest.raises(client.NeedsKey) as e:
        client.Client(address=ADDR).models()
    assert e.value.status == 401 and 'sign in' in str(e.value).lower()


# ── bots ─────────────────────────────────────────────────────────────

def test_bots_are_scoped_to_one_address():
    import client
    client.save_bot(ADDR, 'Skeptic', system='doubt everything')
    assert [b['name'] for b in client.bots(ADDR)] == ['skeptic']
    assert client.bots('0xsomeone-else') == []


def test_bot_update_keeps_the_fields_you_did_not_send():
    import client
    client.save_bot(ADDR, 'skeptic', system='doubt', model='grok-4-fast')
    client.save_bot(ADDR, 'skeptic', temperature=0.2)
    bot = client.get_bot(ADDR, 'skeptic')
    assert bot['system'] == 'doubt' and bot['model'] == 'grok-4-fast'
    assert bot['temperature'] == 0.2


def test_missing_bot_is_a_404():
    import client
    with pytest.raises(client.GrokError) as e:
        client.get_bot(ADDR, 'ghost')
    assert e.value.status == 404


# ── the chat payload (built, never sent) ─────────────────────────────

def test_payload_defaults():
    import client
    body = client.Client(address=ADDR).payload(prompt='hi')
    assert body['model'] == client.DEFAULT_MODEL
    assert body['messages'] == [{'role': 'user', 'content': 'hi'}]
    assert 'search_parameters' not in body


def test_payload_prepends_the_system_prompt_once():
    import client
    body = client.Client(address=ADDR).payload(prompt='hi', system='be brief')
    assert body['messages'][0] == {'role': 'system', 'content': 'be brief'}
    already = [{'role': 'system', 'content': 'mine'}, {'role': 'user', 'content': 'hi'}]
    body = client.Client(address=ADDR).payload(messages=already, system='be brief')
    assert [m['role'] for m in body['messages']] == ['system', 'user']
    assert body['messages'][0]['content'] == 'mine'


def test_a_bot_supplies_model_system_and_search():
    import client
    client.save_bot(ADDR, 'skeptic', system='doubt', model='grok-3',
                    search='auto', temperature=0.1)
    body = client.Client(address=ADDR).payload(prompt='hi', bot='skeptic')
    assert body['model'] == 'grok-3'
    assert body['messages'][0]['content'] == 'doubt'
    assert body['search_parameters'] == {'mode': 'auto'}
    assert body['temperature'] == 0.1
    # an explicit argument still wins over the bot
    body = client.Client(address=ADDR).payload(prompt='hi', bot='skeptic',
                                               model='grok-4-fast')
    assert body['model'] == 'grok-4-fast'


def test_search_off_stays_off():
    import client
    body = client.Client(address=ADDR).payload(prompt='hi', search=False)
    assert 'search_parameters' not in body
    body = client.Client(address=ADDR).payload(prompt='hi', search='on')
    assert body['search_parameters'] == {'mode': 'on'}


def test_payload_without_a_prompt_is_a_400():
    import client
    with pytest.raises(client.GrokError) as e:
        client.Client(address=ADDR).payload()
    assert e.value.status == 400


def test_price_card_is_usd_per_million():
    import client
    row = client.card({'id': 'grok-4-fast', 'prompt_text_token_price': 30000,
                       'completion_text_token_price': 150000})
    assert row['prompt_usd_m'] == 3.0 and row['completion_usd_m'] == 15.0


# ── identity ─────────────────────────────────────────────────────────

def test_an_unsigned_caller_is_anon_and_a_bad_token_is_not_fatal():
    import identity
    assert identity.whoami(None) is None
    assert identity.whoami('Bearer garbage') is None
    assert identity.role(None) == identity.ANON


def test_an_xai_key_in_the_authorization_header_is_not_an_identity():
    import identity
    assert identity.strip('Bearer xai-abc') is None
    assert identity.strip('Bearer tok') == 'tok'


def test_require_explains_how_to_sign_in():
    import identity
    with pytest.raises(identity.AuthError) as e:
        identity.require(None)
    assert 'wallet' in str(e.value)


def test_open_mode_collapses_everyone_into_one_identity(monkeypatch):
    import identity
    monkeypatch.setenv('GROKBOT_OPEN', '1')
    assert identity.require(None) == identity.OPEN_ADDRESS
    assert identity.role(identity.OPEN_ADDRESS) == identity.OWNER


def test_the_first_signed_caller_claims_the_deployment():
    import identity
    assert identity.owner() is None
    identity.claim(ADDR)
    assert identity.owner() == ADDR
    with pytest.raises(identity.Denied):
        identity.claim('0xsomeone-else')


# ── routing ──────────────────────────────────────────────────────────

def test_routes_that_need_a_signature_say_so():
    import api
    import identity
    for method, path in (('POST', '/key'), ('GET', '/bots'), ('POST', '/bots')):
        with pytest.raises(identity.AuthError):
            api.route(method, path, '', {'key': 'xai-x', 'name': 'b'}, None, None)


def test_signin_is_forced_on_everything_that_touches_grok():
    """A BYOK key alone is not enough anywhere — sign-in is mandatory."""
    import api
    import identity
    spends = (('POST', '/chat', {'prompt': 'hi'}),
              ('GET', '/models', {}),
              ('GET', '/model', {'id': 'grok-4-fast'}),
              ('GET', '/keyinfo', {}),
              ('POST', '/images', {'prompt': 'a cat'}),
              ('POST', '/raw', {'path': '/models'}),
              ('GET', '/runs', {}))
    for method, path, body in spends:
        with pytest.raises(identity.AuthError):
            api.route(method, path, '', body, None, 'xai-byok-key')


def test_mcp_chat_without_a_token_is_a_401_not_a_spend():
    import mcp
    out = mcp.handle({'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call',
                      'params': {'name': 'grok_chat',
                                 'arguments': {'prompt': 'hi',
                                               'key': 'xai-byok'}}})
    assert out['result']['isError'] is True
    assert '401' in json.dumps(out['result'])


# ── the run ledger ───────────────────────────────────────────────────

def test_a_run_opens_live_and_closes_done_with_the_counts():
    import runs
    rid = runs.start(ADDR, 'chat', model='grok-4-fast', prompt='hi', search=True)
    board = runs.list_runs(ADDR)
    assert board['counts'] == {'all': 1, 'live': 1, 'done': 0, 'error': 0}
    runs.finish(ADDR, rid, runs.DONE, ms=1234, tokens=99)
    board = runs.list_runs(ADDR)
    assert board['counts'] == {'all': 1, 'live': 0, 'done': 1, 'error': 0}
    row = board['runs'][0]
    assert row['ms'] == 1234 and row['tokens'] == 99 and row['search'] is True


def test_a_failed_run_lands_on_the_error_pill():
    import runs
    rid = runs.start(ADDR, 'chat', model='grok-4-fast')
    runs.finish(ADDR, rid, runs.ERROR, error='429: rate limited')
    board = runs.list_runs(ADDR, status='error')
    assert board['counts']['error'] == 1
    assert board['runs'][0]['error'].startswith('429')


def test_runs_are_scoped_to_one_address_and_the_prompt_is_truncated():
    import runs
    runs.start(ADDR, 'chat', prompt='x' * 500)
    assert runs.list_runs('0xsomeone-else')['counts']['all'] == 0
    assert len(runs.list_runs(ADDR)['runs'][0]['prompt']) == runs.PROMPT_HEAD


def test_a_live_run_older_than_any_timeout_is_reaped_as_lost():
    import runs
    rid = runs.start(ADDR, 'chat')
    with runs._LOCK:
        rows = runs._load(runs._path(ADDR))
        rows[0]['ts'] -= runs.STALE_AFTER + 10
        runs._save(runs._path(ADDR), rows)
    board = runs.list_runs(ADDR)
    assert board['counts'] == {'all': 1, 'live': 0, 'done': 0, 'error': 1}
    assert 'lost' in board['runs'][0]['error']
    assert rid  # it was a real run


def test_the_ledger_never_holds_a_key_and_stays_0600():
    import client
    import runs
    client.set_user_key(ADDR, 'xai-secret')
    rid = runs.start(ADDR, 'chat', model='grok-4-fast', prompt='hello')
    runs.finish(ADDR, rid, runs.DONE)
    path = runs._path(ADDR)
    assert os.stat(path).st_mode & 0o777 == 0o600
    with open(path) as f:
        assert 'xai-secret' not in f.read()


def test_info_lists_every_route_and_never_leaks_a_key():
    import api
    import client
    client.set_user_key(ADDR, 'xai-secret')
    body = json.dumps(api.info())
    assert 'xai-secret' not in body
    assert '/chat' in body and 'POST /mcp' in body


def test_stats_counts_accounts_without_showing_keys():
    import api
    import client
    client.set_user_key(ADDR, 'xai-secret')
    client.save_bot(ADDR, 'skeptic')
    out = api.stats()
    assert out['accounts'] == 1
    assert out['users'][0] == {'address': ADDR, 'key': True, 'bots': 1,
                               'key_set': out['users'][0]['key_set']}
    assert 'xai-secret' not in json.dumps(out)


def test_health_and_me_work_unsigned():
    import api
    assert api.route('GET', '/health', '', {}, None, None)['ok'] is True
    me = api.route('GET', '/me', '', {}, None, None)
    assert me['signed_in'] is False and me['key']['key'] is False


# ── mcp ──────────────────────────────────────────────────────────────

def test_every_tool_has_a_schema_and_a_description():
    import mcp
    for tool in mcp.tool_list():
        assert tool['description'] and tool['inputSchema']['type'] == 'object'
        for field in (tool['inputSchema'].get('required') or []):
            assert field in tool['inputSchema']['properties']


def test_initialize_and_tools_list():
    import mcp
    out = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                      'params': {'protocolVersion': '2025-06-18'}})
    assert out['result']['serverInfo']['name'] == 'grokbot'
    assert out['result']['protocolVersion'] == '2025-06-18'
    listed = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    assert len(listed['result']['tools']) == len(mcp.TOOLS)


def test_a_tool_failure_is_a_result_with_isError_not_a_crash():
    import mcp
    out = mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                      'params': {'name': 'grok_bots', 'arguments': {}}})
    assert out['result']['isError'] is True
    assert '401' in json.dumps(out['result'])


def test_notifications_get_no_response():
    import mcp
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_unknown_tool_names_the_ones_that_exist():
    import mcp
    from client import GrokError
    with pytest.raises(GrokError) as e:
        mcp.call_tool('grok_teleport', {})
    assert 'grok_chat' in str(e.value)


# ── published bots ───────────────────────────────────────────────────

OTHER = '0xdef0000000000000000000000000000000000002'


def test_a_bot_is_private_until_published():
    import client
    client.set_user_key(ADDR, 'xai-secret-key')
    client.save_bot(ADDR, 'skeptic', system='doubt it')
    assert client.public_bots() == []
    client.publish_bot(ADDR, 'skeptic')
    rows = client.public_bots()
    assert [r['ref'] for r in rows] == [f'{ADDR}/skeptic']
    assert rows[0]['system'] == 'doubt it' and rows[0]['published']
    assert 'xai-secret-key' not in json.dumps(rows)
    client.publish_bot(ADDR, 'skeptic', public=False)
    assert client.public_bots() == []


def test_anyone_can_run_a_published_bot_by_ref_and_it_counts_the_use():
    import client
    client.save_bot(ADDR, 'pirate', system='arr', model='grok-3', public=True)
    body = client.Client(key='xai-k', address=OTHER).payload(
        prompt='hi', bot=f'{ADDR}/pirate')
    assert body['model'] == 'grok-3'
    assert body['messages'][0] == {'role': 'system', 'content': 'arr'}
    assert client.public_bots()[0]['uses'] == 1


def test_an_unpublished_bot_is_not_reachable_by_ref():
    import client
    client.save_bot(ADDR, 'secret', system='shh')
    with pytest.raises(client.GrokError) as e:
        client.get_bot(OTHER, f'{ADDR}/secret')
    assert e.value.status == 404


def test_copy_makes_a_private_bot_that_remembers_where_it_came_from():
    import client
    client.save_bot(ADDR, 'pirate', system='arr', public=True)
    got = client.copy_bot(OTHER, f'{ADDR}/pirate', name='my-pirate')
    assert got['name'] == 'my-pirate' and got['from'] == f'{ADDR}/pirate'
    assert got['public'] is False
    assert [b['name'] for b in client.bots(OTHER)] == ['my-pirate']


def test_public_route_is_readable_signed_out_and_searchable():
    import api
    import client
    client.save_bot(ADDR, 'pirate', system='talks like a pirate', public=True)
    client.save_bot(OTHER, 'lawyer', system='cites cases', public=True)
    out = api.route('GET', '/public', '', {}, None, None)
    assert out['count'] == 2 and not any(r['mine'] for r in out['bots'])
    hit = api.route('GET', '/public', 'q=pirate', {}, None, None)
    assert [r['name'] for r in hit['bots']] == ['pirate']


def test_publish_and_copy_routes_need_a_signature():
    import api
    import identity
    for path, body in (('/bots/publish', {'name': 'x'}),
                       ('/bots/copy', {'ref': f'{ADDR}/x'})):
        with pytest.raises(identity.AuthError):
            api.route('POST', path, '', body, None, None)


# ── share routes ──────────────────────────────────────────────────────

def test_share_round_trip(monkeypatch, sandbox):
    import api
    monkeypatch.setenv('GROKBOT_OPEN', '1')
    msgs = [
        {'role': 'user', 'text': 'hello'},
        {'role': 'assistant', 'text': 'hi there',
         'usage': {'total_tokens': 5}, 'model': 'grok-4-fast'},
    ]
    out = api.route('POST', '/share', '',
                    {'title': 'Test convo', 'msgs': msgs}, None, None)
    assert 'id' in out
    assert len(out['id']) == 32  # os.urandom(16).hex() = 32 hex chars

    # GET is unauthenticated — token=None, no GROKBOT_OPEN needed for read
    got = api.route('GET', '/share/' + out['id'], '', {}, None, None)
    assert got['title'] == 'Test convo'
    assert len(got['msgs']) == 2
    assert 'usage' not in got['msgs'][1]           # usage stripped
    assert got['msgs'][1]['model'] == 'grok-4-fast'  # non-sensitive fields kept

    path = os.path.join(str(sandbox), 'shares', out['id'] + '.json')
    assert os.path.exists(path)
    assert os.stat(path).st_mode & 0o777 == 0o644


def test_share_requires_sign_in():
    import api
    import identity
    with pytest.raises(identity.AuthError):
        api.route('POST', '/share', '', {'msgs': []}, None, None)


def test_share_unknown_id_is_404():
    import api
    from client import GrokError
    with pytest.raises(GrokError) as e:
        api.route('GET', '/share/deadbeef0000000000000000deadbeef', '', {}, None, None)
    assert e.value.status == 404


def test_share_image_dataurl_is_stripped(monkeypatch, sandbox):
    import api
    monkeypatch.setenv('GROKBOT_OPEN', '1')
    msgs = [{'role': 'user', 'text': 'look',
             'image': 'data:image/png;base64,abc123'}]
    out = api.route('POST', '/share', '', {'msgs': msgs}, None, None)
    got = api.route('GET', '/share/' + out['id'], '', {}, None, None)
    assert 'image' not in got['msgs'][0]
