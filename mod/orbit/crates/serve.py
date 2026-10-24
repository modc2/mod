"""Server for the crates console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

One process answers both halves of the protocol's URL rule, because the mixing
is all client side and the Spotify calls are stateless:

    /crates/*           → the console (prefix kept by the gateway; stripped here)
    /crates/api/{fn}    → the API, as the console calls it — one relative path
                          that resolves the same locally and behind the gateway
    /api/crates/*       → the API (prefix stripped by the gateway, so the
                          protocol routes land at the root)
    …/stream/bandcamp?id=<page url>[&track=<id>]
                        → the one non-JSON route: a Bandcamp track's MP3,
                          proxied with Range passthrough, because bcbits.com
                          sends no CORS header and the deck needs the bytes.
                          SoundCloud's CDN does send one, so the browser
                          fetches those itself.

    /crates/api/mcp     → the MCP server (POST, JSON-RPC 2.0). GET it and you
                          get the tool list instead of an error, because a
                          person following the link is not speaking JSON-RPC.

    python3 serve.py [--port 50790] [--host 0.0.0.0]
"""

import argparse
import importlib.util
import inspect
import json
import os
import shutil
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/crates'
NAME = 'crates'

_mod = None
_mod_error = None
_mcp = None


def api_fns() -> tuple:
    """The functions the public API exposes — ``Mod.API_FNS`` owns the list."""
    mod = module()
    return tuple(getattr(mod, 'API_FNS', ())) if mod else ()


def module():
    """The Mod instance, loaded lazily from mod.py.

    ``mod.py`` is both this module's anchor file and the name of the framework
    package it imports, so our own directory has to come off sys.path before
    loading it — otherwise ``import mod`` finds the anchor and imports itself.
    """
    global _mod, _mod_error
    if _mod is not None or _mod_error is not None:
        return _mod
    shadow = [p for p in sys.path if p in ('', '.', MODULE_DIR)]
    for p in shadow:
        sys.path.remove(p)
    try:
        spec = importlib.util.spec_from_file_location(
            'crates_mod', os.path.join(MODULE_DIR, 'mod.py'))
        anchor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(anchor)
        _mod = anchor.Mod()
    except Exception as e:                      # the console still serves without it
        _mod_error = f'{type(e).__name__}: {e}'
        sys.stderr.write(f'crates: API unavailable — {_mod_error}\n')
    finally:
        sys.path[:0] = shadow
    return _mod


