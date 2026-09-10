"""
bt.mcp_server — MCP stdio server over the Bittensor protocol.

Zero-dependency implementation of the Model Context Protocol stdio
transport (newline-delimited JSON-RPC 2.0). Exposes every tool in
bt.tools to any MCP client:

    claude mcp add bittensor -- python3 -m bt.mcp_server

or in mcpServers config:

    {"bittensor": {"command": "python3", "args": ["-m", "bt.mcp_server"],
                   "cwd": "/root/mod/mod/orbit/bt"}}
"""
from __future__ import annotations

import json
import sys
import traceback

from . import tools

SERVER_INFO = {'name': 'bittensor', 'version': '2.4.0'}
PROTOCOL_VERSION = '2025-06-18'


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
            'instructions': (
                'Bittensor protocol tools: subnets, neurons, wallets, TAO '
                'balances/transfers, alpha prices, and staking trades '
                '(buy/sell/swap). For market data prefer bt_screener / '
                'bt_history / bt_stats — they answer instantly from a local '
                'price-history indexer (with 1h/24h/7d changes, 24h volume '
                'and sparklines) instead of a slow full-chain scan. To '
                'follow an account over time, bt_track it — then bt_traders, '
                'bt_trader and bt_trader_flows serve its equity curve, PnL '
                'and inferred trades from the same local index. Tools '
                'marked as real on-chain trades move real TAO — confirm '
                'with the user before calling them. bt_view is the one tool '
                'that touches no chain: it opens a view in the bt console '
                'the caller is looking at, so an answer about a subnet or a '
                'trader lands on their screen.'),
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
