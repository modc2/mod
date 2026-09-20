#!/usr/bin/env python3
"""selfinsure api — one port: REST for the pools, MCP for agents, a page for people.

    GET  /                      the transparency page (HTML)
    GET  /health
    GET  /tools                 the MCP tool list
    POST /mcp                   JSON-RPC 2.0 (Streamable HTTP)
    POST /tools/<name>          call one tool with a JSON body (REST alias)
    GET  /pools · /pool/<id> · /stats · /ledger
    GET  /contract              what the contract guarantees, and its calls
    GET  /contract/source · /contract/abi
    GET  /preset?preset=health&decimals=6
    GET  /onchain?address=0x..  a live pool, read off the chain
    GET  /onchain/claim?address=0x..&claim=1

Standard library only. Reads are open; the tools that move money on the
off-chain ledger need the keys they always needed, and an on-chain deploy
needs the eth module's keystore.
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import mcp as M                                              # noqa: E402
import onchain as O                                          # noqa: E402
import pool as P                                             # noqa: E402
from chain import ChainError                                 # noqa: E402
from pool import SelfInsureError                             # noqa: E402

PAGE = os.path.join(HERE, 'console.html')


class Handler(BaseHTTPRequestHandler):
    server_version = 'selfinsure/1'

    def log_message(self, fmt, *args):
        if os.environ.get('SELFINSURE_LOG'):
            super().log_message(fmt, *args)

    # ── plumbing ────────────────────────────────────────────
    def _send(self, status, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else \
            (body.encode() if isinstance(body, str) else
             json.dumps(body, default=str, indent=2).encode())
        self.send_response(status)
        self.send_header('content-type', ctype + ('; charset=utf-8' if ctype.startswith('text') else ''))
        self.send_header('content-length', str(len(data)))
        self.send_header('access-control-allow-origin', '*')
        self.send_header('access-control-allow-headers', 'content-type, authorization, mcp-protocol-version')
        self.send_header('access-control-allow-methods', 'GET, POST, OPTIONS')
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get('content-length') or 0)
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            raise SelfInsureError('body is not JSON')

    def _query(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return {k: v[-1] for k, v in q.items()}

    def _run(self, fn):
        try:
            self._send(200, fn())
        except (SelfInsureError, ChainError) as e:
            self._send(e.status, e.dict())
        except Exception as e:
            self._send(500, {'error': f'{type(e).__name__}: {e}'})

    def do_OPTIONS(self):
        self._send(204, b'')

    # ── routes ──────────────────────────────────────────────
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip('/') or '/'
        q = self._query()
        if path == '/':
            try:
                with open(PAGE) as f:
                    return self._send(200, f.read(), 'text/html')
            except FileNotFoundError:
                return self._send(200, {'service': 'selfinsure', 'see': '/contract'})
        if path == '/health':
            return self._send(200, {'ok': True, 'service': 'selfinsure', 'version': M.version(),
                                    'tools': len(M.TOOLS), 'contract_built': bool(O.artifact())})
        if path in ('/tools', '/mcp/tools'):
            return self._send(200, {'tools': M.tool_list()})
        if path == '/mcp':
            return self._send(405, {'error': 'POST JSON-RPC 2.0 to /mcp'})
        if path == '/pools':
            return self._run(lambda: P.pools(q=q.get('q'), state=q.get('state'),
                                             limit=q.get('limit', 100)))
        if path.startswith('/pool/'):
            return self._run(lambda: P.pool_info(urllib.parse.unquote(path[6:])))
        if path == '/stats':
            return self._run(P.stats)
        if path == '/ledger':
            return self._run(lambda: P.ledger(pool=q.get('pool'), kind=q.get('kind'),
                                              limit=q.get('limit', 100)))
        if path == '/contract':
            return self._run(lambda: M.call_tool('si_contract', {'what': 'describe'}))
        if path == '/contract/source':
            return self._run(lambda: self._send(200, O.source(q.get('contract', 'SelfInsure')),
                                                'text/plain'))
        if path == '/contract/abi':
            return self._run(lambda: {'contract': q.get('contract', 'SelfInsure'),
                                      'abi': O.abi(q.get('contract', 'SelfInsure'))})
        if path == '/preset':
            return self._run(lambda: M.call_tool('si_preset', dict(q)))
        if path == '/onchain':
            return self._run(lambda: M.call_tool('si_onchain', dict(q)))
        if path == '/onchain/claim':
            return self._run(lambda: M.call_tool('si_onchain_claim', dict(q)))
        self._send(404, {'error': f'no route {path}', 'see': __doc__.strip().splitlines()[2:16]})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path.rstrip('/')
        if path == '/mcp':
            try:
                body = self._body()
            except SelfInsureError:
                return self._send(200, M._error(None, -32700, 'parse error'))
            if isinstance(body, list):
                out = [r for r in (M.handle(b) for b in body) if r is not None]
                return self._send(200, out) if out else self._send(202, b'')
            resp = M.handle(body)
            return self._send(200, resp) if resp is not None else self._send(202, b'')
        if path.startswith('/tools/'):
            name = path[7:]
            return self._run(lambda: M.call_tool(name, self._body()))
        self._send(404, {'error': f'no route {path}'})


def serve(port=None):
    port = int(port or os.environ.get('PORT', 50850))
    httpd = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'selfinsure api on :{port} — GET / for the page, POST /mcp for agents', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(argv[i] if i > 0 else None)
