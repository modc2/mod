#!/usr/bin/env python3
"""infer engine — make a model smaller and faster, then prove you did.

One format all the way through: **ONNX**. Whatever the architecture was written
in — an MLP, a CNN, an LSTM, a transformer, a gradient-boosted forest — it
becomes one `.onnx` file, and from there the optimizer does not care what it
came from. It reads the graph, not the framework.

The same bytes then run in two places without being converted again:

    locally   onnxruntime          (this process, `bench`)
    browser   onnxruntime-web      (wasm/webgpu, the console)

Every transform here is measured against the model it replaced: size, node
count, p50 latency, and — the part that matters — whether the outputs still
agree. An optimization that changes the answer is not an optimization, so
`parity` runs on every `optimize` and the report says how far it moved.
"""

import hashlib
import json
import os
import statistics
import time
import warnings

import numpy as np
import onnx
from onnx import helper as onnx_helper

STATE_DIR = os.path.expanduser(os.environ.get('INFER_DIR', '~/.mod/infer'))
MODEL_DIR = os.path.join(STATE_DIR, 'models')
REGISTRY = os.path.join(STATE_DIR, 'registry.json')
MAX_BYTES = int(os.environ.get('INFER_MAX_BYTES', 512 * 1024 * 1024))
DEFAULT_RUNS = int(os.environ.get('INFER_RUNS', 30))
DEFAULT_WARMUP = int(os.environ.get('INFER_WARMUP', 5))

# Which operator domains survive the trip to a browser. This list is measured,
# not assumed — on 2026-08-29 a graph carrying com.microsoft.FusedConv and one
# carrying com.microsoft.BiasGelu both loaded and ran in onnxruntime-web's wasm
# build from the CDN, while com.microsoft.nchwc.Conv came back "is not a
# registered function/op". So the contrib domain is fine and the layout family
# the `all` level emits is not, which is a much narrower line than "anything
# onnxruntime invented".
STANDARD_DOMAINS = {'', 'ai.onnx', 'ai.onnx.ml'}
CONTRIB_DOMAINS = {'com.microsoft', 'com.microsoft.experimental'}
# Prefix match: everything under here is a CPU layout transform by definition.
BLOCKED_PREFIXES = ('com.microsoft.nchwc',)
# The highest ONNX opset a current onnxruntime-web build executes. Newer models
# usually still load — ORT is tolerant — but this is the line where "usually"
# starts doing the work, so it is reported rather than assumed.
WEB_OPSET = 22


class InferError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra

    def dict(self):
        return {'error': self.message, **self.extra}


# ── state ────────────────────────────────────────────────────────

def _ensure():
    os.makedirs(MODEL_DIR, exist_ok=True)


def registry():
    try:
        with open(REGISTRY) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_registry(reg):
    _ensure()
    tmp = REGISTRY + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(reg, f, indent=2, default=str)
    os.replace(tmp, REGISTRY)


def store(data, name=None, source=None, parent=None, passes=None, note=None):
    """Put bytes in the store under the SHA-256 of those bytes.

    Content-addressed on purpose: optimizing the same model with the same
    passes twice gives back the same id, so a report can be trusted to be about
    the bytes it names.
    """
    if len(data) > MAX_BYTES:
        raise InferError(f'model is {len(data):,} bytes, over the '
                         f'{MAX_BYTES:,} limit (raise INFER_MAX_BYTES)', 413)
    digest = hashlib.sha256(data).hexdigest()
    mid = digest[:12]
    _ensure()
    path = os.path.join(MODEL_DIR, digest + '.onnx')
    if not os.path.exists(path):
        with open(path, 'wb') as f:
            f.write(data)
    reg = registry()
    rec = reg.get(mid) or {}
    rec.update({
        'id': mid, 'sha256': digest, 'bytes': len(data),
        'name': name or rec.get('name') or mid,
        'source': source or rec.get('source'), 'parent': parent or rec.get('parent'),
        'passes': passes if passes is not None else rec.get('passes'),
        'note': note or rec.get('note'),
        'added': rec.get('added') or time.time(),
    })
    try:
        rec.update(_summary(path))
    except Exception as e:                      # a stored blob that will not parse
        rec['error'] = f'{type(e).__name__}: {e}'
    reg[mid] = rec
    _write_registry(reg)
    return rec


def resolve(ref):
    """An id, an id prefix, a name, or a path on disk → (record, path)."""
    if ref is None or ref == '':
        raise InferError('which model? pass an id, a name, or a path')
    ref = str(ref)
    reg = registry()
    if ref in reg:
        return reg[ref], _path_of(reg[ref])
    hits = [r for r in reg.values() if r.get('name') == ref]
    hits = hits or [r for r in reg.values()
                    if r['id'].startswith(ref) or r.get('sha256', '').startswith(ref)]
    if len(hits) == 1:
        return hits[0], _path_of(hits[0])
    if len(hits) > 1:
        raise InferError(f'{ref!r} matches {len(hits)} models: '
                         + ', '.join(f"{h['id']} ({h.get('name')})" for h in hits[:8]))
    path = os.path.expanduser(ref)
    if os.path.isfile(path):
        return {'id': None, 'name': os.path.basename(path), 'path': path,
                'bytes': os.path.getsize(path)}, path
    raise InferError(f'no model {ref!r} — `models` lists what is stored', 404)


def _path_of(rec):
    path = rec.get('path') or os.path.join(MODEL_DIR, rec['sha256'] + '.onnx')
    if not os.path.exists(path):
        raise InferError(f"model {rec.get('id')} is in the registry but its "
                         f'bytes are gone from {path}', 410)
    return path


def blob(ref):
    _, path = resolve(ref)
    with open(path, 'rb') as f:
        return f.read()


