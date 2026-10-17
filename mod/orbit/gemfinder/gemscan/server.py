"""
gemscan.server — REST on one port, stdlib only.

    GET /            the app (app/index.html)
    GET /health
    GET /scan?asset=all|crypto|stock&k=20&narrative=&q=&min_score=&sources=a,b&hn=1&fresh=0&sort=upside|score
    GET /narratives?asset=
    GET /history?n=10
    GET /review?n=12    past picks priced now vs then (did they go up?)
    GET /sources
    GET /cache          scan-cache state;  DELETE /cache  (or GET /cache/clear) drops it
    GET /docs           docs for agents: how to read a gem, every MCP tool, every REST route
    POST /mcp           MCP (streamable HTTP, JSON responses) — gem_scan, gem_find, gem_review, ...
    GET /whoami         who the Authorization: Bearer <mod-protocol token> is
    GET /auth/local     loopback only: mint a token with the box's own mod key

Sign-in (identity.py): a browser wallet mints the token in the page; the box
itself gets one from /auth/local. Every scan stays public — the one gated
write is DELETE /cache (box or owner).

Scans are cached in-process (SCAN_TTL, default 10 min) and a warmer refreshes
the asset=all base on boot and every TTL, so the app opens from cache in
milliseconds. ?fresh=1 forces a live rescan. ?q= is a typed theme, matched by
words and by meaning (gemscan.semantic via the modsearch module when it is up).

Paths are also accepted under /gemfinder and /gemfinder/api (gateway convention).
Failures are 4xx with a JSON {error}: Cloudflare eats 5xx bodies.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import engine, http, identity, narratives, semantic, sources
from .mcp_server import INSTRUCTIONS, handle_message, tool_list

PREFIXES = ('/gemfinder/api', '/gemfinder', '/api')
APP = Path(__file__).resolve().parent.parent / 'app' / 'index.html'


def route(path: str) -> str:
    for p in PREFIXES:
        if path == p or path.startswith(p + '/'):
            return path[len(p):] or '/'
    return path


def _bool(v) -> bool:
    return str(v).lower() not in ('0', 'false', 'no', 'off', '')


def source_list() -> dict:
    out = {n: {'asset': s['asset'], 'docs': (s['fn'].__doc__ or '').strip()}
           for n, s in sources.CANDIDATE_SOURCES.items()}
    out.update({n: {'asset': s['asset'], 'theme': True, 'docs': (s['fn'].__doc__ or '').strip()}
                for n, s in sources.THEME_SOURCES.items()})
    return out


def agent_docs() -> dict:
    """GET /docs — the whole surface, written for an agent deciding what to call."""
    return {
        'module': 'gemfinder',
        'what': INSTRUCTIONS,
        'reading_a_gem': {
            'score': 'attention 0-100 = attention(30) + velocity(20) + narrative(25) + momentum(15) + size(10) '
                     '- penalties; `parts` has the breakdown, `why` the reasons in words',
            'upside': 'score x theme-fit x room-to-run — the sort key for "most likely to go up"; '
                      'hard flags cut it',
            'fit': '0-1, how well the gem matches the typed theme (words + semantic); 1 when no theme',
            'flags': 'risk, not color: thin liquidity, no pool, FDV>>liquidity, pair <24h old, '
                     'sell pressure, already vertical, penny stock. Never ignore them.',
            'pick': 'the single gem the scan calls most likely to go up; null when nothing qualifies',
            'honesty': 'this measures attention, not value. Most narrative pumps round-trip; many '
                       'micro-cap DEX tokens are rugs. Use /review for how past picks actually did.',
        },
        'mcp': {
            'http': {'endpoint': '/mcp', 'transport': 'streamable HTTP, JSON responses (no SSE)',
                     'add': 'claude mcp add --transport http gemfinder http://localhost:51320/mcp'},
            'stdio': {'cmd': 'python3 -m gemscan.mcp_server (cwd = the gemfinder module dir)',
                      'add': 'claude mcp add gemfinder -- python3 -m gemscan.mcp_server'},
            'tools': tool_list(),
        },
        'rest': {
            'GET /scan': 'asset=all|crypto|stock & k & q=<any theme> & narrative & min_score & '
                         'sort=upside|score & fresh=1 — same result the app renders',
            'GET /narratives': 'heat 0-100 per built-in narrative with raw drivers',
            'GET /review': 'n=12 — past picks priced now vs when listed (hit rate, median return)',
            'GET /history': 'n=10 — past scans with prices at scan time',
            'GET /sources': 'the feeds and what each contributes',
            'GET /health': 'liveness + cache + semantic state',
            'GET /cache': 'scan-cache keys with ages; DELETE /cache clears (box or owner only)',
            'GET /docs': 'this document',
            'POST /mcp': 'MCP JSON-RPC (initialize, tools/list, tools/call)',
        },
        'conventions': 'paths also answer under /gemfinder and /gemfinder/api; errors are 4xx JSON '
                       '{error}; scans cached ~10 min (fresh=1 re-hits every feed, 10-30s); all reads '
                       'are public and keyless',
    }


def warm(interval: int = None) -> threading.Thread:
    """Background: keep the asset=all scan hot so the app never waits on feeds."""
    interval = interval or max(60, engine.SCAN_TTL - 20)

    def loop():
        while True:
            try:
                engine.scan('all', k=50, save=False, fresh=True)
            except Exception:
                pass
            time.sleep(interval)
    t = threading.Thread(target=loop, name='gemfinder-warm', daemon=True)
    t.start()
    return t


class Handler(BaseHTTPRequestHandler):
    server_version = 'mod-gemfinder/0.1'

    def log_message(self, *a):
        pass

    def _send(self, code: int, body):
        data = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()
        self.wfile.write(data)

    def _html(self, data: bytes):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, {})

    def _is_box(self) -> bool:
        """The box itself: a loopback client that no proxy forwarded for."""
        return (self.client_address[0] in ('127.0.0.1', '::1')
                and not self.headers.get('X-Forwarded-For')
                and not self.headers.get('X-Real-IP'))

    def _caller(self):
        """Lowercase signer address of the bearer token, or None (anon)."""
        return identity.whoami(self.headers.get('Authorization', ''))

    def do_POST(self):
        if route(urllib.parse.urlparse(self.path).path).rstrip('/') != '/mcp':
            return self._send(404, {'error': 'no route'})
        try:
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0) or 0))
            msg = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {'jsonrpc': '2.0', 'id': None,
                                    'error': {'code': -32700, 'message': 'parse error'}})
        resp = handle_message(msg)
        return self._send(202, {}) if resp is None else self._send(200, resp)

    def do_DELETE(self):
        if route(urllib.parse.urlparse(self.path).path).rstrip('/') == '/cache':
            try:
                addr = self._caller()
            except identity.AuthError as e:
                return self._send(401, {'error': str(e)})
            box = identity.box_address()
            if not (self._is_box() or (addr and addr == box)):
                return self._send(401, {'error': 'clearing the cache is for the box or its owner: '
                                                 'sign in (wallet or /auth/local) first'})
            return self._send(200, engine.clear_scan_cache())
        self._send(404, {'error': 'no route'})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = route(u.path).rstrip('/') or '/'
        if path in ('/', '/index.html') and APP.exists():
            return self._html(APP.read_bytes())
        try:
            if path == '/health':
                body = {'ok': True, 'module': 'gemfinder', 'cache': http.stats(),
                        'narratives': narratives.ids(), 'semantic': semantic.status()}
            elif path == '/scan':
                body = engine.scan(asset=q.get('asset', 'all'), k=int(q.get('k', 20)),
                                   narrative=q.get('narrative') or None, min_score=float(q.get('min_score', 0)),
                                   sources=[s for s in q.get('sources', '').split(',') if s] or None,
                                   hn=_bool(q.get('hn', '1')), save=_bool(q.get('save', '1')),
                                   q=q.get('q') or None, fresh=_bool(q.get('fresh', '0')),
                                   sort=q.get('sort') or 'upside')
            elif path == '/review':
                body = engine.review(int(q.get('n', 12)), fresh=_bool(q.get('fresh', '0')))
            elif path == '/cache':
                body = engine.cache_stats()
            elif path == '/cache/clear':
                body = engine.clear_scan_cache()
            elif path == '/narratives':
                body = engine.narratives(asset=q.get('asset', 'all'), hn=_bool(q.get('hn', '1')))
            elif path == '/history':
                body = engine.history(int(q.get('n', 10)))
            elif path == '/sources':
                body = source_list()
            elif path == '/docs':
                body = agent_docs()
            elif path == '/mcp':
                return self._send(405, {'error': 'POST JSON-RPC to /mcp; no SSE stream is offered'})
            elif path == '/whoami':
                try:
                    addr = self._caller()
                except identity.AuthError as e:
                    return self._send(401, {'error': str(e), 'address': None})
                box = identity.box_address()
                body = {'address': addr, 'signed': bool(addr),
                        'owner': bool(addr) and addr == box,
                        'auth': identity.status()}
            elif path == '/auth/local':
                if not self._is_box():
                    return self._send(403, {'error': 'local sign-in is for the box itself '
                                                     '(loopback): connect a browser wallet instead'})
                try:
                    token = identity.local_token()
                except identity.AuthError as e:
                    return self._send(424, {'error': str(e)})
                body = {'token': token, 'address': identity.box_address(), 'owner': True}
            else:
                return self._send(404, {'error': f'no route {u.path}'})
            self._send(200, body)
        except (ValueError, KeyError, TypeError) as e:
            self._send(400, {'error': f'{type(e).__name__}: {e}'})
        except Exception as e:
            self._send(424, {'error': f'{type(e).__name__}: {e}'})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=51320)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--no-warm', action='store_true', help='do not keep the asset=all scan hot in the background')
    a = ap.parse_args()
    if not a.no_warm and os.environ.get('GEMFINDER_WARM', '1') != '0':
        warm()
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
