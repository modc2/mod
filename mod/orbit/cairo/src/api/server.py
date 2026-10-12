"""server.py — stdlib HTTP API + console for cairo. No framework, one port.

Mod protocol URL shapes, all on :51291:

  GET  /cairo                -> the console (static Next.js export, src/dist)
  GET  /cairo/_next/*        -> the export's static assets
  *    /cairo/api/<fn>       -> the JSON protocol API (canonical; the gateway
                                strips the prefix, so bare /<fn> works too)
  *    /api/cairo/<fn>       -> legacy alias of the same API
  *    /cairo/_api/<fn>      -> the app's own self-calls (gateway KEEPS the
                                prefix); mapped onto the same handlers.

All contract logic lives in src/mod.py (the anchor). This file maps URLs onto
Mod methods, ships the console, and adds one thing the CLI doesn't need: a job
table, because vibe (minutes) and a cold build (~60s) outlive any sane HTTP
timeout. POST any of LONG_FNS with {"async": true} -> {"job": id}; poll
GET /job?id=<id>.

Binds 127.0.0.1 by default: write/vibe edit files and spend the host Claude
credentials, and there is no auth here. CAIRO_HOST=0.0.0.0 to widen.
"""
import importlib.util
import json
import mimetypes
import os
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SRC = Path(__file__).resolve().parent.parent

# Load cairo's OWN mod.py by path — a bare `import mod` resolves to the
# protocol's `mod` package. See [[mod-import-shadowing]].
_spec = importlib.util.spec_from_file_location('cairo_mod', SRC / 'mod.py')
modpy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modpy)

MOD = modpy.Mod()
NAME = 'cairo'
PORT = int(os.environ.get('CAIRO_PORT', MOD.port))
HOST = os.environ.get('CAIRO_HOST', '127.0.0.1')
DIST = SRC / 'dist'  # Next export; a symlink to src/releases/<ts> (build.sh)

GET_FNS = {
    'health': lambda q: MOD.health(),
    'info': lambda q: MOD.info(),
    'status': lambda q: MOD.status(),
    'toolchain': lambda q: MOD.toolchain(),
    'readme': lambda q: {'readme': MOD.readme()},
    'list': lambda q: MOD.list(),
    'contracts': lambda q: MOD.list(),
    'get': lambda q: MOD.get(q.get('name', '')),
    'tree': lambda q: MOD.tree(q.get('name', '')),
    'read': lambda q: MOD.read(q.get('name', ''), q.get('path', '')),
    'jobs': lambda q: {'jobs': _jobs_list()},
    'job': lambda q: _job_get(q.get('id', '')),
}
POST_FNS = {'info', 'status', 'toolchain', 'list', 'contracts', 'get', 'tree',
            'read', 'write', 'new', 'vibe', 'build', 'test', 'clean', 'remove'}
LONG_FNS = {'vibe', 'build', 'test', 'status'}

# ---- jobs -------------------------------------------------------------------

JOBS = {}
JOBS_LOCK = threading.Lock()
# one lock per contract: scarb/snforge/claude all write target/ and the
# sources, so two runs on the same submod must queue, not interleave
NAME_LOCKS = {}


def _name_lock(name):
    with JOBS_LOCK:
        return NAME_LOCKS.setdefault(name or '*', threading.Lock())


def _public(j):
    return {k: v for k, v in j.items() if not k.startswith('_')}


def _jobs_list():
    with JOBS_LOCK:
        return sorted((_public(j) for j in JOBS.values()),
                      key=lambda j: j['created'], reverse=True)[:50]


def _job_get(jid):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        return _public(j) if j else {'error': f'no such job {jid!r}'}


def _start_job(fn, kw):
    jid = uuid.uuid4().hex[:12]
    name = kw.get('name')
    job = {'id': jid, 'fn': fn, 'name': name, 'state': 'queued',
           'created': time.time(), 'started': None, 'finished': None,
           'result': None, 'error': None}
    with JOBS_LOCK:
        JOBS[jid] = job
        # keep the table bounded: drop the oldest finished jobs
        done = sorted((j for j in JOBS.values() if j['finished']),
                      key=lambda j: j['finished'])
        for old in done[:max(0, len(JOBS) - 200)]:
            JOBS.pop(old['id'], None)

    def run():
        with _name_lock(name):
            job['state'], job['started'] = 'running', time.time()
            try:
                job['result'] = getattr(MOD, fn)(**kw)
                job['state'] = 'done'
            except Exception as e:  # noqa: BLE001
                job['error'] = str(e)
                job['state'] = 'error'
                traceback.print_exc()
            job['finished'] = time.time()

    threading.Thread(target=run, daemon=True).start()
    return _public(job)


