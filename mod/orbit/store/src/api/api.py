#!/usr/bin/env python3
"""
store API — images, public links, and one-time QR grants, over HTTP.

Stdlib http.server, no framework. It imports the same `src/` the CLI does, so
there is one implementation of every rule and the console cannot drift from
`python3 mod.py`.

    python3 api.py                          # 127.0.0.1:50670
    python3 api.py --port 8080
    STORE_SHARE_HOST=0.0.0.0 python3 api.py  # read the note in identity.py

PAGES AND BYTES ARE DIFFERENT DOORS
    GET /p/<id>     a page about a published image — what you hand to a person
    GET /i/<id>     the published bytes. No credential, no expiry, no log.
    GET /v/<code>   a page about a one-time code. Claims NOTHING.
    GET /g/<code>   the bytes behind that code. Serving them BURNS it.
    GET /image?id=  metadata for a row you own, or for published bytes.

    The QR code encodes the page, because someone who scans a code off a
    screen should land somewhere that says what they are holding — that it
    works once, how long it has left, and a button to save it before it stops
    existing. Bytes cannot say any of that.

THE PAGE IS ALSO WHAT MAKES AN ACCIDENTAL BURN HARD
    A claim has to be a GET, because that is what a scanner does, so anything
    that follows a URL used to be able to spend the grant: a chat client
    making a preview, a browser prefetching, a scanner that opens a link
    twice. Now the scannable URL is the page, fetching the page claims
    nothing, and the claim happens when a person presses the button on it.
    HEAD on /g/ never claims either. What remains is that the code is still a
    credential in a URL and anyone the link reaches can press that button,
    which is why the default TTL is a minute: mint it when the person is in
    front of you.

IMAGES ARE SERVED AS INERT DATA
    Every image response carries `Content-Security-Policy: default-src 'none'`,
    `X-Content-Type-Options: nosniff` and an inline disposition, and the
    Content-Type is the sniffed type from upload rather than anything a caller
    said. Uploads that are not really images were refused at the door, and SVG
    is refused outright; these headers are the second line for the day one of
    those checks is wrong.
"""
import argparse
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

MODULE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MODULE_DIR))

from src import (docs, docs_page, grants, identity, library, links, mcp,  # noqa: E402
                 qr)
from src.library import StoreError  # noqa: E402

PORT = int(os.environ.get('STORE_SHARE_PORT', 50670))
HOST = os.environ.get('STORE_SHARE_HOST', '127.0.0.1')
APP_PORT = links.APP_PORT
BASE = links.BASE

# The console proxies the API under this prefix, so the same route table
# answers whether it is reached directly or through the app's own origin.
API_PREFIXES = ('/store/_api', '/store/api', '/_api')

# The share pages are one HTML file, served by whichever half of this module a
# caller reached. It is the app's file: the console serves it from disk on its
# own port, and this is the same bytes for anyone who only started the API.
VIEW_PAGE = MODULE_DIR / 'src' / 'app' / 'view.html'

IMAGE_HEADERS = {
    'Content-Security-Policy': "default-src 'none'; sandbox",
    'X-Content-Type-Options': 'nosniff',
    'Content-Disposition': 'inline',
}


def _int(params, name, default):
    try:
        return int(params.get(name, [default])[0])
    except (TypeError, ValueError):
        return default


def _str(params, name, default=''):
    value = params.get(name, [default])[0]
    return value if value not in (None, '') else default


def _flag(params, name, default=False):
    raw = _str(params, name, '')
    if raw == '':
        return default
    return raw.lower() in ('1', 'true', 'yes', 'on')


