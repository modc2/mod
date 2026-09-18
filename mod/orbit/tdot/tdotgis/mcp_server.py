"""
tdot.mcp_server — MCP server over the Toronto map, on two transports.

Zero-dependency implementation of the Model Context Protocol, exposing every
tool in :mod:`tdotgis.tools`. The chat agent drives the map through this; so can
any other MCP client, over whichever transport suits it:

    stdio            claude mcp add tdot -- python3 -m tdotgis.mcp_server
    streamable HTTP  claude mcp add --transport http tdot http://localhost:50320/mcp

or in an mcpServers config:

    {"tdot":      {"command": "python3", "args": ["-m", "tdotgis.mcp_server"],
                   "cwd": "/root/mod/mod/orbit/tdot"},
     "tdot-http": {"type": "http", "url": "http://localhost:50320/mcp"}}

:func:`rpc` is the whole protocol and is transport-free — it takes a decoded
JSON-RPC message and returns the reply to send, or ``None`` for a notification.
The stdio loop below and the ``/mcp`` route in ``api/api.py`` are both thin
wrappers over it, so the two transports cannot drift apart.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Dict, Optional

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


def _ok(id_, result) -> dict:
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _err(id_, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def rpc(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Handle one JSON-RPC message and return the reply, or ``None`` if the
    message was a notification (no ``id``) and the protocol wants silence.
    """
    method = msg.get('method')
    id_ = msg.get('id')

    if method == 'initialize':
        client_ver = (msg.get('params') or {}).get('protocolVersion')
        return _ok(id_, {'protocolVersion': client_ver or PROTOCOL_VERSION,
                         'capabilities': {'tools': {}},
                         'serverInfo': SERVER_INFO,
                         'instructions': INSTRUCTIONS})

    if method == 'ping':
        return _ok(id_, {})

    if method == 'tools/list':
        return _ok(id_, {'tools': tools.list_tools()})

    if method == 'tools/call':
        params = msg.get('params') or {}
        try:
            result = tools.call_tool(params.get('name'), params.get('arguments') or {})
            return _ok(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(result, default=str)}],
                             'isError': False})
        except Exception as e:
            # A tool that raised is a *tool* failure, not a protocol failure:
            # it comes back as content with isError so the model can read the
            # message and try something else, rather than as a JSON-RPC error.
            return _ok(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})

    if id_ is not None:
        return _err(id_, -32601, f'method not found: {method}')
    return None  # notification for a method we don't implement


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
            reply = rpc(msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            reply = (_err(msg['id'], -32603, 'internal error')
                     if msg.get('id') is not None else None)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
