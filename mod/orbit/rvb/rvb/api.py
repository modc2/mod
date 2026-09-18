#!/usr/bin/env python3
"""rvb api — REST, the console and MCP on one port, stdlib only.

Every route is a thin call into the same functions the MCP tools and the shell
reach, so a browser, an agent and a person are never told different scores for
the same round. There is exactly one scoreboard.

    GET  /                  what this is and how it scores
    GET  /health            liveness, corpus counts, the default target
    GET  /attacks           red team corpus     POST to add, DELETE to remove
    GET  /defenses          blue team corpus    POST to add, DELETE to remove
    GET  /controls          the fixed benign control set
    POST /fight             one attack × one defense, judged
    POST /round             the tournament (background=1 returns immediately)
    GET  /rounds            history, or ?id=<round> for one in full
    GET  /board             standings, blue and red
    GET  /targets           which backends can run right now
    POST /ping              prove a target is reachable
    GET  /tools             the MCP registry
    POST /mcp               MCP JSON-RPC 2.0 (Streamable HTTP)
    GET  /rvb               the console

WHY WRITES ARE GATED AND READS ARE NOT
    A round spends real model calls — the CLI's or an API key's. So the routes
    that spend (fight, round, ping) and the routes that mutate the corpus are
    behind an optional bearer, and everything that only reads a score is open.
    Write `~/.mod/rvb/server.secret` to turn the gate on; with no secret file
    the server is local and open, which is the right default for a box where
    the only caller is the operator's own shell.

WHY A ROUND CAN RUN IN THE BACKGROUND
    A full seed round against the CLI backend is 10 attacks × 4 defenses plus
    40 controls at ~5s each — minutes, not seconds, and far past any sensible
    HTTP timeout. `background=1` starts it in a thread and hands back the round
    id; the arena writes its record as it goes, so `GET /rounds?id=` is a live
    progress read, not a guess.

    python3 -m rvb.api [--port 50820] [--bind 127.0.0.1]
"""

import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from rvb import arena, builtins as bimod, corpus, defense as defmod
    from rvb import mcp as mcpsrv, models, store
else:
    from . import arena, builtins as bimod, corpus, defense as defmod
    from . import mcp as mcpsrv, models, store

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('RVB_BASE_PATH', '/rvb')
PORT = int(os.environ.get('RVB_PORT', 50820))

# Spending a model call or editing the corpus needs the bearer, when one is
# set. Reading a score never does — a scoreboard nobody can read is not one.
WRITE_ROUTES = {'/fight', '/round', '/ping', '/attacks', '/defenses'}


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def info():
    """The rules, the scoring, and every way in."""
    base = mcpsrv.info()
    return {
        **base,
        'endpoints': {
            'GET /health': 'liveness, corpus counts, default target',
            'GET /attacks': 'the red team corpus (?id=, ?category=)',
            'POST /attacks': '{name, prompt|turns, goal, category, technique, '
                             'markers} — add one',
            'DELETE /attacks': '?id= — remove one',
            'GET /defenses': 'the blue team corpus, built-ins first (?id=)',
            'POST /defenses': '{name, system_prompt, input_rules, output_rules, '
                              'self_check, max_input_chars} — add a pipeline',
            'DELETE /defenses': '?id= — remove one (built-ins are permanent)',
            'GET /controls': 'the fixed benign control set over_refusal is '
                             'measured on',
            'GET /cost': '?defense= — model calls per turn, before you spend them',
            'POST /fight': '{attack, defense, model, judge} — one exchange, judged',
            'POST /round': '{attacks, defenses, model, judge, parallel, controls, '
                           'background} — the tournament',
            'GET /rounds': 'history (?limit=, ?status=) or ?id= for one in full',
            'GET /board': '?rounds=8 — standings, blue and red',
            'GET /targets': 'which model backends can run right now',
            'POST /ping': '{model} — prove a target is reachable',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'the console',
        },
        'auth': {
            'gate': 'bearer token on the write routes only'
                    if store.secret() else 'open — no server.secret on this box',
            'header': 'Authorization: Bearer <the contents of '
                      '~/.mod/rvb/server.secret>',
            'gated': sorted(WRITE_ROUTES),
        },
        'mcp': {'endpoint': 'POST /mcp', 'stdio': 'python3 -m rvb.mcp',
                'tools': len(mcpsrv.TOOLS)},
    }


