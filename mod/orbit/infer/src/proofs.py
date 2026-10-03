#!/usr/bin/env python3
"""infer proofs — a board where "the model said this" is a checkable claim.

Everything here exists to answer one question about a piece of model output:
**would it happen again?** Sampling is what usually makes that unanswerable, so
this board only accepts runs where sampling was turned off — temperature 0,
top_p 1, one candidate, no penalties. A receipt that was not greedy is refused
at the door, because a board that mixes sampled and greedy runs cannot tell a
nondeterministic model from a lucky one.

A receipt is content, not a database row:

    request_hash   sha256 of the canonical request      — the *question*
    output_hash    sha256 of the exact output bytes     — the *answer*
    hash           sha256 of the canonical claim        — the *receipt*
    attestation    a mod-protocol token over `hash`     — *who says so*
    cid            the same bundle in core/store        — *where it lives*

Nothing is taken on trust. `verify` recomputes all three hashes from the claim's
own content, recovers the signing address from the attestation, re-fetches the
bundle from the store by CID and byte-compares it, and — when the runtime is
local ONNX — actually runs the model again and checks the output hash still
comes out the same.

Receipts that share a `request_hash` are one **question**, and the question is
where the interesting part is. Same question, same output, two different
signers ⇒ `reproduced`. Same question, different outputs ⇒ `divergent`, and the
board keeps both, says which is in the majority, and points at the character
where they first parted.

Two runtimes, and they are not equally provable — the module says so rather
than flattening them:

    onnx   bit-exact. Fixed seed, one thread, ORT's own optimizer off, and the
           model addressed by the sha256 of its bytes. Anyone with the file
           gets the same output_hash or something is wrong. Re-runnable proof.
    llm    evidence, not proof. Temperature 0 is necessary and nowhere near
           sufficient: batch composition, expert routing and non-associative
           float reduction all move tokens on a hosted endpoint at temp 0.
           Replication by an independent signer is the only thing that counts,
           which is exactly what the board collects.
"""

import base64
import contextlib
import hashlib
import importlib
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import engine as E                                          # noqa: E402
from engine import InferError                               # noqa: E402

# ── state ────────────────────────────────────────────────────────

PROOF_DIR = os.path.join(E.STATE_DIR, 'proofs')
LEDGER = os.path.join(PROOF_DIR, 'receipts.json')
PROVIDERS_FILE = os.path.join(PROOF_DIR, 'providers.json')
KEYS_FILE = os.path.join(PROOF_DIR, 'keys.json')

MAX_OUTPUT = int(os.environ.get('INFER_MAX_OUTPUT', 256 * 1024))
MAX_RECEIPTS = int(os.environ.get('INFER_MAX_RECEIPTS', 20000))
HTTP_TIMEOUT = float(os.environ.get('INFER_HTTP_TIMEOUT', 180))

# core/store, reached through the activator so a slept store wakes up rather
# than refusing the connection. See the module README for why not :50152.
STORE_URL = os.environ.get('INFER_STORE_URL', 'http://localhost:9000/api/store')
STORE_ON = str(os.environ.get('INFER_STORE', '1')).lower() not in ('0', 'false', 'off')

_LOCK = threading.Lock()


def _ensure():
    os.makedirs(PROOF_DIR, exist_ok=True)


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    _ensure()
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def ledger():
    return _read_json(LEDGER, {})


# ── canonical bytes ──────────────────────────────────────────────