def mcp_module():
    """The MCP server's handlers, loaded the same careful way as mod.py."""
    global _mcp
    if _mcp is None:
        spec = importlib.util.spec_from_file_location(
            'crates_mcp_http', os.path.join(MODULE_DIR, 'mcp.py'))
        _mcp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mcp)
    return _mcp


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _strip(self):
        """Drop the app prefix, serving the bare form as the index.

        The bare ``/crates`` must NOT redirect to ``/crates/``: the gateway 308s
        the directory form back to the bare one, so a redirect here is an
        infinite loop and the published URL never loads. index.html pins
        ``<base href="/crates/">`` — absolute, because at the bare ``/crates`` a
        relative base resolves against the gateway root and css/ and js/ 404
        there; this handler strips the prefix, so the same base works alone.
        """
        if self.path == PREFIX or self.path.startswith(PREFIX + '?'):
            self.path = '/' + self.path[len(PREFIX):]
        elif self.path.startswith(PREFIX + '/'):
            self.path = self.path[len(PREFIX):] or '/'

    # ── mod protocol ─────────────────────────────────────────────────────

    def _route(self) -> str:
        """The function a protocol path names.

        ``/{fn}``, ``/api/{fn}``, ``/mod/crates/{fn}``, and a null
        ``/mod/crates`` that returns info — the same shapes the core Flask
        server answers, plus the ``api/`` form the console fetches.
        """
        path = urlparse(self.path).path.strip('/')
        if path in ('mod/' + NAME, NAME, 'api', 'api/' + NAME):
            return 'info'
        for prefix in ('mod/' + NAME + '/', NAME + '/', 'api/' + NAME + '/', 'api/'):
            if path.startswith(prefix):
                path = path[len(prefix):].strip('/')
                break
        return path or 'info'

    def _json(self, code, payload):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _call(self, fn):
        mod = module()
        if mod is None:
            return self._json(503, {'error': f'module not loaded: {_mod_error}'})
        if fn not in api_fns():
            return self._json(404, {'error': f'no such function {fn!r}',
                                    'fns': list(api_fns())})
        try:
            kwargs = self._args()
        except ValueError as e:
            return self._json(400, {'error': str(e)})
        self._auth_args(getattr(mod, fn), kwargs)
        try:
            return self._json(200, {'result': getattr(mod, fn)(**kwargs)})
        except TypeError as e:
            return self._json(400, {'error': str(e)})
        except Exception as e:
            return self._json(500, {'error': f'{type(e).__name__}: {e}'})

    def _auth_args(self, fn, kwargs) -> None:
        """Lift the caller's credential off the request headers.

        ``Authorization: Bearer <mod token>`` and ``X-Crates-Guest`` are the
        two ways to say who you are, and the playlist functions take them as
        ``token`` and ``guest``. Only functions that actually declare those
        parameters get them — most of this module's API takes ``**_`` and would
        silently swallow a credential it has no business seeing.

        An argument that came in explicitly wins: an agent passing token= in
        the query is being deliberate, and the header is the browser's habit.
        """
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return
        if 'token' in params and not kwargs.get('token'):
            auth = (self.headers.get('Authorization') or '').strip()
            if auth[:7].lower() == 'bearer ':
                auth = auth[7:].strip()
            if auth:
                kwargs['token'] = auth
        if 'guest' in params and not kwargs.get('guest'):
            g = (self.headers.get('X-Crates-Guest') or '').strip()
            if g:
                kwargs['guest'] = g

    def _mcp(self):
        """POST /mcp — the MCP server, over Streamable HTTP.

        Same handlers the REST routes and the console use; mcp.py owns them.
        The Authorization header is passed through so a playlist tool can be
        called with a token instead of an argument.
        """
        mod = module()
        if mod is None:
            return self._json(503, {'error': f'module not loaded: {_mod_error}'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(n) if n else b'{}'
            msg = json.loads(raw or b'{}')
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(200, {'jsonrpc': '2.0', 'id': None,
                                    'error': {'code': -32700,
                                              'message': f'parse error: {e}'}})
        auth = (self.headers.get('Authorization') or '').strip()
        if auth[:7].lower() == 'bearer ':
            auth = auth[7:].strip()
        try:
            out = mcp_module().handle(msg, auth or None)
        except Exception as e:                                  # noqa: BLE001
            return self._json(200, {'jsonrpc': '2.0', 'id': msg.get('id'),
                                    'error': {'code': -32603,
                                              'message': f'{type(e).__name__}: {e}'}})
        if out is None:                       # a notification wants no answer
            self.send_response(202)
            self.send_header('Content-Length', '0')
            return self.end_headers()
        return self._json(200, out)

    def _args(self) -> dict:
        """Keyword arguments, from the query string and then the JSON body."""
        args = {k: v[0] for k, v in
                parse_qs(urlparse(self.path).query).items() if v}
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return args
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw or b'{}')
        except json.JSONDecodeError as e:
            raise ValueError(f'body is not JSON: {e}')
        if body in (None, ''):
            return args
        if not isinstance(body, dict):
            raise ValueError('body must be a JSON object of keyword arguments')
        args.update(body)
        return args

    # ── the stream proxy ─────────────────────────────────────────────────

    def _stream(self):
        """Proxy one platform track's audio to the deck.

        The upstream URL is resolved here, on every request, from the platform
        id — the console never sees or chooses the URL, so this cannot be
        turned into a general-purpose fetch. Range headers pass through, which
        is what lets a browser seek a half-loaded file.
        """
        mod = module()
        if mod is None:
            return self._json(503, {'error': f'module not loaded: {_mod_error}'})
        path = urlparse(self.path).path.rstrip('/')
        source = path.rsplit('/', 1)[-1]
        args = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items() if v}
        if source not in ('bandcamp', 'soundcloud'):
            return self._json(404, {'error': 'stream/bandcamp or stream/soundcloud'})
        where = mod.stream(source=source, id=args.get('id'), track=args.get('track'))
        if where.get('error'):
            return self._json(502, where)
        import requests
        headers = {'User-Agent': 'Mozilla/5.0'}
        if self.headers.get('Range'):
            headers['Range'] = self.headers['Range']
        if where.get('referer'):
            headers['Referer'] = where['referer']
        try:
            up = requests.get(where['url'], headers=headers, stream=True, timeout=30)
        except requests.RequestException as e:
            return self._json(502, {'error': f'upstream failed: {e}'})
        if up.status_code >= 400:
            return self._json(502, {'error': f'upstream answered {up.status_code}'})
        self.send_response(up.status_code)
        for h in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
            if up.headers.get(h):
                self.send_header(h, up.headers[h])
        if not up.headers.get('Content-Type'):
            self.send_header('Content-Type', 'audio/mpeg')
        name = f"{where.get('artists') or ''} - {where.get('name') or 'track'}".strip(' -')
        safe = ''.join(ch if ch.isalnum() or ch in ' -_.' else '_' for ch in name)[:120]
        self.send_header('Content-Disposition', f'inline; filename="{safe}.mp3"')
        self.send_header('X-Crates-Source', source)
        self.end_headers()
        if self.command == 'HEAD':
            return
        try:
            shutil.copyfileobj(up.raw, self.wfile, 64 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            pass                                  # the deck moved on; fine
        finally:
            up.close()

    def _is_stream(self) -> bool:
        return self._route().startswith('stream/')

    def _is_api(self) -> bool:
        """Whether this path is asking for a function rather than a file."""
        path = urlparse(self.path).path.rstrip('/')
        if path in ('', '/'):                   # '/' is the console itself
            return False
        return (path.startswith('/api/') or path == '/api'
                or self._route() in api_fns())

    def _is_mcp(self) -> bool:
        return urlparse(self.path).path.rstrip('/').rsplit('/', 1)[-1] == 'mcp'

    def do_POST(self):
        self._strip()
        if self._is_mcp():
            return self._mcp()
        return self._call(self._route())

    def do_GET(self):
        self._strip()
        clean = urlparse(self.path).path.rstrip('/')
        if clean in ('/health', '/healthz'):
            return self._json(200, {'ok': True, 'module': NAME})
        if self._is_stream():
            return self._stream()
        if self._is_mcp():
            # MCP speaks POST; a GET here is a person looking, so tell them
            # what this endpoint is instead of failing at them.
            return self._call('tools')
        # GET is not the protocol's call verb, but the console fetches with it
        # and a browser pointed at a function should get its answer rather than
        # a directory listing.
        if self._is_api():
            return self._call(self._route())
        return super().do_GET()

    def do_HEAD(self):
        self._strip()
        if self._is_stream():
            return self._stream()
        return super().do_HEAD()

    def end_headers(self):
        # the console is one static bundle; keep browsers from pinning an old build
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, Range, Authorization, X-Crates-Guest')
        self.send_header('Access-Control-Expose-Headers',
                         'Content-Length, Content-Range, X-Crates-Source')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Allow', 'GET, HEAD, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get("PORT", 50790)))
    ap.add_argument('--host', default=os.environ.get('HOST', '0.0.0.0'))
    args = ap.parse_args()

    if not os.path.exists(os.path.join(WEB_DIR, 'index.html')):
        raise SystemExit('crates: web/index.html is missing')

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print('crates serving %s on http://%s:%d%s (api: /api/%s)'
          % (WEB_DIR, args.host, args.port, PREFIX, NAME), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
