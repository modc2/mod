"""
Tests for src/tools/mcp — the fleet's MCP servers as agent tools.

A fake MCP server (stdlib http.server) answers initialize / tools/list /
tools/call, once as plain JSON and once as SSE, so the whole pipeline is
exercised without the fleet: discovery from a temp config tree, the
handshake, schema conversion, calling, error shapes, and the union registry.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.tools.mcp.mod import (Client, McpError, McpTools, PREFIX,
                               _parse_body)
from src.tools.mod import Tools

TOOLS = [
    {'name': 'echo', 'description': 'Echo the arguments back',
     'inputSchema': {'type': 'object',
                     'properties': {
                         'text': {'type': 'string', 'description': 'what to say'},
                         'name': {'type': 'string', 'description': 'who says it'},
                         'n': {'type': 'integer', 'default': 1},
                         'mode': {'type': 'string', 'enum': ['loud', 'quiet']}},
                     'required': ['text']}},
    {'name': 'boom', 'description': 'Always fails', 'inputSchema': {}},
]


class FakeMcp(BaseHTTPRequestHandler):
    """A minimal Streamable HTTP MCP server."""
    sse = False           # class flag: answer as text/event-stream
    calls = []            # (tool, arguments) log

    def log_message(self, *a):
        pass

    def _reply(self, message):
        body = json.dumps(message)
        if type(self).sse:
            body = f"event: message\ndata: {body}\n\n"
            ctype = 'text/event-stream'
        else:
            ctype = 'application/json'
        data = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Mcp-Session-Id', 'sess-1')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        msg = json.loads(raw or b'{}')
        method, mid = msg.get('method'), msg.get('id')
        if mid is None:                       # a notification
            self.send_response(202)
            self.end_headers()
            return
        if method == 'initialize':
            result = {'protocolVersion': '2025-06-18',
                      'serverInfo': {'name': 'fake', 'version': '1'},
                      'capabilities': {'tools': {}}}
        elif method == 'tools/list':
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            params = msg.get('params') or {}
            type(self).calls.append((params.get('name'),
                                     params.get('arguments')))
            if params.get('name') == 'boom':
                result = {'isError': True,
                          'content': [{'type': 'text', 'text': 'it broke'}]}
            else:
                result = {'content': [{'type': 'text', 'text': json.dumps(
                    params.get('arguments') or {})}]}
        else:
            self._reply({'jsonrpc': '2.0', 'id': mid,
                         'error': {'code': -32601, 'message': 'no such method'}})
            return
        self._reply({'jsonrpc': '2.0', 'id': mid, 'result': result})


@pytest.fixture(scope='module')
def server():
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), FakeMcp)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    httpd.shutdown()


@pytest.fixture()
def registry(server, tmp_path):
    """An McpTools whose fleet is one temp module declaring the fake server."""
    fleet = tmp_path / 'orbit'
    (fleet / 'fake').mkdir(parents=True)
    (fleet / 'fake' / 'config.json').write_text(json.dumps(
        {'name': 'fake', 'urls': {'mcp': server}}))
    t = McpTools(roots=[str(fleet)], state_dir=str(tmp_path / 'state'))
    t.refresh()
    return t


class TestParsing:
    def test_json_body(self):
        assert _parse_body(b'{"result": 1, "id": 1}', 'application/json') == \
            {'result': 1, 'id': 1}

    def test_sse_body_takes_last_message(self):
        raw = (b': ping\n\n'
               b'data: {"nope": true}\n\n'
               b'data: {"id": 1, "result": {"a": 1}}\n\n')
        assert _parse_body(raw, 'text/event-stream')['result'] == {'a': 1}

    def test_garbage_is_none(self):
        assert _parse_body(b'<html>502</html>', 'text/html') is None
        assert _parse_body(b'', 'application/json') is None


class TestNames:
    def test_split_and_join(self):
        assert McpTools.split('mcp.build.list_modules') == ('build', 'list_modules')
        assert McpTools.split('mcp.x.dotted.tool') == ('x', 'dotted.tool')
        assert McpTools.tool_name('dns', 'dns_resolve') == 'mcp.dns.dns_resolve'

    def test_schema_conversion(self):
        params = McpTools._params(TOOLS[0]['inputSchema'])
        assert params['text']['required'] is True
        assert params['n']['required'] is False and params['n']['default'] == 1
        assert 'loud' in params['mode']['hint']


class TestDiscovery:
    def test_candidates_cover_the_config_shapes(self):
        urls = McpTools._candidates('x', {'urls': {'mcp': 'http://h:1/mcp'}})
        assert urls[0] == 'http://h:1/mcp'
        urls = McpTools._candidates('x', {'mcp': {'url': 'http://h:2/x/mcp'}})
        assert 'http://h:2/x/mcp' in urls
        # prose block + api port -> derived {api}/mcp
        urls = McpTools._candidates('x', {'mcp': {'endpoint': 'POST /mcp'},
                                          'urls': {'api': 'http://h:3'}})
        assert 'http://h:3/mcp' in urls

    def test_discover_reads_the_temp_fleet(self, registry):
        assert 'fake' in registry.discover()

    def test_skip_and_missing(self, registry):
        # the agent itself is never a candidate — that would nest the loop
        assert 'agent' not in registry.discover()
        assert not registry.exists('mcp.ghost.tool')


class TestRegistry:
    def test_index_and_ls(self, registry):
        assert registry.servers()[0]['ok'] is True
        names = registry.ls()
        assert f'{PREFIX}fake.echo' in names and f'{PREFIX}fake.boom' in names

    def test_get_and_schema(self, registry):
        entry = registry.get('mcp.fake.echo')
        assert entry['kind'] == 'mcp' and entry['server'] == 'fake'
        schema = registry.schema(['mcp.fake.echo'])
        assert schema['mcp.fake.echo']['params']['text']['required']

    def test_call_roundtrip(self, registry):
        out = registry.run('mcp.fake.echo', text='hi', n=3)
        assert out['success'] and out['result'] == {'text': 'hi', 'n': 3}

    def test_tool_arg_named_name_reaches_the_tool(self, registry):
        out = registry.run('mcp.fake.echo', params={'text': 'x', 'name': 'bob'})
        assert out['success'] and out['result']['name'] == 'bob'

    def test_stringified_params(self, registry):
        out = registry.run('mcp.fake.echo', params='{"text": "s"}')
        assert out['success'] and out['result'] == {'text': 's'}

    def test_is_error_fails_the_step(self, registry):
        out = registry.run('mcp.fake.boom')
        assert out['success'] is False and 'it broke' in str(out['error'])

    def test_unknown_tool(self, registry):
        assert registry.run('mcp.fake.nope')['success'] is False

    def test_lowercased_name_still_resolves(self, registry):
        # run_plan lowercases every tool name before dispatch
        assert registry.exists('mcp.fake.ECHO'.lower())
        out = registry.run('mcp.fake.Echo'.lower(), text='case')
        assert out['success'] and out['result']['text'] == 'case'

    def test_index_persists(self, registry, server, tmp_path):
        again = McpTools(roots=registry._roots,
                         state_dir=str(registry._dir))
        # loaded from disk, no fresh probe needed
        assert f'{PREFIX}fake.echo' in [
            t['name'] for t in again.items()]

    def test_sse_transport(self, registry):
        FakeMcp.sse = True
        try:
            out = registry.run('mcp.fake.echo', text='via-sse')
            assert out['success'] and out['result']['text'] == 'via-sse'
        finally:
            FakeMcp.sse = False


class TestUnionRegistry:
    def test_mcp_is_the_fourth_kind(self, registry, monkeypatch):
        tools = Tools()
        monkeypatch.setattr(tools, 'mcp', registry)
        assert tools.kind('mcp.fake.echo') == 'mcp'
        assert tools.is_mcp('mcp.fake.echo') and not tools.is_mod('mcp.fake.echo')
        # off the default loadout, present when asked for by name
        assert 'mcp.fake.echo' not in tools.schema()
        assert 'mcp.fake.echo' in tools.schema(['mcp.fake.echo'])
        out = tools.run('mcp.fake.echo', text='union', name='clash-ok')
        assert out['success'] and out['result']['name'] == 'clash-ok'

    def test_items_search(self, registry, monkeypatch):
        tools = Tools()
        monkeypatch.setattr(tools, 'mcp', registry)
        hits = [t for t in tools.items(mods=True, q='echo')
                if t['kind'] == 'mcp']
        assert hits and hits[0]['name'] == 'mcp.fake.echo'
