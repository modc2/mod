"""One client for the whole OpenRouter API — catalog, routing, inference, spend.

Everything above this file (REST routes, MCP tools, the console, the CLI) is a
thin call into `Client`, so there is exactly one place where a question about
OpenRouter gets answered.

Two things this file insists on:

* **The key is the caller's.** It is resolved per request — explicit argument,
  then env, then the off-tree keystore — and is never logged, never echoed by a
  route, and never shared between the inference key and the provisioning key.
* **Prices are per million tokens.** OpenRouter quotes USD per token, which is
  unreadable at 1e-7 and mis-compared by eye. Every normalized record carries
  `prompt_usd_m` / `completion_usd_m`; the raw strings stay in `raw` for anyone
  who wants them.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://openrouter.ai/api/v1'
KEY_FILE = os.path.expanduser('~/.mod/openrouter/key.json')
CACHE_FILE = os.path.expanduser('~/.mod/openrouter/models.json')

TIMEOUT = 30
CHAT_TIMEOUT = 300
CACHE_TTL = float(os.environ.get('OPENROUTER_CACHE_TTL', 600))
SPEND_USD = float(os.environ.get('OPENROUTER_SPEND_USD', 0.50))
REFERER = os.environ.get('OPENROUTER_REFERER', 'https://modc2.com/openrouter')
TITLE = os.environ.get('OPENROUTER_TITLE', 'mod/openrouter')

# Sort keys the catalog understands. `price` means "cheapest prompt tokens".
SORTS = ('price', 'completion_price', 'context', 'created', 'name', 'throughput')

# What `provider:` accepts on a chat call, straight from OpenRouter's routing
# spec. Anything else is dropped rather than forwarded, so a typo fails here
# with a readable message instead of upstream with a 400.
PROVIDER_PREFS = ('order', 'only', 'ignore', 'allow_fallbacks', 'require_parameters',
                  'data_collection', 'sort', 'quantizations', 'max_price',
                  'experimental', 'zdr')


class ORError(Exception):
    """Anything the caller should read and act on."""

    def __init__(self, message, status=None, hint=None):
        super().__init__(message)
        self.status, self.hint = status, hint

    def dict(self):
        d = {'error': str(self)}
        if self.status:
            d['status'] = self.status
        if self.hint:
            d['hint'] = self.hint
        return d


class NeedsKey(ORError):
    """No key — the caller has to bring their own."""


# ── keystore (off-tree, 0600, never committed) ───────────────────────────

def _keystore():
    try:
        with open(KEY_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def set_key(key=None, provisioning_key=None, persist=True):
    """Write the caller's key(s) to ~/.mod/openrouter/key.json (0600)."""
    store = _keystore()
    for field, value in (('key', key), ('provisioning_key', provisioning_key)):
        if value:
            store[field] = value
        elif value == '':
            store.pop(field, None)
    out = {'key': 'set' if store.get('key') else 'missing',
           'provisioning_key': 'set' if store.get('provisioning_key') else 'missing',
           'persisted': bool(persist)}
    if not persist:
        return out
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(store, f, indent=2)
    os.chmod(KEY_FILE, 0o600)
    return {**out, 'file': KEY_FILE}


# ── http ─────────────────────────────────────────────────────────────────

def _request(method, url, headers, body=None, params=None, timeout=TIMEOUT):
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ''}
        if clean:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(clean)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {'accept': 'application/json', 'user-agent': 'mod-openrouter/0.1', **headers}
    if data is not None:
        hdrs['content-type'] = 'application/json'
    return urllib.request.Request(url, data=data, headers=hdrs, method=method.upper()), url


def http(method, url, headers=None, body=None, params=None, timeout=TIMEOUT):
    """One JSON request. Raises ORError carrying OpenRouter's own message."""
    req, full = _request(method, url, headers or {}, body, params, timeout)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = (e.read() or b'')[:800].decode('utf-8', 'replace').strip()
        try:                                    # unwrap {"error":{"message":…}}
            got = json.loads(detail).get('error')
            detail = got.get('message', detail) if isinstance(got, dict) else detail
        except Exception:
            pass
        raise ORError(f'{method.upper()} {full.split("?")[0]} → {e.code}: {detail}',
                      status=e.code, hint=_hint(e.code))
    except Exception as e:
        raise ORError(f'{method.upper()} {full.split("?")[0]} → {type(e).__name__}: {e}')
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {'text': raw[:4000].decode('utf-8', 'replace')}


