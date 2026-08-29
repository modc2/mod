#!/usr/bin/env python3
"""infer mcp — fourteen tools for making a model cheaper to run.

The tools are ordered the way the work actually goes: get a model in, read what
it is, ask what to try, try it, and check that the result is still the same
model. `infer_optimize` is the one that matters; everything else exists so its
answer can be trusted or reproduced.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50820 # Streamable HTTP — POST /mcp
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything that imports us.
    sys.path.append(HERE)

import engine as E                                          # noqa: E402
from engine import InferError                               # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Inference optimization, architecture-agnostic. Everything here works on '
    'one format — ONNX — because that is the binary both onnxruntime (here) '
    'and onnxruntime-web (a browser tab) execute without a second conversion, '
    'so a model optimized once runs in both places. A CNN, an LSTM, a '
    'transformer and a gradient-boosted forest are all just graphs by the time '
    'they get here, and the passes read the graph. '
    'The path: infer_add (or infer_export from torch) → infer_inspect to see '
    'what it is → infer_plan for what is worth trying on it → infer_optimize '
    'to do it. infer_optimize already benchmarks before and after, compares '
    'the outputs and re-checks browser portability, so read its `verdict` '
    'first and its `passes` list when you want to know which step did what. '
    'Two things to actually watch for. Quantization makes models smaller and '
    'frequently SLOWER on small graphs — never recommend int8 without running '
    'infer_compare on the real model. And exactly one pass breaks browser '
    'deployment: `all`, which rewrites the graph into com.microsoft.nchwc '
    'layout operators for the CPU that ran it; `extended` is safe, because the '
    'browser wasm backend does register the contrib operators fusion emits '
    '(measured in a browser, not assumed). infer_optimize reports '
    '`portability_lost` when it happens. Nothing here guesses at accuracy: '
    'infer_parity feeds both models the same inputs and reports how far the '
    'numbers moved.'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc, **extra):
    return {'type': 'number', 'description': desc, **extra}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_REF = _str('a stored model: its id, its name, an id prefix, or a path to an '
            '.onnx file on this box')
_BENCH = {
    'runs': _num('timed iterations after warmup (default 30)'),
    'batch': _num('what to make the first symbolic dimension (default 1)'),
    'shapes': {'type': 'object', 'description': 'override an input shape by '
                                                'name, e.g. {"input_ids": "1,128"}'},
}


# ── handlers ─────────────────────────────────────────────────────

def _t_health(a):
    return E.health()


def _t_models(a):
    return E.models(limit=a.get('limit') or 200)


def _t_add(a):
    return E.add(data=a.get('data'), path=a.get('path'), url=a.get('url'),
                 name=a.get('name'), note=a.get('note'))


def _t_inspect(a):
    return E.inspect(a['model'])


def _t_plan(a):
    return E.plan(a['model'], target=a.get('target') or 'local')


def _t_passes(a):
    return E.passes()


def _t_optimize(a):
    return E.optimize(a['model'], passes_=a.get('passes'), name=a.get('name'),
                      check=a.get('check', True), runs=a.get('runs') or E.DEFAULT_RUNS,
                      batch=a.get('batch') or 1, samples=a.get('samples') or 4,
                      tol=a.get('tol') if a.get('tol') is not None else 1e-3,
                      shapes=a.get('shapes'), threads=a.get('threads'))


def _t_bench(a):
    return E.bench(a['model'], runs=a.get('runs') or E.DEFAULT_RUNS,
                   warmup=a.get('warmup') if a.get('warmup') is not None
                   else E.DEFAULT_WARMUP,
                   batch=a.get('batch') or 1, threads=a.get('threads'),
                   provider=a.get('provider'), shapes=a.get('shapes'))


def _t_parity(a):
    return E.parity(a['a'], a['b'], samples=a.get('samples') or 8,
                    batch=a.get('batch') or 1,
                    tol=a.get('tol') if a.get('tol') is not None else 1e-3,
                    shapes=a.get('shapes'))


def _t_portable(a):
    return E.portable(a['model'])


# The five that change the answer to "should I ship this". `slim` and `shapes`
# are housekeeping and would only pad the table with two identical rows.
COMPARABLE = ['basic', 'extended', 'all', 'fp16', 'int8']


def _t_compare(a):
    """Every pass in the list, each applied on its own, side by side."""
    which = a.get('passes') or COMPARABLE
    if isinstance(which, str):
        which = [p.strip() for p in which.replace(' ', ',').split(',') if p.strip()]
    rows = []
    for name in which:
        try:
            rep = E.optimize(a['model'], passes_=[name], check=True,
                             runs=a.get('runs') or 20, batch=a.get('batch') or 1,
                             shapes=a.get('shapes'))
            rows.append({'pass': name, 'ok': True, 'id': rep['result']['id'],
                         'bytes': rep['result']['bytes'],
                         'size_ratio': (rep.get('size') or {}).get('ratio'),
                         'speedup': (rep.get('speed') or {}).get('speedup'),
                         'max_abs_err': (rep.get('parity') or {}).get('max_abs_err'),
                         'portable': (rep.get('portable') or {}).get('portable'),
                         'verdict': rep['verdict']})
        except InferError as e:
            rows.append({'pass': name, 'ok': False, 'error': e.message})
        except Exception as e:
            rows.append({'pass': name, 'ok': False, 'error': f'{type(e).__name__}: {e}'})
    ranked = sorted([r for r in rows if r.get('speedup')],
                    key=lambda r: -r['speedup'])
    return {'model': a['model'], 'tried': which, 'results': rows,
            'fastest': ranked[0]['pass'] if ranked else None,
            'smallest': min([r for r in rows if r.get('size_ratio')],
                            key=lambda r: -r['size_ratio'])['pass']
            if any(r.get('size_ratio') for r in rows) else None,
            'note': 'each pass applied alone to the same source, so the rows are '
                    'comparable — a real build usually stacks several'}


def _t_export(a):
    return E.export(a['source'], name=a.get('name'), opset=a.get('opset') or 17,
                    shape=a.get('shape'), weights=a.get('weights'))


def _t_examples(a):
    return E.examples()


def _t_delete(a):
    return E.delete(a['model'])


TOOLS = {
    'infer_health': {
        'description': 'What this box can actually do: onnx and onnxruntime '
                       'versions, which execution providers exist here (CPU '
                       'always, CUDA only if it was built in), which passes are '
                       'available, and how many models are stored. Call it first '
                       'if a pass comes back unavailable.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_health,
    },
    'infer_models': {
        'description': 'Every model in the store, newest first, with size, node '
                       'count, parameter count, guessed architecture, and — for '
                       'anything this module produced — which passes made it and '
                       'what it came from.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('rows to return (default 200)')}},
        'handler': _t_models,
    },
    'infer_add': {
        'description': 'Put an ONNX model into the store, from base64 bytes '
                       '(data), a path on this box (path), or a URL. It is stored '
                       'under the SHA-256 of its bytes, so the same model added '
                       'twice is one entry and any report can be tied back to the '
                       'exact bytes it was measured on. Rejects anything that '
                       'will not parse as a graph.',
        'inputSchema': {'type': 'object', 'properties': {
            'data': _str('base64-encoded .onnx bytes'),
            'path': _str('a path to an .onnx file on this box'),
            'url': _str('an http(s) URL to fetch the model from'),
            'name': _str('what to call it (defaults to the filename or its id)'),
            'note': _str('anything worth remembering about where it came from')}},
        'handler': _t_add,
    },
    'infer_inspect': {
        'description': 'What a model IS, read off the graph: opset, producer, '
                       'every operator and how many of each, parameter count and '
                       'how many bytes of the file are weights, the declared '
                       'inputs and outputs including which dimensions are '
                       'symbolic, and a guess at the architecture. The symbolic '
                       'dimensions are the ones you have to decide before you can '
                       'benchmark anything.',
        'inputSchema': {'type': 'object', 'properties': {'model': _REF},
                        'required': ['model']},
        'handler': _t_inspect,
    },
    'infer_plan': {
        'description': 'What is worth trying on THIS model and why, in a form you '
                       'can pass straight to infer_optimize. target=local '
                       'optimizes for time on this machine; target=web weighs '
                       'the download instead, because in a browser the bytes '
                       'crossing the network usually cost more than the '
                       'arithmetic — so it reaches for quantization earlier and '
                       'never includes `all`. The `why` list is the reasoning, '
                       'not decoration — read it before overriding the plan.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _REF,
            'target': _str('local (default) or web', enum=['local', 'web'])},
            'required': ['model']},
        'handler': _t_plan,
    },
    'infer_passes': {
        'description': 'The catalog of optimization passes: what each one does, '
                       'whether it changes the numbers, whether the result still '
                       'runs in a browser, and whether it is available on this '
                       'box. Also the order they compose in.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_passes,
    },
    'infer_optimize': {
        'description': 'The main tool. Runs passes over a model and reports what '
                       'each one bought: node counts before and after with the '
                       'operators that disappeared, file size, p50 latency before '
                       'and after measured in this process on identical inputs, '
                       'how far the outputs moved, and whether the result still '
                       'runs in a browser. Read `verdict` first. If '
                       '`portability_lost` is present the model got faster here '
                       'and stopped being loadable in a browser — in practice '
                       'that means the `all` pass was used. Default passes are '
                       'slim,extended (both lossless, both browser-safe).',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _REF,
            'passes': _str('comma-separated, in order — slim, shapes, basic, '
                           'extended, all, fp16, int8, uint8 (default '
                           '"slim,extended")'),
            'name': _str('what to call the result'),
            'check': _bool('benchmark and compare against the source (default '
                           'true) — turn it off only for a model too big to run '
                           'twice'),
            'tol': _num('absolute error that still counts as agreement (1e-3)'),
            'samples': _num('input sets to compare outputs on (default 4)'),
            'threads': _num('intra-op threads for the benchmark'),
            **_BENCH}, 'required': ['model']},
        'handler': _t_optimize,
    },
    'infer_bench': {
        'description': 'Time one model: p50, p90, p99, min, max, mean and stdev '
                       'in milliseconds over warmed-up runs on random inputs, '
                       'plus throughput. Percentiles rather than a mean because '
                       'the mean of a cold session measures the allocator. '
                       'Graph optimization is disabled in the session on purpose '
                       'so it measures the binary you handed it, not what '
                       'onnxruntime would have done to it at load time.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _REF,
            'warmup': _num('untimed runs first (default 5)'),
            'threads': _num('intra-op threads — 1 is the honest number for a '
                            'server running many models at once'),
            'provider': _str('execution provider, e.g. CUDAExecutionProvider — '
                             'errors if this box does not have it'),
            **_BENCH}, 'required': ['model']},
        'handler': _t_bench,
    },
    'infer_parity': {
        'description': 'Feed two models the same seeded random inputs and report '
                       'how far apart the outputs are: max absolute error, max '
                       'relative error, worst-case cosine similarity, and how '
                       'often the argmax still agrees. This is the tool that says '
                       'whether an optimization was free or paid for. Fusion '
                       'should come back identical; quantization will not, and '
                       'argmax agreement is usually what you actually care about.',
        'inputSchema': {'type': 'object', 'properties': {
            'a': _str('the original model'), 'b': _str('the optimized one'),
            'samples': _num('input sets to compare (default 8)'),
            'tol': _num('absolute error that still counts as agreement (1e-3)'),
            **_BENCH}, 'required': ['a', 'b']},
        'handler': _t_parity,
    },
    'infer_portable': {
        'description': 'Will these exact bytes run in a browser? Returns three '
                       'answers, not two: `portable` false for operators no '
                       'browser build registers (com.microsoft.nchwc.* from the '
                       '`all` pass, and anyone else\'s custom domain), '
                       '`portable` true with a `cautions` entry for the '
                       'onnxruntime contrib operators that fusion emits — the '
                       'wasm backend does run those, which was measured in a '
                       'browser rather than assumed — and plain true otherwise. '
                       'A static prediction either way; the console proves it '
                       'by actually running the bytes.',
        'inputSchema': {'type': 'object', 'properties': {'model': _REF},
                        'required': ['model']},
        'handler': _t_portable,
    },
    'infer_compare': {
        'description': 'Apply each pass on its own to the same model and put the '
                       'results in one table: size ratio, speedup, output error '
                       'and portability per pass, with the fastest and smallest '
                       'named. Use it when the plan is not obvious — on small '
                       'graphs the answer is regularly "quantization made it '
                       'worse", and this is how you find that out in one call '
                       'instead of four.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _REF,
            'passes': _str('which to try, comma-separated (default: basic, '
                           'extended, all, fp16, int8 — the five that change '
                           'the shipping decision)'),
            **_BENCH}, 'required': ['model']},
        'handler': _t_compare,
    },
    'infer_export': {
        'description': 'Turn something that is not ONNX yet into the standard '
                       'binary: torchvision:<name> for a stock architecture, a '
                       '.py file that defines `model` (and optionally `example`), '
                       'or a TorchScript .pt with shape= given. The result goes '
                       'straight into the store ready to optimize. Weights are '
                       'random unless you ask for pretrained ones — fine for '
                       'measuring a graph, useless for measuring accuracy.',
        'inputSchema': {'type': 'object', 'properties': {
            'source': _str('torchvision:resnet18, a path to a .py, or a .pt'),
            'name': _str('what to call it'),
            'opset': _num('ONNX opset to target (default 17)'),
            'shape': _str('example input shape, e.g. "1,3,224,224"'),
            'weights': _str('torchvision weights enum, e.g. "DEFAULT", to '
                            'download pretrained parameters')},
            'required': ['source']},
        'handler': _t_export,
    },
    'infer_examples': {
        'description': 'Plant three models to work on: a feed-forward net with '
                       'BatchNorm to fuse, a small CNN, and a transformer block. '
                       'One of each architecture, so the difference between what '
                       'the passes do to a Conv stack and what they do to '
                       'attention is visible in two calls.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_examples,
    },
    'infer_delete': {
        'description': 'Remove a model from the store. The bytes go too, unless '
                       'another entry has the same SHA-256.',
        'inputSchema': {'type': 'object', 'properties': {'model': _REF},
                        'required': ['model']},
        'handler': _t_delete,
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────

def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer, so a route and an MCP
    tools/call cannot drift apart."""
    tool = TOOLS.get(name)
    if not tool:
        raise InferError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = {k: v for k, v in (args or {}).items() if v is not None}
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise InferError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(out, default=str,
                                                             indent=2)}],
                             'structuredContent': out if isinstance(out, dict) else None,
                             'isError': False})
    except InferError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), default=str)}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'bad arguments for {name}: {e}'}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'infer', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def tool_list():
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50820)))
    else:
        serve_stdio()
