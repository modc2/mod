"""
tests for the MCP server (src/mcp.py) and its HTTP transport

covers:
    - the tool registry: names, schemas, descriptions
    - JSON-RPC 2.0: initialize/negotiate, ping, notifications, bad messages
    - auth: the writing tools refuse a connection that carried no token,
      the reading ones do not, and a per-call `key` beats the transport's
    - interlacing: tools land in the API's own handlers, so the task registry
      and the write sandbox are shared rather than duplicated
    - resources + prompts
    - the mounted endpoint: sessions, SSE, batching, 405/404/202

run:
    cd ~/mod/mod/orbit/agent && python3 -m pytest tests/test_mcp.py -v
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import mcp


def call(name, args=None, key=None):
    """One tools/call round trip -> (isError, parsed structuredContent or text)."""
    r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                    'params': {'name': name, 'arguments': args or {}}}, key)
    res = r['result']
    body = res.get('structuredContent')
    if body is None:
        body = res['content'][0]['text']
    return bool(res.get('isError')), body


# ═══════════════════════════════════════════════════════════════════════
#  THE REGISTRY
# ═══════════════════════════════════════════════════════════════════════

class TestToolRegistry:
    def test_every_tool_has_a_description_and_schema(self):
        assert len(mcp.TOOLS) >= 20
        for name, tool in mcp.TOOLS.items():
            assert name.startswith('agent_'), f'{name} is not namespaced'
            assert callable(tool['handler'])
            assert len(tool['description']) > 60, f'{name} is under-described'
            schema = tool['inputSchema']
            assert schema['type'] == 'object'
            assert isinstance(schema.get('properties', {}), dict)

    def test_tool_list_is_the_wire_shape(self):
        listed = mcp.tool_list()
        assert {t['name'] for t in listed} == set(mcp.TOOLS)
        for t in listed:
            assert set(t) == {'name', 'description', 'inputSchema'}

    def test_the_headline_tools_are_present(self):
        assert {'agent_run', 'agent_task', 'agent_tool_run', 'agent_recall',
                'agent_whoami'} <= set(mcp.TOOLS)

    def test_run_requires_a_query(self):
        assert mcp.TOOLS['agent_run']['inputSchema']['required'] == ['query']

    def test_info_says_how_to_connect(self):
        info = mcp.info('http://localhost:50117')
        assert info['endpoint'] == 'http://localhost:50117/mcp'
        assert info['tools'] == len(mcp.TOOLS)
        assert 'mcp add' in info['connect']
        assert info['protocol'] == mcp.PROTOCOL_VERSION


# ═══════════════════════════════════════════════════════════════════════
#  JSON-RPC 2.0
# ═══════════════════════════════════════════════════════════════════════

class TestProtocol:
    def test_initialize_answers_with_capabilities(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                        'params': {'protocolVersion': '2025-06-18'}})
        res = r['result']
        assert res['protocolVersion'] == '2025-06-18'
        assert res['serverInfo']['name'] == 'agent'
        assert 'tools' in res['capabilities']
        assert 'agent_run' in res['instructions']

    def test_unknown_protocol_negotiates_down_to_ours(self):
        assert mcp.negotiate('1999-01-01') == mcp.PROTOCOL_VERSION
        assert mcp.negotiate('2024-11-05') == '2024-11-05'

    def test_notifications_get_no_reply(self):
        assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
        assert mcp.handle({'jsonrpc': '2.0', 'method': 'ping'}) is None  # no id

    def test_unknown_method_is_an_error(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'nope'})
        assert r['error']['code'] == -32601

    def test_garbage_is_an_invalid_request(self):
        assert mcp.handle(['not', 'an', 'object'])['error']['code'] == -32600
        assert mcp.handle({'jsonrpc': '2.0', 'id': 3})['error']['code'] == -32600

    def test_tools_list_over_the_wire(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/list'})
        assert len(r['result']['tools']) == len(mcp.TOOLS)

    def test_unknown_tool_is_a_result_not_a_crash(self):
        # a tool failure is a successful JSON-RPC response carrying isError, so
        # the model reads the reason instead of the connection dying
        err, body = call('agent_nope')
        assert err and 'unknown tool' in body


# ═══════════════════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════════════════

class TestAuth:
    WRITES = [('agent_run', {'query': 'hi'}),
              ('agent_remember', {'name': 'a', 'content': 'b'}),
              ('agent_vault', {'op': 'list'}),
              ('agent_toolbox', {'op': 'snap', 'name': 'core'}),
              ('agent_arena_run', {}),
              ('agent_build', {'name': 'x'})]

    @pytest.mark.parametrize('name,args', WRITES)
    def test_writes_refuse_an_unsigned_connection(self, name, args):
        # forward() reads key=None as "the process itself", which is right for a
        # CLI call and wrong for anything that arrived over the network
        assert mcp.LOCAL is False
        err, body = call(name, args)
        assert err, f'{name} answered an unsigned caller'
        assert 'signed-in' in body

    def test_reads_are_open(self):
        for name, args in [('agent_parts', {}), ('agent_agents', {}),
                           ('agent_tools', {'brief': True, 'limit': 3}),
                           ('agent_toolbox', {'op': 'list'}),
                           ('agent_memory', {'op': 'state'}),
                           ('agent_whoami', {})]:
            err, _ = call(name, args)
            assert not err, f'{name} refused an open read'

    def test_a_bad_token_resolves_to_nobody(self):
        err, body = call('agent_whoami', {}, 'not-a-real-token')
        assert body['signed_in'] is False
        assert body['address'] is None

    def test_per_call_key_beats_the_transport(self):
        # the argument is consumed by call_tool, never handed to a handler
        captured = {}
        mcp.TOOLS['agent_whoami']['handler'] = lambda a, k: captured.update(a=a, k=k)
        try:
            mcp.call_tool('agent_whoami', {'key': 'per-call'}, 'transport')
        finally:
            mcp.TOOLS['agent_whoami']['handler'] = mcp._t_whoami
        assert captured['k'] == 'per-call'
        assert 'key' not in captured['a']


# ═══════════════════════════════════════════════════════════════════════
#  INTERLACING — the tools are the API's handlers, not copies of them
# ═══════════════════════════════════════════════════════════════════════

class TestInterlaced:
    def _api(self):
        try:
            import fastapi  # noqa: F401
        except ImportError:
            pytest.skip('fastapi not installed')
        return mcp._api()

    def test_the_api_module_is_the_loaded_one(self):
        api = self._api()
        # whichever name uvicorn loaded it under, there is exactly one Mod and
        # therefore one task registry behind both surfaces
        assert mcp._mod() is api.get_mod()

    def test_tool_run_goes_through_the_api_sandbox(self, tmp_path):
        api = self._api()
        seen = {}
        original = api.run_tool

        def spy(name, req):
            seen['name'], seen['key'] = name, req.key
            return original(name, req)

        api.run_tool = spy
        try:
            err, body = call('agent_tool_run',
                             {'name': 'read', 'params': {'file_path': str(tmp_path / 'no.txt')}},
                             'tok')
        finally:
            api.run_tool = original
        assert seen == {'name': 'read', 'key': 'tok'}
        assert body['tool'] == 'read'

    def test_a_run_lands_in_the_registry_the_console_polls(self):
        api = self._api()
        rows = call('agent_task', {'limit': 5})[1]
        assert set(rows) >= {'tasks', 'running'}
        assert rows['tasks'] == api.list_tasks(limit=5)['tasks']

    def test_unknown_task_id_reports_itself(self):
        self._api()
        err, body = call('agent_task', {'id': 'nope'})
        assert err and 'unknown task' in body['error']

    def test_run_resolves_the_agents_own_model(self):
        mod = mcp._mod()
        name = next((n for n in mod.agents.ls() if (mod.agents.get(n) or {}).get('model')), None)
        if not name:
            pytest.skip('no agent on this host carries its own model')
        want = mod.agents.get(name)['model']
        assert mcp._run_model({'agent': name}, mod) == want
        assert mcp._run_model({'agent': name, 'model': 'x/y'}, mod) == 'x/y'

    def test_steps_are_trimmed_for_the_context_window(self):
        step = {'tool': 'read', 'params': {'file_path': 'x' * 900},
                'result': 'y' * 5000}
        brief = mcp._brief(step)
        assert brief['tool'] == 'read'
        assert len(brief['result']) < 700 and 'chars)' in brief['result']
        assert len(brief['params']['file_path']) < 300


# ═══════════════════════════════════════════════════════════════════════
#  RESOURCES + PROMPTS
# ═══════════════════════════════════════════════════════════════════════

class TestResources:
    def test_list_is_uri_addressed(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'resources/list'})
        uris = [x['uri'] for x in r['result']['resources']]
        assert 'agent://parts' in uris and 'agent://docs/mcp' in uris
        assert all(u.startswith('agent://') for u in uris)

    def test_reading_a_live_one_returns_json(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'resources/read',
                        'params': {'uri': 'agent://parts'}})
        entry = r['result']['contents'][0]
        assert entry['mimeType'] == 'application/json'
        assert 'model' in json.loads(entry['text'])

    def test_reading_a_doc_returns_markdown(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'resources/read',
                        'params': {'uri': 'agent://docs/mcp'}})
        entry = r['result']['contents'][0]
        assert entry['mimeType'] == 'text/markdown'
        assert 'MCP' in entry['text']

    def test_unknown_uri_is_an_invalid_param(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'resources/read',
                        'params': {'uri': 'agent://nope'}})
        assert r['error']['code'] == -32602


class TestPrompts:
    def test_the_library_is_served_as_prompts(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'prompts/list'})
        prompts = r['result']['prompts']
        if not prompts:
            pytest.skip('no prompts in the library on this host')
        assert all(p['name'] == mcp._slug(p['name']) for p in prompts), 'names must be slugs'
        assert all(' ' not in p['name'] for p in prompts)

    def test_getting_one_returns_a_user_message(self):
        prompts = mcp.prompt_list()
        if not prompts:
            pytest.skip('no prompts in the library on this host')
        got = mcp.prompt_get(prompts[0]['name'], {'task': 'do the thing'})
        msg = got['messages'][0]
        assert msg['role'] == 'user'
        assert msg['content']['text'].endswith('do the thing')

    def test_unknown_prompt_is_an_invalid_param(self):
        r = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'prompts/get',
                        'params': {'name': 'nope'}})
        assert r['error']['code'] == -32602


# ═══════════════════════════════════════════════════════════════════════
#  THE MOUNTED ENDPOINT
# ═══════════════════════════════════════════════════════════════════════

class TestHttpTransport:
    def _client(self):
        try:
            from src.api.api import app
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip('fastapi not installed')
        return TestClient(app)

    def _init(self, client):
        r = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                      'params': {'protocolVersion': '2025-06-18'}})
        return r

    def test_initialize_issues_a_session(self):
        r = self._init(self._client())
        assert r.status_code == 200
        assert r.headers['mcp-session-id']
        assert r.headers['mcp-protocol-version'] == mcp.PROTOCOL_VERSION
        assert r.json()['result']['serverInfo']['name'] == 'agent'

    def test_a_session_we_never_issued_is_a_404(self):
        r = self._client().post('/mcp', headers={'mcp-session-id': 'made-up'},
                                json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})
        assert r.status_code == 404
        assert r.json()['error']['code'] == -32001

    def test_notification_only_is_a_202_with_no_body(self):
        r = self._client().post('/mcp', json={'jsonrpc': '2.0',
                                              'method': 'notifications/initialized'})
        assert r.status_code == 202
        assert not r.content

    def test_batches_answer_as_a_list(self):
        r = self._client().post('/mcp', json=[
            {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}])
        body = r.json()
        assert isinstance(body, list) and [x['id'] for x in body] == [1, 2]

    def test_sse_when_that_is_all_the_client_accepts(self):
        r = self._client().post('/mcp', headers={'accept': 'text/event-stream'},
                                json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})
        assert r.headers['content-type'].startswith('text/event-stream')
        assert r.text.startswith('data: ')

    def test_get_is_405_and_says_where_to_look(self):
        r = self._client().get('/mcp')
        assert r.status_code == 405
        assert '/mcp/schema' in r.json()['error']

    def test_delete_ends_a_session(self):
        client = self._client()
        sid = self._init(client).headers['mcp-session-id']
        assert client.delete('/mcp', headers={'mcp-session-id': sid}).status_code == 204
        r = client.post('/mcp', headers={'mcp-session-id': sid},
                        json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})
        assert r.status_code == 404

    def test_bad_json_is_a_parse_error(self):
        r = self._client().post('/mcp', content=b'{not json',
                                headers={'content-type': 'application/json'})
        assert r.status_code == 400
        assert r.json()['error']['code'] == -32700

    def test_schema_route_lists_the_tools(self):
        d = self._client().get('/mcp/schema').json()
        assert len(d['tools']) == len(mcp.TOOLS)
        assert d['resources'] and d['instructions']

    def test_health_advertises_the_endpoint(self):
        d = self._client().get('/health').json()
        assert d['mcp']['tools'] == len(mcp.TOOLS)

    def test_bearer_header_becomes_the_caller(self):
        from starlette.requests import Request
        from src.api.api import _mcp_key
        scope = {'type': 'http', 'headers': [(b'authorization', b'Bearer tok-123')],
                 'query_string': b''}
        assert _mcp_key(Request(scope)) == 'tok-123'
        scope = {'type': 'http', 'headers': [(b'x-mod-key', b'tok-456')], 'query_string': b''}
        assert _mcp_key(Request(scope)) == 'tok-456'
        scope = {'type': 'http', 'headers': [], 'query_string': b''}
        assert _mcp_key(Request(scope)) is None

    def test_an_unsigned_write_is_refused_over_http_too(self):
        r = self._client().post('/mcp', json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'agent_run', 'arguments': {'query': 'hi'}}})
        res = r.json()['result']
        assert res['isError'] and 'signed-in' in res['content'][0]['text']
