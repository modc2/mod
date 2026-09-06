#!/usr/bin/env python3
"""plinyville app — the browser side: the repo gallery and the plinyworld exhibit.

Runs on its own port and holds no data of its own. Everything under /api/* is
proxied to the API server, so the page talks to exactly one origin whether it is
opened at localhost:50593 or at modc2.com/plinyville, and the gateway needs no
CORS story.

    python3 app.py [--port 50593]

Routes:

    GET /                              the repo gallery
    GET /m/<repo>/run/<path>           one repo's own app, running, sandboxed
    GET /plinyworld                    the DEFANGED clipboard-hijack exhibit
    GET /plinyworld/triggers.defanged.js   the script that actually runs
    GET /plinyworld/payload            the preserved upstream payload, as text/plain
    GET /api/*                         proxied to the API server
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

from fonts import FONT_CSS  # noqa: E402
from plinyville import PLINYWORLD_DIR, Ville  # noqa: E402

PORT = int(os.environ.get('PLINYVILLE_APP_PORT', 50593))
API_URL = os.environ.get('PLINYVILLE_API_URL', 'http://127.0.0.1:'
                         + os.environ.get('PLINYVILLE_API_PORT', '50592'))
BASE = os.environ.get('PLINYVILLE_BASE_PATH', '/pliny')
STATE = os.environ.get('PLINYVILLE_STATE') or None
# How long the app will wait on the agent behind /api/chat before giving up.
CHAT_TIMEOUT = int(os.environ.get('PLINYVILLE_CHAT_TIMEOUT', 300)) + 30


# The dir and CLI name moved to `pliny`; the routes, links and store bundles
# still say `plinyville`. Both mount points answer, so old links keep working.
_MOUNTS = tuple(dict.fromkeys([BASE, '/plinyville', '/pliny']))


def _norm(path: str) -> str:
    """Strip the gateway prefix; the app is mounted at /pliny upstream."""
    for pre in _MOUNTS:
        if path == pre:
            return '/'
        if path.startswith(pre + '/'):
            return path[len(pre):] or '/'
    return path or '/'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 302 out of the run server means "the entry is over here", and the
    browser has to see it: follow it inside urllib and every relative path in
    the page would resolve against the wrong directory."""

    def redirect_request(self, *a, **k):
        return None


_RUN_OPENER = urllib.request.build_opener(_NoRedirect)
# Headers a run response carries that actually matter downstream — the sandbox
# above all. Dropping them here would quietly un-sandbox the pages.
_RUN_HEADERS = ('Content-Security-Policy', 'X-Content-Type-Options',
                'Access-Control-Allow-Origin', 'Cache-Control', 'Content-Disposition')


