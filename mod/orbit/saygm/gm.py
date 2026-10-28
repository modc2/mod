"""SayGM client — the catalog, the prices, and one call shape for every model.

SayGM (https://saygm.com) is an LLM gateway on Bittensor: one key, one base
URL, frontier models (Claude, GPT, Gemini) at or below list price, and
open-weight models served inside an Intel TDX enclave (the ``-tee`` ids).

The gateway speaks four upstream wire shapes and every model lists which it
accepts in ``api_shapes``. ``chat()`` reads that and picks the right one, so a
caller only ever says ``model`` + ``prompt``/``messages``:

    messages           → POST /v1/messages                    (Anthropic)
    chat.completions   → POST /v1/chat/completions            (OpenAI)
    responses          → POST /v1/responses                   (OpenAI)
    generateContent    → POST /v1beta/models/{m}:generateContent (Gemini)

Prices on the wire are integer nano-dollars per million tokens
(``ndollars_per_mtok``); everything here is converted to USD per Mtok.

The catalog is public. Spending needs a SayGM key (minted in their
dashboard). Resolution order: explicit ``key=`` → ``SAYGM_API_KEY`` →
``~/.mod/saygm/key.json`` (0600, never in config.json).

Stdlib only.
"""

import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get('SAYGM_API', 'https://api.saygm.com').rstrip('/')
SITE = 'https://saygm.com'
STATE_DIR = os.path.expanduser(os.environ.get('SAYGM_DIR', '~/.mod/saygm'))
KEY_FILE = os.path.join(STATE_DIR, 'key.json')
CACHE_FILE = os.path.join(STATE_DIR, 'models.json')
CACHE_TTL = 300
NANO = 1e9
SPEND_USD = float(os.environ.get('SAYGM_SPEND_USD', '0.50'))
UA = 'mod-saygm/0.1 (+https://saygm.com)'

# api_shapes in the order chat() prefers them.
SHAPES = ('chat.completions', 'messages', 'responses', 'generateContent')


class GMError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# ── http ─────────────────────────────────────────────────────────

def _http(method, url, body=None, headers=None, timeout=120):
    h = {'User-Agent': UA, 'Accept': 'application/json'}
    if body is not None:
        h['Content-Type'] = 'application/json'
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            err = json.loads(raw)
            err = err.get('error', err)
            msg = err.get('message') if isinstance(err, dict) else str(err)
        except Exception:
            msg = raw[:300].decode(errors='replace')
        raise GMError(f'saygm {e.code}: {msg}', e.code) from None
    except urllib.error.URLError as e:
        raise GMError(f'saygm unreachable: {e.reason}', 502) from None
    return json.loads(raw) if raw else {}


# ── key (off-chain, never in config.json) ────────────────────────

def load_key(key=None):
    if key:
        return key
    if os.environ.get('SAYGM_API_KEY'):
        return os.environ['SAYGM_API_KEY']
    try:
        with open(KEY_FILE) as f:
            return json.load(f).get('key') or None
    except Exception:
        return None


def save_key(key):
    os.makedirs(STATE_DIR, exist_ok=True)
    if not key:
        if os.path.exists(KEY_FILE):
            os.remove(KEY_FILE)
        return {'key_set': False}
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'key': key, 'saved': int(time.time())}, f)
    return {'key_set': True, 'key': mask(key)}


def mask(key):
    return (key[:5] + '…' + key[-4:]) if key and len(key) > 12 else ('set' if key else None)


def _need_key(key):
    k = load_key(key)
    if not k:
        raise GMError('no SayGM key — mint one at https://saygm.com/dashboard, '
                      'then `m saygm/set_key key=...` or set SAYGM_API_KEY', 401)
    return k


# ── catalog ──────────────────────────────────────────────────────

def _usd(dims, name):
    v = (dims or {}).get(name)
    return None if v is None else round(v / NANO, 6)