def delete(ref):
    rec, path = resolve(ref)
    reg = registry()
    reg.pop(rec.get('id'), None)
    _write_registry(reg)
    others = any(r.get('sha256') == rec.get('sha256') for r in reg.values())
    if not others and path.startswith(MODEL_DIR):
        try:
            os.remove(path)
        except OSError:
            pass
    return {'deleted': rec.get('id'), 'name': rec.get('name')}


def models(limit=200):
    reg = registry()
    out = sorted(reg.values(), key=lambda r: r.get('added') or 0, reverse=True)
    return {'count': len(out), 'dir': MODEL_DIR, 'models': out[:int(limit)]}


# ── reading a graph ──────────────────────────────────────────────

def _load(path):
    try:
        return onnx.load(path)
    except Exception as e:
        raise InferError(f'not a readable ONNX model: {type(e).__name__}: {e}')


def _all_nodes(graph):
    """Every node, walking into If/Loop/Scan subgraphs as well."""
    for node in graph.node:
        yield node
        for attr in node.attribute:
            if attr.HasField('g'):
                yield from _all_nodes(attr.g)
            for sub in attr.graphs:
                yield from _all_nodes(sub)


def _shape_of(tensor_type):
    dims = []
    for d in tensor_type.shape.dim:
        if d.HasField('dim_value'):
            dims.append(int(d.dim_value))
        elif d.dim_param:
            dims.append(d.dim_param)
        else:
            dims.append('?')
    return dims


def _io(values):
    out = []
    for v in values:
        t = v.type.tensor_type
        try:
            dtype = str(onnx_helper.tensor_dtype_to_np_dtype(t.elem_type))
        except Exception:
            dtype = onnx.TensorProto.DataType.Name(t.elem_type)
        out.append({'name': v.name, 'dtype': dtype, 'shape': _shape_of(t)})
    return out


def _params(model):
    n, nbytes, dtypes = 0, 0, {}
    for init in model.graph.initializer:
        count = 1
        for d in init.dims:
            count *= int(d)
        n += count
        try:
            arr = onnx.numpy_helper.to_array(init)
            nbytes += int(arr.nbytes)
            dtypes[str(arr.dtype)] = dtypes.get(str(arr.dtype), 0) + count
        except Exception:
            pass
    return n, nbytes, dtypes


def _ops(model):
    hist = {}
    for node in _all_nodes(model.graph):
        key = node.op_type if node.domain in ('', 'ai.onnx') \
            else f'{node.domain}.{node.op_type}'
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: (-kv[1], kv[0])))


def _architecture(ops):
    """A label for what kind of model this is, from the ops it actually uses.

    Nothing downstream depends on getting this right — the passes read the
    graph, not the label. It exists so `plan` can say *why* it is suggesting
    something, and so a stored model is recognisable in a list.
    """
    has = lambda *names: any(any(n in op for op in ops) for n in names)  # noqa: E731
    # Quantization renames everything — a Gemm becomes MatMulInteger, a Conv
    # becomes QLinearConv — so a model that has been through int8 has to be
    # recognisable as the architecture it still is.
    conv = sum(v for k, v in ops.items() if 'Conv' in k)
    matmul = sum(v for k, v in ops.items() if k in (
        'MatMul', 'Gemm', 'FusedGemm', 'MatMulInteger', 'QLinearMatMul', 'QGemm',
        'DynamicQuantizeMatMul', 'MatMulIntegerToFloat'))
    if has('TreeEnsemble', 'LinearClassifier', 'SVM'):
        return 'tree/linear (ai.onnx.ml)'
    if has('Attention', 'MultiHeadAttention') or (
            has('Softmax') and has('LayerNormalization', 'SkipLayerNorm') and matmul >= 4):
        return 'transformer'
    if has('LSTM', 'GRU', 'RNN'):
        return 'recurrent'
    if conv and conv >= matmul:
        return 'convolutional'
    if matmul:
        return 'feed-forward'
    return 'other'


def _summary(path):
    model = _load(path)
    ops = _ops(model)
    nparams, pbytes, dtypes = _params(model)
    return {
        'nodes': sum(ops.values()),
        'params': nparams,
        'weight_bytes': pbytes,
        'weight_dtypes': dtypes,
        'opset': max([i.version for i in model.opset_import if i.domain in ('', 'ai.onnx')]
                     or [0]),
        'arch': _architecture(ops),
    }


def inspect(ref):
    """Everything the file will tell you about itself."""
    rec, path = resolve(ref)
    model = _load(path)
    ops = _ops(model)
    nparams, pbytes, dtypes = _params(model)
    size = os.path.getsize(path)
    return {
        'id': rec.get('id'), 'name': rec.get('name'), 'path': path,
        'bytes': size, 'mb': round(size / 1e6, 3),
        'sha256': rec.get('sha256'),
        'producer': f"{model.producer_name} {model.producer_version}".strip(),
        'ir_version': model.ir_version,
        'opset': {i.domain or 'ai.onnx': i.version for i in model.opset_import},
        'arch': _architecture(ops),
        'nodes': sum(ops.values()),
        'ops': ops,
        'params': nparams,
        'weight_bytes': pbytes,
        'weight_dtypes': dtypes,
        'weights_share': round(pbytes / size, 3) if size else 0,
        'inputs': _io(model.graph.input),
        'outputs': _io(model.graph.output),
        'parent': rec.get('parent'), 'passes': rec.get('passes'),
        'source': rec.get('source'),
        'portable': portable(ref)['portable'],
    }


