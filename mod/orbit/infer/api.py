#!/usr/bin/env python3
"""infer api — REST, MCP and the console on one port, standard library only.

Every route is a thin call into the same `engine` the MCP tools use, so a
shell, an agent and the browser console cannot be told different things about
the same model.

One route here is not like the others: `GET /blob/<id>` hands back the raw
.onnx bytes. That is what makes the browser half real — the console fetches
that URL into onnxruntime-web and runs the *same bytes* the server just
benchmarked, rather than a re-export that might not be the same model.

    python3 api.py [--port 50820]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import engine as E                                          # noqa: E402
import mcp                                                  # noqa: E402
import proofs as P                                          # noqa: E402
from engine import InferError                               # noqa: E402

BASE = os.environ.get('BASE_PATH', '/infer')
PORT = int(os.environ.get('PORT', 50820))


def info():
    return {
        'name': 'infer',
        'version': mcp.version(),
        'what': 'a board where model output at temperature 0 is a signed, '
                'content-addressed, re-runnable claim — and the optimizer that '
                'produces the bit-exact half of it',
        'halves': {
            'proofs': 'post what a model said at temperature 0, hash it, sign '
                      'it, publish it to core/store, and let anyone run the '
                      'same question again and file the answer beside yours',
            'optimize': 'inference optimization for any architecture on one '
                        'standard binary — ONNX in, a smaller and faster ONNX '
                        'out, running unchanged in onnxruntime here and '
                        'onnxruntime-web in a browser tab',
        },
        'rule': 'temperature 0, top_p 1, one candidate, no penalties. A sampled '
                'run is refused with 422, because a board that mixes sampled and '
                'greedy receipts cannot tell a nondeterministic model from a '
                'different random draw.',
        'verdicts': {
            'unreplicated': 'one receipt — somebody said so',
            'self-reproduced': 'ran again, same bytes, same signer',
            'reproduced': 'same bytes from two independent signers',
            'divergent': 'one greedy question, more than one answer',
        },
        'binary': {
            'format': 'ONNX',
            'local': 'onnxruntime (CPU, and any provider this build has)',
            'browser': 'onnxruntime-web — wasm, SIMD and threads where the page '
                       'is cross-origin isolated, WebGPU where it exists',
            'why': 'one file executed by two runtimes with no second conversion, '
                   'so what was measured is what ships',
        },
        'passes': list(E.PASSES),
        'default_passes': E.DEFAULT_PASSES,
        'store': E.MODEL_DIR,
        'receipts': P.LEDGER,
        'published_to': P.STORE_URL if P.STORE_ON else 'off',
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'endpoints': {
            'GET /proofs': 'the board — one row per question, with its verdict',
            'POST /proofs': '{model, prompt|messages, output, provider} — post a '
                            'claim somebody ran anywhere',
            'GET /proofs/status': 'totals, this box\'s signing address, store health',
            'GET /proofs/<id>': 'one receipt, whole',
            'DELETE /proofs/<id>': 'drop it from this board (the CID still resolves)',
            'GET /questions/<hash>': 'every receipt for one question, and the verdict',
            'POST /run': '{model, prompt, provider, repeat?} — run it here and post it',
            'POST /replicate': '{question|receipt} — ask it again, file the answer',
            'GET|POST /verify': 'receipt=, rerun= — recheck every hash and the signature',
            'GET /diff': 'a=, b= — where two answers stopped agreeing',
            'GET /leaderboard': 'which models hold still at temperature 0',
            'GET|POST /canon': 'the canonical request bytes and their hash, without running it',
            'GET /providers': 'who is reachable from here',
            'POST /providers': '{name, base, style} — add an openai- or anthropic-shaped one',
            'POST /key': '{provider, key} — kept 0600 off-tree, never in a receipt',
            'POST /import': '{cid} — pull in a receipt published from another box',
            'GET /health': 'runtime versions, providers, available passes',
            'GET /models': 'everything in the store',
            'POST /models': '{data|path|url, name?} — add an .onnx',
            'DELETE /models/<id>': 'remove one',
            'GET /blob/<id>': 'the raw .onnx bytes — what the browser fetches',
            'GET /inspect': 'model= — opset, ops, params, inputs, outputs',
            'GET /plan': 'model=, target=local|web — what to try, and why',
            'GET /passes': 'the pass catalog',
            'POST /optimize': '{model, passes?, batch?, runs?} — the main one',
            'GET|POST /bench': 'model=, runs=, batch=, threads=, provider=',
            'GET|POST /parity': 'a=, b=, samples= — did the answers survive',
            'GET /portable': 'model= — will it run in a browser',
            'POST /compare': '{model, passes?} — every pass, side by side',
            'POST /export': '{source, shape?} — torch → ONNX',
            'POST /examples': 'plant three models to work on',
            'POST /report': '{model, ms, ...} — record what a browser measured',
            'GET /reports': 'model= — browser numbers next to local ones',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


# Browser benchmark results, posted back by the console. Kept next to the
# models so "how fast is this in a browser" has an answer that came from a
# browser rather than from an estimate made on a server CPU.
REPORTS = os.path.join(E.STATE_DIR, 'browser.json')


def _reports():
    try:
        with open(REPORTS) as f:
            return json.load(f)
    except Exception:
        return {}


def _record(rec):
    model = str(rec.get('model') or '')
    if not model:
        raise InferError('a browser report needs model=')
    all_ = _reports()
    rows = all_.setdefault(model, [])
    rows.insert(0, rec)
    del rows[25:]
    E._ensure()
    tmp = REPORTS + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(all_, f, indent=2, default=str)
    os.replace(tmp, REPORTS)
    return rec


def route(method, path, query, body):
    """One request → one JSON answer. Raises InferError for real failures."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}
    args = {**q, **b}

    def arg(name, default=None):
        return args.get(name, default)

    def num(name, default=None):
        v = args.get(name)
        try:
            return type(default)(v) if v is not None and default is not None \
                else (float(v) if v is not None else default)
        except (TypeError, ValueError):
            raise InferError(f'{name}= should be a number, got {v!r}')

    def flag(name, default=False):
        v = args.get(name)
        return default if v is None else str(v).lower() not in ('0', 'false', 'no', '')

    def model_arg():
        m = arg('model') or arg('id') or arg('name')
        if not m:
            raise InferError('which model? pass model=<id|name|path>')
        return m

    if path in ('', '/'):
        return info()
    if path == '/health':
        return E.health()
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    if path == '/models':
        if method == 'POST':
            return E.add(data=arg('data'), path=arg('path'), url=arg('url'),
                         name=arg('name'), note=arg('note'))
        return E.models(limit=num('limit', 200))
    if path.startswith('/models/'):
        ref = path.split('/', 2)[2]
        if method == 'DELETE':
            return E.delete(ref)
        return E.inspect(ref)
    if path == '/inspect':
        return E.inspect(model_arg())
    if path == '/plan':
        return E.plan(model_arg(), target=arg('target') or 'local')
    if path == '/passes':
        return E.passes()
    if path == '/portable':
        return E.portable(model_arg())
    if path == '/optimize':
        return E.optimize(model_arg(), passes_=arg('passes'), name=arg('name'),
                          check=flag('check', True), runs=num('runs', E.DEFAULT_RUNS),
                          batch=num('batch', 1), samples=num('samples', 4),
                          tol=num('tol', 1e-3), shapes=arg('shapes'),
                          threads=arg('threads'))
    if path == '/bench':
        return E.bench(model_arg(), runs=num('runs', E.DEFAULT_RUNS),
                       warmup=num('warmup', E.DEFAULT_WARMUP), batch=num('batch', 1),
                       threads=arg('threads'), provider=arg('provider'),
                       shapes=arg('shapes'))
    if path == '/parity':
        if not (arg('a') and arg('b')):
            raise InferError('parity compares two models — pass a= and b=')
        return E.parity(arg('a'), arg('b'), samples=num('samples', 8),
                        batch=num('batch', 1), tol=num('tol', 1e-3),
                        shapes=arg('shapes'))
    if path == '/compare':
        return mcp.call_tool('infer_compare', args)
    if path == '/export':
        return E.export(arg('source'), name=arg('name'), opset=num('opset', 17),
                        shape=arg('shape'), weights=arg('weights'))
    if path == '/examples':
        return E.examples()
    # ── the board ────────────────────────────────────────────
    if path == '/proofs':
        if method == 'POST':
            return P.post(claim=arg('claim'), sign=flag('sign', True),
                          publish=flag('publish', True), key=arg('key'),
                          attestation=arg('attestation'),
                          **{k: v for k, v in args.items()
                             if k not in ('claim', 'sign', 'publish', 'key',
                                          'attestation')})
        return P.board(model=arg('model'), provider=arg('provider'),
                       runtime=arg('runtime'), verdict=arg('verdict'),
                       by=arg('by'), q=arg('q'), limit=num('limit', 50),
                       sort=arg('sort') or 'recent')
    if path == '/proofs/status':
        return P.status()
    if path.startswith('/proofs/'):
        rid = path.split('/', 2)[2]
        if method == 'DELETE':
            return P.delete(rid)
        return P.receipt(rid)
    if path.startswith('/questions/'):
        return P.question(path.split('/', 2)[2], full=flag('full', False))
    if path == '/questions':
        return P.question(arg('question') or arg('id') or '',
                          full=flag('full', False))
    if path == '/run':
        return P.run(arg('model'), provider=arg('provider'),
                     runtime=arg('runtime'), sign=flag('sign', True),
                     publish=flag('publish', True), key=arg('key'),
                     repeat=num('repeat', 1), prompt=arg('prompt'),
                     messages=arg('messages'), system=arg('system'),
                     max_tokens=num('max_tokens', 512), seed=arg('seed'),
                     stop=arg('stop'), api_key=arg('api_key'),
                     batch=num('batch', 1), shapes=arg('shapes'),
                     params=arg('params'))
    if path == '/replicate':
        return P.replicate(question_id=arg('question'), receipt=arg('receipt'),
                           provider=arg('provider'), sign=flag('sign', True),
                           publish=flag('publish', True), key=arg('key'),
                           api_key=arg('api_key'))
    if path == '/verify':
        return P.verify(arg('receipt') or arg('id'), rerun=flag('rerun', False),
                        fetch=flag('fetch', True))
    if path == '/diff':
        if not (arg('a') and arg('b')):
            raise InferError('diff compares two receipts — pass a= and b=')
        return P.diff(arg('a'), arg('b'))
    if path == '/leaderboard':
        return P.leaderboard(runtime=arg('runtime'),
                             min_receipts=num('min_receipts', 2))
    if path == '/canon':
        return P.canonical(runtime=arg('runtime') or 'llm', model=arg('model'),
                           prompt=arg('prompt'), messages=arg('messages'),
                           system=arg('system'), max_tokens=num('max_tokens', 512),
                           seed=arg('seed'), stop=arg('stop'), batch=num('batch', 1),
                           shapes=arg('shapes'), params=arg('params'))
    if path == '/providers':
        if method == 'POST':
            return P.add_provider(arg('name') or arg('provider'), arg('base'),
                                  style=arg('style') or 'openai', note=arg('note'))
        return P.providers()
    if path == '/key':
        return P.set_key(arg('provider'), arg('key'))
    if path == '/import':
        return P.fetch(arg('cid'), post_it=flag('post', True))

    if path == '/reports':
        model = arg('model')
        all_ = _reports()
        return {'reports': all_.get(model, []) if model else all_}
    if path == '/report':
        return _record(args)
    raise InferError(f'no route {path} — GET / lists them', 404)