def normalize(m):
    """One raw /v1/models entry → a flat row priced in USD per Mtok."""
    p = m.get('pricing') or {}
    d = p.get('dimensions') or {}
    ceil = ((m.get('price_range') or {}).get('ceiling') or {}).get('dimensions') or {}
    row = {
        'id': m['id'],
        'lab': m.get('owned_by'),
        'tier': m.get('tier'),
        'confidential': bool(m.get('confidential')),
        'shapes': m.get('api_shapes') or [],
        'available': bool(m.get('available', True)) and not m.get('coming_soon'),
        'input': _usd(d, 'input_per_mtok_ndollars'),
        'output': _usd(d, 'output_per_mtok_ndollars'),
        'cache_read': _usd(d, 'cache_read_per_mtok_ndollars'),
        'per_image': _usd(d, 'output_per_image_ndollars'),
        'list_input': _usd(ceil, 'input_per_mtok_ndollars'),
        'list_output': _usd(ceil, 'output_per_mtok_ndollars'),
        'capabilities': m.get('capabilities') or {},
        'url': f"{SITE}/models/{m['id']}",
    }
    li, lo, i, o = row['list_input'], row['list_output'], row['input'], row['output']
    if li and lo and i is not None and o is not None:
        # Blend 3:1 input:output — a typical chat mix — so one number ranks.
        row['discount'] = round(1 - (3 * i + o) / (3 * li + lo), 4)
    else:
        row['discount'] = None
    return row


def fetch_models(refresh=False):
    """The raw catalog, cached CACHE_TTL seconds on disk."""
    if not refresh:
        try:
            if time.time() - os.path.getmtime(CACHE_FILE) < CACHE_TTL:
                with open(CACHE_FILE) as f:
                    return json.load(f)
        except Exception:
            pass
    try:
        data = _http('GET', f'{API}/v1/models', timeout=30).get('data') or []
    except GMError:
        # Upstream down: a stale catalog beats none.
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            raise
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = CACHE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, CACHE_FILE)
    return data


def models(q='', tier='', lab='', shape='', confidential=None, available=True,
           sort='id', limit=200, raw=None):
    """Filtered, sorted catalog rows. ``raw`` lets tests pass a fixture."""
    rows = [normalize(m) for m in (raw if raw is not None else fetch_models())]
    q = (q or '').lower()
    if q:
        rows = [r for r in rows if q in r['id'].lower() or q in (r['lab'] or '').lower()]
    if tier:
        rows = [r for r in rows if r['tier'] == tier]
    if lab:
        rows = [r for r in rows if (r['lab'] or '').lower() == lab.lower()]
    if shape:
        rows = [r for r in rows if shape in r['shapes']]
    if confidential not in (None, ''):
        want = str(confidential).lower() in ('1', 'true', 'yes')
        rows = [r for r in rows if r['confidential'] == want]
    if str(available).lower() in ('1', 'true', 'yes'):
        rows = [r for r in rows if r['available']]
    big = float('inf')
    keys = {
        'id': lambda r: r['id'],
        'input': lambda r: r['input'] if r['input'] is not None else big,
        'output': lambda r: r['output'] if r['output'] is not None else big,
        'discount': lambda r: -(r['discount'] or 0),
    }
    rows.sort(key=keys.get(sort, keys['id']))
    return rows[:int(limit)]


def model(id, raw=None):
    for r in models(available=False, raw=raw):
        if r['id'] == id:
            return r
    raise GMError(f'no model {id!r} — see `m saygm/models`', 404)


def quote(model_id, input_tokens=1000, output_tokens=1000, raw=None):
    """What a call costs here vs at the maker's list price, in USD."""
    r = model(model_id, raw)
    it, ot = int(input_tokens), int(output_tokens)
    if r['input'] is None or r['output'] is None:
        raise GMError(f'{model_id} is not priced per token', 400)
    cost = (it * r['input'] + ot * r['output']) / 1e6
    out = {'model': r['id'], 'input_tokens': it, 'output_tokens': ot,
           'usd': round(cost, 6)}
    if r['list_input'] is not None and r['list_output'] is not None:
        lst = (it * r['list_input'] + ot * r['list_output']) / 1e6
        out.update(list_usd=round(lst, 6), saved_usd=round(lst - cost, 6),
                   saved_pct=round(1 - cost / lst, 4) if lst else None)
    return out


def labs(raw=None):
    out = {}
    for r in models(available=False, raw=raw):
        out.setdefault(r['lab'] or '?', 0)
        out[r['lab'] or '?'] += 1
    return [{'lab': k, 'models': v} for k, v in sorted(out.items())]


# ── calling ──────────────────────────────────────────────────────

def _messages(prompt, messages, system):
    if messages:
        if isinstance(messages, str):
            messages = json.loads(messages)
        msgs = list(messages)
    else:
        if not prompt:
            raise GMError('give prompt= or messages=', 400)
        msgs = [{'role': 'user', 'content': prompt}]
    if system:
        msgs = [m for m in msgs if m.get('role') != 'system']
    else:
        sys_msgs = [m for m in msgs if m.get('role') == 'system']
        system = '\n\n'.join(m['content'] for m in sys_msgs if isinstance(m.get('content'), str))
        msgs = [m for m in msgs if m.get('role') != 'system']
    return msgs, system or ''