def portable(ref):
    """Will these exact bytes run in a browser? A prediction, not a proof.

    The proof is the console, which loads the same blob into onnxruntime-web
    and runs it — and the console is why this function is narrower than it
    used to be. The obvious rule, "anything outside standard ONNX will fail",
    is wrong: the wasm build registers onnxruntime's contrib operators, so a
    model fused by `extended` runs there fine. What genuinely fails is the
    layout family `all` emits, and custom domains from somebody else's build.

    Three answers, then: portable, portable-with-a-caveat, and no.
    """
    rec, path = resolve(ref)
    model = _load(path)
    domains, blocked, contrib = set(), {}, {}
    for node in _all_nodes(model.graph):
        d = node.domain or ''
        domains.add(d)
        if d in STANDARD_DOMAINS:
            continue
        key = f'{d}.{node.op_type}'
        if d.startswith(BLOCKED_PREFIXES) or d not in CONTRIB_DOMAINS:
            blocked[key] = blocked.get(key, 0) + 1
        else:
            contrib[key] = contrib.get(key, 0) + 1
    opset = max([i.version for i in model.opset_import
                 if i.domain in ('', 'ai.onnx')] or [0])
    top = lambda h: ', '.join(f'{k}×{v}' for k, v in                      # noqa: E731
                             sorted(h.items(), key=lambda kv: -kv[1])[:6])
    reasons, cautions = [], []
    if blocked:
        reasons.append('operators no browser build registers: ' + top(blocked))
    if opset > WEB_OPSET:
        reasons.append(f'opset {opset} is newer than the {WEB_OPSET} '
                       'onnxruntime-web implements')
    if contrib:
        cautions.append(
            'fused into onnxruntime contrib operators (' + top(contrib) + '). '
            'The wasm backend registers these and runs them — measured, not '
            'assumed — but they are not standard ONNX: another runtime, and '
            'the WebGPU backend on some ops, may not have them. Run it in the '
            'console before you ship it.')
    if os.path.getsize(path) > 100e6:
        cautions.append(f'{os.path.getsize(path) / 1e6:.0f} MB is a long '
                        'download before the first inference')
    return {
        'id': rec.get('id'), 'portable': not blocked and opset <= WEB_OPSET,
        'domains': sorted(domains), 'opset': opset, 'web_opset': WEB_OPSET,
        'blocked_ops': blocked, 'contrib_ops': contrib,
        'reasons': reasons, 'cautions': cautions,
        'checked': 'statically — run it in the console to actually prove it',
    }


# ── the passes ───────────────────────────────────────────────────

def _ort():
    global _ORT
    try:
        return _ORT
    except NameError:
        pass
    try:
        import onnxruntime as ort
    except ImportError as e:                                  # pragma: no cover
        raise InferError(f'onnxruntime is not installed here — {e}', 501)
    ort.set_default_logger_severity(3)
    globals()['_ORT'] = ort
    return ort


def _graph_level(level):
    ort = _ort()
    return {
        'basic': ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
        'extended': ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
        'all': ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
    }[level]


def _pass_graph(src, dst, level):
    """Hand the graph to onnxruntime's own optimizer and keep what comes back.

    ORT already knows how to fold constants, drop dead nodes and fuse
    Conv+BatchNorm or MatMul+Add into one kernel. Re-implementing that would be
    worse and slower to trust, so this pass just asks it to serialize the graph
    it was going to run anyway.
    """
    ort = _ort()
    so = ort.SessionOptions()
    so.graph_optimization_level = _graph_level(level)
    so.optimized_model_filepath = dst
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        ort.InferenceSession(src, so, providers=['CPUExecutionProvider'])
    note = 'fused and folded by onnxruntime'
    if level == 'all':
        note += ' — level `all` emits ops for the CPU that ran it '\
                '(com.microsoft.nchwc.*), which a browser cannot load; that is '\
                'why it is in no default plan'
    return note


def _pass_slim(src, dst):
    """Drop what only ever mattered to the trainer: doc strings, training
    graphs, and initializers no node reads any more."""
    model = _load(src)
    model.doc_string = ''
    del model.training_info[:]
    used = set()
    for node in _all_nodes(model.graph):
        node.doc_string = ''
        used.update(node.input)
    used.update(o.name for o in model.graph.output)
    keep = [i for i in model.graph.initializer if i.name in used]
    dropped = len(model.graph.initializer) - len(keep)
    if dropped:
        del model.graph.initializer[:]
        model.graph.initializer.extend(keep)
    names = {i.name for i in model.graph.initializer}
    stale = [v for v in model.graph.input if v.name not in names and v.name not in used]
    onnx.save(model, dst)
    return (f'dropped {dropped} unused initializer(s)' if dropped
            else 'doc strings and training info stripped') + \
        (f', {len(stale)} dangling input(s) left alone' if stale else '')


def _pass_shapes(src, dst):
    model = onnx.shape_inference.infer_shapes(_load(src), strict_mode=False)
    onnx.save(model, dst)
    return 'every intermediate tensor now carries its shape'


def _pass_float16(src, dst):
    from onnxruntime.transformers.float16 import convert_float_to_float16
    model = convert_float_to_float16(_load(src), keep_io_types=True,
                                     disable_shape_infer=False)
    onnx.save(model, dst)
    # keep_io_types leaves the inputs and outputs fp32, so nothing calling this
    # model has to change — the halving happens strictly inside.
    return 'weights and activations in fp16, fp32 in and out'


def _pass_quant(src, dst, qtype):
    from onnxruntime.quantization import QuantType, quantize_dynamic
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        quantize_dynamic(src, dst, weight_type=getattr(QuantType, qtype),
                         extra_options={'EnableSubgraph': False})
    return ('weights quantized to 8-bit, dequantized per-op at run time — '
            'no calibration data needed, and the answers move')