def _hint(code):
    return {401: 'the key was rejected — check it at https://openrouter.ai/keys',
            402: 'out of credits — top up at https://openrouter.ai/credits',
            403: 'the model or provider is not available to this key '
                 '(moderation, region, or a privacy setting)',
            404: 'no such model or route — openrouter_models lists what exists',
            429: 'rate limited — free-tier models throttle hard; retry or route elsewhere',
            }.get(code)


# ── normalized records ───────────────────────────────────────────────────

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _price(v):
    """A price, or None when the upstream didn't quote one.

    OpenRouter prices its routers — openrouter/auto and friends — at -1. That
    is not a price, it is a sentinel for "depends on which model this request
    lands on". Carried through as a number it becomes -$1,000,000 per million
    tokens, which makes the routers the cheapest thing in the catalog, the top
    of every default listing, and a pass on every price ceiling. None is the
    honest reading: unknown, which already sorts last and fails ceilings.
    """
    v = _f(v)
    return None if v is None or v < 0 else v


def variable_price(pricing):
    """True when the upstream quoted the -1 sentinel — priced per request."""
    return any((_f((pricing or {}).get(k)) or 0) < 0 for k in ('prompt', 'completion'))


def per_m(pricing):
    """USD-per-token strings → USD per million tokens, plus the per-call fields.

    OpenRouter prices tokens at 1e-7-ish magnitudes and everything else (images,
    requests, web search) per unit. Keeping both in one dict, with `_m` only on
    the token fields, is what stops the two from being compared to each other.
    """
    p = pricing or {}
    out = {}
    for src, dst in (('prompt', 'prompt_usd_m'), ('completion', 'completion_usd_m'),
                     ('input_cache_read', 'cache_read_usd_m'),
                     ('input_cache_write', 'cache_write_usd_m'),
                     ('internal_reasoning', 'reasoning_usd_m')):
        v = _price(p.get(src))
        if v is not None:
            out[dst] = round(v * 1_000_000, 4)
    for src, dst in (('request', 'per_request_usd'), ('image', 'per_image_usd'),
                     ('web_search', 'per_web_search_usd'),
                     ('audio', 'per_audio_usd'), ('video', 'per_video_usd')):
        v = _price(p.get(src))
        if v:
            out[dst] = v
    return out


def card(m):
    """One model, in the shape every route and tool returns it."""
    arch = m.get('architecture') or {}
    top = m.get('top_provider') or {}
    params = m.get('supported_parameters') or []
    price = per_m(m.get('pricing'))
    prompt_m, completion_m = price.get('prompt_usd_m'), price.get('completion_usd_m')
    return {
        'id': m.get('id'),
        'name': m.get('name'),
        'context': m.get('context_length') or top.get('context_length'),
        'max_output': top.get('max_completion_tokens'),
        'prompt_usd_m': prompt_m,
        'completion_usd_m': completion_m,
        # A model is only "free" if generating is free too — a $0 prompt with a
        # priced completion is a loss leader, not a free model.
        'free': prompt_m == 0 and completion_m == 0,
        # Unpriced because the router decides per request, not because the
        # catalog is missing a number. Worth telling apart when it's null.
        'variable_price': variable_price(m.get('pricing')),
        'modality': arch.get('modality'),
        'input': arch.get('input_modalities') or [],
        'output': arch.get('output_modalities') or [],
        'tools': 'tools' in params,
        'reasoning': 'reasoning' in params or 'include_reasoning' in params,
        'structured': 'structured_outputs' in params,
        'moderated': top.get('is_moderated'),
        'created': m.get('created'),
        'pricing': price,
        'supported_parameters': params,
        'description': (m.get('description') or '')[:400],
    }


