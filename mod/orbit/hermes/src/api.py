#!/usr/bin/env python3
"""
hermes api — the AGENT contract on one port, zero dependencies.

This file is what makes hermes an agent the rest of the fleet can USE rather
than a class you can import. Two routes carry the whole contract:

    GET  /agents        {"agents": [...]}  — the shape orbit/build probes for
                        before it will mount a module as an agent backend, and
                        the shape orbit/agent's console reads
    POST /run/stream    SSE — model_start, token, tool_start, step, done|error

Everything else (chat, models, health) is convenience around the same object.
The events are orbit/agent's own event names on purpose: build's renderer
already knows how to draw them, so a hermes job in that console looks like
every other job instead of like a wall of JSON.

    python3 api.py [--port 50920]
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# `python3 api.py` puts HERE at the FRONT of sys.path, and this module ships a
# mod.py — so `import mod` anywhere below would find ours instead of the
# fleet's `mod` package, and the agent's own `import mod as m` would import
# itself. Same trap the fleet's shadowing note describes. Keep HERE on the
# path (the siblings live there) but only at the end, and load the agent by
# file path under a name that cannot collide.
sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != HERE]
sys.path.append(HERE)

import importlib.util                                         # noqa: E402

import auth                                                   # noqa: E402
import backend as _backend                                    # noqa: E402
import tools as _tools                                        # noqa: E402


def _load_agent():
    spec = importlib.util.spec_from_file_location(
        'hermes_agent', os.path.join(HERE, 'mod.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules['hermes_agent'] = module
    spec.loader.exec_module(module)
    return module


_agent_mod = _load_agent()
AGENTS = _agent_mod.AGENTS
DEFAULT_AGENT = _agent_mod.DEFAULT_AGENT
HERMES_MODELS = _agent_mod.HERMES_MODELS
Hermes = _agent_mod.Hermes

PORT = int(os.environ.get('PORT', 50920))
BASE = os.environ.get('BASE_PATH', '/hermes')

# One agent object per process. The backend caches loaded weights on it, and
# loading an 8B twice is the difference between a warm module and a swapping
# box — so the state is deliberately process-wide, not per request.
AGENT = Hermes()


def info():
    b = AGENT.backend()
    return {
        'name': 'hermes',
        'what': 'NousResearch Hermes weights as a local agent, speaking the '
                'fleet AGENT contract — mountable in orbit/build, selectable '
                'as a provider in orbit/agent',
        'local_only': True,
        'backend': b.info(),
        'ready': b.ready(),
        'agents': list(AGENTS),
        'default_agent': DEFAULT_AGENT,
        'tools': _tools.names(),
        'models': list(HERMES_MODELS),
        'model_dir': str(_backend.MODEL_DIR),
        'contract': {
            'GET /agents': 'the roster — the compatibility probe',
            'GET /agents/{id}': 'one persona in full, prompt included',
            'POST /run/stream': 'SSE: model_start, token, tool_start, step, done',
            'POST /run': 'the same run, blocking, one JSON answer',
            'POST /chat': 'no tools, no loop — just the model',
            'GET /models': 'registry + whatever the backend already serves',
            'GET /health': 'is a backend actually reachable',
        },
        'auth': auth.state(),
        'endpoints_open': sorted(auth.OPEN),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'hermes/1.0'

    # ── plumbing ─────────────────────────────────────────────────────

    def log_message(self, fmt, *args):
        if os.environ.get('HERMES_ACCESS_LOG'):
            sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _send(self, status, payload, ctype='application/json'):
        body = (json.dumps(payload, default=str) if ctype == 'application/json'
                else payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status, why, **extra):
        # 4xx, not 5xx, for anything a caller can act on: Cloudflare eats 5xx
        # bodies, and a refusal that arrives without its reason is a mystery
        self._send(status, {'error': why, **extra})

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode(errors='replace')
        try:
            return json.loads(raw or '{}')
        except json.JSONDecodeError:
            return {}

    def _guard(self, path, key=None):
        return auth.guard(path, headers=self.headers,
                          client_addr=self.client_address[0], key=key)

    def _path(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        # the gateway serves this module under /hermes; strip the prefix so a
        # proxied request and a direct one hit the same route table
        if path.startswith(BASE):
            path = path[len(BASE):] or '/'
        return path.rstrip('/') or '/', urllib.parse.parse_qs(parsed.query)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Content-Length', '0')
        self.end_headers()

    # ── reads ────────────────────────────────────────────────────────

    def do_GET(self):
        path, query = self._path()
        try:
            self._guard(path)
        except auth.Denied as e:
            return self._fail(401, e.why, hint=e.hint)
        try:
            if path in ('/', '/info'):
                return self._send(200, info())
            if path == '/health':
                h = AGENT.health()
                return self._send(200 if h['ok'] else 503, h)
            if path == '/agents':
                return self._send(200, AGENT.agents())
            if path.startswith('/agents/'):
                return self._send(200, AGENT.agent(path.split('/', 2)[2]))
            if path == '/models':
                return self._send(200, {'models': AGENT.models(),
                                        'backend': AGENT.backend().kind})
            if path == '/tools':
                return self._send(200, {'tools': _tools.schema()})
        except KeyError as e:
            return self._fail(404, str(e))
        except Exception as e:
            return self._fail(400, f'{type(e).__name__}: {e}')
        self._fail(404, f'no route {path}', routes=sorted(info()['contract']))

    # ── runs ─────────────────────────────────────────────────────────

    def do_POST(self):
        path, _ = self._path()
        body = self._body()
        try:
            who = self._guard(path, key=body.get('key'))
        except auth.Denied as e:
            return self._fail(401, e.why, hint=e.hint)

        if path == '/chat':
            return self._chat(body)
        if path == '/run':
            return self._run(body, who)
        if path == '/run/stream':
            return self._run_stream(body, who)
        self._fail(404, f'no route {path}', routes=sorted(info()['contract']))

    def _chat(self, body):
        try:
            text = AGENT.chat(
                message=body.get('message') or body.get('query') or '',
                system=body.get('system'), model=body.get('model'),
                max_tokens=int(body.get('max_tokens') or 1024),
                temperature=float(body.get('temperature') or 0.7),
                clear=bool(body.get('clear')))
        except _backend.BackendError as e:
            return self._fail(503, str(e))
        except Exception as e:
            return self._fail(400, f'{type(e).__name__}: {e}')
        self._send(200, {'text': text, 'model': AGENT._model_id(body.get('model')),
                         'backend': AGENT.backend().kind})

    @staticmethod
    def _run_kwargs(body):
        """The run arguments a caller may set.

        build's dispatcher sends `query`, `key`, `model`, `agent_type` and
        `prompt`; orbit/agent's console sends the same names. Anything else in
        the body is ignored rather than forwarded — a run's arguments are not
        a place to accept surprises.
        """
        return {
            'query': body.get('query') or body.get('message') or '',
            'path': body.get('path'),
            'steps': int(body.get('steps') or 15),
            'max_tokens': int(body.get('max_tokens') or 2048),
            'temperature': float(body.get('temperature') or 0.1),
            'agent': body.get('agent_type') or body.get('agent'),
            'model': body.get('model'),
            'prompt': body.get('prompt'),
            'sandbox': bool(body.get('sandbox')),
        }

    def _run(self, body, who):
        kwargs = self._run_kwargs(body)
        if not kwargs['query']:
            return self._fail(400, 'a run needs a `query`')
        try:
            result = AGENT.run(**kwargs)
        except _backend.BackendError as e:
            return self._fail(503, str(e))
        except KeyError as e:
            return self._fail(404, str(e))
        except Exception as e:
            return self._fail(400, f'{type(e).__name__}: {e}')
        self._send(200, {'result': result, 'agent': kwargs['agent'] or DEFAULT_AGENT,
                         'caller': who, 'free': True})

    def _run_stream(self, body, who):
        kwargs = self._run_kwargs(body)
        if not kwargs['query']:
            return self._fail(400, 'a run needs a `query`')
        if not AGENT.backend().ready():
            # Refuse before opening the stream. A 200 that turns out to be one
            # error frame is a stream a caller has to parse to learn it failed.
            return self._fail(503, AGENT.backend().why(),
                              backend=AGENT.backend().kind)

        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        def frame(ev):
            self.wfile.write(f'data: {json.dumps(ev, default=str)}\n\n'.encode())
            self.wfile.flush()

        try:
            for ev in AGENT.run_stream(**kwargs):
                frame(ev)
        except (BrokenPipeError, ConnectionResetError):
            return          # the caller hung up mid-run; nothing to report to
        except Exception as e:
            try:
                frame({'type': 'error', 'error': f'{type(e).__name__}: {e}'})
            except OSError:
                pass
        self.close_connection = True


def serve(port: int = None, host: str = '0.0.0.0'):
    port = int(port or PORT)
    httpd = ThreadingHTTPServer((host, port), Handler)
    b = AGENT.backend()
    print(f'hermes api on http://{host}:{port} — backend: {b.kind}'
          f'{"" if b.ready() else " (not ready — GET /health says why)"}',
          flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    port = PORT
    if '--port' in argv:
        port = int(argv[argv.index('--port') + 1])
    serve(port=port)