PASSES = {
    'slim': {'what': 'strip doc strings, training info and unused initializers',
             'lossy': False, 'fn': lambda s, d: _pass_slim(s, d)},
    'shapes': {'what': 'run shape inference so every tensor is annotated',
               'lossy': False, 'fn': lambda s, d: _pass_shapes(s, d)},
    'basic': {'what': 'onnxruntime graph optimization: constant folding, dead '
                      'node elimination, redundant cast/identity removal',
              'lossy': False, 'fn': lambda s, d: _pass_graph(s, d, 'basic')},
    'extended': {'what': 'basic, plus operator fusion (Conv+BN, MatMul+Add, '
                         'GELU, attention) — the default. Emits onnxruntime '
                         'contrib operators, which the browser wasm backend '
                         'does register',
                 'lossy': False, 'fn': lambda s, d: _pass_graph(s, d, 'extended')},
    'all': {'what': 'extended, plus layout transforms specific to the machine '
                    'that ran them — the fastest thing here, and the one that '
                    'genuinely will not load in a browser',
            'lossy': False, 'portable': False,
            'fn': lambda s, d: _pass_graph(s, d, 'all')},
    'fp16': {'what': 'half-precision weights: about half the bytes, and the '
                     'format a browser GPU backend actually wants',
             'lossy': True, 'fn': lambda s, d: _pass_float16(s, d)},
    'int8': {'what': 'dynamic int8 quantization of the weights — the biggest '
                     'size win there is, and the one that moves the answers',
             'lossy': True, 'fn': lambda s, d: _pass_quant(s, d, 'QInt8')},
    'uint8': {'what': 'dynamic uint8 quantization — try it when int8 is slower, '
                      'some kernels only have a fast path for one of the two',
              'lossy': True, 'fn': lambda s, d: _pass_quant(s, d, 'QUInt8')},
}
DEFAULT_PASSES = ['slim', 'extended']


def passes():
    """The catalog, with whether each one can run on this box."""
    out = {}
    for name, spec in PASSES.items():
        entry = {'what': spec['what'], 'lossy': spec['lossy'],
                 'portable': spec.get('portable', True), 'available': True}
        if name in ('int8', 'uint8'):
            try:
                import onnxruntime.quantization  # noqa: F401
            except Exception as e:
                entry.update(available=False, reason=str(e))
        if name == 'fp16':
            try:
                from onnxruntime.transformers import float16  # noqa: F401
            except Exception as e:
                entry.update(available=False, reason=str(e))
        out[name] = entry
    return {'passes': out, 'default': DEFAULT_PASSES,
            'order': 'left to right; `slim` first and a quantizer last is the '
                     'order that composes — quantizing before fusion hides the '
                     'patterns the fuser is looking for'}


# ── running it ───────────────────────────────────────────────────

def _resolve_dims(shape, batch, shapes):
    """Turn a declared shape, symbolic dims and all, into real numbers.

    A model with `batch × sequence × 768` cannot be benchmarked until somebody
    decides what batch and sequence are. The first symbolic dim takes `batch`,
    the rest take 1, and `shapes` overrides any of it by name.
    """
    dims, symbolic = [], 0
    for d in shape:
        if isinstance(d, int) and d > 0:
            dims.append(d)
        else:
            dims.append(int(batch) if symbolic == 0 else 1)
            symbolic += 1
    return dims


def _parse_shapes(shapes):
    """`{"x": "1,16"}`, `'{"x":"1,16"}'` or `'x:1,16;y:1'` — all the same thing.

    MCP hands this over as an object and a query string cannot, so both forms
    have to arrive at the same dict or the REST and tool layers would disagree
    about what a benchmark measured.
    """
    if not shapes:
        return {}
    if isinstance(shapes, dict):
        return shapes
    text = str(shapes).strip()
    if text.startswith('{'):
        try:
            return json.loads(text)
        except Exception:
            raise InferError(f'shapes= is not valid JSON: {text[:80]}')
    out = {}
    for part in text.replace(';', ' ').split():
        if ':' not in part:
            raise InferError('shapes= should look like "x:1,16" or '
                             '{"x": "1,16"}, got ' + repr(part))
        name, dims = part.split(':', 1)
        out[name.strip()] = dims.strip()
    return out


def _feed(model_path, batch=1, seed=0, shapes=None):
    """Random inputs shaped the way the model says it wants them."""
    model = _load(model_path)
    rng = np.random.default_rng(seed)
    shapes = _parse_shapes(shapes)
    feed, described = {}, []
    for spec in _io(model.graph.input):
        name = spec['name']
        if name in shapes:
            dims = [int(x) for x in str(shapes[name]).replace('x', ',').split(',') if x]
        else:
            dims = _resolve_dims(spec['shape'], batch, shapes)
        dtype = np.dtype(spec['dtype']) if not spec['dtype'].startswith('UNDEFINED') \
            else np.dtype('float32')
        if dtype.kind == 'f':
            arr = rng.standard_normal(dims).astype(dtype)
        elif dtype.kind == 'b':
            arr = rng.integers(0, 2, dims).astype(dtype)
        else:
            # Integer inputs are almost always indices into an embedding table,
            # and a random int64 is an out-of-range lookup and a crash. 0 and 1
            # are in range for any table with two rows in it.
            arr = rng.integers(0, 2, dims).astype(dtype)
        feed[name] = arr
        described.append({'name': name, 'shape': dims, 'dtype': str(dtype)})
    if not feed:
        raise InferError('this model declares no inputs — nothing to feed it')
    return feed, described


def _session(path, threads=None, provider=None):
    ort = _ort()
    so = ort.SessionOptions()
    # The passes are the experiment. Leaving ORT's own optimizer on would
    # silently apply them again at load time and every measurement would come
    # out the same.
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    if threads:
        so.intra_op_num_threads = int(threads)
    providers = [provider] if provider else ['CPUExecutionProvider']
    available = ort.get_available_providers()
    missing = [p for p in providers if p not in available]
    if missing:
        raise InferError(f'no {missing[0]} on this box — have: {available}')
    return ort.InferenceSession(path, so, providers=providers)


