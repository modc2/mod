"""
mod2mcp.rpc — one generic MCP JSON-RPC dispatcher, parameterised by a tool
list and a call function. Both the HTTP gateway and the stdio bridge are this
plus a transport.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

PROTOCOL_VERSION = '2025-06-18'
SUPPORTED = ('2025-06-18', '2025-03-26', '2024-11-05')


def result_block(out: Any, is_error: bool = False) -> dict:
    if is_error:
        return {'content': [{'type': 'text', 'text': str(out)}], 'isError': True}
    return {'content': [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False, default=str)}],
            'structuredContent': out if isinstance(out, dict) else {'result': out},
            'isError': False}


class McpServer:
    def __init__(self, name: str, version: str, instructions: str,
                 list_tools: Callable[[], List[dict]],
                 call_tool: Callable[[str, dict], Any]):
        self.info = {'name': name, 'title': name, 'version': version}
        self.instructions = instructions
        self.list_tools = list_tools
        self.call_tool = call_tool

    def _call(self, params: dict) -> dict:
        name, args = params.get('name', ''), params.get('arguments') or {}
        try:
            return result_block(self.call_tool(name, args))
        except Exception as e:   # a revert goes back to the model, not the transport
            return result_block(f'{type(e).__name__}: {e}', is_error=True)

    def handle(self, msg: Any) -> Optional[Any]:
        """One JSON-RPC message (or batch) -> response, or None for notifications."""
        if isinstance(msg, list):
            out = [r for r in (self.handle(m) for m in msg) if r is not None]
            return out or None
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
            return {'jsonrpc': '2.0', 'id': None,
                    'error': {'code': -32600, 'message': 'invalid request'}}
        mid, method, params = msg.get('id'), msg.get('method', ''), msg.get('params') or {}
        if mid is None:
            return None                     # notification
        methods: Dict[str, Callable[[dict], Any]] = {
            'initialize': lambda p: {
                'protocolVersion': p.get('protocolVersion')
                if p.get('protocolVersion') in SUPPORTED else PROTOCOL_VERSION,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': self.info, 'instructions': self.instructions},
            'ping': lambda p: {},
            'tools/list': lambda p: {'tools': [
                {k: v for k, v in t.items() if k != 'fn'} for t in self.list_tools()]},
            'tools/call': self._call,
        }
        fn = methods.get(method)
        if fn is None:
            return {'jsonrpc': '2.0', 'id': mid,
                    'error': {'code': -32601, 'message': f'no method {method}'}}
        try:
            return {'jsonrpc': '2.0', 'id': mid, 'result': fn(params)}
        except Exception as e:
            return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32603, 'message': str(e)}}
