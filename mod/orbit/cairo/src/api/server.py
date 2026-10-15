"""server.py — stdlib HTTP API + console for cairo. No framework, one port.

Mod protocol URL shapes, all on :51291:

  GET  /cairo                -> the console (static Next.js export, src/dist)
  GET  /cairo/_next/*        -> the export's static assets
  *    /cairo/api/<fn>       -> the JSON protocol API (canonical; the gateway
                                strips the prefix, so bare /<fn> works too)
  *    /api/cairo/<fn>       -> legacy alias of the same API
  *    /cairo/_api/<fn>      -> the app's own self-calls (gateway KEEPS the
                                prefix); mapped onto the same handlers.
  GET  /cairo/<contract>/    -> that contract's app: its own
                                src/mods/<c>/app/index.html, else the shared
                                ABI-driven default src/templates/app.html

All contract logic lives in src/mod.py (the anchor). This file maps URLs onto
Mod methods, ships the console, and adds one thing the CLI doesn't need: a job
table, because vibe (minutes) and a cold build (~60s) outlive any sane HTTP
timeout. POST any of LONG_FNS with {"async": true} -> {"job": id}; poll
GET /job?id=<id>.

Publicly routed via caddy, so writes are gated (same rule as starknet's
studio): reads are open; WRITE_FNS need either a direct loopback request (no
proxy headers — the CLI, curl on the box) or a mod-protocol token (`token:` /
`Authorization: Bearer`) signed by an owner: config.json `owner`, the build
console's private-flag owner, or CAIRO_OWNERS (comma list). GET /whoami says
which you are.
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

# The protocol package (shared Auth for sign-in tokens). Import it NOW: the
# sys.path insert below puts src/ first, after which `import mod` is our own
# src/mod.py and every token fails with "module 'mod' has no attribute 'mod'".
try:
    import mod as PROTO
    if not hasattr(PROTO, 'mod'):
        raise ImportError(f'shadowed by {PROTO.__file__}')
except Exception as e:  # noqa: BLE001
    PROTO, PROTO_ERR = None, str(e)

# cairo_mcp.py / cairo_helper.py / tutorials.py import by bare name from these two dirs
sys.path[:0] = [str(SRC / 'api'), str(SRC)]
import cairo_mcp as mcp  # noqa: E402  (not `mcp`: the pip SDK)
import cairo_helper as helper  # noqa: E402
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
    'templates': lambda q: MOD.templates(),
    'abi': lambda q: MOD.abi(q.get('name', '')),
    'app': lambda q: MOD.app(q.get('name', '')),
    'events': lambda q: MOD.events(q.get('name', ''), q.get('limit', 50)),
    'sandbox': lambda q: MOD.sandbox(),
    'market': lambda q: MOD.market(),
    'tutorials': lambda q: MOD.tutorials(q.get('id')),
    'fleet': lambda q: MOD.fleet(refresh=q.get('refresh') in ('1', 'true')),
    'agents': lambda q: MOD.agents(refresh=q.get('refresh') in ('1', 'true'),
                                   brief=q.get('brief') in ('1', 'true'), mod=q.get('mod')),
    'whitepaper': lambda q: MOD.whitepaper(),
    'jobs': lambda q: {'jobs': _jobs_list()},
    'job': lambda q: _job_get(q.get('id', '')),
}
POST_FNS = {'info', 'status', 'toolchain', 'list', 'contracts', 'get', 'tree',
            'read', 'write', 'new', 'vibe', 'build', 'test', 'clean', 'remove',
            'templates', 'abi', 'adopt', 'app', 'deploy', 'call', 'events',
            'sandbox', 'market', 'fork', 'tutorials', 'fleet', 'whitepaper', 'agents'}
LONG_FNS = {'vibe', 'build', 'test', 'status'}
# touch files on this box or spend the host Claude account -> owner only
WRITE_FNS = {'write', 'new', 'vibe', 'build', 'test', 'clean', 'remove', 'adopt',
             'status', 'fork'}
# the sandbox is a throwaway local devnet, so its app is open to everyone:
# `call` (views and sandbox-account txs) needs no owner. `deploy` is open
# only for a contract that is already built and has no live deployment (no
# compile on the box, no wiping someone else's running instance);
# `sandbox` start/stop/reset is owner-only.
TOKEN_MAX_AGE = int(os.environ.get('CAIRO_TOKEN_AGE', 7 * 86400))

# ---- who may write ----------------------------------------------------------


def _owners():
    out = set(a for a in os.environ.get('CAIRO_OWNERS', '').split(',') if a)
    for f in (SRC.parent / 'config.json',
              Path.home() / '.mod/build/private' / f'{NAME}.json'):
        try:
            o = json.loads(f.read_text()).get('owner')
            if o:
                out.add(o)
        except Exception:  # noqa: BLE001
            pass
    return {a.strip().lower() for a in out}


_PROXY_HEADERS = ('x-forwarded-for', 'x-real-ip', 'cf-connecting-ip',
                  'forwarded', 'x-forwarded-host')


def _whoami(ip, headers):
    own = sorted(_owners())
    host = (headers.get('host') or '').rsplit(':', 1)[0].strip('[]')
    if (ip in ('127.0.0.1', '::1', '::ffff:127.0.0.1')
            and not any(headers.get(h) for h in _PROXY_HEADERS)
            and host in ('localhost', '127.0.0.1', '::1', '')):
        return {'writable': True, 'via': 'local', 'address': None, 'owners': own}
    tok = headers.get('token') or ''
    auth = headers.get('authorization') or ''
    if not tok and auth.lower().startswith('bearer '):
        tok = auth[7:].strip()
    if not tok:
        return {'writable': False, 'via': None, 'address': None, 'owners': own,
                'why': 'sign in with the owner wallet to build'}
    try:
        if PROTO is None:
            raise RuntimeError(f'auth unavailable ({PROTO_ERR})')
        addr = str(PROTO.mod('auth')(max_age=TOKEN_MAX_AGE).verify(tok)['key']).lower()
    except Exception as e:  # noqa: BLE001
        return {'writable': False, 'via': None, 'address': None, 'owners': own,
                'why': f'bad token: {e}'.split('{')[0].strip()}
    ok = addr in own
    return {'writable': ok, 'via': 'token' if ok else None, 'address': addr,
            'owners': own, **({} if ok else {'why': f'{addr} is not an owner'})}

def _sandbox_needs_owner(fn, kw):
    if fn == 'sandbox':
        return kw.get('action', 'status') != 'status'
    if fn == 'deploy':
        try:
            st = MOD.app(kw.get('name', ''))
        except Exception:  # noqa: BLE001
            return True
        live = (st.get('deployment') or {}).get('live')
        return live or st.get('state') != 'fresh'
    return False

def _gate(ip, headers):
    """The REST write rule as a callback for MCP tools and the helper:
    gate(fn, kw, owner_tool) -> None (go) | why not."""
    cache = {}

    def gate(fn, kw, owner=False):
        if owner or fn in WRITE_FNS or _sandbox_needs_owner(fn, kw):
            if 'who' not in cache:
                cache['who'] = _whoami(ip, headers)
            if not cache['who']['writable']:
                return f"{fn} is owner-only: {cache['who'].get('why')}"
        return None
    return gate


def _special(path):
    """MCP + agent-contract routes, under any gateway prefix:
    /cairo/mcp, /cairo/agents[/<id>], /cairo/.well-known/agent.json,
    /cairo/run, /cairo/run/stream  ->  ('mcp',) ('agents', id) ..."""
    raw = [p for p in path.split('/') if p]
    if 'api' in raw[:2] or '_api' in raw[:2]:
        return None  # /cairo/api/agents is the protocol fn agents(), not the contract
    segs = [p for p in raw if p != NAME]
    if segs == ['mcp']:
        return ('mcp',)
    if segs[:1] == ['agents'] and segs[2:] in (['run'], ['run', 'stream']):
        return ('run', len(segs) == 4, segs[1])
    if segs[:1] == ['agents'] and len(segs) <= 2:
        return ('agents', segs[1] if len(segs) == 2 else None)
    if segs == ['.well-known', 'agent.json']:
        return ('card',)
    if segs in (['run'], ['run', 'stream']):
        return ('run', len(segs) == 2)
    return None


# ---- jobs -------------------------------------------------------------------

JOBS = {}
JOBS_LOCK = threading.Lock()
# the job table survives restarts (the server is restarted on every deploy);
# a job that was mid-run when the process died comes back as 'interrupted'
JOBS_FILE = SRC / '.jobs.json'


def _jobs_load():
    try:
        for j in json.loads(JOBS_FILE.read_text()):
            if j.get('state') in ('queued', 'running'):
                j.update(state='error', finished=j.get('started') or j['created'],
                         error='interrupted: the server restarted mid-run')
            JOBS[j['id']] = j
    except Exception:  # noqa: BLE001  (no file yet / corrupt: start empty)
        pass


def _jobs_save():
    with JOBS_LOCK:
        rows = [_public(j) for j in JOBS.values()]
    tmp = JOBS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(rows, default=str))
    tmp.replace(JOBS_FILE)
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
            if fn == 'vibe':
                # the agent's steps stream in while it works; the console
                # polls /job and shows them as a live trail
                job['steps'] = []
                kw['on_step'] = lambda st: job['steps'].append(
                    modpy._step_brief(st)) if len(job['steps']) < 200 else None
            try:
                job['result'] = getattr(MOD, fn)(**kw)
                job['state'] = 'done'
            except Exception as e:  # noqa: BLE001
                job['error'] = str(e)
                job['state'] = 'error'
                traceback.print_exc()
            job['finished'] = time.time()
        _jobs_save()

    _jobs_save()
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
                           or (len(segs) == 1 and (segs[0] in GET_FNS
                                                   or segs[0] == 'whoami')))


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

    def _serve_app(self, path):
        """/cairo/<contract>/[file] -> the contract's app, or None when the
        first segment isn't a contract (so the console handles it)."""
        segs = [p for p in path.split('/') if p]
        if segs and segs[0] == NAME:
            segs = segs[1:]
        if not segs or segs[0].startswith('_'):
            return None
        try:
            pkg, d = MOD._subdir(segs[0])
        except ValueError:
            return None
        if pkg != segs[0] or not MOD._is_submod(d):
            return None
        if not path.endswith('/') and len(segs) == 1:
            # the page resolves its API from its own URL: pin the slash
            self.send_response(301)
            self.send_header('Location', path + '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return True
        root = Path(d) / 'app'
        rel = '/'.join(segs[1:]) or 'index.html'
        target = root / rel
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            return self._send(404, {'error': 'not found'}) or True
        if not target.is_file():
            if rel != 'index.html':
                return self._send(404, {'error': 'not found'}) or True
            target = Path(modpy.DEFAULT_APP)
        ctype = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        self._send(200, target.read_bytes(), ctype)
        return True

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

    def _base_url(self):
        # the helper's claude brain curls MCP on this box, never the public URL
        return f'http://127.0.0.1:{PORT}'

    def do_GET(self):
        p = urlparse(self.path)
        sp = _special(p.path)
        if sp:
            if sp[0] == 'mcp':
                return self._send(200, mcp.card())
            if sp[0] == 'card':
                return self._send(200, helper.card(MOD.base))
            if sp[0] == 'agents':
                if sp[1]:
                    a = helper.AGENTS.get(sp[1])
                    return self._send(200 if a else 404, a or {'error': f'no agent {sp[1]!r}'})
                return self._send(200, {'agents': list(helper.AGENTS.values()),
                                        'examples': helper.EXAMPLES,
                                        'whoami': _whoami(self.client_address[0], self.headers)})
            return self._send(405, {'error': 'POST /run or /run/stream'})
        if not _is_api_path(p.path):
            if self._serve_app(p.path):
                return
            return self._serve_static(p.path)
        fn = _route(p.path) or 'info'
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        if fn == 'whoami':
            return self._send(200, _whoami(self.client_address[0], self.headers))
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
            if not isinstance(kw, (dict, list)):
                raise ValueError
        except Exception:  # noqa: BLE001
            return self._send(400, {'error': 'body must be a JSON object'})
        sp = _special(p.path)
        if sp and sp[0] == 'mcp':
            gate = _gate(self.client_address[0], self.headers)
            msgs = kw if isinstance(kw, list) else [kw]
            out = [r for r in (mcp.handle(m, MOD, gate, _jobs_list) for m in msgs) if r]
            if not out:
                return self._send(202, b'')
            return self._send(200, out if isinstance(kw, list) else out[0])
        if sp and sp[0] == 'run':
            return self._agent_run(kw, stream=sp[1], agent=sp[2] if len(sp) > 2 else None)
        if not isinstance(kw, dict):
            return self._send(400, {'error': 'body must be a JSON object'})
        if fn in ('job', 'jobs'):
            return self._send(200, GET_FNS[fn](kw))
        kw.pop('on_step', None)   # a server-side hook, never from the wire
        if fn not in POST_FNS:
            return self._send(404, {'error': f'unknown fn {fn!r}',
                                    'post': sorted(POST_FNS)})
        if fn in WRITE_FNS or _sandbox_needs_owner(fn, kw):
            who = _whoami(self.client_address[0], self.headers)
            if not who['writable']:
                # 403 not 5xx: Cloudflare strips 5xx bodies
                return self._send(403, {'error': f"{fn} is owner-only: {who.get('why')}",
                                        'owners': who['owners']})
        if kw.pop('async', False):
            if fn not in LONG_FNS:
                return self._send(400, {'error': f'{fn} has no async form'})
            return self._send(200, {'job': _start_job(fn, kw)})
        try:
            with _name_lock(kw.get('name')) if fn in LONG_FNS else _NULL:
                return self._send(200, getattr(MOD, fn)(**kw))
        except Exception as e:  # noqa: BLE001  (TypeError = bad kwargs)
            return self._send(400, {'error': str(e)})


    def _agent_run(self, body, stream, agent=None):
        if not isinstance(body, dict):
            return self._send(400, {'error': 'body must be a JSON object'})
        ip, hd = self.client_address[0], self.headers
        args = dict(mod=MOD, body=body, who=_whoami(ip, hd), gate=_gate(ip, hd),
                    jobs=_jobs_list, base_url=self._base_url(), root=str(SRC.parent),
                    mods_dir=modpy.MODS, agent=agent, start_job=_start_job, get_job=_job_get)
        if not stream:
            try:
                ev = helper.run(emit=lambda e: None, **args)
            except Exception as e:  # noqa: BLE001
                return self._send(400, {'error': str(e)})
            code = 200 if ev.get('type') == 'done' else 403 if ev.get('owner_only') else 400
            return self._send(code, ev)
        # SSE: no Content-Length, connection closes when the run ends
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

        def emit(ev):
            try:
                self.wfile.write(f'data: {json.dumps(ev, default=str)}\n\n'.encode())
                self.wfile.flush()
            except OSError:
                pass
        try:
            helper.run(emit=emit, **args)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            emit({'type': 'error', 'error': str(e)})


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
    _jobs_load()
    # MCP cairo_vibe/cairo_job ride the same job table as REST async=true
    mcp.HOOKS.update(start=_start_job, get=_job_get)
    print(f'cairo on {HOST}:{PORT} -> http://localhost:{PORT}/{NAME} '
          f'(API: /{NAME}/api/<fn>)', flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
