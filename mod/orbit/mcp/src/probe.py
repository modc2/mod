"""
probe — a minimal MCP client, just enough to ask a server what it can do.

Every directory in the world tells you a server *exists*. None of them tell
you whether it is up right now, or what its tools are actually called today.
So the hub speaks the protocol itself: initialize → notifications/initialized
→ tools/list, over Streamable HTTP, against any remote endpoint.

Two response shapes are handled, because both are legal and servers pick
either one: a plain `application/json` body, or an SSE stream where the JSON-RPC
response arrives in a `data:` frame. Session ids (`Mcp-Session-Id`) are echoed
back on follow-up requests when the server issues one.

stdio servers are deliberately NOT probed: running one means executing a
stranger's npx/uvx/docker command on this box. The hub reports what the
directories claim about them and leaves execution to the user's own client.
"""
import json
import time
from typing import Any, Dict, List, Optional

import requests

PROTOCOL_VERSION = '2025-06-18'
CLIENT_INFO = {'name': 'mod-mcp-hub', 'version': '1.0.0'}


def _parse(resp: requests.Response) -> Optional[Dict[str, Any]]:
    """Pull one JSON-RPC message out of a plain-JSON or SSE response."""
    ctype = (resp.headers.get('content-type') or '').lower()
    body = resp.text or ''
    if 'text/event-stream' in ctype:
        for line in body.splitlines():
            if not line.startswith('data:'):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except Exception:
                continue
            if isinstance(msg, dict) and ('result' in msg or 'error' in msg):
                return msg
        return None
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


class Probe:
    """One-shot handshake against a remote MCP endpoint."""

    def __init__(self, timeout: int = 12):
        self.timeout = timeout

    def _post(self, url: str, payload: Dict, headers: Dict[str, str]) -> requests.Response:
        return requests.post(url, json=payload, timeout=self.timeout, headers={
            'Content-Type': 'application/json',
            # Advertise both so a server may answer with either shape.
            'Accept': 'application/json, text/event-stream',
            'MCP-Protocol-Version': PROTOCOL_VERSION,
            **headers,
        })

    def probe(self, url: str, token: Optional[str] = None,
              tools: bool = True) -> Dict[str, Any]:
        """Handshake + optional tools/list. Never raises — a dead server is a
        result (`ok: false` with the reason), not an exception."""
        started = time.time()
        out: Dict[str, Any] = {'url': url, 'ok': False, 'tools': [],
                               'probed_at': int(started)}
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        try:
            resp = self._post(url, {
                'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': PROTOCOL_VERSION,
                           'capabilities': {}, 'clientInfo': CLIENT_INFO},
            }, headers)
        except requests.RequestException as e:
            out['error'] = f'unreachable: {type(e).__name__}'
            return out
        if resp.status_code >= 400:
            out['error'] = f'HTTP {resp.status_code}' + (
                ' — this server needs an auth token' if resp.status_code in (401, 403) else '')
            out['needs_auth'] = resp.status_code in (401, 403)
            return out
        msg = _parse(resp)
        if not msg or 'result' not in msg:
            err = (msg or {}).get('error') if isinstance(msg, dict) else None
            out['error'] = f"initialize failed: {err.get('message') if err else 'no JSON-RPC result'}"
            return out

        result = msg['result'] or {}
        out.update({
            'ok': True,
            'protocol_version': result.get('protocolVersion'),
            'server_info': result.get('serverInfo') or {},
            'capabilities': result.get('capabilities') or {},
            'instructions': result.get('instructions'),
            'latency_ms': int((time.time() - started) * 1000),
        })
        session = resp.headers.get('mcp-session-id')
        if session:
            headers['Mcp-Session-Id'] = session
            out['session'] = True
        if not tools:
            return out

        # Spec order: tell the server we're initialized before calling tools.
        try:
            self._post(url, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                       headers)
        except requests.RequestException:
            pass  # notification-only; servers that ignore it still answer tools/list
        try:
            resp = self._post(url, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list',
                                    'params': {}}, headers)
            msg = _parse(resp)
        except requests.RequestException as e:
            out['tools_error'] = f'{type(e).__name__}'
            return out
        if not msg or 'result' not in msg:
            err = (msg or {}).get('error') if isinstance(msg, dict) else None
            out['tools_error'] = (err or {}).get('message', 'tools/list returned no result')
            return out
        out['tools'] = [{
            'name': t.get('name'),
            'description': (t.get('description') or '').strip(),
            'input_schema': t.get('inputSchema') or {},
        } for t in (msg['result'].get('tools') or [])]
        out['tool_count'] = len(out['tools'])
        out['latency_ms'] = int((time.time() - started) * 1000)
        return out


def remote_url(rec: Dict[str, Any]) -> Optional[str]:
    """The first remote endpoint a server record advertises, if any."""
    for r in rec.get('remotes') or []:
        if r.get('url'):
            return r['url']
    inst = rec.get('install') or {}
    return inst.get('remote') if isinstance(inst.get('remote'), str) else None


def client_config(rec: Dict[str, Any], client: str = 'claude') -> Dict[str, Any]:
    """Ready-to-paste client configuration for a server card.

    Clients disagree on the wrapper key (`mcpServers` vs `servers`) but agree
    on the body, so build the body once from whichever install recipe the
    directories gave us and wrap it per client.
    """
    name = (rec.get('name') or 'server').replace(' ', '-').lower()
    inst = rec.get('install') or {}
    url = remote_url(rec)
    body: Dict[str, Any]
    if url:
        body = {'type': 'http', 'url': url}
        cli = f'claude mcp add --transport http {name} {url}'
    elif inst.get('npx'):
        args = inst['npx'].split()
        body = {'command': args[0], 'args': args[1:]}
        cli = f"claude mcp add {name} -- {inst['npx']}"
    elif inst.get('uvx'):
        args = inst['uvx'].split()
        body = {'command': args[0], 'args': args[1:]}
        cli = f"claude mcp add {name} -- {inst['uvx']}"
    elif inst.get('docker'):
        args = inst['docker'].split()
        body = {'command': args[0], 'args': args[1:]}
        cli = f"claude mcp add {name} -- {inst['docker']}"
    else:
        return {'id': rec.get('id'), 'client': client, 'config': None,
                'command': None,
                'note': 'no install recipe published — see the repository README',
                'repo': rec.get('repo')}
    wrapper = {'claude': 'mcpServers', 'cursor': 'mcpServers',
               'vscode': 'servers'}.get(client, 'mcpServers')
    return {
        'id': rec.get('id'), 'client': client,
        'config': {wrapper: {name: body}},
        'command': cli,
        'file': {'claude': '~/.claude.json (or claude_desktop_config.json)',
                 'cursor': '~/.cursor/mcp.json',
                 'vscode': '.vscode/mcp.json'}.get(client, '~/.claude.json'),
    }