def endpoint_card(e):
    """One provider's endpoint for a model — the row routing decisions read."""
    price = per_m(e.get('pricing'))
    return {
        'provider': e.get('provider_name'),
        'tag': e.get('tag'),
        'context': e.get('context_length'),
        'max_output': e.get('max_completion_tokens'),
        'prompt_usd_m': price.get('prompt_usd_m'),
        'completion_usd_m': price.get('completion_usd_m'),
        'variable_price': variable_price(e.get('pricing')),
        'quantization': e.get('quantization'),
        'uptime_30m': _round(e.get('uptime_last_30m')),
        'uptime_1d': _round(e.get('uptime_last_1d')),
        'latency_s': _round(e.get('latency_last_30m')),
        'throughput_tps': _round(e.get('throughput_last_30m')),
        'status': e.get('status'),
        'supported_parameters': e.get('supported_parameters') or [],
        'pricing': price,
    }


def _round(v, n=2):
    return round(v, n) if isinstance(v, (int, float)) else v


class Filters:
    """What a catalog search means, applied the same way everywhere."""

    def __init__(self, q=None, modality=None, input=None, output=None, free=None,
                 tools=None, reasoning=None, structured=None, min_context=None,
                 max_prompt_usd_m=None, max_completion_usd_m=None, provider=None,
                 sort='price', limit=40, **_):
        self.q = (q or '').strip().lower() or None
        self.modality = (modality or '').strip().lower() or None
        self.input = (input or '').strip().lower() or None
        self.output = (output or '').strip().lower() or None
        self.free = _bool(free)
        self.tools = _bool(tools)
        self.reasoning = _bool(reasoning)
        self.structured = _bool(structured)
        self.min_context = _f(min_context)
        self.max_prompt_usd_m = _f(max_prompt_usd_m)
        self.max_completion_usd_m = _f(max_completion_usd_m)
        self.provider = (provider or '').strip().lower() or None
        self.sort = (sort or 'price').strip().lower()
        self.limit = max(1, min(int(limit or 40), 500))

    def match(self, c):
        if self.q:
            hay = f"{c['id']} {c.get('name') or ''} {c.get('description') or ''}".lower()
            if not all(w in hay for w in self.q.split()):
                return False
        if self.provider and not str(c['id']).lower().startswith(self.provider.split('/')[0]):
            return False
        if self.modality and self.modality not in str(c.get('modality') or '').lower():
            return False
        if self.input and self.input not in [str(x).lower() for x in c.get('input') or []]:
            return False
        if self.output and self.output not in [str(x).lower() for x in c.get('output') or []]:
            return False
        for want, field in ((self.free, 'free'), (self.tools, 'tools'),
                            (self.reasoning, 'reasoning'), (self.structured, 'structured')):
            if want is not None and bool(c.get(field)) is not want:
                return False
        if self.min_context and (c.get('context') or 0) < self.min_context:
            return False
        for ceiling, field in ((self.max_prompt_usd_m, 'prompt_usd_m'),
                               (self.max_completion_usd_m, 'completion_usd_m')):
            if ceiling is not None:
                v = c.get(field)
                if v is None or v > ceiling:
                    return False
        return True

    def order(self, rows):
        big = float('inf')
        keys = {
            'price': lambda c: (c.get('prompt_usd_m') if c.get('prompt_usd_m') is not None else big),
            'completion_price': lambda c: (c.get('completion_usd_m')
                                           if c.get('completion_usd_m') is not None else big),
            'context': lambda c: -(c.get('context') or 0),
            'created': lambda c: -(c.get('created') or 0),
            'name': lambda c: str(c.get('id') or ''),
        }
        return sorted(rows, key=keys.get(self.sort, keys['price']))

    def dict(self):
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _bool(v):
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


# ── the client ───────────────────────────────────────────────────────────

_CATALOG = {'at': 0.0, 'models': None}


