"""Server for the musica console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

One process answers both halves of the protocol's URL rule, because the mixing
is all client side and the Spotify calls are stateless:

    /musica/*           → the console (prefix kept by the gateway; stripped here)
    /musica/api/{fn}    → the API, as the console calls it — one relative path
                          that resolves the same locally and behind the gateway
    /api/musica/*       → the API (prefix stripped by the gateway, so the
                          protocol routes land at the root)
    …/stream/<source>?id=…
                        → the one non-JSON route: a track's audio, proxied
                          with Range passthrough. Bandcamp's bcbits.com and
                          YouTube's googlevideo send no CORS header at all, so
                          the deck cannot fetch those itself; SoundCloud's CDN
                          and archive.org do, and the browser fetches theirs
                          direct with this left as the fallback.
    /mcp                → the MCP server (mcp.py) over Streamable HTTP: POST
                          one JSON-RPC message, get one back. GET lists the
                          tools, which is not part of the protocol but is what
                          a person pointing a browser at it wants.

    python3 serve.py [--port 50780] [--host 0.0.0.0]
"""

import argparse
import importlib.util
import json
import os
import shutil
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/musica'
NAME = 'musica'

_mod = None
_mod_error = None
_mcp = None

# Every source whose bytes this process will relay. Two of them have to be
# proxied (no CORS header upstream); the other two are here so a browser that
# fails a direct fetch has somewhere to fall back to.
STREAM_SOURCES = ('bandcamp', 'youtube', 'soundcloud', 'archive')


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
            'musica_mod', os.path.join(MODULE_DIR, 'mod.py'))
        anchor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(anchor)
        _mod = anchor.Mod()
    except Exception as e:                      # the console still serves without it
        _mod_error = f'{type(e).__name__}: {e}'
        sys.stderr.write(f'musica: API unavailable — {_mod_error}\n')
    finally:
        sys.path[:0] = shadow
    return _mod


def mcp():
    """The MCP server module, sharing this process's Mod instance."""
    global _mcp
    if _mcp is None:
        spec = importlib.util.spec_from_file_location(
            'musica_mcp', os.path.join(MODULE_DIR, 'mcp.py'))
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        mod = module()
        if mod is not None:
            server.bind(mod)
        _mcp = server
    return _mcp


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _strip(self):
        """Drop the app prefix, serving the bare form as the index.

        The bare ``/musica`` must NOT redirect to ``/musica/``: the gateway 308s
        the directory form back to the bare one, so a redirect here is an
        infinite loop and the published URL never loads. index.html pins
        ``<base href="/musica/">`` — absolute, because at the bare ``/musica`` a
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

        ``/{fn}``, ``/api/{fn}``, ``/mod/musica/{fn}``, and a null
        ``/mod/musica`` that returns info — the same shapes the core Flask
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

    def _call(self, fn, kwargs=None):
        mod = module()
        if mod is None:
            return self._json(503, {'error': f'module not loaded: {_mod_error}'})
        if fn not in api_fns():
            return self._json(404, {'error': f'no such function {fn!r}',
                                    'fns': list(api_fns())})
        try:
            kwargs = self._args() if kwargs is None else kwargs
        except ValueError as e:
            return self._json(400, {'error': str(e)})
        try:
            return self._json(200, {'result': getattr(mod, fn)(**kwargs)})
        except TypeError as e:
            return self._json(400, {'error': str(e)})
        except Exception as e:
            return self._json(500, {'error': f'{type(e).__name__}: {e}'})

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
        if source not in STREAM_SOURCES:
            return self._json(404, {'error': 'stream/ takes one of '
                                             + ', '.join(STREAM_SOURCES)})
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
        # The extension has to match the bytes: YouTube's best audio is m4a,
        # and a browser told .mp3 will still decode it but a save will lie.
        ctype = (up.headers.get('Content-Type') or '').lower()
        ext = ('m4a' if 'mp4' in ctype else 'webm' if 'webm' in ctype
               else 'ogg' if 'ogg' in ctype else 'flac' if 'flac' in ctype else 'mp3')
        self.send_header('Content-Disposition', f'inline; filename="{safe}.{ext}"')
        self.send_header('X-Musica-Source', source)
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
        return urlparse(self.path).path.rstrip('/') in ('/mcp', '/api/mcp')

    def _mcp(self):
        """One JSON-RPC message in, one out — MCP's Streamable HTTP transport.

        The gateway strips ``/api/musica``, so the module's own ``mcp``
        function lands on this same path: a body that is not JSON-RPC (no
        ``method``) is that call instead, which keeps ``/api/musica/mcp?name=…``
        working for anything that does not speak the protocol.

        Notifications have no response, and the protocol wants 202 for those
        rather than an empty 200 body.
        """
        if module() is None:
            return self._json(503, {'error': f'module not loaded: {_mod_error}'})
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        try:
            body = json.loads(raw or b'null')
        except json.JSONDecodeError as e:
            return self._json(200, {'jsonrpc': '2.0', 'id': None,
                                    'error': {'code': -32700,
                                              'message': f'parse error: {e}'}})
        if body is None or (isinstance(body, dict) and 'method' not in body):
            args = {k: v[0] for k, v in
                    parse_qs(urlparse(self.path).query).items() if v}
            args.update(body or {})
            return self._call('mcp', args)
        server = mcp()
        if isinstance(body, list):              # a batch, answered as a batch
            out = [r for r in (server.handle(x) for x in body) if r is not None]
            return self._json(200, out) if out else self._json(202, {})
        resp = server.handle(body)
        if resp is None:
            self.send_response(202)
            self.send_header('Content-Length', '0')
            return self.end_headers()
        return self._json(200, resp)

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
        if self._is_mcp() and not urlparse(self.path).query:
            # Not a transport GET (no SSE here) — the tool list, because that
            # is the useful answer to a browser pointed at /mcp. With a query
            # string it is the module's own mcp() function being called, and
            # that falls through to _call below.
            return self._json(200, {'server': 'musica', 'transport': 'POST /mcp',
                                    'protocol': 'JSON-RPC 2.0 (MCP)',
                                    'tools': mcp().tool_list()})
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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Range')
        self.send_header('Access-Control-Expose-Headers',
                         'Content-Length, Content-Range, X-Musica-Source')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Allow', 'GET, HEAD, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get("PORT", 50780)))
    ap.add_argument('--host', default=os.environ.get('HOST', '0.0.0.0'))
    args = ap.parse_args()

    if not os.path.exists(os.path.join(WEB_DIR, 'index.html')):
        raise SystemExit('musica: web/index.html is missing')

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print('musica serving %s on http://%s:%d%s (api: /api/%s)'
          % (WEB_DIR, args.host, args.port, PREFIX, NAME), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
