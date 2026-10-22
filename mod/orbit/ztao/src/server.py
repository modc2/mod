"""server.py — stdlib HTTP server for ztao: the console + the JSON API.

    GET  /ztao                 -> the console (static/index.html)
    *    /ztao/api/<fn>        -> protocol API (the gateway strips the prefix,
                                  so the bare /<fn> form is accepted too)
    *    /ztao/_api/<fn>       -> the console's own calls (prefix kept)

All subnet logic lives in mod.py (and the chain adapters under networks/);
this file only maps URLs onto it and never does curve math.

Who can do what:
    public    every read (board, tally, members, epoch, last_sim, ...),
              off-chain verify, and `simulate` — a sandbox epoch on the LOCAL
              devnet with throwaway sim-* keys (single-flight + cooldown)
    loopback  anything that touches member secrets or spends from an account:
              keygen/sign/submit/score/claim/register/deploy/devnet/...
              (the box that holds data/keys.json, without proxy headers)
"""
import importlib.util
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent

# load ztao's OWN mod.py by path — a bare `import mod` is the protocol package
_spec = importlib.util.spec_from_file_location('ztao_mod', HERE / 'mod.py')
modpy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modpy)

MOD = modpy.Mod()
PORT = int(os.environ.get('ZTAO_PORT', MOD.config['port']))
INDEX = HERE / 'static' / 'index.html'
# the Next.js console (src/app, `./build.sh` -> dist symlink); static/index.html
# is only the fallback while it is unbuilt
DIST = HERE / 'app' / 'dist'
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
        '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
        '.ico': 'image/x-icon', '.txt': 'text/plain; charset=utf-8', '.woff2': 'font/woff2',
        '.map': 'application/json', '.webp': 'image/webp'}
SIM_COOLDOWN = int(os.environ.get('ZTAO_SIM_COOLDOWN', 60))

WRITE = threading.Lock()  # one chain writer at a time (keys.json, nonces)
JOB = {'running': False, 'id': 0, 'events': [], 'result': None,
       'error': None, 'started': 0, 'finished': 0}


def _strip(d):
    d.pop('keys', None)
    return d


def _q(q, k, default=None):
    v = q.get(k)
    return v if v not in (None, '') else default


GET_FNS = {
    'health': lambda q: MOD.health(),
    'info': lambda q: _strip(MOD.info()),
    'status': lambda q: _strip(MOD.status(_q(q, 'network', 'devnet'))),
    'board': lambda q: MOD.board(_q(q, 'network', 'devnet')),
    'epoch': lambda q: MOD.epoch(_q(q, 'network', 'devnet')),
    'members': lambda q: MOD.members(_q(q, 'role', 'miner'),
                                     _q(q, 'network', 'devnet')),
    'tally': lambda q: MOD.tally(_q(q, 'epoch'), _q(q, 'network', 'devnet')),
    'earned': lambda q: MOD.earned(q['recipient'], _q(q, 'network', 'devnet')),
    'last_sim': lambda q: MOD.last_sim(_q(q, 'chain', 'starknet')),
    'files': lambda q: MOD.files(),
    'source': lambda q: MOD.source(q['path']),
    'whitepaper': lambda q: MOD.whitepaper(_q(q, 'kind', 'technical')),
    'tests': lambda q: MOD.tests(),
    'gas': lambda q: MOD.gas(),
    'compare': lambda q: MOD.compare(),
    'costs': lambda q: MOD.costs(),
    'readme': lambda q: MOD.readme(),
    'networks': lambda q: MOD.networks(),
    'anonymity': lambda q: MOD.anonymity(_q(q, 'role', 'miner'),
                                         _q(q, 'network', 'devnet'),
                                         _q(q, 'ring_size')),
    'economics': lambda q: MOD.economics(),  # fresh= is loopback-only (cli)
    'forecast': lambda q: MOD.forecast(
        _q(q, 'chain'), _q(q, 'miners', '16,256'), _q(q, 'validators', '4,64'),
        min(int(_q(q, 'months', 12)), 120), _q(q, 'shape', 'exp'),
        _q(q, 'ring', 16), _q(q, 'epoch_hours', 1), _q(q, 'k'),
        float(_q(q, 'f', 0.25))),
    'experiment': lambda q: MOD.experiment(
        _q(q, 'chain', 'near'), ','.join(str(_q(q, 'hours', '1,2,4,6,12,24')).split(',')[:8]),
        _q(q, 'ring', 16), _q(q, 'strategy', 'baseline'), _q(q, 'share', 0.1),
        _q(q, 'budget_day'), min(int(_q(q, 'trials', 200)), 400),
        _q(q, 'vol', 0.5), _q(q, 'p_min', 0.8), _q(q, 'seed', 7)),
}
# local key names are operator data: which nickname holds which public key
# never leaves the box (the sets themselves are public on-chain)
LOCAL_GET = {'keys': lambda q: MOD.keys()}
PUBLIC_POST = {'verify', 'scope', 'simulate', 'yuma'} | set(GET_FNS)
LOCAL_POST = {'keys', 'keygen', 'sign', 'mytag', 'register', 'set_open', 'submit',
              'score', 'settle', 'claim', 'preflight', 'deploy', 'devnet', 'build', 'test', 'gas_sweep'}


def _route(path):
    parts = [p for p in path.split('/') if p and p not in ('ztao', 'api', '_api')]
    return parts[-1] if parts else ''