# ---- routing ----------------------------------------------------------------

def _route(path):
    """Collapse every gateway URL form to a bare fn name.

    /cairo/api/build  /cairo/_api/build  /api/cairo/build  /build -> "build"
    """
    parts = [p for p in path.split('/') if p and p not in (NAME, 'api', '_api')]
    return parts[-1] if parts else ''


def _is_api_path(path):
    segs = [p for p in path.split('/') if p]
    return bool(segs) and ('api' in segs[:2] or '_api' in segs[:2]
                           or (len(segs) == 1 and segs[0] in GET_FNS))


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, (bytes, bytearray)) else \
            json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b'')

    def _serve_static(self, path):
        """dist/<route>, SPA-falling back to index.html. A 200 note (not 5xx:
        Cloudflare strips 5xx bodies) when the console hasn't been built."""
        rel = '/'.join(p for p in path.split('/') if p and p != NAME)
        if not DIST.exists():
            return self._send(200, b'<h1>cairo</h1><p>Console not built yet. '
                              b'Run <code>bash src/build.sh</code>. The API is '
                              b'live at <code>/cairo/api/info</code>.</p>',
                              'text/html')
        target = (DIST / rel) if rel else (DIST / 'index.html')
        if target.is_dir():
            target = target / 'index.html'
        if not target.exists() and target.with_suffix('.html').exists():
            target = target.with_suffix('.html')
        if not target.exists() and not rel.startswith('_next'):
            target = DIST / 'index.html'
        try:
            target.resolve().relative_to(DIST.resolve())
        except ValueError:
            return self._send(404, {'error': 'not found'})
        if not target.exists():
            return self._send(404, {'error': 'not found'})
        ctype = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        return self._send(200, target.read_bytes(), ctype)

    def do_GET(self):
        p = urlparse(self.path)
        if not _is_api_path(p.path):
            return self._serve_static(p.path)
        fn = _route(p.path) or 'info'
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        if fn not in GET_FNS:
            return self._send(404, {'error': f'unknown fn {fn!r}',
                                    'get': sorted(GET_FNS),
                                    'post': sorted(POST_FNS)})
        try:
            return self._send(200, GET_FNS[fn](q))
        except Exception as e:  # noqa: BLE001
            return self._send(400, {'error': str(e)})

    def do_POST(self):
        p = urlparse(self.path)
        fn = _route(p.path)
        n = int(self.headers.get('Content-Length') or 0)
        try:
            kw = json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(kw, dict):
                raise ValueError
        except Exception:  # noqa: BLE001
            return self._send(400, {'error': 'body must be a JSON object'})
        if fn in ('job', 'jobs'):
            return self._send(200, GET_FNS[fn](kw))
        if fn not in POST_FNS:
            return self._send(404, {'error': f'unknown fn {fn!r}',
                                    'post': sorted(POST_FNS)})
        if kw.pop('async', False):
            if fn not in LONG_FNS:
                return self._send(400, {'error': f'{fn} has no async form'})
            return self._send(200, {'job': _start_job(fn, kw)})
        try:
            with _name_lock(kw.get('name')) if fn in LONG_FNS else _NULL:
                return self._send(200, getattr(MOD, fn)(**kw))
        except Exception as e:  # noqa: BLE001  (TypeError = bad kwargs)
            return self._send(400, {'error': str(e)})


H.do_HEAD = H.do_GET


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_NULL = _Null()


if __name__ == '__main__':
    if len(sys.argv) > 1:
        PORT = int(sys.argv[1])
    print(f'cairo on {HOST}:{PORT} -> http://localhost:{PORT}/{NAME} '
          f'(API: /{NAME}/api/<fn>)', flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