def bench(ref, runs=DEFAULT_RUNS, warmup=DEFAULT_WARMUP, batch=1, threads=None,
          provider=None, seed=0, shapes=None):
    """Time it honestly: fixed inputs, warmed up, reported as percentiles.

    A mean over a cold session measures the allocator. p50 over a warm one
    measures the model, and p99 is where a browser tab drops a frame.
    """
    rec, path = resolve(ref)
    feed, described = _feed(path, batch=batch, seed=seed, shapes=shapes)
    sess = _session(path, threads=threads, provider=provider)
    names = {i.name for i in sess.get_inputs()}
    feed = {k: v for k, v in feed.items() if k in names}
    for _ in range(int(warmup)):
        sess.run(None, feed)
    times = []
    for _ in range(max(1, int(runs))):
        t0 = time.perf_counter()
        sess.run(None, feed)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    pct = lambda p: times[min(len(times) - 1, int(len(times) * p))]  # noqa: E731
    p50 = pct(0.50)
    return {
        'id': rec.get('id'), 'name': rec.get('name'),
        'runs': len(times), 'warmup': int(warmup), 'batch': int(batch),
        'provider': sess.get_providers()[0],
        'threads': int(threads) if threads else 'default',
        'inputs': described,
        'ms': {'p50': round(p50, 4), 'p90': round(pct(0.90), 4),
               'p99': round(pct(0.99), 4), 'min': round(times[0], 4),
               'max': round(times[-1], 4),
               'mean': round(statistics.fmean(times), 4),
               'stdev': round(statistics.pstdev(times), 4)},
        'throughput_per_s': round(1000.0 * int(batch) / p50, 2) if p50 else None,
        'bytes': os.path.getsize(path),
    }


def parity(a, b, samples=8, batch=1, tol=1e-3, seed=0, shapes=None):
    """Do two models still answer the same question the same way?

    Fusion and folding should be bit-for-bit boring. Quantization is not — it
    is supposed to move the numbers, and the question becomes *how far*, and
    whether the argmax survived. Both cases are reported the same way, so the
    caller decides what is acceptable instead of this function pretending to
    know.
    """
    rec_a, path_a = resolve(a)
    rec_b, path_b = resolve(b)
    sess_a, sess_b = _session(path_a), _session(path_b)
    names_a = {i.name for i in sess_a.get_inputs()}
    names_b = {i.name for i in sess_b.get_inputs()}
    worst = {'max_abs': 0.0, 'max_rel': 0.0}
    agree, compared, cosines = 0, 0, []
    for i in range(max(1, int(samples))):
        feed, _ = _feed(path_a, batch=batch, seed=seed + i, shapes=shapes)
        out_a = sess_a.run(None, {k: v for k, v in feed.items() if k in names_a})
        out_b = sess_b.run(None, {k: v for k, v in feed.items() if k in names_b})
        if len(out_a) != len(out_b):
            raise InferError(f'{rec_a.get("name")} returns {len(out_a)} outputs, '
                             f'{rec_b.get("name")} returns {len(out_b)} — not '
                             'the same model any more')
        for x, y in zip(out_a, out_b):
            x = np.asarray(x).astype('float64')
            y = np.asarray(y).astype('float64')
            if x.shape != y.shape:
                raise InferError(f'output shapes diverged: {x.shape} vs {y.shape}')
            if x.dtype.kind not in 'fiu':
                continue
            diff = np.abs(x - y)
            worst['max_abs'] = max(worst['max_abs'], float(diff.max()))
            denom = np.maximum(np.abs(x), 1e-12)
            worst['max_rel'] = max(worst['max_rel'], float((diff / denom).max()))
            flat_x, flat_y = x.ravel(), y.ravel()
            norm = np.linalg.norm(flat_x) * np.linalg.norm(flat_y)
            if norm:
                cosines.append(float(flat_x @ flat_y / norm))
            if x.ndim >= 2 and x.shape[-1] > 1:
                agree += int((x.argmax(-1) == y.argmax(-1)).all())
                compared += 1
    verdict = 'identical' if worst['max_abs'] == 0 else (
        'within tolerance' if worst['max_abs'] <= tol else 'outputs moved')
    return {
        'a': rec_a.get('id') or rec_a.get('name'),
        'b': rec_b.get('id') or rec_b.get('name'),
        'samples': int(samples), 'batch': int(batch), 'tol': tol,
        'max_abs_err': worst['max_abs'], 'max_rel_err': worst['max_rel'],
        'cosine': round(min(cosines), 6) if cosines else None,
        'argmax_agreement': (round(agree / compared, 4) if compared else None),
        'ok': worst['max_abs'] <= tol,
        'verdict': verdict,
    }


# ── the optimizer ────────────────────────────────────────────────

def _delta(before, after):
    """Which ops went away and which appeared — the readable half of a pass."""
    removed = {k: before[k] - after.get(k, 0) for k in before
               if before[k] > after.get(k, 0)}
    added = {k: after[k] - before.get(k, 0) for k in after
             if after[k] > before.get(k, 0)}
    return removed, added