def _sim_job(miners, validators, chain):
    def on_event(e):
        JOB['events'].append(e)
    try:
        with WRITE:
            JOB['result'] = MOD.simulate(miners, validators, on_event=on_event,
                                         chain=chain)
    except Exception as e:  # noqa: BLE001
        JOB['error'] = str(e)
        JOB['events'].append({'kind': 'error', 'error': str(e)})
    finally:
        JOB['running'] = False
        JOB['finished'] = time.time()


def _start_sim(kw, local):
    if JOB['running']:
        return {'started': False, 'id': JOB['id'], 'running': True}
    wait = SIM_COOLDOWN - (time.time() - JOB['finished'])
    if not local and JOB['finished'] and wait > 0:
        return {'started': False, 'cooldown': int(wait) + 1}
    chain = kw.get('chain') or 'starknet'
    if chain not in modpy.CHAINS:
        return {'started': False, 'error': f'chain is one of {sorted(modpy.CHAINS)}'}
    n = lambda k: max(3, min(int(kw.get(k) or 4), 8 if not local else 32))
    JOB.update(running=True, id=JOB['id'] + 1, events=[], result=None,
               error=None, started=time.time(), chain=chain)
    threading.Thread(target=_sim_job, args=(n('miners'), n('validators'), chain),
                     daemon=True).start()
    return {'started': True, 'id': JOB['id'], 'chain': chain}


def _sim_state(q):
    since = int(_q(q, 'since', 0))
    return {'id': JOB['id'], 'running': JOB['running'], 'chain': JOB.get('chain'),
            'events': JOB['events'][since:], 'next': len(JOB['events']),
            'result': JOB['result'], 'error': JOB['error']}


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
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _local(self):
        proxied = any(self.headers.get(h) for h in (
            'X-Forwarded-For', 'X-Real-IP', 'Forwarded', 'CF-Connecting-IP'))
        return self.client_address[0] in ('127.0.0.1', '::1') and not proxied

    def _asset(self, path):
        """A file of the console export (/_next/*, *.ico, ...), or None."""
        rel = path.split('/ztao/', 1)[-1] if path.startswith('/ztao/') else path.lstrip('/')
        if not rel or rel.split('/', 1)[0] in ('api', '_api') or '.' not in rel.rsplit('/', 1)[-1]:
            return None
        root = DIST.resolve()
        f = (root / rel).resolve()
        if root not in f.parents or not f.is_file():
            return None
        return f

    def _console(self):
        page = DIST / 'index.html'
        if page.exists():
            return self._send(200, page.read_bytes(), 'text/html; charset=utf-8')
        if not INDEX.exists():
            return self._send(200, b'<h1>ztao</h1><p>console missing</p>',
                              'text/html; charset=utf-8')
        return self._send(200, INDEX.read_bytes(), 'text/html; charset=utf-8')

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        p = urlparse(self.path)
        asset = self._asset(p.path)
        if asset is not None:
            return self._send(200, asset.read_bytes(),
                              MIME.get(asset.suffix, 'application/octet-stream'))
        fn = _route(p.path)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        html = 'text/html' in (self.headers.get('Accept') or '')
        try:
            if fn == 'whoami':
                return self._send(200, {'local': self._local(),
                                        'cooldown': SIM_COOLDOWN})
            if fn == 'sim':
                return self._send(200, _sim_state(q))
            if fn in LOCAL_GET:
                if not self._local():
                    return self._send(403, {'error': f'{fn} is local-only'})
                return self._send(200, LOCAL_GET[fn](q))
            if fn in GET_FNS and not (html and fn in ('', 'readme')):
                return self._send(200, GET_FNS[fn](q))
            if fn in ('', 'index.html') or html:
                return self._console()
            return self._send(404, {'error': f'unknown fn {fn!r}'})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {'error': str(e)})

    def do_POST(self):
        p = urlparse(self.path)
        fn = _route(p.path)
        n = int(self.headers.get('Content-Length') or 0)
        try:
            kw = json.loads(self.rfile.read(n) or b'{}')
        except Exception:  # noqa: BLE001
            kw = {}
        if not isinstance(kw, dict):
            kw = {}
        local = self._local()
        if fn == 'simulate':
            return self._send(200, _start_sim(kw, local))
        if fn in LOCAL_POST and not local:
            return self._send(403, {'error': f'{fn} touches member keys; '
                                    f'run it on the anchor host: m ztao/{fn}'})
        if fn not in PUBLIC_POST and fn not in LOCAL_POST:
            return self._send(404, {'error': f'unknown fn {fn!r}'})
        try:
            if fn in GET_FNS:
                return self._send(200, GET_FNS[fn](kw))
            if fn in LOCAL_GET:
                return self._send(200, LOCAL_GET[fn](kw))
            if fn in LOCAL_POST:
                with WRITE:
                    return self._send(200, getattr(MOD, fn)(**kw))
            return self._send(200, getattr(MOD, fn)(**kw))
        except Exception as e:  # noqa: BLE001
            return self._send(400, {'error': str(e)})


if __name__ == '__main__':
    if len(sys.argv) > 1:
        PORT = int(sys.argv[1])
    print(f'ztao on :{PORT} -> http://localhost:{PORT}/ztao/ '
          f'(API: /ztao/api/<fn>)', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
