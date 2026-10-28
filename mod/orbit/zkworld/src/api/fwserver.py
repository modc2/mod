"""fwserver.py — the server of one framework submod (zkworld.<fid>).

    python3 src/api/fwserver.py <fid> <port>

Binds 127.0.0.1 only. The parent (src/api/server.py) keeps one of these up per
framework and proxies /zkworld/<fid>/* to it, applying the owner gate to
writes before it forwards. So the rule here is the parent's local rule:
writes need a loopback request with no proxy headers (the CLI, curl on the
box, or the parent after it gated); the parent stamps X-Forwarded-For on
everything it has NOT cleared, which makes a forwarded write fail here too.

URL shapes (any prefix of /zkworld/<fid> is stripped):
  GET  /                -> the framework's page (src/templates/framework.html)
  GET  /api/<fn>?k=v    -> info health toolchain templates projects abi witness jobs job
  POST /api/<fn> {..}   -> the same + new build test vibe clean; async=true -> job
"""
import importlib.util
import json
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
FID = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('ZKWORLD_FW', 'cairo')
HERE = SRC / 'mods' / FID
PAGE = SRC / 'templates' / 'framework.html'

_spec = importlib.util.spec_from_file_location(f'zkworld_fw_{FID}', HERE / 'mod.py')
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
MOD = _m.Mod()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else MOD.port

READ_FNS = {'info', 'health', 'toolchain', 'templates', 'projects', 'abi', 'witness'}
WRITE_FNS = set(MOD.config.get('write_fns', ['new', 'build', 'test', 'vibe', 'clean']))
LONG_FNS = {'build', 'test', 'vibe'}
_PROXY = ('x-forwarded-for', 'x-real-ip', 'cf-connecting-ip', 'forwarded', 'x-forwarded-host')

JOBS, LOCK = {}, threading.Lock()


def _local(ip, headers):
    return (ip in ('127.0.0.1', '::1', '::ffff:127.0.0.1')
            and not any(headers.get(h) for h in _PROXY))


def _start(fn, kw):
    jid = uuid.uuid4().hex[:12]
    job = {'id': jid, 'fn': fn, 'name': kw.get('name'), 'framework': FID,
           'state': 'running', 'created': time.time(), 'finished': None,
           'result': None, 'error': None}
    if fn == 'vibe':
        job['steps'] = []
        kw['on_step'] = lambda st: job['steps'].append(st.get('tool')) \
            if len(job['steps']) < 200 else None
    with LOCK:
        JOBS[jid] = job
        for old in sorted(JOBS.values(), key=lambda j: j['created'])[:-100]:
            JOBS.pop(old['id'], None)

    def run():
        try:
            job['result'], job['state'] = getattr(MOD, fn)(**kw), 'done'
        except Exception as e:  # noqa: BLE001
            job['error'], job['state'] = str(e), 'error'
            traceback.print_exc()
        job['finished'] = time.time()
    threading.Thread(target=run, daemon=True).start()
    return job


def _call(fn, kw):
    if fn == 'jobs':
        with LOCK:
            return {'jobs': sorted(JOBS.values(), key=lambda j: -j['created'])[:50]}
    if fn == 'job':
        return JOBS.get(kw.get('id', '')) or {'error': f"no such job {kw.get('id')!r}"}
    if fn == 'witness' and isinstance(kw.get('input'), str):
        kw['input'] = json.loads(kw['input'] or '{}')
    return getattr(MOD, fn)(**kw)


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _fn(self, path):
        segs = [p for p in path.split('/') if p and p not in ('zkworld', FID, 'api', '_api')]
        return segs[-1] if segs else ''

    def do_GET(self):
        p = urlparse(self.path)
        fn = self._fn(p.path)
        if not fn or fn == 'index.html':
            return self._send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
        if fn not in READ_FNS | {'jobs', 'job'}:
            return self._send(404, {'error': f'unknown fn {fn!r}',
                                    'get': sorted(READ_FNS), 'post': sorted(WRITE_FNS)})
        try:
            return self._send(200, _call(fn, {k: v[0] for k, v in parse_qs(p.query).items()}))
        except Exception as e:  # noqa: BLE001
            return self._send(400, {'error': str(e)})

    def do_POST(self):
        fn = self._fn(urlparse(self.path).path)
        try:
            kw = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            assert isinstance(kw, dict)
        except Exception:  # noqa: BLE001
            return self._send(400, {'error': 'body must be a JSON object'})
        kw.pop('on_step', None)
        if fn not in READ_FNS | WRITE_FNS | {'jobs', 'job'}:
            return self._send(404, {'error': f'unknown fn {fn!r}'})
        if fn in WRITE_FNS and not _local(self.client_address[0], self.headers):
            return self._send(403, {'error': f'{fn} is owner-only: sign in on /zkworld'})
        if kw.pop('async', False) and fn in LONG_FNS:
            return self._send(200, {'job': _start(fn, kw)})
        try:
            return self._send(200, _call(fn, kw))
        except Exception as e:  # noqa: BLE001
            return self._send(400, {'error': str(e)})

    do_HEAD = do_GET


if __name__ == '__main__':
    print(f'zkworld.{FID} on 127.0.0.1:{PORT} (proxied at /zkworld/{FID}/)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