def plan(ref, target='local'):
    """What to try on this model, and why — read off the graph it actually is.

    `target` is the fork that matters. Optimizing for this box and optimizing
    for a browser are not the same request: the fusions that make it fastest
    here are emitted into onnxruntime's own operator domain, and a browser
    build does not have those kernels. So `target=web` deliberately leaves
    speed on the table to keep every node inside standard ONNX.
    """
    info = inspect(ref)
    web = str(target).lower() in ('web', 'browser')
    ops, why = info['ops'], []
    want = list(DEFAULT_PASSES)
    if web:
        why.append('target=web: `slim,extended` is the same floor as local — the '
                   'browser wasm backend does register the contrib operators '
                   'that fusion emits, which was measured rather than assumed. '
                   'What a web build must never include is `all`: it emits '
                   'com.microsoft.nchwc layout ops for the CPU that ran it, and '
                   'those really are missing in the browser')
    else:
        why.append('slim + extended is the floor: both are lossless, so there '
                   'is no reason not to ship them')
    heavy = info['weight_bytes'] > 2e6 and info['weights_share'] > 0.3
    if web and info['bytes'] > 4e6:
        want.append('int8')
        why.append(f"{info['bytes'] / 1e6:.1f} MB has to cross the network "
                   'before the first inference, so in a browser the download '
                   'usually dominates the arithmetic — quantize, then check '
                   '`parity` to see what it cost')
    elif heavy:
        want.append('int8')
        why.append(f"{info['weight_bytes'] / 1e6:.1f} MB of this model is weights "
                   f"({info['weights_share'] * 100:.0f}% of the file) — int8 is "
                   'the only pass that touches that, and it is worth the '
                   'accuracy check')
    elif info['params'] > 100_000:
        want.append('fp16')
        why.append('too small for int8 to pay for its dequantize overhead, big '
                   'enough that fp16 halves the download')
    else:
        why.append(f"only {info['params']:,} parameters — quantization would "
                   'cost more in dequantize nodes than it saves in bytes')
    if info['arch'] == 'transformer':
        why.append('transformer: `extended` is doing the real work here, it '
                   'fuses attention and GELU into single kernels')
    if info['arch'] == 'convolutional':
        why.append('convolutional: `extended` folds BatchNorm into the '
                   'preceding Conv, which removes a whole pass over the data')
    if not info['portable']:
        why.append('this model is not browser-portable as it stands — see '
                   '`portable` for which ops are the problem')
    return {'id': info['id'], 'name': info['name'], 'arch': info['arch'],
            'target': 'web' if web else 'local',
            'params': info['params'], 'bytes': info['bytes'],
            'plan': want, 'why': why,
            'run': f"optimize {info['id'] or info['name']} {','.join(want)}"}


