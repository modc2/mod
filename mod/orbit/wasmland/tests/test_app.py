"""
The app half: does the console service put the page and the API on one origin?

The console is only correct if `/wasmland/_api/*` behaves exactly like the API
itself — same status, same body, same bytes. The market's refusals are carried
in status codes (402 for unpaid, 401 for unsigned), and the browser venue
fetches artifact bytes it will then hash, so a proxy that normalises a status
or mangles a body breaks verification rather than merely the styling. These
tests run the real server against a stub upstream and check that.
"""
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src' / 'app'))

import server as console  # noqa: E402

RAW = bytes(range(256))  # binary that a text-mode proxy would corrupt


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Upstream(BaseHTTPRequestHandler):
    """Stands in for the API: echoes what it was handed, refuses like a market."""

    protocol_version = 'HTTP/1.1'

    def _send(self, status, body, kind='application/json'):
        self.send_response(status)
        self.send_header('content-type', kind)
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/raw':
            return self._send(200, RAW, 'application/wasm')
        if self.path == '/paid':
            return self._send(402, b'{"detail":"buy it first"}')
        self._send(200, json.dumps({'saw': self.path,
                                    'auth': self.headers.get('authorization')}).encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('content-length') or 0))
        self._send(200, json.dumps({'saw': self.path, 'echo': body.decode()}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture(scope='module')
def app():
    """The real console server, pointed at the stub."""
    api_port, app_port = free_port(), free_port()
    upstream = ThreadingHTTPServer(('127.0.0.1', api_port), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    console.API = f'http://127.0.0.1:{api_port}'
    site = ThreadingHTTPServer(('127.0.0.1', app_port), console.Console)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{app_port}'
    site.shutdown()
    upstream.shutdown()


def get(url, **kw):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, **kw), timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


# ── the page ─────────────────────────────────────────────────────────

def test_console_is_served_at_its_base_path(app):
    status, body, headers = get(f'{app}/wasmland/')
    assert status == 200
    assert b'<base href="/wasmland/">' in body
    assert headers['content-type'].startswith('text/html')


def test_scripts_keep_their_javascript_type(app):
    """A module Worker refuses a script served as text/plain, so the browser
    venue exists only if this header is right."""
    status, _, headers = get(f'{app}/wasmland/app.js')
    assert status == 200
    assert 'javascript' in headers['content-type']


def test_the_bare_port_lands_on_the_console(app):
    """Hitting the app port with no path is the commonest way in."""
    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as caught:  # not followed
        opener.open(f'{app}/')
    assert caught.value.code == 302
    assert caught.value.headers['location'] == '/wasmland/'
    assert get(f'{app}/')[0] == 200  # and following it arrives


def test_assets_cannot_escape_the_app_directory(app):
    """..%2f walks are the reason this is not a bare file server."""
    status, body, _ = get(f'{app}/wasmland/../../mod.py')
    assert status == 200
    assert b'class Mod' not in body


# ── the API, one hop away ────────────────────────────────────────────

def test_api_alias_reaches_the_api_with_the_prefix_stripped(app):
    status, body, _ = get(f'{app}/wasmland/_api/listings?limit=3')
    assert status == 200
    assert json.loads(body)['saw'] == '/listings?limit=3'


def test_authorization_is_forwarded(app):
    """Without this the console is signed in and the API never hears about it."""
    _, body, _ = get(f'{app}/wasmland/_api/auth/me',
                     headers={'authorization': 'token-abc'})
    assert json.loads(body)['auth'] == 'token-abc'


def test_a_refusal_keeps_its_status_and_its_reason(app):
    status, body, _ = get(f'{app}/wasmland/_api/paid')
    assert status == 402
    assert json.loads(body)['detail'] == 'buy it first'


def test_bytes_survive_the_hop_unchanged(app):
    """The browser venue hashes what it fetches; a mangled byte is a false
    dispute."""
    status, body, headers = get(f'{app}/wasmland/_api/raw')
    assert status == 200
    assert body == RAW
    assert headers['content-type'] == 'application/wasm'


def test_a_body_is_posted_through(app):
    _, body, _ = get(f'{app}/wasmland/_api/run', data=b'{"seed":7}',
                     headers={'content-type': 'application/json'})
    assert json.loads(body)['echo'] == '{"seed":7}'


def test_a_down_api_is_named_as_the_thing_that_is_down(app):
    """The console stays up when the API restarts — it should say which half
    failed rather than looking broken itself."""
    was, console.API = console.API, f'http://127.0.0.1:{free_port()}'
    try:
        status, body, _ = get(f'{app}/wasmland/_api/health')
    finally:
        console.API = was
    assert status == 502
    assert 'not answering' in json.loads(body)['detail']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kw):
        return None