class Client:
    """Every OpenRouter call, with the caller's key and nobody else's."""

    def __init__(self, key=None, provisioning_key=None, referer=None, title=None):
        self._key = key or None
        self._prov = provisioning_key or None
        self.referer = referer or REFERER
        self.title = title or TITLE

    # ── auth ──

    def key(self, required=True):
        """Explicit key > env > off-tree keystore."""
        k = self._key or os.environ.get('OPENROUTER_API_KEY') or _keystore().get('key')
        if not k and required:
            raise NeedsKey(
                'no OpenRouter API key — bring your own',
                status=401,
                hint='create one at https://openrouter.ai/keys, then '
                     '`m openrouter/set_key key=sk-or-v1-…` (or send it as the '
                     'x-openrouter-key header, or set OPENROUTER_API_KEY)')
        return k or ''

    def provisioning_key(self, required=True):
        """A different, stronger key: it mints and revokes inference keys."""
        k = (self._prov or os.environ.get('OPENROUTER_PROVISIONING_KEY')
             or _keystore().get('provisioning_key'))
        if not k and required:
            raise NeedsKey(
                'no provisioning key — key management needs one',
                status=401,
                hint='create a Provisioning API key at '
                     'https://openrouter.ai/settings/provisioning-keys, then '
                     '`m openrouter/set_key provisioning_key=…`. It is not the same '
                     'as an inference key and should never be sent to a model.')
        return k or ''

    def has_key(self):
        return bool(self.key(required=False))

    def key_state(self):
        return {'key': 'set' if self.has_key() else 'missing',
                'provisioning_key': 'set' if self.provisioning_key(required=False)
                else 'missing',
                'keystore': KEY_FILE}

    def _headers(self, key=None, extra=None):
        h = {'authorization': f'Bearer {key or self.key()}',
             'http-referer': self.referer, 'x-title': self.title}
        h.update(extra or {})
        return h

    # ── catalog (public — no key needed) ──

    def models(self, refresh=False):
        """The full model catalog, cached for CACHE_TTL — 400+ rows, ~650KB."""
        now = time.time()
        if not refresh and _CATALOG['models'] and now - _CATALOG['at'] < CACHE_TTL:
            return _CATALOG['models']
        if not refresh:
            disk = self._disk_cache()
            if disk:
                _CATALOG.update(at=now, models=disk)
                return disk
        data = http('GET', f'{BASE}/models').get('data') or []
        _CATALOG.update(at=now, models=data)
        self._write_cache(data)
        return data

    def _disk_cache(self):
        """Survives a restart, so the console's first paint is not a round trip."""
        try:
            if time.time() - os.path.getmtime(CACHE_FILE) > CACHE_TTL:
                return None
            with open(CACHE_FILE) as f:
                d = json.load(f)
            return d if isinstance(d, list) and d else None
        except Exception:
            return None

    def _write_cache(self, data):
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            tmp = CACHE_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, CACHE_FILE)
        except Exception:
            pass

    def search(self, refresh=False, **filters):
        """Filter and sort the catalog. Every argument is optional."""
        f = Filters(**filters)
        rows = [card(m) for m in self.models(refresh=refresh)]
        hit = f.order([c for c in rows if f.match(c)])
        return {'models': hit[:f.limit], 'total_found': len(hit),
                'total_catalog': len(rows), 'filters': f.dict()}

    def model(self, id, endpoints=True):
        """One model: its catalog row, and every provider serving it."""
        rows = [m for m in self.models() if m.get('id') == id]
        if not rows:
            near = [m['id'] for m in self.models()
                    if id.lower() in str(m.get('id', '')).lower()][:8]
            raise ORError(f'no model {id}', status=404,
                          hint=f'did you mean: {", ".join(near)}' if near else
                          'openrouter_models lists the catalog')
        out = card(rows[0])
        out['raw'] = rows[0]
        if endpoints:
            try:
                out['endpoints'] = self.endpoints(id)['endpoints']
            except ORError as e:
                out['endpoints_error'] = str(e)
        return out

    def endpoints(self, id):
        """Per-provider price, context, quantization, uptime and throughput.

        This is the call that makes routing a decision instead of a guess: the
        catalog quotes one price, but the endpoint list is what `provider.order`
        and `provider.sort` actually choose between.
        """
        if '/' not in str(id):
            raise ORError(f'model id must be author/slug, got {id!r}', status=400)
        author, slug = str(id).split('/', 1)
        d = http('GET', f'{BASE}/models/{urllib.parse.quote(author)}/'
                        f'{urllib.parse.quote(slug)}/endpoints').get('data') or {}
        rows = [endpoint_card(e) for e in (d.get('endpoints') or [])]
        return {'id': d.get('id') or id, 'name': d.get('name'),
                'architecture': d.get('architecture'),
                'endpoints': sorted(rows, key=lambda r: (r['prompt_usd_m'] is None,
                                                         r['prompt_usd_m'] or 0)),
                'count': len(rows)}

    def providers(self, q=None):
        """The provider catalog: who they are, where they are, what they promise."""
        rows = http('GET', f'{BASE}/providers').get('data') or []
        if q:
            needle = str(q).lower()
            rows = [p for p in rows
                    if needle in f"{p.get('name','')} {p.get('slug','')}".lower()]
        return {'providers': rows, 'count': len(rows)}

    # ── inference ──

    def chat(self, model=None, messages=None, prompt=None, system=None, models=None,
             temperature=None, max_tokens=None, top_p=None, stop=None, seed=None,
             tools=None, tool_choice=None, response_format=None, reasoning=None,
             provider=None, transforms=None, plugins=None, route=None, user=None,
             confirm=False, extra=None, raw=False):
        """One chat completion. Spends the caller's credits.

        `messages` is the OpenAI shape; `prompt`(+`system`) is the shortcut for a
        single turn. `models` is OpenRouter's fallback list — the first one that
        can serve the request wins — and `provider` is the routing preference
        block (order / only / ignore / sort / allow_fallbacks / …).
        """
        payload = self._chat_payload(
            model=model, messages=messages, prompt=prompt, system=system, models=models,
            temperature=temperature, max_tokens=max_tokens, top_p=top_p, stop=stop,
            seed=seed, tools=tools, tool_choice=tool_choice,
            response_format=response_format, reasoning=reasoning, provider=provider,
            transforms=transforms, plugins=plugins, route=route, user=user, extra=extra)

        guard = self._guard(payload, confirm)
        if guard:
            return guard

        d = http('POST', f'{BASE}/chat/completions', headers=self._headers(),
                 body=payload, timeout=CHAT_TIMEOUT)
        return d if raw else self._answer(d)

    def _chat_payload(self, model=None, messages=None, prompt=None, system=None,
                      models=None, tools=None, provider=None, transforms=None,
                      extra=None, **opts):
        msgs = messages
        if not msgs:
            if not prompt:
                raise ORError('chat needs messages or prompt', status=400)
            msgs = ([{'role': 'system', 'content': system}] if system else []) + \
                   [{'role': 'user', 'content': prompt}]
        elif isinstance(msgs, str):
            try:
                msgs = json.loads(msgs)
            except Exception:
                msgs = [{'role': 'user', 'content': messages}]
        if not model and not models:
            raise ORError('chat needs a model (or a models fallback list)', status=400,
                          hint='openrouter_models finds one; "openrouter/auto" lets '
                               'OpenRouter pick')
        payload = {'messages': msgs}
        if model:
            payload['model'] = model
        if models:
            payload['models'] = _list(models)
        # Ask for the real cost back on the response rather than making the caller
        # follow up with a /generation lookup.
        payload['usage'] = {'include': True}
        for k, v in opts.items():
            if v is not None and v != '':
                payload[k] = v
        if tools:
            payload['tools'] = tools if isinstance(tools, list) else json.loads(tools)
        if transforms:
            payload['transforms'] = _list(transforms)
        if provider:
            payload['provider'] = self._provider_prefs(provider)
        if extra and isinstance(extra, dict):
            payload.update(extra)
        return payload

    def _provider_prefs(self, provider):
        """Accept `provider="groq,cerebras"` as sugar for `{"order": [...]}`."""
        if isinstance(provider, str):
            provider = provider.strip()
            if provider.startswith('{'):
                provider = json.loads(provider)
            else:
                return {'order': _list(provider)}
        if not isinstance(provider, dict):
            raise ORError('provider must be an object or a comma-separated list',
                          status=400)
        unknown = [k for k in provider if k not in PROVIDER_PREFS]
        if unknown:
            raise ORError(f'unknown provider preference: {", ".join(unknown)}',
                          status=400, hint=f'valid: {", ".join(PROVIDER_PREFS)}')
        return {k: (_list(v) if k in ('order', 'only', 'ignore', 'quantizations')
                    else v) for k, v in provider.items()}

    def _guard(self, payload, confirm):
        """A spend guard on the *worst case*, not the likely case.

        max_tokens at the model's completion price is what a runaway request
        actually costs, so that is what gets checked. Under the ceiling — which
        is nearly everything — this is silent.
        """
        if confirm:
            return None
        model = payload.get('model') or (payload.get('models') or [None])[0]
        est = self.estimate(model, prompt_tokens=self._count(payload),
                           completion_tokens=payload.get('max_tokens') or 1000)
        if est.get('total_usd') is None or est['total_usd'] <= SPEND_USD:
            return None
        return {'needs_confirm': True, 'estimate': est, 'spend_guard_usd': SPEND_USD,
                'why': f"the worst case for this call is about ${est['total_usd']} — "
                       f'over the ${SPEND_USD} guard',
                'next': 'call again with confirm=true'}

    @staticmethod
    def _count(payload):
        """Characters/4 — the standard rough token count, and honest about it."""
        text = json.dumps(payload.get('messages') or '', default=str)
        return max(1, len(text) // 4)

    @staticmethod
    def _answer(d):
        """The parts of a completion a caller actually reads, plus the receipt."""
        choice = (d.get('choices') or [{}])[0]
        msg = choice.get('message') or {}
        usage = d.get('usage') or {}
        out = {
            'text': msg.get('content') or choice.get('text') or '',
            'model': d.get('model'),
            'provider': d.get('provider'),
            'id': d.get('id'),
            'finish_reason': choice.get('finish_reason') or choice.get('native_finish_reason'),
            'usage': {'prompt_tokens': usage.get('prompt_tokens'),
                      'completion_tokens': usage.get('completion_tokens'),
                      'total_tokens': usage.get('total_tokens')},
            'cost_usd': usage.get('cost'),
        }
        if msg.get('reasoning'):
            out['reasoning'] = msg['reasoning']
        if msg.get('tool_calls'):
            out['tool_calls'] = msg['tool_calls']
        if usage.get('cost_details'):
            out['cost_details'] = usage['cost_details']
        return out

    def stream(self, **kwargs):
        """Yield raw SSE bytes from a streaming completion.

        Handed straight through by the API server so the console renders tokens
        as they arrive; the MCP tools stay non-streaming, because a tool result
        is one value.
        """
        kwargs.pop('raw', None)
        payload = self._chat_payload(**{k: v for k, v in kwargs.items()
                                        if k not in ('confirm',)})
        payload['stream'] = True
        req, _ = _request('POST', f'{BASE}/chat/completions',
                          {**self._headers(), 'accept': 'text/event-stream'}, payload)
        try:
            r = urllib.request.urlopen(req, timeout=CHAT_TIMEOUT)
        except urllib.error.HTTPError as e:
            detail = (e.read() or b'')[:800].decode('utf-8', 'replace')
            raise ORError(f'stream → {e.code}: {detail}', status=e.code, hint=_hint(e.code))
        except Exception as e:
            raise ORError(f'stream → {type(e).__name__}: {e}')
        with r:
            while True:
                chunk = r.readline()
                if not chunk:
                    return
                yield chunk

    def complete(self, model, prompt, max_tokens=None, temperature=None, stop=None,
                 provider=None, confirm=False, raw=False, **opts):
        """The legacy text-completion route, for base models that want no chat template."""
        payload = {'model': model, 'prompt': prompt, 'usage': {'include': True}}
        for k, v in dict(max_tokens=max_tokens, temperature=temperature, stop=stop,
                         **opts).items():
            if v is not None and v != '':
                payload[k] = v
        if provider:
            payload['provider'] = self._provider_prefs(provider)
        guard = self._guard({**payload, 'messages': prompt}, confirm)
        if guard:
            return guard
        d = http('POST', f'{BASE}/completions', headers=self._headers(), body=payload,
                 timeout=CHAT_TIMEOUT)
        return d if raw else self._answer(d)

    # ── money ──

    def estimate(self, model, prompt_tokens=1000, completion_tokens=1000):
        """What one call to one model would cost, before making it."""
        rows = [m for m in self.models() if m.get('id') == model]
        if not rows:
            return {'model': model, 'total_usd': None, 'note': 'model not in catalog'}
        c = card(rows[0])
        p, q = c.get('prompt_usd_m'), c.get('completion_usd_m')
        if p is None or q is None:
            return {'model': model, 'total_usd': None,
                    'variable_price': c.get('variable_price', False),
                    'note': 'a router — it prices per request, by whichever model it '
                            'picks, so the spend guard cannot price it up front; the '
                            'response carries the real cost'
                            if c.get('variable_price') else 'model is not token-priced'}
        pt, ct = float(prompt_tokens or 0), float(completion_tokens or 0)
        total = pt / 1e6 * p + ct / 1e6 * q
        return {'model': model, 'name': c.get('name'),
                'prompt_tokens': int(pt), 'completion_tokens': int(ct),
                'prompt_usd': round(pt / 1e6 * p, 6),
                'completion_usd': round(ct / 1e6 * q, 6),
                'total_usd': round(total, 6),
                'prompt_usd_m': p, 'completion_usd_m': q, 'free': c.get('free')}

    def cost(self, prompt_tokens=1000, completion_tokens=1000, model=None, limit=15,
             **filters):
        """Price one call across the catalog, cheapest first.

        With `model` it is a quote. Without one it is a ranking — the same
        filters as a search, sorted by what the call would actually cost, which
        is not the same order as the prompt price alone.
        """
        if model:
            rows = [self.estimate(m, prompt_tokens, completion_tokens)
                    for m in _list(model)]
            return {'quotes': rows, 'prompt_tokens': int(prompt_tokens or 0),
                    'completion_tokens': int(completion_tokens or 0)}
        found = self.search(limit=500, **filters)['models']
        quotes = []
        for c in found:
            p, q = c.get('prompt_usd_m'), c.get('completion_usd_m')
            if p is None or q is None:
                continue
            total = (float(prompt_tokens or 0) / 1e6 * p +
                     float(completion_tokens or 0) / 1e6 * q)
            quotes.append({'model': c['id'], 'name': c.get('name'),
                           'total_usd': round(total, 6), 'free': c.get('free'),
                           'context': c.get('context'), 'tools': c.get('tools'),
                           'prompt_usd_m': p, 'completion_usd_m': q})
        quotes.sort(key=lambda r: r['total_usd'])
        return {'quotes': quotes[:max(1, min(int(limit or 15), 200))],
                'ranked': len(quotes),
                'prompt_tokens': int(prompt_tokens or 0),
                'completion_tokens': int(completion_tokens or 0)}

    def generation(self, id):
        """What a finished generation really cost, in native provider tokens.

        The usage block on a response is OpenRouter's normalized GPT-tokenizer
        count; this is the provider's own accounting, and the two differ.
        """
        d = http('GET', f'{BASE}/generation', headers=self._headers(),
                 params={'id': id}).get('data') or {}
        return {
            'id': d.get('id'), 'model': d.get('model'), 'provider': d.get('provider_name'),
            'cost_usd': d.get('total_cost'),
            'upstream_cost_usd': d.get('upstream_inference_cost'),
            'tokens_prompt': d.get('tokens_prompt'),
            'tokens_completion': d.get('tokens_completion'),
            'native_tokens_prompt': d.get('native_tokens_prompt'),
            'native_tokens_completion': d.get('native_tokens_completion'),
            'native_tokens_reasoning': d.get('native_tokens_reasoning'),
            'cache_discount': d.get('cache_discount'),
            'latency_ms': d.get('latency'), 'generation_ms': d.get('generation_time'),
            'moderated': d.get('moderation_latency') is not None,
            'finish_reason': d.get('finish_reason'),
            'streamed': d.get('streamed'), 'created': d.get('created_at'),
            'raw': d,
        }

    def key_info(self):
        """This key's label, usage, limit and rate limit — plus the credit balance."""
        d = http('GET', f'{BASE}/key', headers=self._headers()).get('data') or {}
        out = {'label': d.get('label'), 'usage_usd': d.get('usage'),
               'limit_usd': d.get('limit'), 'limit_remaining_usd': d.get('limit_remaining'),
               'is_free_tier': d.get('is_free_tier'),
               'rate_limit': d.get('rate_limit'), 'raw': d}
        try:
            out['credits'] = self.credits()
        except ORError as e:
            out['credits_error'] = str(e)
        return out

    def credits(self):
        """Lifetime credits purchased vs used — the balance is the difference."""
        d = http('GET', f'{BASE}/credits', headers=self._headers()).get('data') or {}
        total, used = _f(d.get('total_credits')), _f(d.get('total_usage'))
        return {'total_credits_usd': total, 'total_usage_usd': used,
                'balance_usd': round(total - used, 6)
                if total is not None and used is not None else None}

    # ── provisioning (a different key, on purpose) ──

    def provision(self, action='list', hash=None, name=None, label=None, limit=None,
                  include_byok_in_limit=None, disabled=None, offset=None):
        """Mint, inspect, cap, disable and revoke inference keys.

        Guarded by the provisioning key rather than the inference key, so a model
        that can call this module's tools still cannot create keys unless the
        caller deliberately supplies one.
        """
        key = self.provisioning_key()
        h = self._headers(key=key)
        act = (action or 'list').strip().lower()
        if act == 'list':
            d = http('GET', f'{BASE}/keys', headers=h,
                     params={'offset': offset} if offset else None)
            rows = d.get('data') or []
            return {'keys': [_key_row(k) for k in rows], 'count': len(rows)}
        if act == 'get':
            _need(hash, 'hash')
            return _key_row((http('GET', f'{BASE}/keys/{hash}', headers=h).get('data') or {}))
        if act == 'create':
            body = {'name': _need(name or label, 'name')}
            if limit is not None:
                body['limit'] = float(limit)
            if include_byok_in_limit is not None:
                body['include_byok_in_limit'] = _bool(include_byok_in_limit)
            d = http('POST', f'{BASE}/keys', headers=h, body=body)
            # The one and only time OpenRouter returns the secret. Passed straight
            # back to the caller and never written to this module's keystore.
            return {'created': _key_row(d.get('data') or {}), 'key': d.get('key'),
                    'note': 'this secret is shown once — store it now'}
        if act == 'update':
            _need(hash, 'hash')
            body = {}
            if name or label:
                body['name'] = name or label
            if limit is not None:
                body['limit'] = float(limit)
            if disabled is not None:
                body['disabled'] = _bool(disabled)
            if include_byok_in_limit is not None:
                body['include_byok_in_limit'] = _bool(include_byok_in_limit)
            if not body:
                raise ORError('update needs one of: name, limit, disabled', status=400)
            return {'updated': _key_row(
                http('PATCH', f'{BASE}/keys/{hash}', headers=h, body=body).get('data') or {})}
        if act == 'delete':
            _need(hash, 'hash')
            http('DELETE', f'{BASE}/keys/{hash}', headers=h)
            return {'deleted': hash}
        raise ORError(f'unknown action {act}', status=400,
                      hint='list | get | create | update | delete')

    # ── escape hatch ──

    def raw(self, path, method='GET', body=None, params=None, provisioning=False):
        """Any OpenRouter route, with the caller's key attached.

        For whatever this module has not normalized yet — new endpoints, beta
        routes, fields dropped by the summarizers above.
        """
        p = '/' + str(path or '').lstrip('/')
        key = self.provisioning_key() if provisioning else self.key()
        return http(method, BASE + p, headers=self._headers(key=key), body=body,
                    params=params, timeout=CHAT_TIMEOUT)


def _key_row(k):
    """A provisioned key's metadata — never its secret."""
    return {'hash': k.get('hash'), 'name': k.get('name'), 'label': k.get('label'),
            'limit_usd': k.get('limit'), 'usage_usd': k.get('usage'),
            'disabled': k.get('disabled'), 'created': k.get('created_at'),
            'updated': k.get('updated_at')}


def _need(v, name):
    if v in (None, ''):
        raise ORError(f'{name} is required', status=400)
    return v


def _list(v):
    """'a,b' | ['a','b'] | 'a' → ['a', 'b']."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).split(',') if s.strip()]