class Handler(BaseHTTPRequestHandler):
    server_version = 'store-api'
    protocol_version = 'HTTP/1.1'

    # ── plumbing ─────────────────────────────────────────────────────

    def log_message(self, fmt, *args):
        if os.environ.get('STORE_SHARE_QUIET'):
            return
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _send(self, status, body=None, content_type='application/json',
              extra=None):
        payload = b''
        if body is not None:
            payload = body if isinstance(body, bytes) else \
                json.dumps(body, default=str).encode('utf-8')
        self.send_response(status)
        if body is not None:
            self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers',
                         'Authorization,Content-Type,Accept,'
                         'Mcp-Session-Id,MCP-Protocol-Version')
        self.send_header('Access-Control-Expose-Headers',
                         'Mcp-Session-Id,MCP-Protocol-Version')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS')
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if payload and self.command != 'HEAD':
            self.wfile.write(payload)

    def _error(self, status, message):
        self._send(status, {'error': message, 'status': status})

    def _body(self):
        """Read the request body, refusing anything larger than an image."""
        if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            raise StoreError('send a Content-Length, not a chunked body', 411)
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise StoreError('bad Content-Length', 400)
        if length < 0:
            raise StoreError('bad Content-Length', 400)
        # Refused on the header, before a byte is read — an oversized upload
        # should cost the box nothing.
        if length > library.MAX_BYTES:
            raise StoreError(
                f'{length} bytes exceeds the {library.MAX_BYTES} byte limit',
                413)
        return self.rfile.read(length) if length else b''

    def _json_body(self):
        raw = self._body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode('utf-8'))
        except Exception:
            raise StoreError('body was not JSON', 400)
        return data if isinstance(data, dict) else {}

    def _caller(self):
        return identity.whoami(self.headers.get('Authorization'),
                               allow_local=identity.is_loopback(
                                   self.server.server_address[0]))

    # ── routing ──────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self._send(204, None)

    def do_GET(self):
        self._handle('GET')

    def do_HEAD(self):
        # Deliberately not routed into the claim path: a HEAD is something a
        # link checker does, and it must not spend a one-time grant.
        self._handle('HEAD')

    def do_POST(self):
        self._handle('POST')

    def do_DELETE(self):
        self._handle('DELETE')

    def _handle(self, method):
        parsed = urlparse(self.path)
        route = parsed.path
        for prefix in API_PREFIXES:
            if route.startswith(prefix):
                route = route[len(prefix):] or '/'
                break
        route = route.rstrip('/') or '/'
        params = parse_qs(parsed.query)
        try:
            self._route(method, route, params)
        except StoreError as error:
            self._error(error.status, str(error))
        except BrokenPipeError:
            pass
        except Exception as error:
            traceback.print_exc()
            self._error(500, f'{type(error).__name__}: {error}')

    def _route(self, method, route, params):
        segments = [unquote(s) for s in route.strip('/').split('/') if s]

        # ── the share paths, all open ────────────────────────────────
        if segments[:1] == ['g'] and len(segments) == 2:
            return self._claim(method, segments[1])
        if segments[:1] == ['g'] and segments[2:] == ['qr']:
            return self._grant_qr(segments[1], params)
        if segments[:1] == ['i'] and len(segments) == 2:
            return self._public_bytes(segments[1])
        # The one read path that asks who you are. Without it the owner of a
        # private picture is the only person on earth who cannot look at it,
        # which made the console show empty squares for everything unpublished.
        if segments[:1] == ['b'] and len(segments) == 2:
            return self._own_bytes(segments[1])
        # The pages. Deliberately incurious: the same HTML for any code and any
        # id, whether either exists, so the page itself cannot be used to probe
        # for one. What is behind it is resolved by the page, from the same
        # endpoints anyone else would have to ask.
        if segments[:1] in (['v'], ['p']) and len(segments) == 2:
            return self._page(method)

        if method in ('GET', 'HEAD'):
            if route == '/':
                return self._send(200, self._info())
            if route == '/health':
                return self._send(200, {'ok': True, **library.stats()})
            if route == '/docs':
                return self._docs(params)
            if route == '/mcp/schema':
                return self._send(200, mcp.schema())
            if route == '/mcp':
                # Streamable HTTP allows a GET that opens a server-initiated
                # stream. This server never initiates anything, and the spec's
                # answer for that is 405 rather than an idle open socket.
                return self._error(405, 'POST JSON-RPC 2.0 messages here. GET '
                                        '/mcp/schema for the tool list.')
            if route == '/public':
                return self._send(200, {'images': library.public_listing(
                    _int(params, 'limit', 100), _int(params, 'offset', 0))})
            if route == '/me':
                return self._send(200, {'address': self._caller(),
                                        'local_owner': identity.local_owner()})
            if route == '/images':
                owner = self._caller()
                return self._send(200, {'owner': owner, 'images':
                                        library.listing(
                                            owner,
                                            _int(params, 'limit', 100),
                                            _int(params, 'offset', 0),
                                            _flag(params, 'public'))})
            if route == '/image':
                return self._image_info(params)
            if route == '/grants':
                owner = self._caller()
                return self._send(200, {'owner': owner, 'grants':
                                        grants.listing(
                                            owner,
                                            _flag(params, 'all'),
                                            _int(params, 'limit', 100))})
            if route == '/grant':
                grant = grants.peek(_str(params, 'code'))
                if not grant:
                    raise StoreError('no such grant', 404)
                # Peeking is open — it reveals only whether a code someone
                # already holds is still good — but never the image behind it.
                grant.pop('image', None)
                grant.pop('owner', None)
                return self._send(200, grant)
            if route == '/qr':
                return self._qr(_str(params, 'text'), params)

        if method == 'POST':
            if route == '/upload':
                return self._upload(params)
            if route == '/publish':
                body = self._json_body()
                return self._send(200, library.publish(
                    body.get('id', _str(params, 'id')), self._caller(),
                    bool(body.get('public', True))))
            if route == '/unpublish':
                body = self._json_body()
                return self._send(200, library.publish(
                    body.get('id', _str(params, 'id')), self._caller(), False))
            if route == '/grant':
                return self._mint(params)
            if route == '/sweep':
                self._caller()
                return self._send(200, grants.sweep())
            if route == '/mcp':
                return self._mcp()

        if method == 'DELETE':
            if route == '/mcp':
                # A client ending its session. There is no session state to
                # drop, but answering 405 here makes well-behaved clients log
                # a shutdown failure on every disconnect.
                return self._send(204, None)
            if route == '/image':
                return self._send(200, library.remove(
                    _str(params, 'id'), self._caller()))
            if route == '/grant':
                return self._send(200, grants.revoke(
                    _str(params, 'code'), self._caller()))

        raise StoreError(f'no route for {method} {route}', 404)

    # ── handlers ─────────────────────────────────────────────────────

    def _info(self):
        return {
            'name': 'store',
            'what': 'image sharing — public links, and one-time QR grants '
                    'that last N seconds',
            'api': f'http://{HOST}:{PORT}',
            'app': f'http://127.0.0.1:{APP_PORT}/store',
            'share_base': BASE,
            'state': str(library.HOME),
            'max_bytes': library.MAX_BYTES,
            'formats': ['image/png', 'image/jpeg', 'image/gif', 'image/webp',
                        'image/bmp'],
            'ttl': {'min': grants.MIN_TTL, 'max': grants.MAX_TTL,
                    'default': grants.DEFAULT_TTL},
            'qr': qr.available(),
            'stats': library.stats(),
            'docs': f'{BASE}/docs',
            'mcp': {'http': f'{BASE}/mcp', 'schema': f'{BASE}/mcp/schema',
                    'tools': len(mcp.TOOLS)},
            'share_paths': {'public_page': '/p/{id}', 'public_bytes': '/i/{id}',
                            'grant_page': '/v/{code} — claims nothing',
                            'grant_bytes': '/g/{code} — burns the code'},
            'note': 'this is orbit/store and it is NOT m.mod("store") — that '
                    'name resolves to core/store. See the README.',
        }

    def _docs(self, params):
        """
        The manual — as a page for a person, as JSON for a program.

        Both come off the same dictionary in src/docs.py. The negotiation is on
        Accept rather than on a path or a suffix, because the caller that wants
        JSON here is a program that already says so on every request, and the
        one that wants the page typed a URL into a browser and should not have
        to know a second one. `?format=` overrides for anyone testing.
        """
        section = _str(params, 'section')
        wanted = _str(params, 'format').lower()
        accept = (self.headers.get('Accept') or '').lower()
        if not wanted:
            wanted = 'html' if ('text/html' in accept
                                and 'application/json' not in accept) else 'json'
        if wanted == 'html':
            return self._send(200, docs_page.render(section).encode('utf-8'),
                              'text/html; charset=utf-8',
                              {'Cache-Control': 'no-cache'})
        return self._send(200, docs.document(section))

    def _page(self, method):
        """The share pages — one file, no state, nothing spent by asking."""
        if method not in ('GET', 'HEAD'):
            raise StoreError('these pages are read with GET', 405)
        if not VIEW_PAGE.exists():
            raise StoreError('the share page is missing from this install', 500)
        return self._send(200, VIEW_PAGE.read_bytes(), 'text/html; charset=utf-8',
                          {'Cache-Control': 'no-cache',
                           'Referrer-Policy': 'no-referrer'})

    def _mcp(self):
        raw = self._body()
        try:
            body = json.loads(raw.decode('utf-8')) if raw else {}
        except Exception:
            return self._send(200, {'jsonrpc': '2.0', 'id': None, 'error': {
                'code': -32700, 'message': 'parse error: body was not JSON'}})

        # The owner is recovered here, not in the MCP layer: an agent reaching
        # this over the network is exactly as authenticated as a browser is,
        # and gets exactly the same pictures.
        response = mcp.handle_message(body, owner=self._caller())

        # The session id is echoed rather than enforced. This server keeps no
        # per-session state — every request carries its own identity in the
        # Authorization header — so a client that wants to track a session can,
        # and one that does not is not made to invent one.
        headers = {'MCP-Protocol-Version': mcp.DEFAULT_PROTOCOL_VERSION}
        given = self.headers.get('Mcp-Session-Id')
        if given:
            headers['Mcp-Session-Id'] = given

        if response is None:
            return self._send(202, None, extra=headers)   # notifications only

        # Streamable HTTP: a client may accept only an event stream. One
        # message, one event, and the stream ends — there is nothing here that
        # streams, and pretending otherwise would leave the socket open.
        accept = (self.headers.get('Accept') or '').lower()
        if 'text/event-stream' in accept and 'application/json' not in accept:
            payload = ('event: message\ndata: '
                       + json.dumps(response, default=str) + '\n\n')
            return self._send(200, payload.encode('utf-8'),
                              'text/event-stream',
                              {**headers, 'Cache-Control': 'no-store'})
        return self._send(200, response, extra=headers)

    def _upload(self, params):
        owner = self._caller()
        data = self._body()
        record = library.put(data, name=_str(params, 'name'), owner=owner,
                             public=_flag(params, 'public'))
        return self._send(201, links.decorate_image(record))

    def _image_info(self, params):
        image_id = _str(params, 'id')
        if not image_id:
            raise StoreError('which image', 400)
        try:
            owner = self._caller()
            record = library.record(image_id, owner)
        except StoreError:
            record = None
        if record is None:
            record = library.public_record(image_id)
        if record is None:
            raise StoreError('no such image', 404)
        return self._send(200, links.decorate_image(record))

    def _public_bytes(self, image_id):
        record = library.public_record(image_id)
        if record is None:
            # Same answer for "never existed" and "exists but private", so the
            # endpoint cannot be used to probe for private images.
            raise StoreError('no such published image', 404)
        data = library.read(image_id)
        return self._send(200, data, record['mime'],
                          {**IMAGE_HEADERS, 'Cache-Control': 'public, max-age=3600'})

    def _own_bytes(self, image_id):
        record = library.record(image_id, self._caller())
        if record is None:
            # Not "you may not" — the same 404 a stranger's id gets, so this
            # path cannot enumerate what other people are holding either.
            raise StoreError('no such image of yours', 404)
        data = library.read(image_id)
        return self._send(200, data, record['mime'],
                          {**IMAGE_HEADERS,
                           'Cache-Control': 'private, no-store'})

    def _mint(self, params):
        owner = self._caller()
        body = self._json_body()
        image_id = body.get('id') or _str(params, 'id')
        ttl = body.get('ttl_seconds', body.get('ttl'))
        if ttl is None:
            ttl = _int(params, 'ttl_seconds', grants.DEFAULT_TTL)
        out = links.decorate_grant(grants.create(image_id, owner, ttl))
        if qr.available():
            # The QR encodes the PAGE, not the bytes: a scan should land
            # somewhere that explains the code, and a preview bot that follows
            # the link should not be able to spend it.
            out['qr_svg'] = qr.svg(out['page_url'])
        return self._send(201, out)

    def _claim(self, method, code):
        if method == 'HEAD':
            # Never spends the grant. Say whether the code is live and stop.
            grant = grants.peek(code)
            if not grant:
                raise StoreError('no such grant', 404)
            return self._send(200 if grant['live'] else 410, None)
        if method != 'GET':
            raise StoreError('claim a grant with GET', 405)
        grant = grants.claim(code, claimed_by=self.address_string())
        record = library.record(grant['image'], grant['owner'])
        if record is None:
            raise StoreError('the image behind this link is gone', 410)
        data = library.read(grant['image'])
        return self._send(200, data, record['mime'],
                          {**IMAGE_HEADERS,
                           # It was good for one fetch; nothing may keep it.
                           'Cache-Control': 'no-store, no-cache, must-revalidate',
                           'Pragma': 'no-cache'})

    def _grant_qr(self, code, params):
        # Renders a picture of the link. Does NOT claim it — otherwise showing
        # someone the QR code would spend the grant before they scanned it.
        if not grants.peek(code):
            raise StoreError('no such grant', 404)
        return self._qr(links.grant_page(code), params)

    def _qr(self, text, params):
        if not text:
            raise StoreError('nothing to encode', 400)
        if not qr.available():
            raise StoreError(
                'no QR encoder on this box — pip install segno. The link '
                'itself still works.', 501)
        body = qr.svg(text, scale=_int(params, 'scale', 6),
                      border=_int(params, 'border', 2))
        return self._send(200, body.encode('utf-8'), 'image/svg+xml',
                          {'Cache-Control': 'no-store'})


def serve(host=HOST, port=PORT):
    server = ThreadingHTTPServer((host, port), Handler)
    where = 'loopback only' if identity.is_loopback(host) else \
        'REACHABLE — every caller must present a signed token'
    print(f'store api  http://{host}:{port}  ({where})')
    print(f'state      {library.HOME}')
    print(f'shares as  {BASE}')
    if not qr.available():
        print('note       segno is not installed — links work, pictures of '
              'them do not')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='store API')
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    serve(args.host, args.port)