def canon(obj):
    """The one serialization every hash on this board is taken over.

    Sorted keys, no insignificant whitespace, UTF-8, unescaped non-ASCII. Any
    implementation in any language that follows those four rules reproduces
    these hashes, which is the point — the receipts are meant to be checkable
    off this box.
    """
    return json.dumps(obj, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def sha(data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def canon_hash(obj):
    return sha(canon(obj))


# ── the temperature-0 gate ───────────────────────────────────────

# Sampling knobs and the only value each may hold here. `None` means the caller
# left it alone, which is always fine — it is a stated non-zero that is refused.
GREEDY = {
    'temperature': (0, 0.0),
    'top_p': (1, 1.0),
    'top_k': (0, 1),
    'n': (1,),
    'best_of': (1,),
    'presence_penalty': (0, 0.0),
    'frequency_penalty': (0, 0.0),
    'repetition_penalty': (1, 1.0),
    'typical_p': (1, 1.0),
    'min_p': (0, 0.0),
}


def greedy_violations(params):
    """Which knobs would let this run come out differently next time."""
    bad = []
    for knob, allowed in GREEDY.items():
        v = (params or {}).get(knob)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            bad.append(f'{knob}={v!r} is not a number')
            continue
        if not any(abs(v - float(a)) < 1e-12 for a in allowed):
            bad.append(f'{knob}={v:g} (this board takes '
                       f'{" or ".join(f"{a:g}" for a in map(float, allowed))})')
    return bad


def require_greedy(params):
    bad = greedy_violations(params)
    if bad:
        raise InferError(
            'this board only holds greedy runs — ' + '; '.join(bad),
            422, refused=bad,
            why='a sampled run cannot be replicated, so posting one would make '
                'every divergence on the board unreadable: nobody could tell a '
                'nondeterministic model from a different random draw')


# ── model names ──────────────────────────────────────────────────

# One model reached through two routers is the same model, and the board should
# group those receipts into one question. The prefix is kept on the receipt as
# `model_as_given` so nothing is lost.
VENDORS = ('openai', 'anthropic', 'google', 'meta-llama', 'meta', 'mistralai',
           'mistral', 'deepseek', 'qwen', 'x-ai', 'cohere', 'ai21', 'amazon',
           'microsoft', 'nvidia', 'perplexity', 'nousresearch', 'openrouter')


def model_key(name):
    """`openai/gpt-4o` and `gpt-4o` are one question; `gpt-4o:free` is too."""
    s = str(name or '').strip().lower()
    if not s:
        raise InferError('a claim needs model=')
    head, _, tail = s.partition('/')
    if tail and head in VENDORS:
        s = tail
    return s.split(':')[0] if s.count(':') == 1 and s.endswith((
        ':free', ':beta', ':extended', ':nitro', ':floor')) else s


# ── requests ─────────────────────────────────────────────────────

def _messages(prompt=None, messages=None, system=None):
    if messages:
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except Exception:
                raise InferError('messages= should be a list of {role, content}')
        out = []
        for m in messages:
            if not isinstance(m, dict) or 'content' not in m:
                raise InferError('every message needs {role, content}')
            out.append({'role': str(m.get('role') or 'user'),
                        'content': m['content'] if isinstance(m['content'], str)
                        else json.loads(json.dumps(m['content'], default=str))})
        if not out:
            raise InferError('messages= is empty')
        return out
    if prompt is None or str(prompt) == '':
        raise InferError('a claim needs prompt= or messages=')
    return [{'role': 'user', 'content': str(prompt)}]


def chat_request(model, prompt=None, messages=None, system=None, max_tokens=512,
                 seed=None, stop=None, params=None):
    """The canonical form of "ask this model this, greedily".

    Every field that could change the answer is in here and nothing else is —
    no api key, no base url, no provider. Two people asking the same thing
    through different routers land on the same `request_hash`, which is what
    makes cross-provider divergence visible instead of invisible.
    """
    params = dict(params or {})
    params.setdefault('temperature', 0)
    require_greedy(params)
    if isinstance(stop, str):
        stop = [stop]
    req = {
        'kind': 'chat',
        'model': model_key(model),
        'system': str(system) if system not in (None, '') else None,
        'messages': _messages(prompt, messages, system),
        'temperature': 0.0,
        'top_p': 1.0,
        'max_tokens': int(max_tokens or 512),
        'seed': int(seed) if seed is not None and str(seed) != '' else None,
        'stop': [str(s) for s in stop] if stop else None,
    }
    return req


def onnx_request(sha256, seed=0, batch=1, shapes=None):
    """The canonical form of "run these exact bytes on these exact inputs".

    The model is named by the sha256 of its file, so the question cannot drift
    onto a different model, and every knob that changes the arithmetic is
    pinned: one thread (reduction order is not associative in float), ORT's
    graph optimizer off (so the executed graph is the file), CPU only.
    """
    return {
        'kind': 'onnx',
        'model': str(sha256),
        'seed': int(seed or 0),
        'batch': int(batch or 1),
        'shapes': {str(k): str(v) for k, v in (shapes or {}).items()} or None,
        'threads': 1,
        'providers': ['CPUExecutionProvider'],
        'graph_optimization': 'disabled',
        'inputs': 'numpy default_rng(seed): standard_normal for floats, '
                  'integers(0,2) for ints and bools, in graph input order',
    }


def request_hash(runtime, request):
    return canon_hash({'runtime': runtime, 'request': request})


# ── attestation ──────────────────────────────────────────────────

_MINE = {HERE, os.path.join(HERE, 'test')}
_AUTH = {}


def _protocol():
    """`import mod` meaning the protocol package, not this module's own mod.py.

    Every mod ships a mod.py, so whichever directory is first on sys.path
    decides what `import mod` means; from in here it resolves to ours and dies
    as "module 'mod' has no attribute 'mod'".
    """
    got = sys.modules.get('mod')
    if got is not None and hasattr(got, 'mod'):
        return got
    saved = list(sys.path)
    sys.modules.pop('mod', None)
    try:
        sys.path = [p for p in sys.path
                    if p and os.path.abspath(p) not in _MINE]
        return importlib.import_module('mod')
    finally:
        sys.path = saved
        for name in list(sys.modules):
            if name == 'mod' and not hasattr(sys.modules[name], 'mod'):
                del sys.modules[name]


def auth(key=None):
    """The fleet's shared identity — the same `m.mod('auth')` every module uses.

    max_age is deliberately enormous. A session token expires because a session
    should; an attestation that a model produced an output is a fact about the
    past and does not stop being true in a week.
    """
    cached = _AUTH.get(key)
    if cached is not None:
        return cached
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        a = _protocol().mod('auth')(crypto_type='ecdsa', key=key,
                                    max_age=10 ** 12)
    _AUTH[key] = a
    return a


def attest(claim_hash, key=None):
    """Sign a receipt hash. The token is the standard protocol envelope, so any
    module in the fleet can check it without knowing anything about infer."""
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        return auth(key).token({'receipt': claim_hash})


def signer(attestation, claim_hash=None):
    """Recover the address behind an attestation, or say why it is not one."""
    if not attestation:
        return {'signed': False, 'by': None, 'reason': 'no attestation'}
    quiet = io.StringIO()
    try:
        with contextlib.redirect_stdout(quiet):
            headers = auth().verify(attestation)
    except Exception as e:
        return {'signed': False, 'by': None,
                'reason': f'signature did not verify — {type(e).__name__}: {e}'}
    if not isinstance(headers, dict) or not headers.get('key'):
        return {'signed': False, 'by': None, 'reason': 'token carried no key'}
    got = ((headers.get('data') or {}) or {}).get('receipt')
    if claim_hash is not None and got != claim_hash:
        return {'signed': False, 'by': None,
                'reason': f'attestation is over a different receipt ({got}), '
                          f'not this one ({claim_hash})'}
    return {'signed': True, 'by': headers['key'], 'at': headers.get('time'),
            'receipt': got}


# ── core/store ───────────────────────────────────────────────────

def _http(url, data=None, headers=None, method=None, timeout=None, raw=False):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = (e.read() or b'')[:600].decode('utf-8', 'replace')
        raise InferError(f'{urllib.parse.urlsplit(url).netloc} said {e.code}: '
                         f'{detail}', 502)
    except Exception as e:
        raise InferError(f'could not reach {url} — {type(e).__name__}: {e}', 502)
    if raw:
        return body
    try:
        return json.loads(body or b'{}')
    except Exception:
        return {'raw': body[:2000].decode('utf-8', 'replace')}


def _multipart(fields, filename, payload, ctype='application/json'):
    bnd = '----infer' + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"'
                   f'\r\n\r\n{v}\r\n'.encode())
    out.append(f'--{bnd}\r\nContent-Disposition: form-data; name="file"; '
               f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode())
    out.append(payload)
    out.append(f'\r\n--{bnd}--\r\n'.encode())
    return b''.join(out), 'multipart/form-data; boundary=' + bnd


def store_put(bundle, key=None):
    """Publish a receipt bundle to core/store and get its CID back.

    The store is content-addressed, so the CID is a second, independent hash of
    the same bytes taken by somebody else's code — a receipt cannot be edited
    in place and still resolve.
    """
    payload = canon(bundle)
    body, ctype = _multipart(
        {'backend': 'localfs', 'public': 'true'},
        f"infer-receipt-{bundle.get('hash', '')[:16]}.json", payload)
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        token = auth(key).token({})
    r = _http(STORE_URL.rstrip('/') + '/put', data=body,
              headers={'content-type': ctype, 'authorization': 'Bearer ' + token})
    results = r.get('results') or {}
    first = next((v for v in results.values() if isinstance(v, dict)
                  and v.get('cid')), {})
    if not first.get('cid'):
        raise InferError(f'store accepted the upload but returned no cid: {r}', 502)
    return {'cid': first['cid'], 'backend': first.get('backend'),
            'bytes': first.get('size') or len(payload),
            'url': STORE_URL.rstrip('/') + '/get?cid=' + first['cid'],
            'owner': r.get('owner'), 'at': time.time()}


def store_get(cid):
    """The exact bytes the store holds for a CID."""
    return _http(STORE_URL.rstrip('/') + '/get?cid=' + urllib.parse.quote(str(cid)),
                 raw=True)


# ── providers ────────────────────────────────────────────────────

BUILTIN_PROVIDERS = {
    'onnx': {'style': 'onnx', 'base': None, 'env': [],
             'note': 'this box, onnxruntime on CPU — the bit-exact runtime'},
    'openrouter': {'style': 'openai', 'base': 'https://openrouter.ai/api/v1',
                   'env': ['OPENROUTER_API_KEY'],
                   'keyfile': '~/.mod/openrouter/key.json',
                   'note': 'one key, most models — and it routes, so the same '
                           'model can answer from different hardware'},
    'openai': {'style': 'openai', 'base': 'https://api.openai.com/v1',
               'env': ['OPENAI_API_KEY'],
               'note': 'returns system_fingerprint, which is the closest thing '
                       'a hosted endpoint gives you to a build id'},
    'anthropic': {'style': 'anthropic', 'base': 'https://api.anthropic.com/v1',
                  'env': ['ANTHROPIC_API_KEY'],
                  'note': 'no seed parameter exists; temperature 0 is the whole '
                          'of the determinism story here'},
    'deepseek': {'style': 'openai', 'base': 'https://api.deepseek.com/v1',
                 'env': ['DEEPSEEK_API_KEY']},
    'groq': {'style': 'openai', 'base': 'https://api.groq.com/openai/v1',
             'env': ['GROQ_API_KEY']},
    'together': {'style': 'openai', 'base': 'https://api.together.xyz/v1',
                 'env': ['TOGETHER_API_KEY']},
    'mistral': {'style': 'openai', 'base': 'https://api.mistral.ai/v1',
                'env': ['MISTRAL_API_KEY']},
    'local': {'style': 'openai',
              'base': os.environ.get('OPENAI_BASE_URL') or 'http://localhost:11434/v1',
              'env': ['OPENAI_API_KEY'],
              'note': 'ollama, vllm, llama.cpp — anything openai-shaped'},
}


def provider_table():
    table = {k: dict(v) for k, v in BUILTIN_PROVIDERS.items()}
    for name, spec in (_read_json(PROVIDERS_FILE, {}) or {}).items():
        table.setdefault(name, {'style': 'openai', 'env': []})
        table[name].update(spec or {})
        table[name]['added'] = True
    return table


def provider_spec(name):
    spec = provider_table().get(str(name or '').lower())
    if not spec:
        raise InferError(f'no provider {name!r} — have: '
                         + ', '.join(provider_table()), 404)
    return spec


def provider_key(name, explicit=None):
    """A key, from the caller or the environment. Never from config.json, and
    never written into a receipt."""
    if explicit:
        return str(explicit)
    spec = provider_spec(name)
    for var in spec.get('env') or []:
        if os.environ.get(var):
            return os.environ[var]
    saved = _read_json(KEYS_FILE, {}) or {}
    if saved.get(name):
        return str(saved[name])
    kf = spec.get('keyfile')
    if kf:
        blob = _read_json(os.path.expanduser(kf), {}) or {}
        for field in ('key', 'api_key', 'token'):
            if blob.get(field):
                return str(blob[field])
    return None


def set_key(provider, key):
    """Keep a provider key off the tree, 0600, next to the receipts."""
    provider_spec(provider)
    _ensure()
    saved = _read_json(KEYS_FILE, {}) or {}
    if key in (None, '', False):
        saved.pop(provider, None)
    else:
        saved[provider] = str(key)
    _write_json(KEYS_FILE, saved)
    try:
        os.chmod(KEYS_FILE, 0o600)
    except OSError:
        pass
    return {'provider': provider, 'key': bool(saved.get(provider)),
            'file': KEYS_FILE, 'stored': 'off-tree, 0600, never in a receipt'}


def add_provider(name, base, style='openai', note=None):
    name = str(name or '').strip().lower()
    if not name or '/' in name:
        raise InferError('a provider needs a simple name')
    if style not in ('openai', 'anthropic'):
        raise InferError("style is 'openai' or 'anthropic'")
    table = _read_json(PROVIDERS_FILE, {}) or {}
    table[name] = {'style': style, 'base': str(base).rstrip('/'), 'env': [],
                   'note': note}
    _write_json(PROVIDERS_FILE, table)
    return {'provider': name, **table[name]}


def providers():
    out = []
    for name, spec in provider_table().items():
        out.append({
            'provider': name, 'style': spec.get('style'), 'base': spec.get('base'),
            'key': bool(provider_key(name)) if spec.get('style') != 'onnx' else None,
            'ready': spec.get('style') == 'onnx' or bool(provider_key(name)),
            'env': spec.get('env') or [], 'note': spec.get('note'),
            'added': bool(spec.get('added')),
        })
    ready = [p['provider'] for p in out if p['ready']]
    return {'providers': out, 'ready': ready,
            'keys': 'set one with infer_set_key, or export the env var — keys '
                    'live in ' + KEYS_FILE + ' and never enter a receipt',
            'note': 'a provider you cannot reach is not a wall: post receipts '
                    'run anywhere else with infer_post and the board checks '
                    'them the same way'}


# ── running a model ──────────────────────────────────────────────

def run_chat(model, provider='openrouter', prompt=None, messages=None,
             system=None, max_tokens=512, seed=None, stop=None, key=None,
             params=None):
    """Ask a hosted model, greedily, and come back with a claim."""
    spec = provider_spec(provider)
    if spec.get('style') == 'onnx':
        raise InferError("provider 'onnx' runs local graphs — use run_onnx")
    req = chat_request(model, prompt=prompt, messages=messages, system=system,
                       max_tokens=max_tokens, seed=seed, stop=stop, params=params)
    api_key = provider_key(provider, key)
    if not api_key and not str(spec.get('base', '')).startswith('http://localhost'):
        raise InferError(
            f'no key for {provider} — set {" or ".join(spec.get("env") or ["a key"])}'
            f', or call infer_set_key, or post a receipt you ran elsewhere', 401)
    base = str(spec['base']).rstrip('/')
    t0 = time.time()
    if spec['style'] == 'anthropic':
        body = {'model': str(model), 'max_tokens': req['max_tokens'],
                'temperature': 0,
                'messages': [m for m in req['messages'] if m['role'] != 'system']}
        if req['system']:
            body['system'] = req['system']
        if req['stop']:
            body['stop_sequences'] = req['stop']
        r = _http(base + '/messages', data=canon(body), headers={
            'content-type': 'application/json', 'x-api-key': api_key,
            'anthropic-version': '2023-06-01'})
        text = ''.join(b.get('text') or '' for b in (r.get('content') or [])
                       if isinstance(b, dict) and b.get('type') == 'text')
        meta = {'usage': r.get('usage'), 'stop_reason': r.get('stop_reason'),
                'response_id': r.get('id'), 'served_model': r.get('model'),
                'fingerprint': None}
    else:
        msgs = ([{'role': 'system', 'content': req['system']}] if req['system'] else []) \
            + req['messages']
        body = {'model': str(model), 'messages': msgs, 'temperature': 0, 'top_p': 1,
                'max_tokens': req['max_tokens'], 'n': 1}
        if req['seed'] is not None:
            body['seed'] = req['seed']
        if req['stop']:
            body['stop'] = req['stop']
        headers = {'content-type': 'application/json',
                   'authorization': 'Bearer ' + (api_key or 'none')}
        if provider == 'openrouter':
            headers['http-referer'] = 'https://modc2.com/infer'
            headers['x-title'] = 'infer — reproducibility board'
        r = _http(base + '/chat/completions', data=canon(body), headers=headers)
        choice = (r.get('choices') or [{}])[0]
        text = ((choice.get('message') or {}).get('content')) or ''
        meta = {'usage': r.get('usage'),
                'finish_reason': choice.get('finish_reason'),
                'response_id': r.get('id'),
                'served_model': r.get('model'),
                'served_by': r.get('provider'),
                'fingerprint': r.get('system_fingerprint')}
    meta['latency_ms'] = round((time.time() - t0) * 1000, 1)
    meta['endpoint'] = base
    if not isinstance(text, str):
        text = json.dumps(text, default=str)
    return _claim('llm', provider, model, req, text, meta)


def run_onnx(model, seed=0, batch=1, shapes=None):
    """Run a stored graph and hash what came out — the reproducible half.

    Single-threaded on CPU with ORT's optimizer disabled, because both of those
    change the arithmetic: thread count changes the order of float reductions
    and the optimizer changes the graph. Pin them and the output hash is a
    property of the file, which is what makes this re-checkable by anyone.
    """
    import numpy as np
    rec, path = E.resolve(model)
    digest = rec.get('sha256') or sha(open(path, 'rb').read())
    req = onnx_request(digest, seed=seed, batch=batch, shapes=shapes)
    feed, described = E._feed(path, batch=req['batch'], seed=req['seed'],
                              shapes=shapes)
    sess = E._session(path, threads=1)
    t0 = time.time()
    outs = sess.run(None, feed)
    ms = round((time.time() - t0) * 1000, 3)
    names = [o.name for o in sess.get_outputs()]
    blocks, summary = [], []
    for name, arr in zip(names, outs):
        arr = np.ascontiguousarray(arr)
        blocks.append(b'|'.join([name.encode(), str(arr.dtype).encode(),
                                 ','.join(str(d) for d in arr.shape).encode(),
                                 arr.tobytes()]))
        flat = arr.reshape(-1)
        summary.append({
            'name': name, 'dtype': str(arr.dtype), 'shape': list(arr.shape),
            'sha256': sha(arr.tobytes()),
            'head': [round(float(x), 6) for x in flat[:8].tolist()],
            'argmax': int(flat.argmax()) if flat.size and arr.dtype.kind == 'f' else None,
        })
    output_hash = sha(b'\n'.join(blocks))
    meta = {
        'latency_ms': ms, 'inputs': described,
        'outputs': summary,
        'runtime_versions': _versions(),
        'recipe': 'sha256 of "name|dtype|shape|raw bytes" per output in graph '
                  'order, joined by \\n',
        'model_name': rec.get('name'), 'model_id': rec.get('id'),
        'blob': f"/blob/{rec.get('id') or digest[:12]}",
    }
    return _claim('onnx', 'onnx', digest, req, json.dumps(summary, sort_keys=True),
                  meta, output_hash=output_hash, model_as_given=rec.get('name'))


_VERSIONS = {}


def _versions():
    """onnx/onnxruntime versions belong in the receipt: a bit-exact claim is
    only checkable against a runtime, and these are the two that decide."""
    if not _VERSIONS:
        try:
            h = E.health()
            _VERSIONS.update({'onnx': h.get('onnx'),
                              'onnxruntime': h.get('onnxruntime'),
                              'provider': 'CPUExecutionProvider'})
        except Exception:
            _VERSIONS.update({'onnx': None, 'onnxruntime': None})
    return dict(_VERSIONS)


def _claim(runtime, provider, model, request, output, meta, output_hash=None,
           model_as_given=None):
    output = output if isinstance(output, str) else json.dumps(output, default=str)
    if len(output.encode('utf-8')) > MAX_OUTPUT:
        raise InferError(f'output is over the {MAX_OUTPUT:,}-byte limit '
                         '(raise INFER_MAX_OUTPUT)', 413)
    claim = {
        'kind': 'infer/claim@1',
        'runtime': runtime,
        'provider': str(provider),
        'model': request['model'],
        'model_as_given': str(model_as_given or model),
        'request': request,
        'request_hash': request_hash(runtime, request),
        'output': output,
        'output_hash': output_hash or sha(output),
        'output_bytes': len(output.encode('utf-8')),
        'meta': _clean(meta),
        'at': time.time(),
    }
    return claim


SECRET = re.compile(r'(api[_-]?key|authorization|bearer|secret|password|token)',
                    re.I)


def _clean(meta):
    """Nothing that looks like a credential goes into a receipt that is about to
    be published, content-addressed and made public."""
    if isinstance(meta, dict):
        return {k: ('[redacted]' if SECRET.search(str(k)) else _clean(v))
                for k, v in meta.items() if v is not None}
    if isinstance(meta, (list, tuple)):
        return [_clean(v) for v in meta]
    return meta


# ── posting ──────────────────────────────────────────────────────

def _bundle(claim, attestation=None, by=None):
    return {'kind': 'infer/receipt@1', 'claim': claim,
            'hash': canon_hash(claim), 'attestation': attestation, 'by': by}


def post(claim=None, sign=True, key=None, publish=True, attestation=None,
         **fields):
    """Put a claim on the board: hash it, sign it, publish it, index it.

    The claim may be one this box just ran or one handed over by whoever ran it
    somewhere else — the board treats them identically, because every check it
    performs is over the claim's own bytes.
    """
    if claim is None:
        claim = fields.get('receipt') or fields.get('bundle')
    if isinstance(claim, str):
        try:
            claim = json.loads(claim)
        except Exception:
            raise InferError('claim= should be a JSON object')
    if isinstance(claim, dict) and claim.get('claim'):      # a whole bundle
        attestation = attestation or claim.get('attestation')
        claim = claim['claim']
    if not isinstance(claim, dict):
        claim = _external_claim(fields)
    claim = _normalize(claim)
    require_greedy(claim['request'] if claim['runtime'] == 'llm' else {})

    if attestation:
        who = signer(attestation, canon_hash(claim))
        if not who['signed']:
            raise InferError('that attestation does not sign this claim — '
                             + str(who.get('reason')), 401)
        by = who['by']
    elif sign:
        h = canon_hash(claim)
        attestation, by = attest(h, key), auth(key).key_address()
    else:
        by = None

    bundle = _bundle(claim, attestation, by)
    rid = bundle['hash'][:16]
    store = None
    if publish and STORE_ON:
        try:
            store = store_put(bundle, key=key)
        except InferError as e:
            store = {'error': e.message}
    rec = {'id': rid, **bundle, 'store': store, 'posted': time.time()}
    with _LOCK:
        book = ledger()
        prior = book.get(rid)
        if prior:
            # Identical bytes, so it is the same receipt — keep the first CID
            # and the first signature rather than minting a second identity for
            # a claim that has not changed.
            prior['seen'] = (prior.get('seen') or 1) + 1
            prior['last_seen'] = time.time()
            book[rid] = prior
            _write_json(LEDGER, book)
            return {'receipt': prior, 'duplicate': True,
                    'question': question(claim['request_hash'])}
        book[rid] = rec
        if len(book) > MAX_RECEIPTS:
            for dead in sorted(book.values(),
                               key=lambda r: r.get('posted') or 0)[:len(book) - MAX_RECEIPTS]:
                book.pop(dead['id'], None)
        _write_json(LEDGER, book)
    return {'receipt': rec, 'duplicate': False,
            'question': question(claim['request_hash'])}


def _external_claim(f):
    """Build a claim out of loose fields, for the common case: an agent that ran
    a model somewhere else and wants the run on the board."""
    runtime = str(f.get('runtime') or 'llm').lower()
    if runtime not in ('llm', 'onnx'):
        raise InferError("runtime is 'llm' or 'onnx'")
    if f.get('output') in (None, ''):
        raise InferError('a posted claim needs output= — what the model said')
    if runtime == 'onnx':
        req = onnx_request(f.get('model'), seed=f.get('seed') or 0,
                           batch=f.get('batch') or 1, shapes=f.get('shapes'))
    else:
        req = chat_request(f.get('model'), prompt=f.get('prompt'),
                           messages=f.get('messages'), system=f.get('system'),
                           max_tokens=f.get('max_tokens') or 512,
                           seed=f.get('seed'), stop=f.get('stop'),
                           params=f.get('params') or {
                               k: f.get(k) for k in GREEDY if k in f})
    return _claim(runtime, f.get('provider') or 'unknown', f.get('model'), req,
                  f['output'], f.get('meta') or {},
                  output_hash=f.get('output_hash'),
                  model_as_given=f.get('model'))


def _normalize(claim):
    """Recompute everything derived, so a claim cannot arrive with a hash that
    flatters it. The hashes on the board are always ours."""
    for field in ('runtime', 'request', 'output'):
        if field not in claim:
            raise InferError(f'a claim needs {field}')
    c = dict(claim)
    c['kind'] = 'infer/claim@1'
    c['runtime'] = str(c['runtime']).lower()
    c['provider'] = str(c.get('provider') or 'unknown')
    c['request'] = dict(c['request'])
    c['request']['model'] = model_key(c['request'].get('model') or c.get('model'))
    c['model'] = c['request']['model']
    c['model_as_given'] = str(c.get('model_as_given') or c['model'])
    c['output'] = c['output'] if isinstance(c['output'], str) \
        else json.dumps(c['output'], sort_keys=True, default=str)
    stated = c.get('output_hash')
    computed = sha(c['output'])
    if c['runtime'] != 'onnx':
        # For text the hash IS the output, so a mismatch is a lie and gets no
        # quiet correction.
        if stated and stated != computed:
            raise InferError('output_hash does not match the output it names '
                             f'(claimed {stated}, the bytes hash to {computed})',
                             422)
        c['output_hash'] = computed
    elif not stated:
        raise InferError('an onnx claim carries output_hash over the raw tensor '
                         'bytes — run it with infer_run rather than posting it '
                         'by hand', 422)
    c['output_bytes'] = len(c['output'].encode('utf-8'))
    c['request_hash'] = request_hash(c['runtime'], c['request'])
    c['meta'] = _clean(c.get('meta') or {})
    c['at'] = float(c.get('at') or time.time())
    return c


def run(model, provider=None, runtime=None, sign=True, publish=True, key=None,
        repeat=1, **kw):
    """Run and post in one move — the path most callers want.

    repeat>1 runs the same question that many times against the same endpoint,
    which is the cheapest way to find out whether a model is deterministic at
    all before anybody else spends money replicating it.
    """
    runtime = (runtime or ('onnx' if (provider or '') == 'onnx' else 'llm')).lower()
    repeat = max(1, min(int(repeat or 1), 10))
    posted = []
    for _ in range(repeat):
        if runtime == 'onnx':
            claim = run_onnx(model, seed=kw.get('seed') or 0,
                             batch=kw.get('batch') or 1, shapes=kw.get('shapes'))
        else:
            claim = run_chat(model, provider=provider or 'openrouter',
                             prompt=kw.get('prompt'), messages=kw.get('messages'),
                             system=kw.get('system'),
                             max_tokens=kw.get('max_tokens') or 512,
                             seed=kw.get('seed'), stop=kw.get('stop'),
                             key=kw.get('api_key'), params=kw.get('params'))
        posted.append(post(claim, sign=sign, publish=publish, key=key))
    q = question(posted[0]['receipt']['claim']['request_hash'])
    out = {'runs': len(posted), 'receipts': [p['receipt']['id'] for p in posted],
           'new': sum(0 if p['duplicate'] else 1 for p in posted),
           'output': posted[0]['receipt']['claim']['output'],
           'question': q}
    if repeat > 1:
        same = len({p['receipt']['claim']['output_hash'] for p in posted}) == 1
        out['self_consistent'] = same
        out['note'] = (f'{repeat} runs, one answer — this endpoint held still'
                       if same else
                       f'{repeat} runs, '
                       f"{len({p['receipt']['claim']['output_hash'] for p in posted})}"
                       ' different answers at temperature 0 — the endpoint is '
                       'not deterministic, and no amount of replication will '
                       'make it look like it is')
    return out


def replicate(question_id=None, receipt=None, provider=None, sign=True,
              publish=True, key=None, api_key=None):
    """Ask the same question again and report whether the answer held.

    This is the whole point of the board. A claim with one receipt is somebody's
    word; a claim with two receipts from two signers is a fact about the model.
    """
    src = _pick(question_id, receipt)
    claim = src['claim']
    if claim['runtime'] == 'onnx':
        fresh = run_onnx(claim['request']['model'], seed=claim['request']['seed'],
                         batch=claim['request']['batch'],
                         shapes=claim['request'].get('shapes'))
    else:
        r = claim['request']
        fresh = run_chat(claim.get('model_as_given') or claim['model'],
                         provider=provider or claim['provider'],
                         messages=r['messages'], system=r['system'],
                         max_tokens=r['max_tokens'], seed=r['seed'],
                         stop=r['stop'], key=api_key)
    if fresh['request_hash'] != claim['request_hash']:
        raise InferError('the replication asked a different question than the '
                         'receipt did — refusing to file it as a replication',
                         500, asked=fresh['request_hash'],
                         original=claim['request_hash'])
    out = post(fresh, sign=sign, publish=publish, key=key)
    same = fresh['output_hash'] == claim['output_hash']
    q = question(claim['request_hash'])
    return {
        'reproduced': same,
        'original': {'id': src['id'], 'output_hash': claim['output_hash'],
                     'by': src.get('by'), 'provider': claim['provider']},
        'replication': {'id': out['receipt']['id'],
                        'output_hash': fresh['output_hash'],
                        'by': out['receipt'].get('by'),
                        'provider': fresh['provider']},
        'diff': None if same else _diff_text(claim['output'], fresh['output']),
        'verdict': q['verdict'],
        'question': q,
        'note': 'byte-identical output' if same else
                'the same question at temperature 0 produced different bytes — '
                'that is a real property of the endpoint, not an error here',
    }


def _pick(question_id=None, receipt=None):
    book = ledger()
    if receipt:
        rec = book.get(str(receipt)) or next(
            (r for r in book.values() if r['hash'].startswith(str(receipt))), None)
        if not rec:
            raise InferError(f'no receipt {receipt!r}', 404)
        return rec
    if not question_id:
        raise InferError('which one? pass receipt= or question=')
    rows = _receipts_for(str(question_id), book)
    if not rows:
        raise InferError(f'no question {question_id!r}', 404)
    return rows[0]


def _receipts_for(qid, book=None):
    book = book if book is not None else ledger()
    rows = [r for r in book.values()
            if r['claim']['request_hash'] == qid
            or r['claim']['request_hash'].startswith(qid)]
    return sorted(rows, key=lambda r: r['claim'].get('at') or 0)


# ── verdicts ─────────────────────────────────────────────────────

def _verdict(rows):
    """What a set of receipts for one question adds up to."""
    if not rows:
        return {'verdict': 'empty', 'receipts': 0}
    groups = {}
    for r in rows:
        groups.setdefault(r['claim']['output_hash'], []).append(r)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    top_hash, top = ranked[0]
    signers = {r.get('by') for r in rows if r.get('by')}
    top_signers = {r.get('by') for r in top if r.get('by')}
    if len(rows) == 1:
        verdict = 'unreplicated'
        why = 'one receipt — nobody has run it again'
    elif len(groups) == 1:
        if len(signers) >= 2:
            verdict, why = 'reproduced', (
                f'{len(rows)} receipts from {len(signers)} independent signers, '
                'all byte-identical')
        else:
            verdict, why = 'self-reproduced', (
                f'{len(rows)} receipts, all byte-identical, but all signed by '
                'the same key — it holds still, nobody else has checked')
    else:
        verdict, why = 'divergent', (
            f'{len(rows)} receipts split across {len(groups)} different outputs '
            'for one greedy question')
    out = {
        'verdict': verdict, 'why': why, 'receipts': len(rows),
        'outputs': len(groups), 'signers': len(signers),
        'agreement': round(len(top) / len(rows), 4),
        'majority': {'output_hash': top_hash, 'count': len(top),
                     'signers': len(top_signers),
                     'providers': sorted({r['claim']['provider'] for r in top})},
        'variants': [{'output_hash': h, 'count': len(g),
                      'providers': sorted({r['claim']['provider'] for r in g}),
                      'receipts': [r['id'] for r in g[:8]]}
                     for h, g in ranked],
    }
    if verdict == 'divergent':
        a, b = ranked[0][1][0]['claim']['output'], ranked[1][1][0]['claim']['output']
        out['first_divergence'] = _diff_text(a, b)
    return out


def _diff_text(a, b, window=90):
    """Where two greedy answers to one question stop agreeing."""
    a, b = str(a), str(b)
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    words = len(a[:i].split())
    return {
        'common_prefix_chars': i,
        'common_prefix_words': words,
        'identical': i == len(a) == len(b),
        'at': i,
        'a_len': len(a), 'b_len': len(b),
        'prefix_tail': a[max(0, i - window):i],
        'a': a[i:i + window],
        'b': b[i:i + window],
        'note': ('identical' if i == len(a) == len(b) else
                 f'agreed for {i} characters ({words} words), then parted'),
    }


def diff(a, b):
    """Two receipts, side by side, and the exact character they disagree on."""
    book = ledger()
    ra, rb = _pick(receipt=a), _pick(receipt=b)
    same_q = ra['claim']['request_hash'] == rb['claim']['request_hash']
    return {
        'a': _slim(ra), 'b': _slim(rb),
        'same_question': same_q,
        'same_output': ra['claim']['output_hash'] == rb['claim']['output_hash'],
        'diff': _diff_text(ra['claim']['output'], rb['claim']['output']),
        'note': None if same_q else
        'these are answers to DIFFERENT questions, so a difference between them '
        'says nothing about either model',
        'outputs': {'a': ra['claim']['output'][:4000],
                    'b': rb['claim']['output'][:4000]},
        'count': len(book),
    }


def _slim(r):
    c = r['claim']
    return {
        'id': r['id'], 'hash': r['hash'], 'runtime': c['runtime'],
        'model': c['model'], 'model_as_given': c.get('model_as_given'),
        'provider': c['provider'], 'request_hash': c['request_hash'],
        'output_hash': c['output_hash'], 'output_bytes': c['output_bytes'],
        'preview': c['output'][:280],
        'by': r.get('by'), 'signed': bool(r.get('attestation')),
        'cid': (r.get('store') or {}).get('cid'),
        'at': c.get('at'), 'seen': r.get('seen') or 1,
        'latency_ms': (c.get('meta') or {}).get('latency_ms'),
        'fingerprint': (c.get('meta') or {}).get('fingerprint'),
        'served_by': (c.get('meta') or {}).get('served_by'),
    }


def question(qid, full=False):
    """One question: every receipt filed against it, and what they add up to."""
    rows = _receipts_for(str(qid))
    if not rows:
        raise InferError(f'no question {qid!r} — the board lists them', 404)
    first = rows[0]['claim']
    v = _verdict(rows)
    out = {
        'question': first['request_hash'],
        'runtime': first['runtime'],
        'model': first['model'],
        'request': first['request'],
        'prompt': _prompt_of(first),
        'providers': sorted({r['claim']['provider'] for r in rows}),
        'first_at': first.get('at'),
        'last_at': rows[-1]['claim'].get('at'),
        'cids': [c for c in ((r.get('store') or {}).get('cid') for r in rows) if c],
        **v,
        'receipts': [_slim(r) for r in rows],
    }
    if full:
        out['full'] = [r['claim']['output'] for r in rows]
    return out


def _prompt_of(claim):
    if claim['runtime'] == 'onnx':
        r = claim['request']
        return f"onnx {r['model'][:12]} seed={r['seed']} batch={r['batch']}"
    msgs = claim['request'].get('messages') or []
    last = next((m for m in reversed(msgs) if m.get('role') == 'user'), msgs[-1] if msgs else {})
    body = last.get('content')
    return body if isinstance(body, str) else json.dumps(body, default=str)


def receipt(rid):
    """One receipt, whole — the bundle exactly as it was published."""
    r = _pick(receipt=rid)
    return {**r, 'question_verdict': _verdict(_receipts_for(
        r['claim']['request_hash']))['verdict']}


def board(model=None, provider=None, runtime=None, verdict=None, by=None,
          q=None, limit=50, sort='recent'):
    """The board: one row per question, newest first."""
    book = ledger()
    groups = {}
    for r in book.values():
        groups.setdefault(r['claim']['request_hash'], []).append(r)
    rows = []
    for qid, rs in groups.items():
        rs = sorted(rs, key=lambda r: r['claim'].get('at') or 0)
        c = rs[0]['claim']
        v = _verdict(rs)
        rows.append({
            'question': qid, 'model': c['model'], 'runtime': c['runtime'],
            'name': c.get('model_as_given') or c['model'],
            'providers': sorted({x['claim']['provider'] for x in rs}),
            'signers': sorted({x['by'] for x in rs if x.get('by')}),
            'prompt': _prompt_of(c)[:400],
            'preview': rs[0]['claim']['output'][:400],
            'first_at': c.get('at'), 'last_at': rs[-1]['claim'].get('at'),
            'cid': (rs[0].get('store') or {}).get('cid'),
            'verdict': v['verdict'], 'why': v['why'], 'receipts': v['receipts'],
            'outputs': v['outputs'], 'agreement': v['agreement'],
            'divergence_at': (v.get('first_divergence') or {}).get('at'),
        })
    def keep(row):
        if model and model_key(model) not in row['model']:
            return False
        if provider and provider not in row['providers']:
            return False
        if runtime and row['runtime'] != runtime:
            return False
        if verdict and row['verdict'] != verdict:
            return False
        if by and by.lower() not in [s.lower() for s in row['signers']]:
            return False
        if q:
            hay = (row['prompt'] + ' ' + row['preview'] + ' ' + row['model']).lower()
            if str(q).lower() not in hay:
                return False
        return True
    rows = [r for r in rows if keep(r)]
    keys = {
        'recent': lambda r: -(r['last_at'] or 0),
        'oldest': lambda r: (r['first_at'] or 0),
        'replicated': lambda r: (-r['receipts'], -(r['last_at'] or 0)),
        'divergent': lambda r: (r['agreement'], -(r['last_at'] or 0)),
        'model': lambda r: (r['model'], -(r['last_at'] or 0)),
    }
    rows.sort(key=keys.get(sort, keys['recent']))
    tally = {}
    for r in rows:
        tally[r['verdict']] = tally.get(r['verdict'], 0) + 1
    return {
        'questions': len(rows), 'receipts': len(book), 'by_verdict': tally,
        'sort': sort, 'board': rows[:int(limit or 50)],
        'store': STORE_URL if STORE_ON else 'off',
    }


def leaderboard(runtime=None, min_receipts=2):
    """Which models actually hold still at temperature 0.

    Only questions that somebody bothered to run twice can say anything, so
    single-receipt questions are counted but never scored — a model is not
    reproducible because nobody checked.
    """
    book = ledger()
    groups = {}
    for r in book.values():
        if runtime and r['claim']['runtime'] != runtime:
            continue
        groups.setdefault(r['claim']['request_hash'], []).append(r)
    models = {}
    for qid, rs in groups.items():
        c = rs[0]['claim']
        m = models.setdefault(c['model'], {
            'model': c['model'], 'name': c.get('model_as_given') or c['model'],
            'runtime': c['runtime'], 'questions': 0,
            'receipts': 0, 'tested': 0, 'reproduced': 0, 'divergent': 0,
            'providers': set(), 'signers': set(), 'divergence_points': [],
            'unreplicated': 0,
        })
        v = _verdict(rs)
        m['questions'] += 1
        m['receipts'] += len(rs)
        m['providers'] |= {x['claim']['provider'] for x in rs}
        m['signers'] |= {x['by'] for x in rs if x.get('by')}
        if len(rs) < int(min_receipts or 2):
            m['unreplicated'] += 1
            continue
        m['tested'] += 1
        if v['verdict'] == 'divergent':
            m['divergent'] += 1
            m['divergence_points'].append((v.get('first_divergence') or {}).get('at') or 0)
        else:
            m['reproduced'] += 1
    rows = []
    for m in models.values():
        pts = sorted(m.pop('divergence_points'))
        m['providers'] = sorted(m['providers'])
        m['signers'] = len(m['signers'])
        m['reproducibility'] = round(m['reproduced'] / m['tested'], 4) if m['tested'] else None
        m['median_divergence_char'] = pts[len(pts) // 2] if pts else None
        m['grade'] = ('untested' if not m['tested'] else
                      'holds' if m['reproducibility'] == 1 else
                      'drifts' if m['reproducibility'] >= 0.5 else 'unstable')
        rows.append(m)
    rows.sort(key=lambda r: (-(r['tested'] or 0), -(r['reproducibility'] or 0)))
    return {
        'models': rows, 'count': len(rows),
        'min_receipts': int(min_receipts or 2),
        'reads': 'reproducibility = share of replicated questions whose receipts '
                 'were byte-identical. Only `onnx` rows are expected to be 1.0: '
                 'a hosted endpoint at temperature 0 is greedy, not '
                 'deterministic, because batching and float reduction order are '
                 'not part of the request.',
    }


# ── verification ─────────────────────────────────────────────────

def verify(rid, rerun=False, fetch=True):
    """Check a receipt against itself, against its signature, against the store,
    and — for ONNX — against the model running again right now.

    Every check recomputes from content. Nothing here trusts a stored field,
    which is why a `pass` means something.
    """
    r = _pick(receipt=rid)
    claim, checks = r['claim'], []

    def check(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
        return ok

    rh = request_hash(claim['runtime'], claim['request'])
    check('request_hash', rh == claim['request_hash'],
          f"canonical request hashes to {rh}"
          + ('' if rh == claim['request_hash'] else
             f", the receipt says {claim['request_hash']}"))

    if claim['runtime'] != 'onnx':
        oh = sha(claim['output'])
        check('output_hash', oh == claim['output_hash'],
              'the output bytes hash to their stated hash' if oh == claim['output_hash']
              else f'output hashes to {oh}, receipt says {claim["output_hash"]}')
    else:
        check('output_hash', True, 'tensor-bytes hash — re-run to check it '
                                   '(rerun=true)')

    ch = canon_hash(claim)
    check('receipt_hash', ch == r['hash'],
          f'claim canonicalizes to {ch}'
          + ('' if ch == r['hash'] else f", the receipt is filed under {r['hash']}"))

    check('greedy', not greedy_violations(claim['request'] if claim['runtime'] == 'llm'
                                          else {}),
          'temperature 0, top_p 1, one candidate, no penalties')

    who = signer(r.get('attestation'), ch)
    if r.get('attestation'):
        check('signature', who['signed'] and who.get('by') == r.get('by'),
              f"signed by {who.get('by')}" if who['signed'] else who.get('reason'))
    else:
        checks.append({'check': 'signature', 'ok': None,
                       'detail': 'unsigned — the content is checkable, the '
                                 'author is not'})

    cid = (r.get('store') or {}).get('cid')
    if fetch and cid:
        try:
            raw = store_get(cid)
            want = canon(_bundle(claim, r.get('attestation'), r.get('by')))
            check('store', raw == want,
                  f'{len(raw)} bytes at {cid} are byte-identical to the receipt'
                  if raw == want else
                  f'the object at {cid} is NOT the bytes this receipt says it is')
        except InferError as e:
            checks.append({'check': 'store', 'ok': None, 'detail': e.message})
    elif fetch:
        checks.append({'check': 'store', 'ok': None,
                       'detail': (r.get('store') or {}).get('error')
                       or 'never published'})

    if rerun:
        if claim['runtime'] == 'onnx':
            try:
                fresh = run_onnx(claim['request']['model'],
                                 seed=claim['request']['seed'],
                                 batch=claim['request']['batch'],
                                 shapes=claim['request'].get('shapes'))
                check('rerun', fresh['output_hash'] == claim['output_hash'],
                      'ran the model again and got the same tensor bytes'
                      if fresh['output_hash'] == claim['output_hash'] else
                      f"re-run produced {fresh['output_hash']}, receipt says "
                      f"{claim['output_hash']} — the same file no longer "
                      'produces the same numbers on this box')
            except InferError as e:
                checks.append({'check': 'rerun', 'ok': None, 'detail': e.message})
        else:
            checks.append({'check': 'rerun', 'ok': None,
                           'detail': 'a hosted model is re-run with replicate, '
                                     'which files the result as its own signed '
                                     'receipt instead of overwriting this one'})

    hard = [c for c in checks if c['ok'] is False]
    return {
        'id': r['id'], 'hash': r['hash'], 'ok': not hard,
        'verdict': 'verified' if not hard else 'FAILED',
        'checks': checks,
        'failed': [c['check'] for c in hard],
        'receipt': _slim(r),
        'question': _verdict(_receipts_for(claim['request_hash']))['verdict'],
        'means': 'the bytes are what they say they are and the signature is who '
                 'it says it is. It does NOT mean the model would say it again — '
                 'that is what the question verdict is for.'
                 if not hard else 'this receipt does not check out; see failed',
    }


def fetch(cid, post_it=True):
    """Import a receipt somebody else published, by CID.

    This is how two boards on two boxes become one board: the CID is the whole
    handoff, and every check runs again locally on arrival.
    """
    raw = store_get(cid)
    try:
        bundle = json.loads(raw)
    except Exception:
        raise InferError(f'{cid} is not JSON — that is not a receipt', 422)
    if not isinstance(bundle, dict) or not bundle.get('claim'):
        raise InferError(f'{cid} is not an infer receipt bundle', 422)
    claim = _normalize(bundle['claim'])
    h = canon_hash(claim)
    if bundle.get('hash') and bundle['hash'] != h:
        raise InferError(f'{cid} carries hash {bundle["hash"]} but its claim '
                         f'canonicalizes to {h}', 422)
    who = signer(bundle.get('attestation'), h)
    if not post_it:
        return {'cid': cid, 'hash': h, 'claim': claim, 'signer': who}
    out = post(claim, attestation=bundle.get('attestation') if who['signed'] else None,
               sign=False, publish=False)
    rec = out['receipt']
    with _LOCK:
        book = ledger()
        if book.get(rec['id']):
            book[rec['id']]['store'] = {'cid': cid, 'imported': True,
                                        'url': STORE_URL.rstrip('/') + '/get?cid=' + cid,
                                        'at': time.time()}
            _write_json(LEDGER, book)
    return {'imported': rec['id'], 'cid': cid, 'signer': who,
            'duplicate': out['duplicate'], 'question': out['question']}


def delete(rid):
    r = _pick(receipt=rid)
    with _LOCK:
        book = ledger()
        book.pop(r['id'], None)
        _write_json(LEDGER, book)
    return {'deleted': r['id'],
            'note': 'removed from this board. If it was published its CID still '
                    'resolves — content-addressed storage has no delete, which '
                    'is the property that makes a receipt worth anything.'}


def canonical(runtime='llm', **kw):
    """Show the canonical bytes and the hash for a request, without running it.

    So a claim can be prepared, hashed and signed anywhere, by anything, and
    still land on the same question as a run from this box.
    """
    runtime = str(runtime or 'llm').lower()
    if runtime == 'onnx':
        req = onnx_request(kw.get('model'), seed=kw.get('seed') or 0,
                           batch=kw.get('batch') or 1, shapes=kw.get('shapes'))
    else:
        req = chat_request(kw.get('model'), prompt=kw.get('prompt'),
                           messages=kw.get('messages'), system=kw.get('system'),
                           max_tokens=kw.get('max_tokens') or 512,
                           seed=kw.get('seed'), stop=kw.get('stop'),
                           params=kw.get('params'))
    body = canon({'runtime': runtime, 'request': req})
    return {
        'runtime': runtime, 'request': req,
        'request_hash': sha(body),
        'canonical': body.decode('utf-8'),
        'rules': ['JSON with keys sorted', 'no whitespace between tokens',
                  'UTF-8, non-ASCII left unescaped',
                  'sha256 of those bytes, lowercase hex'],
        'existing': _existing(sha(body)),
    }


def _existing(qid):
    rows = _receipts_for(qid)
    if not rows:
        return None
    v = _verdict(rows)
    return {'question': qid, 'verdict': v['verdict'], 'receipts': v['receipts'],
            'note': 'this question is already on the board'}


def status():
    book = ledger()
    qs = {}
    for r in book.values():
        qs.setdefault(r['claim']['request_hash'], []).append(r)
    tally = {}
    for rs in qs.values():
        v = _verdict(rs)['verdict']
        tally[v] = tally.get(v, 0) + 1
    signed = sum(1 for r in book.values() if r.get('attestation'))
    published = sum(1 for r in book.values() if (r.get('store') or {}).get('cid'))
    store_ok, store_note = None, 'off (INFER_STORE=0)'
    if STORE_ON:
        try:
            _http(STORE_URL.rstrip('/') + '/status', timeout=20)
            store_ok, store_note = True, 'reachable'
        except InferError as e:
            store_ok, store_note = False, e.message[:200]
    return {
        'receipts': len(book), 'questions': len(qs), 'by_verdict': tally,
        'signed': signed, 'unsigned': len(book) - signed, 'published': published,
        'models': len({r['claim']['model'] for r in book.values()}),
        'signers': sorted({r['by'] for r in book.values() if r.get('by')}),
        'identity': _identity(),
        'store': {'url': STORE_URL, 'ok': store_ok, 'note': store_note},
        'providers': providers()['ready'],
        'ledger': LEDGER,
        'rule': 'temperature 0, top_p 1, n 1, no penalties — anything else is '
                'refused with 422',
    }


def _identity():
    try:
        return auth().key_address()
    except Exception as e:
        return f'unavailable — {type(e).__name__}: {e}'
