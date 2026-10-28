"""Server for the artist studio: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

    /artist/*             → the studio (prefix kept by the gateway; stripped here)
    /artist/api/{fn}      → the API, as the studio calls it
    /api/artist/{fn}      → the API (prefix stripped by the gateway)
    /{fn}                 → the API, bare, for local curl
    /artist/media/{id}    → one asset's bytes, with Range support (video seek)

Reads are GET with query args. Writes are POST: JSON bodies, except
``upload`` which takes the file's raw bytes (?name=clip.mp4). Deletes only
answer a loopback caller: anyone you share the studio with can cut and save,
only this box can destroy.

    python3 serve.py [--port 51140] [--host 0.0.0.0]
"""

import argparse
import json
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/artist'
MAX_BODY = 512 * 1024 * 1024  # media files are big

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import studio as studiomod  # noqa: E402

READ_FNS = ('health', 'info', 'readme', 'assets', 'projects', 'project',
            'export_pack')
WRITE_FNS = ('upload', 'save_project', 'import_pack', 'render',
             'remove_project', 'remove_asset')
LOCAL_ONLY = ('remove_asset',)

MIME = {'mp4': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime',
        'mkv': 'video/x-matroska', 'avi': 'video/x-msvideo', 'm4v': 'video/mp4',
        'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg',
        'oga': 'audio/ogg', 'm4a': 'audio/mp4', 'flac': 'audio/flac',
        'aac': 'audio/aac', 'opus': 'audio/opus',
        'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp'}

_studio = None


def get_studio():
    global _studio
    if _studio is None:
        _studio = studiomod.Studio()
    return _studio


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def api(fn, a):
    s = get_studio()
    if fn == 'health':
        return {'ok': True, **s.stats()}
    if fn == 'info':
        cfg = _config()
        return {'name': 'artist', 'version': cfg.get('version'),
                'description': cfg.get('description'), 'stats': s.stats(),
                'endpoints': cfg.get('endpoints', {})}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    if fn == 'assets':
        return {'assets': s.assets(a.get('kind', ''))}
    if fn == 'projects':
        return {'projects': s.projects()}
    if fn == 'project':
        return s.get_project(a.get('id', '')) or {'error': 'no such project'}
    if fn == 'export_pack':
        return s.export_pack(a.get('id', ''))
    if fn == 'save_project':
        return s.save_project(a.get('name', ''), a.get('timeline') or {},
                              a.get('id'))
    if fn == 'import_pack':
        return s.import_pack(a.get('pack', a))
    if fn == 'render':
        return s.render(a.get('id', ''), int(a.get('width', 1280)),
                        int(a.get('height', 720)), int(a.get('fps', 30)))
    if fn == 'remove_project':
        return s.remove_project(a.get('id', ''))
    if fn == 'remove_asset':
        return s.remove_asset(a.get('id', ''))
    return None


class Handler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):  # quiet — pm2 keeps the logs
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _fn(self, path, allowed):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in allowed:
                return path[len(pre):]
        return None

    def _run(self, fn, args):
        try:
            out = api(fn, args)
        except (ValueError, KeyError) as e:
            return self._json({'error': str(e)}, 400)
        except Exception as e:  # noqa: BLE001 — the caller gets the reason
            # 4xx, not 5xx: Cloudflare swaps 5xx bodies for its own page.
            return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
        return self._json(out)

    # ── media: asset bytes with Range, so <video> can seek ──────

    def _media(self, aid):
        s = get_studio()
        a = s.get_asset(aid)
        path = s.asset_path(aid, a['ext']) if a else None
        if not path or not os.path.exists(path):
            return self._json({'error': 'no such asset'}, 404)
        size = os.path.getsize(path)
        ctype = MIME.get(a['ext'], 'application/octet-stream')
        start, end = 0, size - 1
        rng = self.headers.get('Range')
        m = re.match(r'bytes=(\d*)-(\d*)$', rng or '')
        partial = bool(m and (m.group(1) or m.group(2)))
        if partial:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:  # suffix range: last N bytes
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:  # player closed mid-stream — normal
                    return
                left -= len(chunk)

    # ── verbs ────────────────────────────────────────────────────

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        fn = self._fn(path, WRITE_FNS)
        if not fn:
            return self._json({'error': 'not found'}, 404)
        # A gateway on this box also connects from loopback, so a forwarded
        # request is treated as remote whatever its socket says.
        local = (self.client_address[0] in ('127.0.0.1', '::1')
                 and not self.headers.get('X-Forwarded-For'))
        if fn in LOCAL_ONLY and not local:
            return self._json({'error': f'{fn} is local-only'}, 403)
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            return self._json({'error': 'body too large'}, 413)
        if fn == 'upload':  # raw bytes in, ?name= carries the filename
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            name = q.get('name') or self.headers.get('X-Name') or ''
            data = self.rfile.read(n)
            try:
                return self._json(get_studio().add_asset(name, data))
            except Exception as e:  # noqa: BLE001
                return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
        try:
            args = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._json({'error': 'body must be JSON'}, 400)
        if not isinstance(args, dict):
            args = {'pack': args}
        return self._run(fn, args)

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        path = raw.rstrip('/') or '/'

        for pre in (f'{PREFIX}/media/', '/media/'):
            if path.startswith(pre):
                return self._media(path[len(pre):])

        fn = self._fn(path, READ_FNS)
        if fn:
            args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            return self._run(fn, args)

        # The console. Bare /artist + relative asset paths is the gateway trap.
        if raw == PREFIX:
            self.send_response(301)
            self.send_header('Location', PREFIX + '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if raw.startswith(PREFIX + '/'):
            self.path = self.path[len(PREFIX):]
        if urlparse(self.path).path.rstrip('/') in ('', '/'):
            self.path = '/index.html'
        return super().do_GET()


def serve(port=None, host='0.0.0.0'):
    port = int(port or _config().get('port', 51140))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'artist: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/info')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