def pick_shape(row):
    for s in SHAPES:
        if s in row['shapes']:
            return s
    raise GMError(f"{row['id']} serves {row['shapes']}, not chat — no text call for it", 400)


def build_request(row, msgs, system, max_tokens, temperature):
    """→ (shape, path, body). Pure; tested offline."""
    shape = pick_shape(row)
    mid = row['id']
    if shape == 'chat.completions':
        body = {'model': mid, 'max_tokens': max_tokens,
                'messages': ([{'role': 'system', 'content': system}] if system else []) + msgs}
        if temperature is not None:
            body['temperature'] = temperature
        return shape, '/v1/chat/completions', body
    if shape == 'messages':
        body = {'model': mid, 'max_tokens': max_tokens, 'messages': msgs}
        if system:
            body['system'] = system
        if temperature is not None:
            body['temperature'] = temperature
        return shape, '/v1/messages', body
    if shape == 'responses':
        body = {'model': mid, 'max_output_tokens': max_tokens, 'input': msgs}
        if system:
            body['instructions'] = system
        return shape, '/v1/responses', body
    contents = [{'role': 'model' if m['role'] == 'assistant' else 'user',
                 'parts': [{'text': m['content'] if isinstance(m['content'], str)
                            else json.dumps(m['content'])}]} for m in msgs]
    body = {'contents': contents, 'generationConfig': {'maxOutputTokens': max_tokens}}
    if temperature is not None:
        body['generationConfig']['temperature'] = temperature
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}
    return shape, f'/v1beta/models/{mid}:generateContent', body


def extract(shape, resp):
    """→ (text, input_tokens, output_tokens) from any of the four shapes."""
    if shape == 'chat.completions':
        ch = (resp.get('choices') or [{}])[0].get('message') or {}
        u = resp.get('usage') or {}
        return ch.get('content') or '', u.get('prompt_tokens'), u.get('completion_tokens')
    if shape == 'messages':
        txt = ''.join(b.get('text', '') for b in resp.get('content') or [] if b.get('type') == 'text')
        u = resp.get('usage') or {}
        return txt, u.get('input_tokens'), u.get('output_tokens')
    if shape == 'responses':
        txt = resp.get('output_text') or ''.join(
            c.get('text', '') for o in resp.get('output') or [] if o.get('type') == 'message'
            for c in o.get('content') or [] if c.get('type') in ('output_text', 'text'))
        u = resp.get('usage') or {}
        return txt, u.get('input_tokens'), u.get('output_tokens')
    cand = (resp.get('candidates') or [{}])[0]
    txt = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts') or [])
    u = resp.get('usageMetadata') or {}
    return txt, u.get('promptTokenCount'), u.get('candidatesTokenCount')


def chat(model_id, prompt='', messages=None, system='', max_tokens=1024,
         temperature=None, confirm=False, key=None, raw=None):
    """One text call to any SayGM chat model; the wire shape is picked for you."""
    row = model(model_id, raw)
    if not row['available']:
        raise GMError(f'{model_id} is not available right now', 409)
    max_tokens = int(max_tokens)
    msgs, system = _messages(prompt, messages, system)
    shape, path, body = build_request(row, msgs, system, max_tokens,
                                      None if temperature in (None, '') else float(temperature))
    # Spend guard: worst case is every max_token billed at the output rate.
    worst = max_tokens * (row['output'] or 0) / 1e6
    if worst > SPEND_USD and str(confirm).lower() not in ('1', 'true', 'yes'):
        return {'needs_confirm': True, 'model': model_id, 'worst_case_usd': round(worst, 4),
                'limit_usd': SPEND_USD, 'hint': 'lower max_tokens or pass confirm=true'}
    k = _need_key(key)
    hdr = {'x-api-key': k, 'anthropic-version': '2023-06-01'} if shape == 'messages' \
        else {'Authorization': f'Bearer {k}'}
    t0 = time.time()
    resp = _http('POST', API + path, body, hdr)
    text, it, ot = extract(shape, resp)
    out = {'model': model_id, 'shape': shape, 'text': text,
           'input_tokens': it, 'output_tokens': ot,
           'ms': int((time.time() - t0) * 1000)}
    if it is not None and ot is not None and row['input'] is not None:
        out['usd'] = round((it * row['input'] + ot * (row['output'] or 0)) / 1e6, 6)
    return out


def credits(key=None):
    """The key's prepaid balance (authoritative, never cached upstream)."""
    return _http('GET', f'{API}/v1/credits', headers={'Authorization': f'Bearer {_need_key(key)}'},
                 timeout=20)
