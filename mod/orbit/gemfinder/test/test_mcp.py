"""Offline tests for the MCP server: JSON-RPC dispatch, tool schemas, agent docs (no network)."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('GEMFINDER_HOME', tempfile.mkdtemp())
sys.path.append(str(Path(__file__).resolve().parents[1]))

from gemscan import mcp_server as mcp  # noqa: E402
from gemscan.server import agent_docs  # noqa: E402


def rpc(method, params=None, mid=1):
    return mcp.handle_message({'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params or {}})


def test_initialize_negotiates_and_instructs():
    res = rpc('initialize', {'protocolVersion': '2025-06-18'})['result']
    assert res['protocolVersion'] == '2025-06-18'
    assert res['serverInfo']['name'] == 'gemfinder'
    assert 'attention' in res['instructions']
    # an unknown client version falls back to ours instead of failing
    assert rpc('initialize', {'protocolVersion': '1999-01-01'})['result']['protocolVersion'] == mcp.PROTOCOL_VERSION


def test_tool_list_is_read_only_with_valid_schemas():
    tools = rpc('tools/list')['result']['tools']
    names = {t['name'] for t in tools}
    assert {'gem_scan', 'gem_find', 'gem_review', 'gem_narratives',
            'gem_history', 'gem_sources', 'gem_status'} <= names
    for t in tools:
        assert t['annotations']['readOnlyHint'] is True        # the whole MCP surface is read-only
        assert t['inputSchema']['type'] == 'object'
        assert t['description']
    assert 'q' in next(t for t in tools if t['name'] == 'gem_find')['inputSchema']['required']


def test_call_tool_offline_and_unknown():
    out = rpc('tools/call', {'name': 'gem_status', 'arguments': {}})['result']
    assert out['isError'] is False
    body = json.loads(out['content'][0]['text'])
    assert body['module'] == 'gemfinder' and 'narratives' in body
    assert rpc('tools/call', {'name': 'nope'})['result']['isError'] is True
    # a tool that blows up reports the error to the model instead of raising
    bad = rpc('tools/call', {'name': 'gem_review', 'arguments': {'n': 'not-a-number'}})['result']
    assert bad['isError'] is True and 'ValueError' in bad['content'][0]['text']


def test_jsonrpc_envelope():
    assert mcp.handle_message({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    assert mcp.handle_message({'nope': 1})['error']['code'] == -32600
    assert rpc('no/such')['error']['code'] == -32601
    batch = mcp.handle_message([{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
                                {'jsonrpc': '2.0', 'method': 'notifications/x'}])
    assert len(batch) == 1 and batch[0]['result'] == {}


def test_agent_docs_cover_the_surface():
    docs = agent_docs()
    assert docs['module'] == 'gemfinder'
    tool_names = {t['name'] for t in docs['mcp']['tools']}
    assert {t['name'] for t in mcp.tool_list()} == tool_names
    assert 'POST /mcp' in docs['rest'] and 'GET /scan' in docs['rest']
    for key in ('score', 'upside', 'fit', 'flags', 'pick', 'honesty'):
        assert key in docs['reading_a_gem']
