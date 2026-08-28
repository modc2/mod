"""
nyc.mcp_server — MCP stdio server over NYC open data.

Zero-dependency implementation of the Model Context Protocol stdio transport
(newline-delimited JSON-RPC 2.0). Exposes every tool in nycgis.tools to any
MCP client:

    claude mcp add nyc -- python3 -m nycgis.mcp_server

or in mcpServers config:

    {"nyc": {"command": "python3", "args": ["-m", "nycgis.mcp_server"],
             "cwd": "/root/mod/mod/orbit/nyc"}}

The same JSON-RPC surface is also served over HTTP at the API's /mcp endpoint.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import tools

SERVER_INFO = {'name': 'nyc', 'version': '1.1.0'}
PROTOCOL_VERSION = '2025-06-18'

INSTRUCTIONS = (
    'NYC open-data tools. For housing questions use nyc_housing / nyc_prices '
    '/ nyc_trend / nyc_sales (recorded deeds, 2016-present). For transit, '
    'parks, flood zones, crashes and boundaries use nyc_layers then '
    'nyc_layer. For ANY other topic — 311, crime, schools, health, budgets, '
    'permits — search the whole portal with nyc_find_datasets, read the '
    'columns with nyc_dataset, then aggregate with nyc_query (SoQL). '
    'Everything is public, key-free city and state open data; cite the '
    'dataset a number came from.')


def _reply(id_, result=None, error=None):
    msg = {'jsonrpc': '2.0', 'id': id_}
    if error is not None:
        msg['error'] = error
    else:
        msg['result'] = result
    sys.stdout.write(json.dumps(msg) + '\n')
    sys.stdout.flush()


def handle(msg: dict):
    method = msg.get('method')
    id_ = msg.get('id')

    if method == 'initialize':
        client_ver = (msg.get('params') or {}).get('protocolVersion')
        _reply(id_, {
            'protocolVersion': client_ver or PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': SERVER_INFO,
            'instructions': INSTRUCTIONS,
        })
    elif method == 'ping':
        _reply(id_, {})
    elif method == 'tools/list':
        _reply(id_, {'tools': tools.list_tools()})
    elif method == 'tools/call':
        params = msg.get('params') or {}
        name = params.get('name')
        args = params.get('arguments') or {}
        try:
            result = tools.call_tool(name, args)
            _reply(id_, {
                'content': [{'type': 'text',
                             'text': json.dumps(result, indent=2, default=str)}],
                'isError': False,
            })
        except Exception as e:
            _reply(id_, {
                'content': [{'type': 'text', 'text': f'{type(e).__name__}: {e}'}],
                'isError': True,
            })
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