def serve(port=PORT):
    console = os.path.join(HERE, 'console.html')
    base = BASE if BASE.startswith('/') else '/' + BASE
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /infer behind the gateway or served bare at :50820/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/infer', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'infer/' + mcp.version()

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
            self.send_header('access-control-allow-methods', 'GET,POST,DELETE,OPTIONS')

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
            """Strip the gateway prefixes so /infer/_api/models == /models."""
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

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                resp = mcp.handle(self._read())
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        return self._send(200, f.read(), 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, json.dumps(info(), indent=2).encode())
            if p.startswith('/blob/') and self.command in ('GET', 'HEAD'):
                try:
                    data = E.blob(p.split('/', 2)[2])
                except InferError as e:
                    return self._send(e.status, e.dict())
                self.send_response(200)
                self.send_header('content-type', 'application/octet-stream')
                self.send_header('content-length', str(len(data)))
                # Content-addressed bytes never change, so the browser may keep
                # them: a second benchmark run should not re-download 40 MB.
                self.send_header('cache-control', 'public, max-age=31536000, immutable')
                self._cors()
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)
                return
            try:
                return self._send(200, route(self.command, p, query, self._read()))
            except InferError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_DELETE = do_HEAD = _dispatch

        def log_message(self, *a):
            pass

    print(f'infer on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools, store {E.MODEL_DIR}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