def optimize(ref, passes_=None, name=None, check=True, runs=DEFAULT_RUNS,
             batch=1, samples=4, tol=1e-3, shapes=None, threads=None):
    """Run passes over a model and report what each one actually bought.

    Nothing is taken on faith. Every pass is timed and diffed, the result is
    benchmarked against the original in the same process on the same inputs,
    the outputs of both are compared, and the portability of the result is
    re-checked — because the fastest local graph is quite often the one that
    stopped being able to run anywhere else.
    """
    import shutil
    import tempfile

    rec, path = resolve(ref)
    chosen = passes_ if passes_ is not None else DEFAULT_PASSES
    if isinstance(chosen, str):
        chosen = [p.strip() for p in chosen.replace(' ', ',').split(',') if p.strip()]
    unknown = [p for p in chosen if p not in PASSES]
    if unknown:
        raise InferError(f'no such pass: {", ".join(unknown)} — have: '
                         + ', '.join(PASSES))
    if not chosen:
        raise InferError('no passes given, and nothing to do without them')

    tmp = tempfile.mkdtemp(prefix='infer-')
    steps, current = [], os.path.join(tmp, 'step0.onnx')
    shutil.copyfile(path, current)
    try:
        for i, pname in enumerate(chosen, 1):
            spec = PASSES[pname]
            before_ops = _ops(_load(current))
            before_bytes = os.path.getsize(current)
            dst = os.path.join(tmp, f'step{i}.onnx')
            t0 = time.perf_counter()
            try:
                note = spec['fn'](current, dst)
                ok = os.path.exists(dst)
            except Exception as e:
                steps.append({'pass': pname, 'ok': False, 'skipped': True,
                              'error': f'{type(e).__name__}: {e}',
                              'what': spec['what']})
                continue
            if not ok:
                steps.append({'pass': pname, 'ok': False, 'skipped': True,
                              'error': 'the pass produced no file',
                              'what': spec['what']})
                continue
            after_ops = _ops(_load(dst))
            after_bytes = os.path.getsize(dst)
            removed, added = _delta(before_ops, after_ops)
            steps.append({
                'pass': pname, 'ok': True, 'lossy': spec['lossy'],
                'what': spec['what'], 'note': note,
                'ms': round((time.perf_counter() - t0) * 1000, 1),
                'bytes': {'before': before_bytes, 'after': after_bytes,
                          'delta': after_bytes - before_bytes},
                'nodes': {'before': sum(before_ops.values()),
                          'after': sum(after_ops.values())},
                'removed': removed, 'added': added,
            })
            current = dst

        applied = [s['pass'] for s in steps if s.get('ok')]
        if not applied:
            raise InferError('every pass failed — nothing was produced',
                             steps=steps)
        with open(current, 'rb') as f:
            data = f.read()
        out = store(
            data,
            name=name or f"{rec.get('name') or rec.get('id')}+{'+'.join(applied)}",
            source='optimize', parent=rec.get('id') or rec.get('name'),
            passes=applied)

        report = {
            'source': {'id': rec.get('id'), 'name': rec.get('name'),
                       'bytes': os.path.getsize(path),
                       'nodes': _summary(path)['nodes']},
            'result': {'id': out['id'], 'name': out['name'],
                       'bytes': out['bytes'], 'nodes': out.get('nodes')},
            'passes': steps, 'applied': applied,
        }
        src_bytes, dst_bytes = report['source']['bytes'], out['bytes']
        report['size'] = {
            'before': src_bytes, 'after': dst_bytes,
            'saved': src_bytes - dst_bytes,
            'ratio': round(src_bytes / dst_bytes, 3) if dst_bytes else None,
            'percent': round(100 * (src_bytes - dst_bytes) / src_bytes, 1)
            if src_bytes else 0}
        report['portable'] = portable(out['id'])
        was = portable(rec.get('id') or path)
        if was['portable'] and not report['portable']['portable']:
            # The most common way this module could mislead someone: the local
            # numbers improve, the model quietly stops running in a browser,
            # and nobody finds out until the page 404s on a kernel.
            report['portability_lost'] = {
                'by': applied,
                'ops': report['portable']['blocked_ops'],
                'note': 'this ran in a browser before these passes and does not '
                        'now. In practice one pass does this: `all`, which '
                        'rewrites the graph into layout operators for the CPU '
                        'that ran it. Drop it — `slim,extended` keeps almost '
                        'all of the speed and still loads in a browser — or '
                        'keep both binaries and serve each where it works.',
                'instead': 'optimize {} {}'.format(
                    rec.get('id') or rec.get('name'),
                    ','.join(p for p in applied if p != 'all') or 'slim,extended'),
            }

        if check:
            try:
                b0 = bench(rec.get('id') or path, runs=runs, batch=batch,
                           shapes=shapes, threads=threads)
                b1 = bench(out['id'], runs=runs, batch=batch, shapes=shapes,
                           threads=threads)
                # A sub-millisecond model measured 30 times will hand you a
                # 5% "speedup" that is really the scheduler. Carry the spread
                # alongside the number so the verdict can decline to claim it.
                # p90 − p50 rather than the standard deviation: one descheduled
                # run inflates a stdev enough to swallow a genuine win, and the
                # point is to suppress noise, not results.
                spread = ((b0['ms']['p90'] - b0['ms']['p50'])
                          + (b1['ms']['p90'] - b1['ms']['p50']))
                gap = abs(b0['ms']['p50'] - b1['ms']['p50'])
                report['speed'] = {
                    'before_ms_p50': b0['ms']['p50'], 'after_ms_p50': b1['ms']['p50'],
                    'speedup': round(b0['ms']['p50'] / b1['ms']['p50'], 3)
                    if b1['ms']['p50'] else None,
                    'spread_ms': round(spread, 4), 'gap_ms': round(gap, 4),
                    'within_noise': gap <= spread,
                    'runs': b0['runs'], 'batch': int(batch),
                    'provider': b1['provider']}
                report['parity'] = parity(rec.get('id') or path, out['id'],
                                          samples=samples, batch=batch, tol=tol,
                                          shapes=shapes)
            except InferError as e:
                report['check_failed'] = e.message
            except Exception as e:
                report['check_failed'] = f'{type(e).__name__}: {e}'
        report['verdict'] = _verdict(report)
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _verdict(report):
    """One sentence a human can act on, instead of four nested dicts."""
    bits = []
    speed = report.get('speed') or {}
    if speed.get('speedup'):
        s = speed['speedup']
        if speed.get('within_noise'):
            # Refusing to claim a number the measurement cannot support is the
            # whole reason to report percentiles instead of a single mean.
            bits.append(f"no measurable speed change ({speed['gap_ms']:.3f} ms "
                        f"apart, {speed['spread_ms']:.3f} ms of run-to-run "
                        'spread — raise runs= to decide)')
        else:
            bits.append(f'{s:.2f}× faster' if s >= 1.02 else
                        (f'{1 / s:.2f}× slower' if s <= 0.98 else 'the same speed'))
    size = report.get('size') or {}
    if size.get('ratio'):
        bits.append(f"{size['ratio']:.2f}× smaller "
                    f"({size['before'] / 1e6:.2f} → {size['after'] / 1e6:.2f} MB)"
                    if size['ratio'] >= 1.01 else
                    f"{size['after'] / 1e6:.2f} MB, no smaller")
    par = report.get('parity') or {}
    if par:
        err = par.get('max_abs_err')
        bits.append('outputs identical' if err == 0 else
                    f"outputs moved by at most {err:.2e}" +
                    (f", argmax agrees {par['argmax_agreement'] * 100:.0f}% "
                     'of the time' if par.get('argmax_agreement') is not None else ''))
    port = report.get('portable') or {}
    if port.get('portable'):
        bits.append('runs in the browser')
    elif report.get('portability_lost'):
        bits.append('LOCAL ONLY NOW — it ran in the browser before these passes')
    else:
        bits.append('local only: ' + '; '.join(port.get('reasons') or ['?']))
    return ', '.join(bits) or 'nothing measured'


# ── getting a model in ───────────────────────────────────────────

def add(data=None, path=None, url=None, name=None, note=None):
    """Take in an .onnx from bytes, base64, a path, or a URL."""
    import base64
    import urllib.request
    if data is not None:
        if isinstance(data, str):
            try:
                data = base64.b64decode(data, validate=True)
            except Exception:
                raise InferError('data= must be base64-encoded ONNX bytes')
        source = 'upload'
    elif path:
        p = os.path.expanduser(path)
        if not os.path.isfile(p):
            raise InferError(f'no file at {p}', 404)
        with open(p, 'rb') as f:
            data = f.read()
        source, name = f'file:{p}', name or os.path.basename(p)[:-5] or None
    elif url:
        if not str(url).startswith(('http://', 'https://')):
            raise InferError('url= must be http(s)')
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read(MAX_BYTES + 1)
        source, name = f'url:{url}', name or os.path.basename(url).split('?')[0]
    else:
        raise InferError('nothing to add — pass data=, path= or url=')
    # No sniffing of magic bytes: store() re-reads the graph on the way in, so
    # "is this a model" is answered by parsing it rather than by guessing from
    # a header. Anything that will not parse is unstored again immediately.
    rec = store(data, name=name, source=source, note=note)
    if rec.get('error'):
        delete(rec['id'])
        raise InferError(f"that is not a model this can read — {rec['error']}")
    return rec


