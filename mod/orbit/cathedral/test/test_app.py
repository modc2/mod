"""Tests for the console server (app/serve.py).

Offline: the "upstream" is a stub HTTP server on localhost that records what it
was asked, so the BYOK invariant — this hop forwards the caller's key and
invents nothing — is checked rather than asserted in a comment.
"""
import importlib.util
import json
import re
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SERVE_PY = Path(__file__).resolve().parent.parent / 'app' / 'serve.py'
spec = importlib.util.spec_from_file_location('cathedral_app_serve', SERVE_PY)
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Stub(BaseHTTPRequestHandler):
    """Stands in for api/api.py: echoes back what it received."""
    seen = []

    def log_message(self, *a):
        pass

    def _answer(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        Stub.seen.append({
            'method': self.command,
            'path': self.path,
            'auth': self.headers.get('Authorization'),
            'content_type': self.headers.get('Content-Type'),
            'cookie': self.headers.get('Cookie'),
            'body': body.decode() or None,
        })
        if self.path.startswith('/boom'):
            payload = json.dumps({'detail': {'error': 'missing credentials'}}).encode()
            self.send_response(401)
        else:
            payload = json.dumps({'ok': True, 'saw': self.path}).encode()
            self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_DELETE = _answer


@pytest.fixture
def console(monkeypatch):
    """The console server, wired to a stub upstream. Yields its base URL."""
    Stub.seen = []
    up = ThreadingHTTPServer(('127.0.0.1', free_port()), Stub)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setattr(serve, 'UPSTREAM', f'http://127.0.0.1:{up.server_port}')

    app = ThreadingHTTPServer(('127.0.0.1', free_port()), serve.Handler)
    threading.Thread(target=app.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{app.server_port}'
    finally:
        app.shutdown()
        up.shutdown()


def get(url, headers=None, method='GET', data=None):
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# ── the app half of the route ────────────────────────────────────────────

def test_serves_the_console_under_the_gateway_prefix(console):
    status, body = get(console + '/cathedral')
    assert status == 200
    assert '<title>cathedral' in body


def test_bare_prefix_does_not_redirect(console):
    """The gateway 308s /cathedral/ back to /cathedral — a redirect here loops."""
    req = urllib.request.Request(console + '/cathedral')

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a):
            raise AssertionError('the console must not redirect the bare prefix')

    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(req, timeout=10) as r:
        assert r.status == 200


def test_serves_the_bare_port_too(console):
    assert get(console + '/')[0] == 200
    assert get(console + '/app.js')[0] == 200
    assert get(console + '/cathedral/app.css')[0] == 200


def test_assets_resolve_from_the_published_bare_prefix(console):
    """The published link is `{host}/cathedral`, with no trailing slash.

    A relative <base> resolves that against the SITE ROOT, so the browser asked
    the gateway for /app.css and /app.js — which belong to no module — and the
    console came up unstyled and inert with no error anywhere. The base must be
    pinned to the mount prefix, and that prefix must serve the assets.
    """
    body = get(console + '/cathedral')[1]
    base = re.search(r'<base\s+href="([^"]+)"', body)
    assert base, 'the console needs a <base> to resolve its assets'
    assert base.group(1) == '/cathedral/', (
        'a relative base breaks the bare /cathedral link on the gateway')
    for asset in re.findall(r'(?:src|href)="((?!http|data:|/)[^"]+\.(?:js|css))"', body):
        assert get(console + '/cathedral/' + asset)[0] == 200, asset


def test_health(console):
    status, body = get(console + '/health')
    assert status == 200
    assert json.loads(body)['module'] == 'cathedral'


# ── the /_api alias ──────────────────────────────────────────────────────

def test_api_alias_forwards_the_callers_key(console):
    status, body = get(console + '/cathedral/_api/credits',
                       headers={'Authorization': 'Bearer cat_sk_caller'})
    assert status == 200
    assert json.loads(body)['saw'] == '/credits'
    assert Stub.seen[-1]['auth'] == 'Bearer cat_sk_caller'


def test_api_alias_invents_no_credential(console):
    """No key in, no key out — the console never supplies one of its own."""
    get(console + '/cathedral/_api/credits')
    assert Stub.seen[-1]['auth'] is None


def test_cookies_do_not_cross_the_hop(console):
    """Only the allowlisted headers travel; ambient browser state stays put."""
    get(console + '/cathedral/_api/credits', headers={'Cookie': 'session=someone-elses'})
    assert Stub.seen[-1]['cookie'] is None


def test_api_alias_forwards_method_and_body(console):
    payload = json.dumps({'image': 'python:3.12-slim', 'confirm': True}).encode()
    status, _ = get(console + '/cathedral/_api/run', method='POST', data=payload,
                    headers={'Content-Type': 'application/json',
                             'Authorization': 'Bearer cat_sk_caller'})
    assert status == 200
    call = Stub.seen[-1]
    assert (call['method'], call['path']) == ('POST', '/run')
    assert json.loads(call['body'])['confirm'] is True


def test_delete_reaches_the_upstream(console):
    assert get(console + '/cathedral/_api/workers/wrk_1', method='DELETE')[0] == 200
    assert (Stub.seen[-1]['method'], Stub.seen[-1]['path']) == ('DELETE', '/workers/wrk_1')


def test_upstream_status_passes_through(console):
    """A 401/402/425 is the answer, not an error to be flattened into 500."""
    status, body = get(console + '/cathedral/_api/boom')
    assert status == 401
    assert json.loads(body)['detail']['error'] == 'missing credentials'


def test_query_string_survives(console):
    get(console + '/cathedral/_api/workers?limit=5')
    assert Stub.seen[-1]['path'] == '/workers?limit=5'


def test_unreachable_upstream_is_a_502_with_a_reason(console, monkeypatch):
    monkeypatch.setattr(serve, 'UPSTREAM', f'http://127.0.0.1:{free_port()}')
    status, body = get(console + '/cathedral/_api/credits')
    assert status == 502
    assert json.loads(body)['error'] == 'cathedral api unreachable'


def test_post_off_the_alias_is_not_a_directory_listing(console):
    status, body = get(console + '/cathedral/anything', method='POST', data=b'{}')
    assert status == 404
    assert '_api' in json.loads(body)['error']


# ── the bundle hangs together ────────────────────────────────────────────

APP = SERVE_PY.parent


def test_every_id_the_script_reaches_for_exists_in_the_markup():
    """A renamed id fails silently in a browser — `$('gone')` is just null.

    Cheaper to catch here than by noticing a dead button in the console.
    """
    import re
    html = (APP / 'index.html').read_text()
    js = (APP / 'app.js').read_text()
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    used = set(re.findall(r"\$\('([^']+)'\)", js))
    assert used - ids == set()


def test_the_console_never_authorizes_a_spend_by_itself():
    """No box ticked, no confirmation sent — the payer says yes, not the page."""
    js = (APP / 'app.js').read_text()
    assert 'return box ? box.checked : false;' in js
