"""
tdot.mcp_server — MCP stdio server over the Toronto map.

Zero-dependency implementation of the Model Context Protocol stdio transport
(newline-delimited JSON-RPC 2.0), exposing every tool in :mod:`tdotgis.tools`.
The chat agent drives the map through this; so can any other MCP client:

    claude mcp add tdot -- python3 -m tdotgis.mcp_server

or in an mcpServers config:

    {"tdot": {"command": "python3", "args": ["-m", "tdotgis.mcp_server"],
              "cwd": "/root/mod/mod/orbit/tdot"}}
"""
from __future__ import annotations

import json
import sys
import traceback

from . import tools

SERVER_INFO = {'name': 'tdot', 'version': '2.1.0'}
PROTOCOL_VERSION = '2025-06-18'

INSTRUCTIONS = (
    'Toronto open-data map tools. tdot_list_layers is the catalogue; '
    'tdot_layer_summary describes a layer\'s fields; tdot_layer_query answers '
    'questions with grouped counts and totals instead of raw geometry. The '
    'tdot_show_layers / tdot_fly_to / tdot_set_crime_view tools change the map '
    'the person is looking at right now — use them, do not just describe what '
    'they would see. If no layer covers the question, tdot_search_open_data '
    'the city portal and tdot_add_open_data the dataset you find.')


def _reply(id_, result=None, error=None):
    msg = {'jsonrpc': '2.0', 'id': id_}
    if error is not None:
        msg['error'] = error
    else:
        msg['result'] = result
    sys.stdout.write(json.dumps(msg, default=str) + '\n')
    sys.stdout.flush()


def handle(msg: dict):
    method = msg.get('method')
    id_ = msg.get('id')

    if method == 'initialize':
        client_ver = (msg.get('params') or {}).get('protocolVersion')
        _reply(id_, {'protocolVersion': client_ver or PROTOCOL_VERSION,
                     'capabilities': {'tools': {}},
                     'serverInfo': SERVER_INFO,
                     'instructions': INSTRUCTIONS})
    elif method == 'ping':
        _reply(id_, {})
    elif method == 'tools/list':
        _reply(id_, {'tools': tools.list_tools()})
    elif method == 'tools/call':
        params = msg.get('params') or {}
        try:
            result = tools.call_tool(params.get('name'), params.get('arguments') or {})
            _reply(id_, {'content': [{'type': 'text',
                                      'text': json.dumps(result, default=str)}],
                         'isError': False})
        except Exception as e:
            _reply(id_, {'content': [{'type': 'text',
                                      'text': f'{type(e).__name__}: {e}'}],
                         'isError': True})
    elif id_ is not None:
        _reply(id_, error={'code': -32601, 'message': f'method not found: {method}'})
    # notifications (no id) are ignored


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            if msg.get('id') is not None:
                _reply(msg['id'], error={'code': -32603, 'message': 'internal error'})


if __name__ == '__main__':
    main()