def _torch():
    try:
        import torch
        return torch
    except ImportError as e:
        raise InferError(f'torch is not installed here, so there is nothing to '
                         f'export from — add an .onnx directly instead ({e})', 501)


def _torch_export(module, example, dst, opset=17, dynamic_batch=True):
    """torch → onnx, whichever exporter this torch has.

    2.9 made the torch.export-based path the default and it needs onnxscript;
    where that is missing the TorchScript path is still there and still
    correct, so try the new one and fall back rather than requiring an install.
    """
    torch = _torch()
    module = module.eval()
    names_in = ['input']
    dynamic = {'input': {0: 'batch'}, 'output': {0: 'batch'}} if dynamic_batch else None
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            torch.onnx.export(module, example, dst, input_names=names_in,
                              output_names=['output'], dynamic_axes=dynamic,
                              opset_version=opset)
        except Exception:
            torch.onnx.export(module, example, dst, input_names=names_in,
                              output_names=['output'], dynamic_axes=dynamic,
                              opset_version=opset, dynamo=False)
    return dst


def export(source, name=None, opset=17, shape=None, weights=None):
    """A model that is not ONNX yet → the standard binary.

        export torchvision:resnet18
        export mymodel.py                  # a file exposing `model` (+ `example`)
        export traced.pt shape=1,3,224,224 # a TorchScript archive
    """
    import tempfile
    torch = _torch()
    tmp = tempfile.mkdtemp(prefix='infer-export-')
    dst = os.path.join(tmp, 'model.onnx')
    dims = [int(x) for x in str(shape).replace('x', ',').split(',')] if shape else None
    try:
        if str(source).startswith('torchvision:'):
            import torchvision
            which = source.split(':', 1)[1]
            factory = getattr(torchvision.models, which, None)
            if factory is None:
                raise InferError(f'torchvision has no model {which!r}')
            module = factory(weights=weights)
            example = torch.randn(*(dims or [1, 3, 224, 224]))
            label = name or which
        elif str(source).endswith('.py'):
            import runpy
            ns = runpy.run_path(os.path.expanduser(source))
            module = ns.get('model')
            if module is None:
                raise InferError(f'{source} defines no `model`')
            example = ns.get('example')
            if example is None:
                if not dims:
                    raise InferError(f'{source} defines no `example` input — '
                                     'pass shape=1,3,224,224')
                example = torch.randn(*dims)
            label = name or os.path.basename(source)[:-3]
        else:
            p = os.path.expanduser(str(source))
            if not os.path.isfile(p):
                raise InferError(f'no file at {p}', 404)
            obj = torch.jit.load(p) if p.endswith(('.pt', '.pth')) else None
            if obj is None:
                raise InferError('only torchvision:<name>, a .py defining '
                                 '`model`, or a TorchScript .pt can be exported')
            module, label = obj, name or os.path.basename(p).rsplit('.', 1)[0]
            if not dims:
                raise InferError('a TorchScript archive does not carry its input '
                                 'shape — pass shape=1,3,224,224')
            example = torch.randn(*dims)
        _torch_export(module, example, dst, opset=opset)
        with open(dst, 'rb') as f:
            data = f.read()
        rec = store(data, name=label, source=f'export:{source}')
        rec['exported_from'] = str(source)
        rec['note'] = ('weights are random unless you asked for pretrained ones — '
                       'the graph is real, the numbers are not'
                       if weights is None and str(source).startswith('torchvision:')
                       else rec.get('note'))
        return rec
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _example_models():
    """Three architectures, so the module has something to be tried on."""
    torch = _torch()
    nn = torch.nn

    class MLP(nn.Module):
        """Deliberately holds a BatchNorm the fuser can eat."""

        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(64, 512), nn.BatchNorm1d(512), nn.ReLU(),
                nn.Linear(512, 512), nn.BatchNorm1d(512), nn.ReLU(),
                nn.Linear(512, 10))

        def forward(self, x):
            return self.net(x)

    class CNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.Sequential(
                nn.Conv2d(3, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
                nn.AdaptiveAvgPool2d(1))
            self.head = nn.Linear(64, 10)

        def forward(self, x):
            return self.head(self.body(x).flatten(1))

    class Block(nn.Module):
        """One transformer block — attention and a GELU MLP, which is where
        `extended` has the most to fuse."""

        def __init__(self, d=128, heads=4):
            super().__init__()
            self.attn = nn.MultiheadAttention(d, heads, batch_first=True)
            self.n1, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d)
            self.ff = nn.Sequential(nn.Linear(d, 512), nn.GELU(), nn.Linear(512, d))

        def forward(self, x):
            h = self.n1(x)
            x = x + self.attn(h, h, h, need_weights=False)[0]
            return x + self.ff(self.n2(x))

    return [('mlp', MLP(), torch.randn(8, 64)),
            ('cnn', CNN(), torch.randn(4, 3, 32, 32)),
            ('transformer-block', Block(), torch.randn(2, 32, 128))]


def examples():
    """Plant one of each architecture, so `optimize` has something to chew on."""
    import tempfile
    out, tmp = [], tempfile.mkdtemp(prefix='infer-examples-')
    try:
        for label, module, example in _example_models():
            dst = os.path.join(tmp, label + '.onnx')
            _torch_export(module, example, dst)
            with open(dst, 'rb') as f:
                out.append(store(f.read(), name=label, source='example'))
        return {'planted': [r['id'] for r in out], 'models': out}
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def health():
    ort = _ort()
    return {
        'ok': True,
        'onnx': onnx.__version__,
        'onnxruntime': ort.__version__,
        'providers': ort.get_available_providers(),
        'passes': [k for k, v in passes()['passes'].items() if v['available']],
        'models': len(registry()),
        'store': MODEL_DIR,
    }