def _handler():
    ville = Ville(STATE)

    class H(BaseHTTPRequestHandler):
        server_version = 'pliny-app'

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype='text/html; charset=utf-8', headers=None):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, default=str), 'application/json')

        def _asset(self, fname, ctype):
            fp = os.path.join(PLINYWORLD_DIR, fname)
            if not os.path.isfile(fp):
                return self._json(404, {'error': 'not found'})
            with open(fp, 'rb') as f:
                return self._send(200, f.read(), ctype)

        # ── /api/* → the API server ─────────────────────────────────────────

        def _proxy(self, path, body=None):
            url = API_URL.rstrip('/') + path
            req = urllib.request.Request(
                url, data=body,
                method='POST' if body is not None else 'GET',
                headers={'Content-Type': 'application/json'} if body is not None else {})
            # The agent takes as long as it takes to read a repo; the rest of
            # the api answers in milliseconds.
            slow = path.split('?')[0].rstrip('/').endswith(('/chat', '/ask'))
            try:
                with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT if slow else 60) as r:
                    return self._send(r.status, r.read(),
                                      r.headers.get('Content-Type', 'application/json'))
            except urllib.error.HTTPError as e:
                return self._send(e.code, e.read(),
                                  e.headers.get('Content-Type', 'application/json'))
            except urllib.error.URLError as e:
                return self._json(502, {'error': f'api unreachable at {API_URL}: {e.reason}'})

        def _proxy_stream(self, path, body):
            """Server-sent events, forwarded line by line.

            `_proxy` reads the whole response before it writes a byte, which is
            exactly wrong for a chat: the point of the stream is that the tool
            calls arrive while the agent is still working. Nothing is buffered
            here, and the reader hanging up just ends the loop."""
            url = API_URL.rstrip('/') + path
            req = urllib.request.Request(
                url, data=body or b'{}', method='POST',
                headers={'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
            try:
                r = urllib.request.urlopen(req, timeout=CHAT_TIMEOUT)
            except urllib.error.HTTPError as e:
                return self._send(e.code, e.read(),
                                  e.headers.get('Content-Type', 'application/json'))
            except urllib.error.URLError as e:
                return self._json(502, {'error': f'api unreachable at {API_URL}: '
                                                 f'{e.reason}'})
            self.send_response(200)
            for k, v in (('Content-Type', 'text/event-stream; charset=utf-8'),
                         ('Cache-Control', 'no-cache, no-transform'),
                         ('Connection', 'close'), ('X-Accel-Buffering', 'no')):
                self.send_header(k, v)
            self.end_headers()
            try:
                for line in r:
                    self.wfile.write(line)
                    if line in (b'\n', b'\r\n'):
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                r.close()

        def _prefix(self):
            """Whatever the gateway left on the front of the path ('/pliny'),
            so a Location the api wrote in its own space can be handed back in
            the browser's."""
            raw = urllib.parse.urlparse(self.path).path
            n = _norm(raw)
            return raw[:len(raw) - len(n)] if raw.endswith(n) else ''

        def _proxy_run(self, path, query):
            """A repo's own app, streamed through from the api with its sandbox
            intact. Redirects are forwarded, not followed."""
            url = API_URL.rstrip('/') + path + ('?' + query if query else '')
            try:
                with _RUN_OPENER.open(urllib.request.Request(url), timeout=60) as r:
                    return self._send(r.status, r.read(),
                                      r.headers.get('Content-Type',
                                                    'application/octet-stream'),
                                      self._run_headers(r.headers))
            except urllib.error.HTTPError as e:
                h = self._run_headers(e.headers)
                if e.headers.get('Location'):
                    h['Location'] = self._prefix() + e.headers['Location']
                return self._send(e.code, e.read(),
                                  e.headers.get('Content-Type', 'application/json'), h)
            except urllib.error.URLError as e:
                return self._json(502, {'error': f'api unreachable at {API_URL}: '
                                                 f'{e.reason}'})

        @staticmethod
        def _run_headers(headers):
            return {k: headers[k] for k in _RUN_HEADERS if headers.get(k)}

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            p = _norm(u.path)
            try:
                if p in ('/', '/index.html'):
                    return self._send(200, INDEX_HTML)
                if p.startswith('/m/'):
                    # /m/<repo>/run/... is the repo's own app, not our page
                    sub = ('/' + p[len('/m/'):].partition('/')[2]).rstrip()
                    if sub == '/run' or sub.startswith('/run/'):
                        return self._proxy_run(p, u.query)
                if p == '/m' or p.startswith('/m/'):
                    # every market mod's app is the same file browser; the page
                    # reads the repo name out of its own URL and talks to
                    # /api/plinyville/m/<repo>/*.
                    return self._send(200, MOD_HTML)
                if p == '/plinyworld':
                    # The page loads its script relatively; without the trailing
                    # slash './triggers.defanged.js' would resolve to the root and
                    # 404, leaving the exhibit inert. Redirect on the raw path so
                    # this holds behind the gateway prefix too.
                    return self._send(301, b'', 'text/plain',
                                      {'Location': self.path.split('?')[0] + '/'
                                       + ('?' + u.query if u.query else '')})
                if p in ('/plinyworld/', '/plinyworld/index.html'):
                    return self._send(200, ville.plinyworld_html())
                if p == '/plinyworld/triggers.defanged.js':
                    return self._asset('triggers.defanged.js', 'application/javascript')
                if p in ('/plinyworld/payload', '/plinyworld/upstream/triggers.js'):
                    # text/plain + nosniff: readable as evidence, never runnable.
                    return self._send(200, ville.payload_source(),
                                      'text/plain; charset=utf-8',
                                      {'X-Content-Type-Options': 'nosniff'})
                if p.startswith('/api/') or p == '/api':
                    tail = p[4:] or '/'
                    return self._proxy(tail + ('?' + u.query if u.query else ''))
                if p == '/health':
                    return self._json(200, {'ok': True, 'api': API_URL})
                return self._json(404, {'error': f'no route {p}'})
            except Exception as e:                       # noqa: BLE001
                return self._json(500, {'error': f'{type(e).__name__}: {e}'})

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            p = _norm(u.path)
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = 0
            raw = self.rfile.read(n) if n else b''
            if p.startswith('/api/') or p == '/api':
                tail = p[4:] or '/'
                if tail.rstrip('/').endswith(('/chat/stream', '/chat/sse')):
                    return self._proxy_stream(tail, raw)
                return self._proxy(tail, body=raw)
            return self._json(404, {'error': f'no route POST {p}'})

    return H


def serve(port=PORT, host='0.0.0.0'):
    httpd = ThreadingHTTPServer((host, int(port)), _handler())
    print(f'plinyville app on http://{host}:{port}  (api: {API_URL})', flush=True)
    httpd.serve_forever()


# ── pixel art ──────────────────────────────────────────────────────────────
# The icons are not glyphs. The two faces below carry a latin subset, the box
# that serves this has no emoji font at all (★ renders, 🐉 renders as tofu),
# and a smooth vector glyph dropped into a bitmap UI reads as a mistake. So
# each icon is a box-shadow sprite drawn from an ASCII map: it renders the
# same everywhere, takes the colour of the text around it, and scales in
# whole pixels the way the rest of the console does.

SPRITES = {
    # the mark: the arcade invader. Nothing says 8-bit faster, and a mirror of
    # jailbreak repos may as well fly the flag of the things that get in.
    'inv': (3, """
        ..#.....#..
        ...#...#...
        ..#######..
        .##.###.##.
        ###########
        #.#######.#
        #.#.....#.#
        ...##.##...
    """),
    'star': (2, """
        ...#...
        ..###..
        #######
        .#####.
        ..###..
        .##.##.
        ##...##
    """),
    # a floppy: this repo is archived into the store, N files of it
    'disk': (2, """
        #######
        #..#..#
        #..#..#
        #.....#
        #.###.#
        #.###.#
        #######
    """),
    # the exhibit is a live-fire clipboard hijack, defanged. Label it as such.
    'skull': (2, """
        ..####..
        .######.
        ########
        #..##..#
        #..##..#
        ########
        ###..###
        .#.##.#.
    """),
}


def _sprite_css(name: str, px: int, art: str) -> str:
    """One ASCII pixel map → a sized box and the box-shadow stack that fills it."""
    rows = [r.strip() for r in art.strip().splitlines()]
    w, h = max(len(r) for r in rows), len(rows)
    dots = ','.join(f'{x * px}px {y * px}px 0 currentColor'
                    for y, row in enumerate(rows)
                    for x, ch in enumerate(row) if ch == '#')
    return (f'  .spr-{name}{{width:{w * px}px;height:{h * px}px}}\n'
            f'  .spr-{name}::before{{width:{px}px;height:{px}px;box-shadow:{dots}}}\n')


SPRITE_CSS = ('  .spr{position:relative;display:inline-block;flex:none;vertical-align:-2px}\n'
              '  .spr::before{content:"";position:absolute;left:0;top:0}\n'
              + ''.join(_sprite_css(n, px, art) for n, (px, art) in SPRITES.items()))


# ── the skin ───────────────────────────────────────────────────────────────
# One 8-bit console, twenty-one palettes. A theme declares only the ten colours
# that make it itself; every derived token (panels, borders, rings, shadows) is
# a color-mix off those, and the geometry is the same for all of them — square
# corners, 2px borders, hard offset shadows, two bitmap faces, scanlines over
# the lot. So adding a theme really is one line of palette. `data-base` says
# light or dark and rides next to data-theme, both set by the head script
# before first paint so a saved theme never flashes the default.
#
# Everything from CONSOLE_CSS down is shared chrome: both pages get the same
# body, buttons, pills, panels and scrollbars, and only their layout is their
# own.

THEME_CSS = FONT_CSS + SPRITE_CSS + r"""
  :root{--bg:#0a0810;--panel:#140f1c;--text:#f2ecff;--muted:#a99cc4;--faint:#6f6390;
    --accent:#b061ff;--accent2:#d08bff;--green:#3fb950;--warn:#ffb454;--danger:#d9534f;
    --on-accent:#fff}
  /* everything below is derived — themes never declare these */
  :root{--bw:2px;--r:0px;
    --f-hd:'PressStart',ui-monospace,monospace;
    --f-bd:'Terminal8',ui-monospace,Menlo,Consolas,monospace;
    --panel2:color-mix(in srgb,var(--panel),var(--text) 10%);
    --line:color-mix(in srgb,var(--panel),var(--text) 30%);
    --line2:color-mix(in srgb,var(--accent) 60%,var(--line));
    --glow:color-mix(in srgb,var(--accent) 15%,transparent);
    --glow2:color-mix(in srgb,var(--accent2) 11%,transparent);
    --ring:color-mix(in srgb,var(--accent) 34%,transparent);
    --sunk:color-mix(in srgb,var(--panel),var(--bg) 55%);
    --code:color-mix(in srgb,var(--warn),var(--text) 35%);
    /* the HUD bar is a panel, not the field: on HOT DOG or MARIO the page
       background is a saturated colour and accent-on-it is unreadable. */
    --hdr:var(--panel);
    --shadow:4px 4px 0 var(--line2)}

  /* ── dark ── */
  :root[data-theme="glass"]{--bg:#07070d;--panel:#14141f;--text:#fff;--muted:#d8d8e4;--faint:#8a8aa2;
    --accent:#a78bfa;--accent2:#38bdf8;--green:#34d399;--warn:#fbbf24;--danger:#fb7185}
  :root[data-theme="matrix"]{--bg:#010502;--panel:#04120a;--text:#d7ffe6;--muted:#8effbe;--faint:#3e9a68;
    --accent:#00ff7f;--accent2:#4defc9;--green:#00ff7f;--warn:#b8ff5c;--danger:#ff6a5c;
    --on-accent:#02170b}
  :root[data-theme="neon"]{--bg:#0a0416;--panel:#170b2a;--text:#f4e9ff;--muted:#d4b8f5;--faint:#8a6bb8;
    --accent:#ff2da0;--accent2:#0ff0d4;--green:#0ff0d4;--warn:#ffd319;--danger:#ff2d78}
  :root[data-theme="ember"]{--bg:#0c0603;--panel:#1a0f06;--text:#ffe8cc;--muted:#f5c896;--faint:#9c6f4a;
    --accent:#ff9e2c;--accent2:#ffd75e;--green:#ffb347;--warn:#ffd75e;--danger:#ff5c40;
    --on-accent:#2a1300}
  :root[data-theme="abyss"]{--bg:#030b18;--panel:#08182b;--text:#e6f2ff;--muted:#b6d4f0;--faint:#5d7fa3;
    --accent:#38bdf8;--accent2:#2dd4bf;--green:#2dd4bf;--warn:#fbbf24;--danger:#fb7185;
    --on-accent:#03192b}
  :root[data-theme="drive"]{--bg:#07080f;--panel:#12122a;--text:#fdf3f7;--muted:#d3c6d8;--faint:#8d7f9c;
    --accent:#ff2f8e;--accent2:#ffc44d;--green:#4fe0a8;--warn:#ffc44d;--danger:#ff4f6d}
  :root[data-theme="vapor"]{--bg:#120a24;--panel:#241442;--text:#f6ecff;--muted:#cfc0ea;--faint:#9182b4;
    --accent:#7ef6d8;--accent2:#e88fe0;--green:#7ef6d8;--warn:#ffd98e;--danger:#ff8fb8;
    --on-accent:#0d2a24}
  :root[data-theme="disco"]{--bg:#150b1c;--panel:#2b1638;--text:#fdf1ff;--muted:#d9c3e2;--faint:#9b83a8;
    --accent:#c04ff0;--accent2:#ffd166;--green:#5fd6a4;--warn:#ffd166;--danger:#ff6b8a}
  :root[data-theme="babe"]{--bg:#0b0614;--panel:#1e1034;--text:#fdf2ff;--muted:#d6c6ee;--faint:#907fb2;
    --accent:#ff6ec7;--accent2:#61e8ff;--green:#3dfabf;--warn:#ffd93d;--danger:#ff5d8f}
  /* ── ours: the funky end ── */
  :root[data-theme="acid"]{--bg:#060a00;--panel:#0e1704;--text:#e8ff9b;--muted:#b7f04a;--faint:#6f8f2a;
    --accent:#ccff00;--accent2:#39ff14;--green:#39ff14;--warn:#ffe600;--danger:#ff3d00;
    --on-accent:#0a1400}
  :root[data-theme="void"]{--bg:#000;--panel:#0b0b0b;--text:#fff;--muted:#b3b3b3;--faint:#6e6e6e;
    --accent:#fff;--accent2:#8f8f8f;--green:#cfcfcf;--warn:#e6e6e6;--danger:#ff2222;
    --on-accent:#000}
  /* ── light ── */
  :root[data-theme="rainbow"]{--bg:#fffdf7;--panel:#fdf3fa;--text:#2a1245;--muted:#46296b;--faint:#7a639c;
    --accent:#c026d3;--accent2:#0891b2;--green:#059669;--warn:#c2410c;--danger:#e11d48}
  :root[data-theme="surf"]{--bg:#fdf5e3;--panel:#fffbf0;--text:#14313a;--muted:#2c4f59;--faint:#5c7a83;
    --accent:#0d7f8c;--accent2:#e2553d;--green:#0f7d6b;--warn:#b45f06;--danger:#c2381f}
  :root[data-theme="paper"]{--bg:#f7f2e9;--panel:#fffcf5;--text:#292018;--muted:#52422f;--faint:#8c7a63;
    --accent:#9a5b2c;--accent2:#1d4ed8;--green:#15803d;--warn:#b45309;--danger:#be123c}
  :root[data-theme="bubblegum"]{--bg:#ffe3f4;--panel:#fff6fb;--text:#4a1039;--muted:#7c2b5f;--faint:#b06a92;
    --accent:#ff2d8f;--accent2:#7b5cff;--green:#00a884;--warn:#d97706;--danger:#e11d48}
  /* ── the console palettes ── */
  :root[data-theme="gameboy"]{--bg:#b4c4ab;--panel:#9bbc0f;--text:#0f380f;--muted:#163e10;--faint:#1e4713;
    --accent:#0f380f;--accent2:#306230;--green:#0f380f;--warn:#453a05;--danger:#5a2a0c;--on-accent:#9bbc0f}
  :root[data-theme="mario"]{--bg:#5c94fc;--panel:#fffdf2;--text:#1c1c28;--muted:#3a3b52;--faint:#4e4f68;
    --accent:#c42400;--accent2:#0a5fb0;--green:#0d7a14;--warn:#8a6200;--danger:#c42400}
  :root[data-theme="warp"]{--bg:#050712;--panel:#0b1024;--text:#f2f2e6;--muted:#a8b0cc;--faint:#7681a6;
    --accent:#2fb1f0;--accent2:#fbd000;--green:#43b047;--warn:#fbd000;--danger:#e52521;--on-accent:#04121f}
  :root[data-theme="win95"]{--bg:#c0c0c0;--panel:#dfdfdf;--text:#000;--muted:#222;--faint:#555;
    --accent:#000080;--accent2:#008080;--green:#008000;--warn:#808000;--danger:#a00}
  :root[data-theme="hotdog"]{--bg:#e40000;--panel:#ffe100;--text:#101010;--muted:#3a3226;--faint:#6b5f3a;
    --accent:#0018a8;--accent2:#e40000;--green:#006b1f;--warn:#a35a00;--danger:#a80000}

  /* ── the console: chrome both pages share ── */
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;color:var(--text);font:19px/1.35 var(--f-bd);
    /* a faint CRT grid, so the empty half of the page is still a screen */
    background:
      radial-gradient(900px 500px at 12% -8%,var(--glow),transparent 62%),
      radial-gradient(760px 440px at 96% 0%,var(--glow2),transparent 58%),
      repeating-linear-gradient(0deg,color-mix(in srgb,var(--text) 4%,transparent) 0 1px,transparent 1px 32px),
      repeating-linear-gradient(90deg,color-mix(in srgb,var(--text) 4%,transparent) 0 1px,transparent 1px 32px),
      var(--bg);
    background-attachment:fixed;image-rendering:pixelated}
  /* scanlines. Flat black at low alpha, not a tint of the theme: a scanline is
     the gap between two rows of phosphor, so it only ever darkens, and one
     value reads as subtle on VOID and as stripes on HOT DOG the way it should. */
  body::after{content:"";position:fixed;inset:0;z-index:900;pointer-events:none;
    background:repeating-linear-gradient(0deg,rgba(0,0,0,.16) 0 1px,transparent 1px 3px)}
  a{color:inherit}
  ::selection{background:var(--accent);color:var(--on-accent)}
  b,strong{font-weight:400;color:var(--text)}
  h1,h2,h3,h4{font-family:var(--f-hd);font-weight:400;letter-spacing:0;line-height:1.45}
  code,.mono,pre{font-family:var(--f-bd)}
  @keyframes blink{50%{opacity:0}}
  .cur::after{content:"\2588";animation:blink 1s steps(1) infinite;margin-left:1px}
  /* chunky scrollbars — a thin rounded one gives the whole illusion away */
  *::-webkit-scrollbar{width:12px;height:12px}
  *::-webkit-scrollbar-track{background:var(--sunk)}
  *::-webkit-scrollbar-thumb{background:var(--line2);border:2px solid var(--sunk)}
  *::-webkit-scrollbar-thumb:hover{background:var(--accent)}

  /* buttons press: the shadow is the gap under the key, so losing it on
     :active is the key going down. */
  .btn{cursor:pointer;display:inline-flex;gap:6px;align-items:center;text-decoration:none;
    font:9px/1 var(--f-hd);color:var(--text);background:var(--panel2);
    border:var(--bw) solid var(--line);border-radius:0;padding:8px 10px;white-space:nowrap;
    box-shadow:3px 3px 0 var(--line);transition:none}
  .btn:hover{border-color:var(--accent);color:var(--accent);box-shadow:3px 3px 0 var(--line2)}
  .btn:active{transform:translate(3px,3px);box-shadow:none}
  .btn:disabled{opacity:.5;cursor:default;transform:none}
  .btn.primary{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}
  .btn.primary:hover{color:var(--on-accent);background:var(--accent2);border-color:var(--accent2)}
  .btn.ghost{font-size:8px;padding:6px 8px;background:transparent;box-shadow:2px 2px 0 var(--line)}
  .btn.ghost:active{transform:translate(2px,2px)}
  .pill{cursor:pointer;display:inline-flex;gap:6px;align-items:center;white-space:nowrap;
    font:9px/1 var(--f-hd);color:var(--muted);background:var(--panel);
    border:var(--bw) solid var(--line);border-radius:0;padding:8px 9px;box-shadow:3px 3px 0 var(--line)}
  .pill:hover{color:var(--text);border-color:var(--accent);box-shadow:3px 3px 0 var(--line2)}
  .pill:active{transform:translate(3px,3px);box-shadow:none}
  .pill.ok{color:var(--green);border-color:var(--green);box-shadow:3px 3px 0 var(--green)}
  .pill.stale{color:var(--warn);border-color:var(--warn);box-shadow:3px 3px 0 var(--warn)}
  .pill.bad{color:var(--danger);border-color:var(--danger);box-shadow:3px 3px 0 var(--danger)}
  input{font:16px/1 var(--f-bd);color:var(--text);background:var(--panel2);border-radius:0;
    border:var(--bw) solid var(--line);padding:8px 10px;outline:none}
  input::placeholder{color:var(--faint)}
  input:focus{border-color:var(--accent);box-shadow:3px 3px 0 var(--line2)}
  /* a panel is a NES dialog box: square, bordered, sitting on its own shadow */
  .panel,.card{background:var(--panel);border:var(--bw) solid var(--line);border-radius:0;
    box-shadow:var(--shadow)}
  .lbl{font:8px/1.5 var(--f-hd);color:var(--faint);letter-spacing:.5px;text-transform:uppercase}
  .loading{color:var(--muted);padding:22px;text-align:center;font-size:17px}

  /* ── the picker ── */
  .themes{position:absolute;right:14px;top:calc(100% + 4px);z-index:60;display:none;
    grid-template-columns:repeat(auto-fill,minmax(150px,1fr));width:min(620px,calc(100vw - 28px));gap:6px;
    padding:11px;border:var(--bw) solid var(--line2);background:var(--panel);
    box-shadow:6px 6px 0 var(--line2)}
  .themes.on{display:grid}
  .themes .hd{grid-column:1/-1;font:8px/1 var(--f-hd);color:var(--faint);letter-spacing:.5px;
    text-transform:uppercase;padding:4px 2px 2px}
  .tsw{display:flex;gap:8px;align-items:center;cursor:pointer;text-align:left;color:var(--text);
    font:8px/1 var(--f-hd);background:var(--panel2);border:var(--bw) solid var(--line);
    border-radius:0;padding:8px}
  .tsw:hover{border-color:var(--accent);color:var(--accent)}
  .tsw.on{border-color:var(--accent);background:var(--accent);color:var(--on-accent)}
  .sw{width:26px;height:11px;flex:none;border:2px solid var(--text);image-rendering:pixelated}
  .tsw.on .sw{border-color:var(--on-accent)}
"""

# The picker itself. Lives in the head of both pages: it applies the saved
# theme before first paint and, once the DOM is up, renders the swatch grid
# into whatever #themes container the page provides.
THEME_JS = r"""
// [id, label, dark|light, [bg, accent, accent2]] — the three colours are the
// swatch only; the palette itself lives in the stylesheet.
const THEMES=[
  ['pliny','PLINY','dark',['#0a0810','#b061ff','#3fb950']],
  ['glass','GLASS','dark',['#07070d','#a78bfa','#34d399']],
  ['matrix','MATRIX','dark',['#010502','#00ff7f','#4defc9']],
  ['neon','NEON','dark',['#0a0416','#ff2da0','#0ff0d4']],
  ['ember','EMBER','dark',['#0c0603','#ff9e2c','#ffd75e']],
  ['abyss','ABYSS','dark',['#030b18','#38bdf8','#2dd4bf']],
  ['drive','DRIVE','dark',['#07080f','#ff2f8e','#ffc44d']],
  ['vapor','VAPOR','dark',['#120a24','#7ef6d8','#e88fe0']],
  ['disco','DISCO','dark',['#150b1c','#c04ff0','#ffd166']],
  ['babe','BABE','dark',['#0b0614','#ff6ec7','#61e8ff']],
  ['acid','ACID','dark',['#060a00','#ccff00','#39ff14']],
  ['void','VOID','dark',['#000000','#ffffff','#ff2222']],
  ['warp','WARP','dark',['#050712','#2fb1f0','#fbd000']],
  ['rainbow','RAINBOW','light',['#fffdf7','#c026d3','#059669']],
  ['bubblegum','BUBBLEGUM','light',['#ffe3f4','#ff2d8f','#7b5cff']],
  ['surf','SURF','light',['#fdf5e3','#0d7f8c','#e2553d']],
  ['paper','PAPER','light',['#f7f2e9','#9a5b2c','#15803d']],
  ['gameboy','GAMEBOY','light',['#b4c4ab','#0f380f','#9bbc0f']],
  ['mario','MARIO','light',['#5c94fc','#c42400','#fbd000']],
  ['win95','WIN 95','light',['#c0c0c0','#000080','#008000']],
  ['hotdog','HOT DOG','light',['#e40000','#ffe100','#0018a8']],
];
const THEME_KEY='plinyville_theme';
// three flat bands, no gradient: a blur here is the one thing a bitmap UI
// cannot have, and it is also the honest picture of a ten-colour palette.
const swatch = c => '<i class="sw" style="background:linear-gradient(90deg,'
  +c[0]+' 0 34%,'+c[1]+' 34% 67%,'+c[2]+' 67% 100%)"></i>';
function applyTheme(id){
  const t=THEMES.find(t=>t[0]===id)||THEMES[0];
  const r=document.documentElement;
  r.setAttribute('data-theme',t[0]);
  r.setAttribute('data-base',t[2]);
  try{ localStorage.setItem(THEME_KEY,t[0]); }catch(e){}
  const host=document.getElementById('themes');
  if(host) host.querySelectorAll('.tsw').forEach(b=>b.classList.toggle('on',b.dataset.t===t[0]));
  const pill=document.getElementById('themepill');
  if(pill) pill.innerHTML=swatch(t[3])+t[1];
}
try{ applyTheme(localStorage.getItem(THEME_KEY)||'pliny'); }catch(e){ applyTheme('pliny'); }

function toggleThemes(ev){
  if(ev) ev.stopPropagation();
  const host=document.getElementById('themes');
  if(!host) return;
  if(!host.dataset.ready){
    host.dataset.ready='1';
    let html='', base='';
    for(const t of THEMES){
      if(t[2]!==base){ base=t[2]; html+='<div class="hd">'+base+' skins</div>'; }
      html+='<button class="tsw" data-t="'+t[0]+'">'+swatch(t[3])+t[1]+'</button>';
    }
    host.innerHTML=html;
    host.addEventListener('click',e=>{
      const b=e.target.closest('button[data-t]'); if(b) applyTheme(b.dataset.t);
      e.stopPropagation();
    });
    applyTheme(document.documentElement.getAttribute('data-theme'));
  }
  host.classList.toggle('on');
}
document.addEventListener('click',()=>{
  const h=document.getElementById('themes'); if(h) h.classList.remove('on');
});
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape') return;
  const h=document.getElementById('themes'); if(h) h.classList.remove('on');
});
document.addEventListener('DOMContentLoaded',()=>applyTheme(
  document.documentElement.getAttribute('data-theme')||'pliny'));
"""


def _skin(html: str) -> str:
    """Drop the shared theme sheet + picker into a page template."""
    return html.replace('/*THEME_CSS*/', THEME_CSS).replace('/*THEME_JS*/', THEME_JS)


# ── the gallery UI (zero-dep) ───────────────────────────────────────────────

INDEX_HTML = _skin(r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>plinyville · elder-plinius mirror</title>
<style>
/*THEME_CSS*/
  header{position:sticky;top:0;z-index:20;background:var(--hdr);
    border-bottom:4px solid var(--line2);padding:10px 14px}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:nowrap}
  .brand{display:flex;align-items:center;gap:11px;min-width:0;white-space:nowrap;
    font:15px/1 var(--f-hd);color:var(--accent);text-shadow:3px 3px 0 var(--line)}
  .brand .spr{color:var(--accent2)}
  .sub{font:16px/1.2 var(--f-bd);color:var(--muted);text-shadow:none;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .grow{flex:1}
  /* the header is one line or it is nothing: the filter shrinks, the status
     pills ellipsis, and the tagline drops before anything wraps onto a second
     row and leaves a band of empty header behind it. */
  input#q{flex:1 1 120px;min-width:92px;max-width:230px}
  .row>.pill{overflow:hidden;text-overflow:ellipsis;max-width:min(28vw,270px)}
  @media(max-width:1280px){.brand .sub{display:none}}
  @media(max-width:900px){.row{flex-wrap:wrap}.row>.pill{max-width:46vw}}
  main{max-width:1240px;margin:0 auto;padding:14px 14px 48px}
  /* the exhibit and the mcp endpoint ride as slim strips, not banners: the
     market is the page. Detail lives behind the toggles. */
  .strips{display:flex;gap:9px;flex-wrap:wrap;margin:0 0 11px}
  .strip{display:flex;gap:10px;align-items:center;padding:7px 8px 7px 11px;
    border:var(--bw) solid var(--line);background:var(--panel);box-shadow:3px 3px 0 var(--line);
    font:16px/1.1 var(--f-bd);color:var(--muted)}
  .strip .t{display:flex;gap:8px;align-items:center;font:9px/1 var(--f-hd);color:var(--text)}
  .strip.warn{border-color:var(--danger);box-shadow:3px 3px 0 var(--danger)}
  .strip.warn .t{color:var(--danger)}
  .strip code{color:var(--accent2)}
  .facts{display:none;margin:0 0 12px;padding:13px 15px;
    border:var(--bw) solid var(--danger);background:var(--panel);box-shadow:5px 5px 0 var(--danger)}
  .facts.on{display:block}
  .facts h4{margin:13px 0 6px;font-size:9px;color:var(--warn);text-transform:uppercase}
  .facts h4:first-child{margin-top:0}
  .facts ul{margin:0;padding-left:20px;color:var(--muted);font-size:17px;line-height:1.35}
  .facts code{color:var(--accent2)}
  .mono{font:15px/1.45 var(--f-bd);background:var(--sunk);border:var(--bw) solid var(--line);
    padding:9px 11px;color:var(--code);overflow:auto;white-space:pre-wrap;word-break:break-word}
  /* ── the taxonomy ──
     Forty-seven cartridges in one wall is a pile. The pills are the shelf
     labels: press JAILBREAK and the wall is the thirteen liberation repos,
     press APP as well and it is the six you can also press RUN on. They are
     the same ids the API and the chat take, so what you see and what an agent
     is allowed to read are one vocabulary. */
  .types{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px;align-items:center}
  .tpill{cursor:pointer;display:inline-flex;gap:7px;align-items:center;white-space:nowrap;
    font:9px/1 var(--f-hd);color:var(--muted);background:var(--panel);
    border:var(--bw) solid var(--line);padding:8px 9px;box-shadow:3px 3px 0 var(--line)}
  .tpill:hover{color:var(--text);border-color:var(--accent)}
  .tpill:active{transform:translate(3px,3px);box-shadow:none}
  .tpill.on{background:var(--accent);color:var(--on-accent);border-color:var(--accent);
    box-shadow:3px 3px 0 var(--line2)}
  .tpill b{font:8px/1 var(--f-hd);opacity:.75}
  .tpill.off{opacity:.4}
  .ttags{display:flex;gap:5px;flex-wrap:wrap}
  .ttag{cursor:pointer;font:8px/1 var(--f-hd);color:var(--accent2);background:var(--panel2);
    border:var(--bw) solid var(--line);padding:5px 6px}
  .ttag:hover{border-color:var(--accent2);color:var(--text)}
  /* ── the chat ──
     A search box matches names; a question needs somebody to go and read. The
     panel is the agent's desk: what it is allowed to open, every tool call as
     it happens, then the answer with the paths in it. */
  .chat{display:none;margin:0 0 12px;padding:13px 15px;
    border:var(--bw) solid var(--accent2);background:var(--panel);
    box-shadow:5px 5px 0 var(--accent2)}
  .chat.on{display:block}
  .chat h4{margin:0 0 8px;font:9px/1 var(--f-hd);color:var(--accent2);text-transform:uppercase}
  .ask{display:flex;gap:8px;flex-wrap:wrap}
  .ask input{flex:1;min-width:220px}
  .scope{margin:9px 0 0;font:14px/1.4 var(--f-bd);color:var(--muted)}
  .scope b{color:var(--accent2)}
  .steps{margin:11px 0 0;display:flex;flex-direction:column;gap:5px;max-height:190px;overflow:auto}
  .step{font:14px/1.35 var(--f-bd);color:var(--muted);background:var(--sunk);
    border-left:4px solid var(--accent2);padding:5px 9px;word-break:break-word}
  .step.err{border-left-color:var(--danger);color:var(--danger)}
  .answer{margin:11px 0 0;padding:11px 13px;background:var(--sunk);
    border:var(--bw) solid var(--line);color:var(--text);
    font:17px/1.45 var(--f-bd);white-space:pre-wrap;word-break:break-word}
  .answer code,.answer b{color:var(--accent2)}
  .answer a{color:var(--accent);text-decoration:underline}
  .cites{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
  .cite{font:13px/1 var(--f-bd);color:var(--code);background:var(--panel2);
    border:var(--bw) solid var(--line);padding:5px 8px;text-decoration:none}
  .cite:hover{border-color:var(--accent)}
  .doms{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
  .dom{font:14px/1 var(--f-bd);color:var(--danger);background:var(--panel2);
    border:var(--bw) solid var(--danger);padding:5px 9px}
  /* a shelf of cartridges: the cards in a row are one height and their buttons
     sit on one line, which only works because the two things that vary without
     limit — the title and the description — are clamped below. */
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(274px,1fr));gap:13px;
    align-items:stretch}
  /* every card is a menu box: a filled title bar with the name and the state
     badge in it, then the body. The bar is the accent, so the market reads as
     rows of cartridges and the theme is visible in every single one. */
  .card{display:flex;flex-direction:column;padding:0}
  .card:hover{border-color:var(--accent);box-shadow:7px 7px 0 var(--line2);transform:translate(-2px,-2px)}
  .card .hd{display:flex;align-items:center;gap:9px;padding:9px 10px;min-height:44px;
    background:var(--accent);color:var(--on-accent);border-bottom:var(--bw) solid var(--line)}
  .card .hd a{flex:1;min-width:0;font:11px/1.4 var(--f-hd);text-decoration:none;word-break:break-word;
    display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .card .hd a:hover{text-decoration:underline}
  .card .bd{flex:1;padding:10px;display:flex;flex-direction:column;gap:9px}
  .badge{flex:none;font:8px/1 var(--f-hd);padding:5px 6px;white-space:nowrap;
    border:2px solid var(--on-accent);color:var(--on-accent);opacity:.72}
  .badge.on{opacity:1;background:var(--on-accent);color:var(--accent)}
  /* no min-height, no empty rows: a repo with no description or no topics
     simply has one fewer line. Four lines is the cap — the rest is on hover
     and on the mod's own page, and one manifesto-length blurb is not worth
     three cards' worth of void beside it. */
  .card .desc{color:var(--muted);font-size:17px;line-height:1.3;
    display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
  .desc:empty,.topics:empty{display:none}
  .meta{display:flex;gap:13px;flex-wrap:wrap;align-items:center;color:var(--faint);font:15px/1 var(--f-bd)}
  .meta .spr{margin-right:6px;vertical-align:-3px}
  .lang{color:var(--accent2)}
  .acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto;padding-top:2px}
  .topics{display:flex;gap:5px;flex-wrap:wrap}
  .topic{font:13px/1 var(--f-bd);color:var(--muted);background:var(--panel2);
    border:var(--bw) solid var(--line);padding:4px 7px}
  /* the tally is the one line that used to sit straight on the page, which on
     the loud themes is a saturated colour — a green "46" on HOT DOG's red was
     unreadable. It gets a panel of its own, like everything else here. */
  .count{font:10px/1.5 var(--f-hd);color:var(--text);margin:0 0 12px;width:fit-content;
    display:flex;gap:14px;align-items:center;flex-wrap:wrap;
    background:var(--panel);border:var(--bw) solid var(--line);padding:9px 11px;
    box-shadow:3px 3px 0 var(--line)}
  .count b{color:var(--green)}
  dialog{border:var(--bw) solid var(--accent);background:var(--panel);color:var(--text);
    max-width:880px;width:92vw;padding:0;box-shadow:8px 8px 0 var(--line2)}
  dialog::backdrop{background:color-mix(in srgb,var(--bg) 78%,transparent)}
  .dlg-head{position:sticky;top:0;display:flex;gap:12px;align-items:center;padding:11px 13px;
    border-bottom:var(--bw) solid var(--line);background:var(--panel);font:10px/1 var(--f-hd)}
  .dlg-body{padding:14px;max-height:66vh;overflow:auto}
  .dlg-body pre{white-space:pre-wrap;word-break:break-word;font:16px/1.4 var(--f-bd);
    color:var(--muted);margin:0}
</style>
<script>
/*THEME_JS*/
</script>
</head>
<body>
<header>
  <div class="row">
    <div class="brand"><span class="spr spr-inv"></span>PLINYVILLE
      <span class="sub">the elder-plinius market - every repo, its own mod</span></div>
    <div class="grow"></div>
    <!-- what the daily scan last did, and the CID of the module doing it -->
    <button id="fresh" class="pill" onclick="rescan()" title="scanning">SCAN ?</button>
    <button id="cid" class="pill" onclick="copyCid()" title="this module's content id">CID ?</button>
    <!-- the skin: 21 palettes, one console -->
    <button id="themepill" class="pill" onclick="toggleThemes(event)" title="skin">PLINY</button>
    <input id="q" placeholder="filter repos" oninput="render()"/>
    <button class="btn" onclick="load(true)">RELOAD</button>
  </div>
  <div id="themes" class="themes"></div>
</header>
<main>
  <div class="strips">
    <div class="strip warn">
      <span class="t"><span class="spr spr-skull"></span>PLINYWORLD</span><span>defanged exhibit</span>
      <button class="btn ghost" onclick="toggleFacts()">WHAT IT DOES</button>
      <a class="btn ghost" href="./plinyworld" target="_blank" rel="noopener">RUN &gt;</a>
    </div>
    <div class="strip">
      <span class="t">MCP</span><span id="mcpn">every repo, its own tool</span>
      <button class="btn ghost" onclick="toggleMcp()">CONNECT</button>
    </div>
    <!-- a third of the corpus is not prose, it is an app. those ones run here. -->
    <div class="strip" id="arcade" style="display:none">
      <span class="t"><span class="spr spr-inv"></span>ARCADE</span><span id="arcaden"></span>
      <button class="btn ghost" id="arcadebtn" onclick="toggleArcade()">RUN ONLY</button>
    </div>
    <!-- only rendered when the GitHub budget is worth knowing about: anonymous,
         or running low. A silent 403 mid-browse is the thing this prevents. -->
    <!-- the corpus, asked rather than filtered: the claude agent reads these
         repos with this module's own MCP tools and answers with the paths. -->
    <div class="strip" id="askstrip">
      <span class="t"><span class="spr spr-inv"></span>ASK</span>
      <span id="askn">the claude agent reads these repos for you</span>
      <button class="btn ghost" onclick="toggleChat()">ASK IT</button>
    </div>
    <div class="strip" id="ratestrip" style="display:none"></div>
  </div>
  <div id="facts" class="facts"><div class="loading cur">reading the preserved payload</div></div>
  <div id="mcp" class="facts" style="border-color:var(--accent2);box-shadow:5px 5px 0 var(--accent2)">
    <div class="loading cur">reading the tool registry</div>
  </div>
  <div id="types" class="types"></div>
  <div id="chat" class="chat">
    <h4>ask the corpus</h4>
    <div class="ask">
      <input id="cq" placeholder="which of these jailbreak Claude, and what does the prompt look like?"
             onkeydown="if(event.key==='Enter')askAgent()"/>
      <button class="btn primary" id="askbtn" onclick="askAgent()">ASK &gt;</button>
      <button class="btn ghost" onclick="newChat()" title="forget this conversation">NEW</button>
    </div>
    <div id="cscope" class="scope"></div>
    <div id="csteps" class="steps"></div>
    <div id="canswer" class="answer" style="display:none"></div>
    <div id="ccites" class="cites"></div>
  </div>
  <div id="count" class="count"></div>
  <div id="grid" class="grid"><div class="loading cur">loading elder-plinius's repos</div></div>
</main>

<dialog id="dlg">
  <div class="dlg-head">
    <b id="dlg-title">README</b><div class="grow"></div>
    <a id="dlg-link" class="btn ghost" target="_blank" rel="noopener">GITHUB</a>
    <button class="btn ghost" onclick="document.getElementById('dlg').close()">CLOSE</button>
  </div>
  <div class="dlg-body"><pre id="dlg-body"></pre></div>
</dialog>

<script>
const B = location.pathname.replace(/\/index\.html$/,'').replace(/\/$/,'');
const api = p => fetch(B + '/api' + p).then(r => r.json());
const apiPost = p => fetch(B + '/api' + p, {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r => r.json());
let REPOS = [], FACTS = null, ONLY_RUN = false;
// the taxonomy, and which shelf labels are pressed. Multiple pills are AND —
// JAILBREAK+APP is "a jailbreak I can also run", which is six of them.
let TYPES = [], SEL = new Set(), TLABEL = {}, CHAT_SESSION = null, ASKING = false;
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

async function load(refresh){
  const g=document.getElementById('grid');
  g.innerHTML='<div class="loading cur">loading the elder-plinius market</div>';
  try{
    const j=await api('/market'+(refresh?'?refresh=1':''));
    if(j.error) throw new Error(j.error);
    REPOS=j.mods||[]; renderArcade(j.runnable||0); loadTypes(); render();
  }catch(e){ g.innerHTML='<div class="loading">failed to load: '+esc(e.message)+'</div>'; loadRate(); }
}

// The GitHub budget. Anonymous is 60/hour per IP for the whole box, so the wall
// arrives mid-browse and the market simply stops loading; say so, and say the
// one command that fixes it.
async function loadRate(){
  const el=document.getElementById('ratestrip');
  let j; try{ j=await api('/rate'); }catch(e){ return; }
  if(!j || j.error || j.remaining==null) return;
  const low = j.limit ? j.remaining <= Math.max(5, j.limit*0.15) : false;
  if(j.authenticated && !low){ el.style.display='none'; return; }
  const mins = j.resets_in!=null ? Math.ceil(j.resets_in/60) : null;
  el.className = 'strip' + (low || !j.authenticated ? ' warn' : '');
  el.innerHTML = '<span class="t">GITHUB</span><span>'
    + (j.authenticated ? 'authenticated' : 'anonymous')
    + ' · <b>'+j.remaining+'</b>/'+j.limit+' calls left'
    + (mins!=null ? ' · resets in '+mins+'m' : '')
    + (j.authenticated ? '' : ' · <code>m pliny/token &lt;github_pat&gt;</code> for 5,000/hr')
    + '</span>';
  el.style.display='';
}

// The arcade strip: how many of these repos are things you can press play on.
function renderArcade(n){
  const el=document.getElementById('arcade');
  if(!n){ el.style.display='none'; return; }
  el.style.display='';
  // …and the ones that are apps but shipped as source: the strip should say
  // they exist, because a BUILD button on a card nobody scrolled to is a
  // feature nobody finds.
  const b=REPOS.filter(r=>!r.run&&r.build).length;
  document.getElementById('arcaden').innerHTML='<b>'+n+'</b> of them are apps - they run here, sandboxed'
    +(b?' &middot; <b>'+b+'</b> more build into one':'');
}
// recount after a build lands, without refetching the whole market
function arcade(){ renderArcade(REPOS.filter(r=>r.run).length); }
function toggleArcade(){
  ONLY_RUN=!ONLY_RUN;
  document.getElementById('arcadebtn').textContent=ONLY_RUN?'SHOW ALL':'RUN ONLY';
  document.getElementById('arcade').className='strip'+(ONLY_RUN?' warn':'');
  render();
}

// The shelf labels. Counts come from the server's own classifier (GET /types),
// which shows its evidence per repo at /types?repo=<name> — a type nobody can
// justify is a type nobody should filter on.
async function loadTypes(){
  if(TYPES.length) return renderTypes();
  try{ const j=await api('/types'); TYPES=j.types||[]; }catch(e){ return; }
  TYPES.forEach(t=>{ TLABEL[t.id]=t.label; });
  renderTypes();
}
function renderTypes(){
  const el=document.getElementById('types');
  if(!TYPES.length){ el.innerHTML=''; return; }
  const live=id=>REPOS.filter(r=>(r.types||[]).includes(id)).length;
  el.innerHTML='<button class="tpill'+(SEL.size?'':' on')+'" onclick="clearTypes()">ALL'
      +'<b>'+REPOS.length+'</b></button>'
    + TYPES.map(t=>{const n=live(t.id)||t.count;
        return '<button class="tpill'+(SEL.has(t.id)?' on':(n?'':' off'))+'" title="'+esc(t.blurb)
          +'" onclick="toggleType(\''+t.id+'\')">'+esc(t.label)+'<b>'+n+'</b></button>';}).join('');
}
function toggleType(id){
  SEL.has(id)?SEL.delete(id):SEL.add(id);
  renderTypes(); render(); scopeLine();
}
function clearTypes(){ SEL.clear(); renderTypes(); render(); scopeLine(); }
function typeOk(r){ for(const t of SEL){ if(!(r.types||[]).includes(t)) return false; } return true; }

function render(){
  const q=(document.getElementById('q').value||'').toLowerCase();
  const rows=REPOS.filter(r=>(!ONLY_RUN||r.run)&&typeOk(r)&&(!q||(r.name||'').toLowerCase().includes(q)
    ||(r.description||'').toLowerCase().includes(q)
    ||(r.topics||[]).some(t=>t.toLowerCase().includes(q))));
  // the tallies count what is on the shelf right now, not the whole corpus:
  // "13 MODS - JAILBREAK ... 13 RUN IN THE BROWSER" was reading the global
  // arcade count next to a filtered wall, which makes it a lie about the six.
  const inst=rows.filter(r=>r.installed).length;
  const runs=rows.filter(r=>r.run).length;
  const sel=[...SEL].map(t=>TLABEL[t]||t).join(' + ');
  document.getElementById('count').innerHTML='<span>'+rows.length+' MODS'
    +(sel?' - '+esc(sel):'')+(q?' MATCHING "'+esc(q)+'"':'')
    +'</span><span><b>'+inst+'</b> IN THE STORE</span>'
    +(runs?'<span><b>'+runs+'</b> RUN IN THE BROWSER</span>':'');
  document.getElementById('grid').innerHTML=rows.map(r=>`
    <div class="card" id="card-${esc(r.slug)}">
      <div class="hd">
        <a href="${B}/m/${encodeURIComponent(r.name)}">${esc(r.name)}</a>
        ${r.installed?'<span class="badge on" title="archived into the store'+(r.source==='git-clone'?' by cloning it':'')+' · '+esc((r.cid||'').slice(0,18))+'">MOD</span>'
          :'<span class="badge">REPO</span>'}
      </div>
      <div class="bd">
        <div class="desc" title="${esc(r.description)}">${esc(r.description)||'<span style="color:var(--faint)">no description</span>'}</div>
        <div class="ttags">${(r.types||[]).slice(0,3).map(t=>
          `<span class="ttag" title="what sort of repo this is - click to see only these" onclick="toggleType('${esc(t)}')">${esc(TLABEL[t]||t)}</span>`).join('')}</div>
        <div class="topics">${(r.topics||[]).slice(0,4).map(t=>`<span class="topic">${esc(t)}</span>`).join('')}</div>
        <div class="meta">
          ${r.language?`<span class="lang">${esc(r.language)}</span>`:''}
          <span title="stars"><span class="spr spr-star"></span>${r.stars}</span>
          ${r.installed?`<span title="files archived"><span class="spr spr-disk"></span>${r.files_stored}</span>`:''}
        </div>
        <div class="acts">
          ${r.run&&r.run_degraded?`<span class="ttag" style="color:var(--warn);border-color:var(--warn)" title="${esc(r.run_degraded_why||'')}">RUNS, BUT...</span>`:''}
          ${r.run?(r.run_defanged
            ? `<a class="btn" href="${B}/plinyworld" target="_blank" rel="noopener" title="a live clipboard-hijack PoC - the defanged exhibit runs instead">RUN &gt;</a>`
            : `<a class="btn" href="${B}/m/${encodeURIComponent(r.name)}#run" title="run it here, sandboxed">RUN &gt;</a>`):''}
          ${(!r.run&&r.build)?`<button class="btn" onclick="build('${esc(r.name)}',this)" title="${esc(r.build_note||'')} - this app ships as source; build it here and it becomes playable">BUILD &gt;</button>`:''}
          <a class="btn ghost" href="${B}/m/${encodeURIComponent(r.name)}">OPEN &gt;</a>
          ${r.installed?'':`<button class="btn ghost" onclick="install('${esc(r.name)}',this)">+ INSTALL</button>`}
          <a class="btn ghost" href="${esc(r.url)}" target="_blank" rel="noopener">GITHUB</a>
        </div>
      </div>
    </div>`).join('') || '<div class="loading">no matching mods</div>';
}

// BUILD: the two or three repos in this corpus that are apps nobody ran the
// build for. The POST returns at once - an npm install is minutes long - so
// this polls the same route for the step it is on and only claims the app is
// playable once the server says the arcade can serve a page.
async function build(name, btn){
  const label=(t,cur)=>{ if(!btn) return; btn.textContent=t;
    btn.classList.toggle('cur',!!cur); btn.disabled=!!cur; };
  label('BUILDING',true);
  try{
    const j=await apiPost('/m/'+encodeURIComponent(name)+'/build');
    if(j.error) throw new Error(j.error);
    for(let i=0;i<200;i++){
      const st=await api('/m/'+encodeURIComponent(name)+'/build');
      // "INSTALL" would read as the archive button two along; this is the
      // dependency install, and the card already has an + INSTALL of its own.
      if(st.running){ label((st.step==='install'?'DEPS':'BUILD')+' '+Math.round(st.seconds||0)+'S',true);
        await new Promise(r=>setTimeout(r,3000)); continue; }
      const rec=st.receipt||{};
      if(!rec.ok) throw new Error((rec.error||'the build did not produce a page')
        +((rec.log||[]).length?' - '+rec.log[rec.log.length-1].slice(0,120):''));
      const r=REPOS.find(x=>x.name===name);
      if(r&&st.run&&st.run.runnable){ r.run=true; r.run_kind=st.run.kind; r.build=null;
        r.run_url=st.run.run_url; r.run_degraded=!!st.run.degraded;
        r.run_degraded_why=st.run.degraded_why; }
      render(); arcade(); return;
    }
    throw new Error('still building after ten minutes - see GET /builds');
  }catch(e){
    label('BUILD FAILED',false);
    alert('build failed: '+e.message);
  }
}

async function install(name, btn){
  if(btn){ btn.textContent='ARCHIVING'; btn.classList.add('cur'); btn.disabled=true; }
  try{
    const j=await apiPost('/market/install?name='+encodeURIComponent(name));
    if(j.error) throw new Error(j.error);
    const r=REPOS.find(x=>x.name===name);
    if(r){ r.installed=true; r.cid=j.cid; r.files_stored=j.files_stored; }
    render();
  }catch(e){
    if(btn){ btn.classList.remove('cur'); btn.textContent='FAILED'; btn.disabled=false; }
    alert('install failed: '+e.message);
  }
}

async function readme(name,url){
  const d=document.getElementById('dlg');
  document.getElementById('dlg-title').textContent=name.toUpperCase()+' README';
  document.getElementById('dlg-link').href=url;
  document.getElementById('dlg-body').textContent='loading...';
  d.showModal();
  const j=await api('/readme?name='+encodeURIComponent(name));
  document.getElementById('dlg-body').textContent=j.markdown||j.error||'(no readme)';
}

async function toggleFacts(){
  const el=document.getElementById('facts');
  el.classList.toggle('on');
  if(!el.classList.contains('on')||FACTS) return;
  try{ FACTS=await api('/exhibit'); }catch(e){ el.innerHTML='<div class="loading">'+esc(e.message)+'</div>'; return; }
  el.innerHTML=`
    <h4>the exhibit</h4>
    <ul><li>A fork of elder-plinius.github.io, a clipboard-hijack (pastejacking) red-team PoC.
      Served <b>DEFANGED</b>: it copies nothing and shows what the live attack would have done.</li></ul>
    <h4>mechanism</h4>
    <ul>${(FACTS.mechanism||[]).map(m=>`<li>${esc(m)}</li>`).join('')}</ul>
    <h4>what the live page writes to your clipboard</h4>
    <div class="mono">${esc(FACTS.clipboard_payload)} &lt;one of ${(FACTS.phishing_links||[]).length} links&gt;</div>
    <div class="doms">${(FACTS.typosquatted_domains||[]).map(d=>`<span class="dom">${esc(d)}</span>`).join('')}</div>
    <h4>here, defanged</h4>
    <ul><li>${esc((FACTS.defanged||{}).behaviour)}</li>
        <li>payload preserved unrun at <code>${esc((FACTS.defanged||{}).preserved_unrun)}</code> -
            <a href="${B}/plinyworld/payload" target="_blank" rel="noopener">read it as text</a></li></ul>`;
}

// the MCP panel: one connection, every repo as its own tool.
let MCP=null;
async function toggleMcp(){
  const el=document.getElementById('mcp');
  el.classList.toggle('on');
  if(!el.classList.contains('on')||el.dataset.ready) return;   // the strip preloads MCP
  try{ MCP=MCP||await api('/tools?all=1'); }catch(e){ el.innerHTML='<div class="loading">'+esc(e.message)+'</div>'; return; }
  el.dataset.ready='1';
  const names=(MCP.tools||[]).map(t=>t.name);
  const repo=names.filter(n=>!n.match(/^pv_(info|repos|repo|readme|tree|file|search|exhibit|update|status|market|install)$/));
  const url=location.origin+B+'/api/mcp/all';
  el.innerHTML=`
    <h4 style="color:var(--accent2)">${names.length} tools on one endpoint</h4>
    <ul><li><b>${repo.length}</b> repo tools - one per elder-plinius repo, named after it
      (${repo.slice(0,3).map(n=>'<code>'+esc(n)+'</code>').join(', ')}...). Each takes
      <code>op=readme|tree|file|search|info|install</code>: reads the archived copy when the repo
      is installed, live from GitHub otherwise, and archives on demand to grep.</li>
    <li><b>${names.length-repo.length}</b> corpus tools - <code>pv_repos</code>, <code>pv_search</code>,
      <code>pv_market</code>, <code>pv_exhibit</code>... when you do not know which repo holds the thing.</li></ul>
    <div class="mono">${esc(url)}</div>
    <div class="mono" style="margin-top:6px;color:color-mix(in srgb,var(--accent) 60%,var(--text))">claude mcp add --transport http plinyville ${esc(url)}</div>
    <div class="sub" style="margin-top:9px">stdio: <code>python3 mcp.py --all</code> ·
      the ${esc(String(names.length-repo.length))}-tool core stays at <code>${esc(location.origin+B)}/api/mcp</code></div>`;
}

// ── the chat: the claude agent, reading this corpus ────────────────────────
// The type pills are the fence, not a hint: they go to the server as `types`,
// which starts the agent's MCP server scoped to those repos, so a jailbreak
// question cannot open anything else. Every tool call arrives as it happens.
function toggleChat(){
  const el=document.getElementById('chat');
  el.classList.toggle('on');
  if(el.classList.contains('on')){ scopeLine(); document.getElementById('cq').focus(); }
}
function scopeLine(){
  const el=document.getElementById('cscope'); if(!el) return;
  const sel=[...SEL];
  const n=REPOS.filter(typeOk).length;
  el.innerHTML=sel.length
    ? 'scope: <b>'+esc(sel.map(t=>TLABEL[t]||t).join(' + '))+'</b> - '+n
      +' repos. The agent\'s tools refuse everything else; press ALL to open the corpus.'
    : 'scope: <b>the whole corpus</b> - '+(REPOS.length||47)
      +' repos. Press a type above to fence the question to one sort.';
}
function newChat(){
  CHAT_SESSION=null;
  document.getElementById('csteps').innerHTML='';
  document.getElementById('ccites').innerHTML='';
  const a=document.getElementById('canswer'); a.style.display='none'; a.innerHTML='';
  scopeLine();
}
function step(text, cls){
  const el=document.getElementById('csteps');
  const d=document.createElement('div');
  d.className='step'+(cls?' '+cls:''); d.textContent=text;
  el.appendChild(d); el.scrollTop=el.scrollHeight;
}
// markdown-lite: this answer is prose with paths in it, and the paths are the
// part worth clicking. Everything is escaped first.
function mdlite(t){
  return esc(t)
    .replace(/`([^`]+)`/g,(m,c)=>'<code>'+c+'</code>')
    .replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
}
async function askAgent(){
  if(ASKING) return;
  const inp=document.getElementById('cq'), q=(inp.value||'').trim();
  if(!q) return;
  ASKING=true;
  const btn=document.getElementById('askbtn');
  btn.disabled=true; btn.classList.add('cur'); btn.textContent='READING';
  const ans=document.getElementById('canswer');
  document.getElementById('csteps').innerHTML='';
  document.getElementById('ccites').innerHTML='';
  ans.style.display='none'; ans.innerHTML='';
  step('asked: '+q);
  const body={question:q, types:[...SEL], session:CHAT_SESSION||undefined};
  try{
    const r=await fetch(B+'/api/chat/stream',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok||!r.body){ const j=await r.json().catch(()=>({})); throw new Error(j.error||('http '+r.status)); }
    const rd=r.body.getReader(), dec=new TextDecoder(); let buf='', text='';
    for(;;){
      const {value,done}=await rd.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i;
      while((i=buf.indexOf('\n\n'))>=0){
        const line=buf.slice(0,i).trim(); buf=buf.slice(i+2);
        if(!line.startsWith('data:')) continue;
        let e; try{ e=JSON.parse(line.slice(5)); }catch(_){ continue; }
        if(e.type==='start') step('scope: '+(e.repos==null?'the whole corpus':e.repos+' repos'
            +(e.scope&&e.scope.length?' - '+e.scope.join(' + '):''))+' - model '+e.model);
        else if(e.type==='tool') step('reading - '+e.tool+' '+JSON.stringify(e.input||{}).slice(0,140));
        else if(e.type==='refused') step(e.error,'err');
        else if(e.type==='text') text=e.text;
        else if(e.type==='error') { step(e.error,'err'); ans.style.display='block';
          ans.innerHTML='<span style="color:var(--danger)">'+esc(e.error)+'</span>'; }
        else if(e.type==='done'){
          CHAT_SESSION=e.session||null;
          ans.style.display='block';
          // A declined run is the runtime talking, not the corpus. This wall is
          // jailbreak prompts and leaked system prompts, so the model does
          // sometimes stop; printing its error in the answer box unmarked would
          // read as if the repos had said it.
          ans.innerHTML=(e.declined?'<div class="sub" style="margin-bottom:9px;color:var(--warn)">'
              +'the model declined this one - what follows is its runtime\'s message, '
              +'not something read out of the corpus</div>':'')
            +mdlite(e.answer||text||'(no answer)')
            +((e.grounded||e.declined)?'':'<div class="sub" style="margin-top:9px;color:var(--warn)">'
              +'it answered without opening a file - that is its opinion, not the corpus</div>')
            +((e.declined&&(e.read_count||0))?'<div class="sub" style="margin-top:9px">'
              +'it had already read '+(e.read_count)+' thing'+(e.read_count>1?'s':'')
              +' - those are listed below</div>':'');
          const seen=[];
          (e.reads||[]).forEach(rd2=>{ const n=(rd2.input||{}).name;
            if(n&&!seen.includes(n)) seen.push(n); });
          document.getElementById('ccites').innerHTML=seen.map(n=>
            '<a class="cite" href="'+B+'/m/'+encodeURIComponent(n)+'">'+esc(n)+' &gt;</a>').join('')
            +'<span class="cite" style="border-style:dashed">'+(e.read_count||0)+' reads - '
            +((e.duration_ms||0)/1000).toFixed(1)+'s'+(e.turns?' - '+e.turns+' turns':'')+'</span>';
          step('done - '+(e.read_count||0)+' tool calls');
        }
      }
    }
    inp.value='';
  }catch(err){
    step(String(err.message||err),'err');
  }finally{
    ASKING=false; btn.disabled=false; btn.classList.remove('cur'); btn.textContent='ASK >';
  }
}

// The ASK strip only promises what the host can deliver: no claude on the box,
// no button that spins forever.
async function chatCard(){
  try{
    const c=await api('/chat');
    const on=((c.agent||{}).available!==false);
    document.getElementById('askn').innerHTML=on
      ? 'the <b>claude</b> agent reads these repos for you - '+((c.agent||{}).tools||[]).length
        +' tools, scoped by the type you pick'
      : 'no claude agent on this host - '+esc((c.agent||{}).why_not||'unavailable');
    if(!on) document.getElementById('askstrip').className='strip warn';
  }catch(e){}
}

async function mcpCount(){
  try{
    MCP=await api('/tools?all=1');
    document.getElementById('mcpn').textContent=MCP.tools.length+' tools · every repo, its own';
  }catch(e){ MCP=null; }
}

// ── freshness: a cron job scans once a day and leaves a receipt; this reads it.
// A mirror that cannot say how old it is is worse than one that says "stale".
let STATUS=null;
const CLASS={ok:'ok', stale:'stale', failed:'bad', never:'stale'};
// the header is set in a face that is ten pixels wide per character, so the
// pill says the state in as few of them as it can and the tooltip says the
// rest. "SCAN OK 15H" is the whole receipt at a glance.
const MARK={ok:'OK', stale:'STALE', failed:'FAILED', never:'NEVER'};
async function loadStatus(){
  const p=document.getElementById('fresh'), c=document.getElementById('cid');
  let j; try{ j=await api('/status'); }catch(e){ p.className='pill bad'; p.textContent='SCAN ?'; return; }
  // a receipt carries the *last scan's* error in j.error — that is data, not a
  // failed request. Only a reply with no state at all is a broken status route.
  if(!j.state){ p.className='pill bad'; p.textContent='SCAN ?'; p.title=j.error||''; return; }
  STATUS=j;
  p.className='pill '+(CLASS[j.state]||'');
  p.textContent='SCAN '+(MARK[j.state]||'?')
    +(j.age?' '+String(j.age).replace(/\s*ago$/,'').toUpperCase():'');
  const next=j.next_scan_iso?('next daily scan '+j.next_scan_iso):'no scan scheduled';
  const good=j.state==='ok'||j.state==='stale';   // a failed scan counted nothing
  p.title=[!j.last_scan?'never scanned'
      :good?('last scan '+j.last_scan_iso+' · '+j.repos+' repos'
        +(j.changes?' · '+j.changes+' changed':' · nothing changed'))
      :('last scan '+j.last_scan_iso+' · did not finish'),
    j.error?('error: '+j.error):null,
    (j.cron&&j.cron.installed)?('cron '+j.cron.schedule+' · '+next):'cron not installed — m pliny/cron',
    'click to scan now'].filter(Boolean).join('\n');
  if(j.cid){ c.textContent='CID '+j.cid_short; c.title='module CID '+j.cid+'\nclick to copy'; c.style.display=''; }
  else { c.style.display='none'; }
}

async function rescan(){
  const p=document.getElementById('fresh');
  p.className='pill'; p.textContent='SCANNING'; p.classList.add('cur');
  try{
    const j=await apiPost('/scan');
    STATUS=j.status||null;
    p.classList.remove('cur');
    await loadStatus();
    if(j.scan&&!j.scan.ok) return;
    load(true);                       // a scan may have added or dropped repos
  }catch(e){ p.classList.remove('cur'); p.className='pill bad'; p.textContent='SCAN FAILED'; }
}

function copyCid(){
  if(!STATUS||!STATUS.cid) return;
  const c=document.getElementById('cid'), was=c.textContent;
  navigator.clipboard.writeText(STATUS.cid).then(()=>{
    c.textContent='COPIED'; setTimeout(()=>{c.textContent=was;},1200);
  }).catch(()=>{});
}

load(); mcpCount(); loadStatus(); loadRate(); chatCard();
// #chat in the URL opens the agent straight away (the arcade links do the same for #run)
if(location.hash==='#chat') toggleChat();
</script>
</body>
</html>
""")


# ── one market mod's app: a file browser over /m/<repo> ─────────────────────

MOD_HTML = _skin(r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>plinyville · mod</title>
<style>
/*THEME_CSS*/
  header{position:sticky;top:0;z-index:20;background:var(--hdr);
    border-bottom:4px solid var(--line2);padding:10px 14px}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .brand{display:flex;align-items:center;gap:11px;min-width:0;
    font:13px/1 var(--f-hd);color:var(--accent);text-shadow:3px 3px 0 var(--line)}
  .brand .spr{color:var(--accent2)}
  .brand a{text-decoration:none}
  .brand .sub{font:16px/1.2 var(--f-bd);color:var(--muted);text-shadow:none;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .grow{flex:1}
  main{max-width:1240px;margin:0 auto;padding:14px 14px 48px;display:grid;
    grid-template-columns:300px 1fr;gap:13px;align-items:stretch;min-height:calc(100vh - 110px)}
  @media(max-width:900px){main{grid-template-columns:1fr}}
  .panel{padding:12px 13px}
  /* the mod's own title bar, same cartridge look as its card in the market */
  .hd{display:flex;align-items:center;gap:9px;margin:-12px -13px 11px;padding:10px;
    background:var(--accent);color:var(--on-accent);border-bottom:var(--bw) solid var(--line)}
  h1{flex:1;min-width:0;font:12px/1.4 var(--f-hd);margin:0;word-break:break-word}
  .desc{color:var(--muted);font-size:17px;line-height:1.3;margin-bottom:9px}
  .desc:empty{display:none}
  .runnote.warn{color:var(--warn)}
  .ttags{display:inline-flex;gap:5px;flex-wrap:wrap}
  .ttag{font:8px/1 var(--f-hd);color:var(--accent2);background:var(--panel2);
    border:var(--bw) solid var(--line);padding:5px 6px;cursor:help}
  .kv{display:flex;gap:13px;font:15px/1 var(--f-bd);color:var(--faint);flex-wrap:wrap;
    align-items:center;margin-bottom:9px}
  .kv .spr{margin-right:6px;vertical-align:-3px}
  .kv .lang{color:var(--accent2)}
  .badge{flex:none;font:8px/1 var(--f-hd);padding:5px 6px;white-space:nowrap;
    border:2px solid var(--on-accent);background:var(--on-accent);color:var(--accent)}
  .badge.off{background:transparent;color:var(--on-accent);opacity:.72}
  .lbl{margin:13px 0 5px}
  .lbl:first-child{margin-top:0}
  .wire{font:14px/1.7 var(--f-bd);background:var(--sunk);border:var(--bw) solid var(--line);
    padding:9px 10px;color:color-mix(in srgb,var(--accent) 65%,var(--text));
    overflow:auto;word-break:break-all;margin:6px 0 0}
  .wire b{color:var(--accent2)}
  .tree{list-style:none;margin:0;padding:0;font:16px/1.55 var(--f-bd);max-height:62vh;overflow:auto}
  .tree li{padding:2px 7px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
    border:var(--bw) solid transparent}
  .tree li:hover{background:var(--panel2);border-color:var(--line)}
  .tree li.dir{color:var(--accent2)}
  .tree li .st{color:var(--green);margin-left:5px}
  pre.file{white-space:pre-wrap;word-break:break-word;font:16px/1.4 var(--f-bd);
    color:var(--muted);margin:0;max-height:72vh;overflow:auto}
  .path{font:15px/1 var(--f-bd);color:var(--faint);margin-bottom:9px;
    border-bottom:var(--bw) solid var(--line);padding-bottom:9px;word-break:break-all}
  /* RUN: the repo's own page, in a box. The frame is flush to the panel edge -
     it is someone else's design and our chrome stops at the border. */
  /* the right column stretches so the frame gets the whole page, not 74% of it */
  main>div:last-child{display:flex;flex-direction:column;min-width:0}
  #stage{padding:0;display:flex;flex-direction:column;flex:1;min-height:74vh}
  .stagebar{display:flex;gap:8px;align-items:center;padding:8px 9px;
    border-bottom:var(--bw) solid var(--line);background:var(--panel2)}
  .stagebar select{font:9px/1 var(--f-hd);background:var(--sunk);color:var(--text);
    border:var(--bw) solid var(--line);padding:6px 7px;max-width:min(46vw,420px)}
  #frame{flex:1;width:100%;border:0;background:#fff;min-height:66vh;display:block}
  .runnote{font:16px/1.35 var(--f-bd);color:var(--muted);margin:7px 0 0}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
  .chip{font:8px/1.35 var(--f-hd);padding:5px 6px;border:2px solid var(--line);
    background:color-mix(in srgb,var(--accent) 13%,var(--panel));color:var(--text)}
  .chip.hot{border-color:var(--warn);color:var(--warn);
    background:color-mix(in srgb,var(--warn) 13%,var(--panel))}
  .chip.host{background:var(--panel);color:var(--faint);font:14px/1.2 var(--f-bd)}
</style>
<script>
/*THEME_JS*/
</script>
</head>
<body>
<header>
  <div class="row">
    <div class="brand"><span class="spr spr-inv"></span><a href="../">PLINYVILLE</a>
      <span class="sub">/ <span id="modname">mod</span></span></div>
    <div class="grow"></div>
    <a class="btn" href="../">&lt; MARKET</a>
    <a class="btn" id="gh" target="_blank" rel="noopener">GITHUB</a>
    <button id="themepill" class="pill" onclick="toggleThemes(event)" title="skin">PLINY</button>
    <button class="btn primary" id="install" onclick="install()" style="display:none">+ INSTALL</button>
  </div>
  <div id="themes" class="themes"></div>
</header>
<main>
  <div>
    <div class="panel">
      <div class="hd"><h1 id="title">LOADING</h1>
        <span class="badge off" id="state">?</span></div>
      <div class="desc" id="desc"></div>
      <div class="kv" id="kv"></div>
      <div class="lbl">run it</div>
      <div id="runbox"><div class="loading cur">looking for a page</div></div>
      <div class="lbl">this mod's backend</div>
      <div class="wire" id="wire"></div>
    </div>
    <div class="panel" style="margin-top:13px">
      <div class="lbl">files</div>
      <ul class="tree" id="tree"><li class="loading cur">reading</li></ul>
    </div>
  </div>
  <div>
    <div class="panel" id="reader">
      <div class="path" id="path">select a file</div>
      <pre class="file" id="file">This mod's app reads from its own api at <span id="apibase"></span>.
Its MCP server is one POST away - see the backend panel on the left.</pre>
    </div>
    <div class="panel" id="stage" style="display:none">
      <div class="stagebar">
        <button class="btn ghost" onclick="stopRun()">&lt; FILES</button>
        <select id="entry" onchange="startRun(this.value)" title="entry point"></select>
        <div class="grow"></div>
        <a class="btn ghost" id="newtab" target="_blank" rel="noopener">NEW TAB</a>
      </div>
      <!-- The same sandbox the api sets as a header. The attribute is the
           belt; the header is the braces, and it is the header that holds when
           someone opens the page directly. No allow-same-origin, ever: this
           host shares one origin across every mod on it. -->
      <iframe id="frame" title="the repo, running"
        sandbox="allow-scripts allow-forms allow-modals allow-pointer-lock allow-downloads"></iframe>
    </div>
  </div>
</main>
<script>
const B = location.pathname.replace(/\/m\/.*$/,'');
const NAME = decodeURIComponent(location.pathname.split('/m/')[1]||'').replace(/\/$/,'');
const mapi = (p) => fetch(B+'/api/m/'+encodeURIComponent(NAME)+p).then(r=>r.json());
const mpost = (p) => fetch(B+'/api/m/'+encodeURIComponent(NAME)+p,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json());
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
let INSTALLED=false;

async function boot(){
  document.getElementById('modname').textContent=NAME;
  document.getElementById('apibase').textContent=B+'/api/m/'+NAME;
  let m; try{ m=await mapi(''); }catch(e){ document.getElementById('title').textContent=NAME; return; }
  document.getElementById('title').textContent=m.title||NAME;
  document.getElementById('desc').textContent=m.description||'';
  document.getElementById('gh').href=m.upstream||('https://github.com/elder-plinius/'+NAME);
  document.getElementById('kv').innerHTML=[
    m.language?'<span class="lang">'+esc(m.language)+'</span>':'',
    '<span class="spr spr-star"></span>'+(m.stars||0),
    m.default_branch?('branch '+esc(m.default_branch)):''
  ].filter(Boolean).map(x=>'<span>'+x+'</span>').join('');
  INSTALLED=!!m.installed;
  // the badge says which of the two backends this page is talking to, and the
  // title says the rest: archived = the store, otherwise live GitHub calls.
  document.getElementById('state').outerHTML = INSTALLED
    ? '<span class="badge" id="state" title="archived into the store">MOD '+(m.files_stored||0)+'</span>'
    : '<span class="badge off" id="state" title="not archived - read live from github">LIVE</span>';
  document.getElementById('install').style.display = INSTALLED?'none':'';
  const api=B+'/api/m/'+NAME;
  document.getElementById('wire').innerHTML=
    '<b>app</b>  '+B+'/m/'+NAME+'<br><b>api</b>  '+api
    +'<br><b>mcp</b>  POST '+api+'/mcp'
    +(m.cid?'<br><b>cid</b>  '+esc(m.cid):'');
  loadTree('');
  loadRun();
  loadKinds();
}

// ── RUN: this repo, as the page it actually is ──────────────────────────────
// A third of the corpus is not prose, it is an app. The mod page can read the
// source of one; this runs it - out of the clone, inside a sandbox, with what
// the audit found printed next to the button rather than after the fact.
let RUN=null;
function runUrl(p){ return B+'/m/'+encodeURIComponent(NAME)+'/run/'
  +String(p).split('/').map(encodeURIComponent).join('/'); }

async function loadRun(){
  const el=document.getElementById('runbox');
  let m; try{ m=await mapi('/run'); }catch(e){
    el.innerHTML='<div class="runnote">'+esc(e.message)+'</div>'; return; }
  RUN=m;
  if(m.defanged){
    el.innerHTML='<a class="btn primary" href="'+esc(m.run_url)+'" target="_blank" rel="noopener">RUN DEFANGED &gt;</a>'
      +'<div class="runnote">'+esc(m.why)+'. '+esc(m.note)+'.</div>';
    return;
  }
  if(!m.runnable){
    el.innerHTML='<div class="runnote">'+esc(m.note||'nothing here runs in a browser')+'.</div>'
      +((m.needs_build&&m.entry)?'<div class="chips"><span class="chip">'+esc(m.entry)+'</span></div>':'');
    return;
  }
  const a=m.audit||{};
  const chips=[].concat(
    (a.touches||[]).map(t=>'<span class="chip hot" title="the page\'s own scripts reach for this">'+esc(t)+'</span>'),
    (a.services||[]).map(t=>'<span class="chip" title="a back end it will look for">'+esc(t)+'</span>'),
    (a.hosts||[]).slice(0,6).map(h=>'<span class="chip host" title="a host it loads from or calls">'+esc(h)+'</span>')
  ).join('');
  // What the health check found: a script upstream left mid-merge (repaired,
  // and said so), a script that will not compile at all, a back end of its own
  // that is not running here. Better on the button than discovered by clicking.
  const notes=[]
    .concat(m.degraded_why?['<b>heads up</b> - '+esc(m.degraded_why)]:[])
    .concat((m.repairs||[]).filter(r=>r.conflicts).map(r=>
      esc(r.file)+' was left mid-merge upstream; served with the HEAD side kept'))
    .join('<br>');
  el.innerHTML='<button class="btn primary" onclick="startRun()">RUN &gt;</button>'
    +'<div class="runnote">'+(m.count>1?esc(m.count)+' pages, starting at ':'')
    +'<b>'+esc(m.entry)+'</b> - sandboxed: an origin of its own, no reach into this host.</div>'
    +(notes?'<div class="runnote warn">'+notes+'</div>':'')
    +(chips?'<div class="chips">'+chips+'</div>':'');
  if(location.hash==='#run') startRun();
}

function startRun(entry){
  if(!RUN||!RUN.runnable) return;
  const e=entry||RUN.entry;
  const sel=document.getElementById('entry');
  if(!sel.options.length){
    sel.innerHTML=(RUN.entries||[]).filter(x=>x.status==='ready')
      .map(x=>'<option value="'+esc(x.path)+'">'+esc(x.title||x.path)+' - '+esc(x.path)+'</option>').join('');
  }
  sel.value=e;
  const u=runUrl(e);
  document.getElementById('frame').src=u;
  document.getElementById('newtab').href=u;
  document.getElementById('reader').style.display='none';
  document.getElementById('stage').style.display='';
  if(location.hash!=='#run') history.replaceState(null,'','#run');
}

function stopRun(){
  document.getElementById('frame').src='about:blank';
  document.getElementById('stage').style.display='none';
  document.getElementById('reader').style.display='';
  if(location.hash==='#run') history.replaceState(null,'',location.pathname+location.search);
}

let CWD='';
// what sort of repo this is, and the evidence for it — the same call the
// gallery's type pills are built from, asked about one repo.
async function loadKinds(){
  let k; try{ k=await mapi('/types'); }catch(e){ return; }
  if(!k || !k.types || !k.types.length) return;
  const why=t=>((k.why||{})[t]||[]).map(w=>w.word+' in the '+w.in).join(', ');
  document.getElementById('kv').innerHTML +=
    '<span class="ttags">'+k.types.map(t=>'<span class="ttag" title="'+esc(why(t)
      ||k.pinned||'')+'">'+esc((k.labels||[])[k.types.indexOf(t)]||t)+'</span>').join('')
    +'</span>';
}

async function loadTree(path){
  CWD=path||'';
  const el=document.getElementById('tree');
  el.innerHTML='<li class="loading cur">reading</li>';
  let j; try{ j=await mapi('/tree'+(path?'?path='+encodeURIComponent(path):'')); }
  catch(e){ el.innerHTML='<li class="loading">'+esc(e.message)+'</li>'; return; }
  if(j.error){ el.innerHTML='<li class="loading">'+esc(j.error)+'</li>'; return; }
  let rows='';
  if(path){ const parent=path.split('/').slice(0,-1).join('/');
    rows+='<li class="dir" data-dir="'+esc(parent)+'">^ ..</li>'; }
  rows+=(j.entries||[]).map(e=>e.type==='dir'
    ? '<li class="dir" data-dir="'+esc(e.path)+'">&gt; '+esc(e.name)+'</li>'
    : '<li data-file="'+esc(e.path)+'">'+esc(e.name)+(e.stored?'<span class="st" title="in the store">*</span>':'')+'</li>').join('');
  el.innerHTML=rows||'<li class="loading">empty</li>';
}
document.getElementById('tree').addEventListener('click',ev=>{
  const li=ev.target.closest('li'); if(!li) return;
  if(li.dataset.dir!==undefined) loadTree(li.dataset.dir);
  else if(li.dataset.file) openFile(li.dataset.file);
});

async function openFile(path){
  document.getElementById('path').textContent=path;
  document.getElementById('file').textContent='loading...';
  const j=await mapi('/file?path='+encodeURIComponent(path));
  document.getElementById('file').textContent = j.text!=null ? j.text : (j.note||j.error||'(no text)');
}

async function install(){
  const b=document.getElementById('install');
  b.textContent='ARCHIVING'; b.classList.add('cur'); b.disabled=true;
  try{ const j=await mpost('/install'); if(j.error) throw new Error(j.error); b.classList.remove('cur'); boot(); }
  catch(e){ b.classList.remove('cur'); b.textContent='FAILED'; b.disabled=false; alert(e.message); }
}
boot();
</script>
</body>
</html>
""")


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
