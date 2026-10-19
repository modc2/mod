"""Server for the judge console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

    /judge/*            → the console (prefix kept by the gateway; stripped here)
    /judge/api/{fn}     → the API, as the console calls it
    /api/judge/{fn}     → the API (prefix stripped by the gateway)
    /{fn}               → the API, bare, for local curl

It also speaks the agent protocol (agentproto.py), so every judge here is
an agent the fleet can mount (orbit/build POST /agents/mods):

    GET  /agents        → {"agents": ["scorer", <every panel>...]}
    POST /run/stream    {query, agent_type, prompt, model} → SSE steps/done
    POST /run           the same, blocking

Reads are GET with query args. Writes are POST with a JSON body. The panel
params are the multisig itself, so create_panel / update_panel / remove_panel
/ add_judge / install_judge / score answer only a caller who has said who
they are (identity.py):

  box     loopback, or a request carrying the box's token (minted once to
          ~/.mod/judge/token, sent as X-Judge-Token). `creator` passes
          through as given — the CLI path.
  signed  a mod-protocol token (m.mod('auth').token, or a browser wallet's
          personal_sign) as `Authorization: Bearer <token>`. The signer's
          address IS the creator/author on every write, so a signed caller
          manages exactly the panels and listings they made.

Anyone else can submit inputs for judgment and browse the judge market — a
listing is just a spec, it runs nothing until it is seated.

    python3 serve.py [--port 51150] [--host 0.0.0.0]
"""

import argparse
import hmac
import json
import os
import secrets
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/judge'
MAX_BODY = 1 * 1024 * 1024

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import agentproto  # noqa: E402
import identity  # noqa: E402
import market  # noqa: E402
import panel  # noqa: E402

READ_FNS = ('health', 'info', 'readme', 'panels', 'panel', 'verdict',
            'verdicts', 'verify', 'key_kinds', 'market', 'listing',
            'agents', 'roster', 'providers', 'auth', 'whoami', 'judge_keys')
WRITE_FNS = ('judge', 'score', 'create_panel', 'update_panel', 'remove_panel',
             'add_judge', 'publish_judge', 'unpublish_judge', 'install_judge',
             'run', 'run/stream')
# Need a known caller: the box, or a signed address.
GATED = ('score', 'create_panel', 'update_panel', 'remove_panel', 'add_judge',
         'install_judge')
LOCAL_ONLY = GATED  # the v0.2 name, kept for anything that imports it
# A signed caller acts as themself: the field the signer's address pins.
SIGNER_FIELD = {'create_panel': 'creator', 'update_panel': 'creator',
                'remove_panel': 'creator', 'add_judge': 'creator',
                'install_judge': 'creator', 'publish_judge': 'author',
                'unpublish_judge': 'author'}

_book = None
_shop = None


def book():
    global _book
    if _book is None:
        _book = panel.Panels()
    return _book


def shop():
    global _shop
    if _shop is None:
        _shop = market.Market(book())
    return _shop


def token():
    """The box's write token: minted once, 0600, in the judge store. A
    request carrying it in X-Judge-Token counts as local, so the owner can
    manage panels from the gateway console without opening writes up."""
    p = os.path.join(panel._store_dir(), 'token')
    try:
        with open(p) as f:
            t = f.read().strip()
        if t:
            return t
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(p), exist_ok=True)
    t = secrets.token_urlsafe(24)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(t)
    return t


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def api(fn, a):
    b = book()
    if fn == 'health':
        return {'ok': True, **b.stats()}
    if fn == 'info':
        cfg = _config()
        return {'name': 'judge', 'version': cfg.get('version'),
                'description': cfg.get('description'), 'stats': b.stats(),
                'endpoints': cfg.get('endpoints', {})}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    if fn == 'panels':
        return {'panels': b.list()}
    if fn == 'panel':
        return b.get(a.get('name', ''))
    if fn == 'verdict':
        return b.verdict(int(a.get('id', 0)))
    if fn == 'verify':
        return b.verify_verdict(int(a.get('id', 0)))
    if fn == 'key_kinds':
        import keys
        return {'kinds': keys.kinds()}
    if fn == 'verdicts':
        return {'verdicts': b.verdicts(a.get('panel', ''),
                                       a.get('limit', 50), a.get('offset', 0))}
    if fn == 'judge':
        return b.judge(a.get('panel', ''), a.get('input', ''))
    if fn == 'score':
        return b.score(a.get('spec'), a.get('input', ''))
    if fn == 'create_panel':
        return b.create(a.get('name', ''), a.get('creator', ''),
                        a.get('judges'), a.get('threshold', 60),
                        a.get('min_votes'))
    if fn == 'update_panel':
        return b.update(a.get('name', ''), a.get('creator', ''),
                        a.get('judges'), a.get('threshold'), a.get('min_votes'))
    if fn == 'remove_panel':
        return b.remove(a.get('name', ''), a.get('creator', ''))
    if fn == 'add_judge':
        return b.add_judge(a.get('name', '') or a.get('panel', ''),
                           a.get('creator', ''), a.get('judge'))
    if fn == 'judge_keys':
        return b.judge_keys(a.get('panel', '') or a.get('name', ''),
                            a.get('judge', ''))
    if fn == 'market':
        return {'listings': shop().list(a.get('q', ''), a.get('kind', ''))}
    if fn == 'listing':
        return shop().get(int(a.get('id', 0)))
    if fn == 'agents':  # this module's own agent-protocol roster
        return agentproto.agents(b)
    if fn == 'roster':  # any provider's roster, for the console's pickers
        return agentproto.roster(a.get('provider') or agentproto.SELF, b)
    if fn == 'providers':
        return {'providers': agentproto.providers(), 'default': agentproto.SELF}
    if fn == 'run':
        return agentproto.run_local(b, a)
    if fn == 'publish_judge':
        return shop().publish(a.get('name', ''), a.get('author', ''),
                              a.get('spec'), a.get('description', ''),
                              a.get('tags'))
    if fn == 'unpublish_judge':
        return shop().unpublish(int(a.get('id', 0)), a.get('author', ''))
    if fn == 'install_judge':
        return shop().install(int(a.get('id', 0)), a.get('panel', ''),
                              a.get('creator', ''), a.get('name'),
                              a.get('weight'))
    return None