# ── routing ──────────────────────────────────────────────────────

def route(method, path, query, body):
    """One request → one JSON answer. Raises ApiError for a caller's mistake."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}

    def arg(name, default=None):
        v = b.get(name, q.get(name, default))
        return default if v in (None, '') else v

    if path in ('', '/'):
        return info()
    if path == '/health':
        return {'ok': True, 'corpus': store.counts(), 'tools': len(mcpsrv.TOOLS),
                'model': models.DEFAULT, 'state': store.DIR}

    if path == '/attacks':
        if method == 'GET':
            if arg('id'):
                return store.get('attack', arg('id'))
            return {'attacks': store.listing('attack', category=arg('category'),
                                             limit=int(arg('limit', 200)))}
        if method == 'POST':
            return mcpsrv.t_attack(b)
        if method == 'DELETE':
            return store.delete('attack', _need(arg('id'), 'id'))

    if path == '/defenses':
        if method == 'GET':
            if arg('id'):
                return mcpsrv.t_defenses({'id': arg('id')})
            return mcpsrv.t_defenses({})
        if method == 'POST':
            return mcpsrv.t_defend(b)
        if method == 'DELETE':
            ident = _need(arg('id'), 'id')
            if ident in bimod.BUILTIN:
                raise ApiError(f'{ident!r} is a built-in defense', 400)
            return store.delete('defense', ident)

    if path == '/controls' and method == 'GET':
        return {'controls': corpus.CONTROL_SET,
                'why': 'a defense that blocks the attack by refusing the whole '
                       'topic is caught refusing the control next to it'}

    if path == '/cost' and method == 'GET':
        return defmod.cost(_defense(_need(arg('defense'), 'defense')))

    if path == '/fight' and method == 'POST':
        return mcpsrv.t_fight(b)

    if path == '/round' and method == 'POST':
        return _round(b)

    if path == '/rounds' and method == 'GET':
        if arg('id'):
            return mcpsrv._trim_round(store.get('round', arg('id')),
                                      verbose=_flag(arg('verbose')))
        return mcpsrv.t_rounds({'limit': int(arg('limit', 20)),
                                'status': arg('status')})

    if path == '/board' and method == 'GET':
        return bimod.board_across(int(arg('rounds', 8)))

    if path == '/targets' and method == 'GET':
        return mcpsrv.t_targets({})

    if path == '/ping' and method == 'POST':
        try:
            out = models.complete(
                [{'role': 'user', 'content': arg('prompt',
                                                 'Say the single word: pong.')}],
                model=arg('model') or models.DEFAULT)
            return {'ok': True, **out}
        except models.ModelError as e:
            return {'ok': False, 'error': str(e),
                    'model': arg('model') or models.DEFAULT}

    if path == '/tools' and method == 'GET':
        return {'tools': mcpsrv.tool_list(), 'count': len(mcpsrv.TOOLS)}

    raise ApiError(f'no route {method} {path} — GET / lists them', 404)


def _round(b):
    """Start a tournament. Synchronous by default, threaded on request.

    The background path returns the round id and nothing else that could go
    stale: the record on disk is the truth, and `GET /rounds?id=` reads it.
    """
    atks = mcpsrv._resolve(b.get('attacks'), 'attack')
    dfns = mcpsrv._resolve(b.get('defenses'), 'defense')
    if not atks:
        raise ApiError('no attacks — seed the corpus or write one', 400)
    if not dfns:
        raise ApiError('no defenses', 400)
    kwargs = dict(model=b.get('model') or models.DEFAULT,
                  judge_kind=b.get('judge') or 'model',
                  parallel=int(b.get('parallel') or 6),
                  controls=_flag(b.get('controls', True)),
                  timeout=int(b['timeout']) if b.get('timeout') else None,
                  name=b.get('name'))
    if not _flag(b.get('background')):
        return mcpsrv._trim_round(arena.run_round(atks, dfns, **kwargs),
                                  verbose=bool(b.get('verbose')))

    # The id is minted here and the placeholder written here, in the request
    # thread — an id handed back before its record exists is a poll that 404s
    # on the caller's first, fastest attempt.
    rid = kwargs['name'] = kwargs['name'] or arena.round_id()
    total = len(atks) * len(dfns) + \
        (len(corpus.CONTROL_SET) * len(dfns) if kwargs['controls'] else 0)
    store.put('round', {'id': rid, 'kind': 'round', 'status': 'running',
                        'model': kwargs['model'], 'judge': kwargs['judge_kind'],
                        'attacks': [a.get('id') for a in atks],
                        'defenses': [d.get('id') for d in dfns],
                        'total_matches': total, 'done': 0,
                        'matches': [], 'control_matches': []})

    def go():
        try:
            arena.run_round(atks, dfns, **kwargs)
        except Exception as e:                          # noqa: BLE001
            rec = store.find('round', rid) or {'id': rid, 'kind': 'round'}
            store.put('round', dict(rec, status='error', error=str(e)))

    threading.Thread(target=go, daemon=True).start()
    return {'id': rid, 'status': 'running', 'background': True,
            'total_matches': total, 'poll': f'GET /rounds?id={rid}'}


def _defense(ref):
    if ref in bimod.BUILTIN:
        return bimod.BUILTIN[ref]
    return store.get('defense', ref)


def _need(value, name):
    if value in (None, ''):
        raise ApiError(f'{name} is required', 400)
    return value


def _flag(v):
    if isinstance(v, bool):
        return v
    return str(v).lower() not in ('0', 'false', 'no', 'none', '')


# ── the server ───────────────────────────────────────────────────

def serve(port=PORT, bind=None, base=BASE):
    console = os.path.join(HERE, 'console.html')
    bind = bind if bind is not None else os.environ.get('RVB_BIND', '0.0.0.0')
    # The console calls `<its own path>/_api`, so the same file works mounted
    # at /rvb behind the gateway and served bare on the port.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/rvb', '/_api')
    corpus.seed_store()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'rvb/' + mcpsrv.version()

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str, indent=2).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self._cors()
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def _cors(self):
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.send_header('access-control-allow-methods',
                             'GET,POST,DELETE,OPTIONS')

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def _read(self):
            n = int(self.headers.get('content-length') or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                return {}

        def _path(self):
            """Strip the gateway prefixes so /rvb/_api/board == /board."""
            raw = urllib.parse.urlparse(self.path)
            p, query = raw.path, raw.query
            for prefix in api_prefixes:
                if p == prefix or p.startswith(prefix + '/'):
                    return p[len(prefix):] or '/', query
            if p in (base, base + '/'):
                return '/console', query
            if p.startswith(base + '/'):
                return p[len(base):], query
            return p, query

        def _authed(self, path, method):
            secret = store.secret()
            if not secret:
                return True
            if method == 'GET' or path not in WRITE_ROUTES:
                return True
            auth = (self.headers.get('authorization') or '').strip()
            token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
            return token == secret

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                resp = mcpsrv.handle(self._read())
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p == '/favicon.ico':
                return self._send(204, b'', 'image/x-icon')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        return self._send(200, f.read(), 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, info())
            body = self._read()
            if not self._authed(p, self.command):
                return self._send(401, {'error': 'this route spends model calls '
                                        'or edits the corpus — send the bearer '
                                        'from ~/.mod/rvb/server.secret'})
            try:
                return self._send(200, route(self.command, p, query, body))
            except ApiError as e:
                return self._send(e.status, {'error': str(e)})
            except (store.StoreError, defmod.DefenseError, arena.ArenaError,
                    models.ModelError) as e:
                return self._send(400, {'error': str(e),
                                        'kind': type(e).__name__})
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:                      # noqa: BLE001
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'rvb on {bind}:{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcpsrv.TOOLS)} tools, target {models.DEFAULT}', flush=True)
    ThreadingHTTPServer((bind, port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]

    def opt(name, default):
        return argv[argv.index(name) + 1] if name in argv and \
            argv.index(name) + 1 < len(argv) else default

    serve(int(opt('--port', PORT)), bind=opt('--bind', None))