class Handler(SimpleHTTPRequestHandler):
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
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, X-Judge-Token, Authorization')
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

    def _stream(self, args):
        """POST /run/stream: the agent protocol's SSE — `data: {type…}`."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.close_connection = True

        def emit(ev):
            self.wfile.write(f'data: {json.dumps(ev)}\n\n'.encode())
            self.wfile.flush()
        try:
            agentproto.run_local(book(), args, emit)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001 — the stream carries the reason
            try:
                emit({'type': 'error', 'error': str(e)})
            except Exception:
                pass

    def _trusted(self):
        # A gateway on this box also connects from loopback, so a forwarded
        # request is treated as remote whatever its socket says — unless it
        # carries the box's token.
        if (self.client_address[0] in ('127.0.0.1', '::1')
                and not self.headers.get('X-Forwarded-For')):
            return True
        t = self.headers.get('X-Judge-Token', '')
        return bool(t) and hmac.compare_digest(t, token())

    def _caller(self):
        """Who is asking: {box, address, role}. The box (loopback / box
        token) outranks a signature so the CLI keeps passing `creator`
        through; otherwise a valid mod-protocol token names the caller."""
        box = self._trusted()
        address = identity.whoami(self.headers.get('Authorization', ''))
        role = 'box' if box else 'signed' if address else 'anon'
        return {'box': box, 'address': address, 'role': role}

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/')
        fn = self._fn(path, WRITE_FNS)
        if not fn:
            return self._json({'error': 'not found'}, 404)
        who = self._caller()
        if fn in GATED and who['role'] == 'anon':
            return self._json(
                {'error': f'{fn} needs a signed-in caller: send a mod-protocol '
                          'token as Authorization: Bearer <token> (sign in with '
                          'your wallet in the console), or the box token '
                          '(X-Judge-Token, minted at ~/.mod/judge/token)'}, 401)
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            return self._json({'error': 'body too large'}, 413)
        try:
            args = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._json({'error': 'body must be JSON'}, 400)
        if not isinstance(args, dict):
            return self._json({'error': 'body must be a JSON object'}, 400)
        if fn in ('run', 'run/stream'):
            # Scoring with the bare scorer spends a model turn like /score
            # does; posing an input to a panel is open like /judge.
            if ((a_t := args.get('agent_type') or agentproto.SCORER) == agentproto.SCORER
                    and who['role'] == 'anon'):
                return self._json({'error': 'the scorer agent needs a signed-in '
                                            'caller (Authorization: Bearer <token>)',
                                   'agent_type': a_t}, 401)
            if fn == 'run/stream':
                return self._stream(args)
        # A signed caller is whoever signed — not whoever they claim to be.
        if who['role'] == 'signed' and fn in SIGNER_FIELD:
            args[SIGNER_FIELD[fn]] = who['address']
        return self._run(fn, args)

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        path = raw.rstrip('/') or '/'
        fn = self._fn(path, READ_FNS)
        if fn in ('auth', 'whoami'):  # needs the request context, not the store
            who = self._caller()
            return self._json({'trusted': who['box'], 'address': who['address'],
                               'role': who['role'],
                               'can_change_panels': who['role'] != 'anon',
                               'auth': identity.status()})
        if fn:
            args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            return self._run(fn, args)

        # The console. The gateway publishes the BARE /judge form (its 308
        # goes /judge/ -> /judge), so redirecting the other way loops; serve
        # index.html at both, with <base href="/judge/"> anchoring the assets.
        if raw == PREFIX:
            self.path = '/index.html'
            return super().do_GET()
        if raw.startswith(PREFIX + '/'):
            self.path = self.path[len(PREFIX):]
        if urlparse(self.path).path.rstrip('/') in ('', '/'):
            self.path = '/index.html'
        return super().do_GET()


def serve(port=None, host='0.0.0.0'):
    port = int(port or _config().get('port', 51150))
    token()  # mint the write token on first boot so the owner can find it
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'judge: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/panels')
    print(f'judge: write token at {os.path.join(panel._store_dir(), "token")}')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
