"""One client for the Grok API, and one place the users of this bot live.

Two halves, deliberately separate:

* `Client` talks to xAI (https://api.x.ai/v1) — models, chat, streaming, key
  state. It never decides *whose* key it holds; it is handed one.
* the store below decides that. A signed-in caller's xAI key and their saved
  bots live under ~/.mod/grokbot/users/<address>.json, 0600, off-tree — never
  in this repo, never in config.json.

Key resolution, in order: the key passed in → the signed-in caller's stored key
→ XAI_API_KEY / GROK_API_KEY in the environment → the operator's own keystore.
So a laptop-local server is convenient and a shared one is BYOK, with the same
code either way.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('GROKBOT_UPSTREAM', 'https://api.x.ai/v1')
STATE = os.path.expanduser(os.environ.get('GROKBOT_DIR', '~/.mod/grokbot'))
USERS = os.path.join(STATE, 'users')
KEY_FILE = os.path.join(STATE, 'key.json')          # the operator's own key

TIMEOUT = 30
CHAT_TIMEOUT = 300
DEFAULT_MODEL = os.environ.get('GROKBOT_MODEL', 'grok-4-fast')
CACHE_TTL = float(os.environ.get('GROKBOT_CACHE_TTL', 600))

_MODELS_CACHE = {}   # key fingerprint → (fetched_at, payload)


class GrokError(Exception):
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


class NeedsKey(GrokError):
    """No xAI key for this caller — they have to bring their own."""

    def __init__(self, message='no xAI key — sign in and save one at POST /key '
                               '(get it from https://console.x.ai)'):
        super().__init__(message, status=401,
                         hint='console: sign in with your wallet, then paste an '
                              'xai-… key. API: POST /key {"key": "xai-…"} with '
                              'your mod-protocol token as Authorization: Bearer.')


# ── the user store (off-tree, 0600) ──────────────────────────────────────

def _user_path(address):
    safe = ''.join(c for c in (address or '').lower() if c.isalnum() or c in '-_.')
    if not safe:
        raise GrokError('an address is required', status=400)
    return os.path.join(USERS, f'{safe}.json')


def load_user(address):
    try:
        with open(_user_path(address)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_user(address, record):
    os.makedirs(USERS, exist_ok=True)
    path = _user_path(address)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, indent=2)
    os.chmod(path, 0o600)
    return record


def set_user_key(address, key, persist=True):
    """Store (or clear, with an empty string) the caller's own xAI key."""
    if key and not str(key).startswith('xai-'):
        raise GrokError('that does not look like an xAI key — they start with '
                        '"xai-"', status=400)
    user = load_user(address)
    if key:
        user['key'] = key
        user['key_set'] = int(time.time())
    else:
        user.pop('key', None)
        user.pop('key_set', None)
    out = {'address': address, 'key': 'set' if user.get('key') else 'missing',
           'persisted': bool(persist)}
    if not persist:
        return out
    save_user(address, user)
    return {**out, 'file': _user_path(address)}


def bots(address):
    return list((load_user(address).get('bots') or {}).values())


def get_bot(address, name):
    got = (load_user(address).get('bots') or {}).get(_slug(name))
    if not got:
        raise GrokError(f'no bot named {name!r} — GET /bots lists yours', status=404)
    return got


def save_bot(address, name, system=None, model=None, temperature=None,
             search=None, description=None):
    """A grokbot is a name, a model and a system prompt. Nothing more."""
    slug = _slug(name)
    if not slug:
        raise GrokError('name is required', status=400)
    user = load_user(address)
    all_bots = user.setdefault('bots', {})
    bot = all_bots.get(slug) or {'name': slug, 'created': int(time.time())}
    for field, value in (('system', system), ('model', model),
                         ('temperature', temperature), ('search', search),
                         ('description', description)):
        if value is not None:
            bot[field] = value
    bot['updated'] = int(time.time())
    all_bots[slug] = bot
    save_user(address, user)
    return bot


def delete_bot(address, name):
    user = load_user(address)
    slug = _slug(name)
    if slug not in (user.get('bots') or {}):
        raise GrokError(f'no bot named {name!r}', status=404)
    user['bots'].pop(slug)
    save_user(address, user)
    return {'deleted': slug}


def _slug(name):
    return ''.join(c if (c.isalnum() or c in '-_') else '-'
                   for c in str(name or '').strip().lower()).strip('-')


# ── the operator's own keystore ──────────────────────────────────────────

def _keystore():
    try:
        with open(KEY_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def set_key(key, persist=True):
    """The operator's fallback key, used when nobody signed in brought one."""
    store = _keystore()
    if key:
        store['key'] = key
    else:
        store.pop('key', None)
    if not persist:
        return {'key': 'set' if store.get('key') else 'missing', 'persisted': False}
    os.makedirs(STATE, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(store, f, indent=2)
    os.chmod(KEY_FILE, 0o600)
    return {'key': 'set' if store.get('key') else 'missing', 'persisted': True,
            'file': KEY_FILE}


# ── http ─────────────────────────────────────────────────────────────────

def _request(method, url, key, body=None, params=None, stream=False):
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, '')}
        if clean:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(clean)
    data = json.dumps(body).encode() if body is not None else None
    headers = {'accept': 'text/event-stream' if stream else 'application/json',
               'user-agent': 'mod-grokbot/0.1',
               'authorization': f'Bearer {key}'}
    if data is not None:
        headers['content-type'] = 'application/json'
    return urllib.request.Request(url, data=data, headers=headers,
                                  method=method.upper()), url


def _raise(method, url, e):
    detail = (e.read() or b'')[:800].decode('utf-8', 'replace').strip()
    try:                                    # xAI answers {"code":…,"error":…}
        got = json.loads(detail)
        detail = got.get('error') or got.get('message') or detail
        if isinstance(detail, dict):
            detail = detail.get('message') or json.dumps(detail)
    except Exception:
        pass
    raise GrokError(f'{method.upper()} {url.split("?")[0]} → {e.code}: {detail}',
                    status=e.code, hint=_hint(e.code))


def _hint(code):
    return {401: 'the key was rejected — check it at https://console.x.ai',
            403: 'this key is not entitled to that model or endpoint',
            404: 'no such route or model — GET /models lists what your key sees',
            429: 'rate limited — back off, or check limits at console.x.ai',
            402: 'out of credits — top up at https://console.x.ai'}.get(code)


def http(method, path, key, body=None, params=None, timeout=TIMEOUT):
    """One JSON request against xAI. Raises GrokError carrying their message."""
    req, url = _request(method, BASE.rstrip('/') + path, key, body, params)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        _raise(method, url, e)
    except Exception as e:
        raise GrokError(f'{method.upper()} {url.split("?")[0]} → '
                        f'{type(e).__name__}: {e}')
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {'text': raw[:4000].decode('utf-8', 'replace')}


# ── the client ───────────────────────────────────────────────────────────

class Client:
    """Grok, with exactly one key attached — whoever's it is."""

    def __init__(self, key=None, address=None):
        self.address = (address or '').lower() or None
        self._explicit = key
        self._source = None

    @property
    def key(self):
        for value, source in ((self._explicit, 'request'),
                              (load_user(self.address).get('key')
                               if self.address else None, 'account'),
                              (os.environ.get('XAI_API_KEY'), 'env'),
                              (os.environ.get('GROK_API_KEY'), 'env'),
                              (_keystore().get('key'), 'operator')):
            if value:
                self._source = source
                return value
        return None

    def _key(self):
        key = self.key
        if not key:
            raise NeedsKey()
        return key

    def key_state(self):
        key = self.key
        return {'address': self.address, 'key': bool(key),
                'source': self._source if key else None,
                'fingerprint': (key[:7] + '…' + key[-4:]) if key else None}

    # ── catalog ──────────────────────────────────────────────────

    def models(self, refresh=False):
        """Every model this key can see. xAI needs a key even to list them."""
        key = self._key()
        fp = key[-8:]
        hit = _MODELS_CACHE.get(fp)
        if hit and not refresh and (time.time() - hit[0]) < CACHE_TTL:
            return hit[1]
        raw = http('GET', '/language-models', key)
        rows = [card(m) for m in (raw.get('models') or raw.get('data') or [])]
        out = {'models': sorted(rows, key=lambda m: m['id']), 'count': len(rows),
               'default': DEFAULT_MODEL, 'cached': False}
        _MODELS_CACHE[fp] = (time.time(), {**out, 'cached': True})
        return out

    def model(self, id):
        for m in self.models().get('models', []):
            if m['id'] == id:
                return m
        raise GrokError(f'no model {id!r} for this key — GET /models lists them',
                        status=404)

    def key_info(self):
        """What xAI says about the key itself: name, blocked, permissions."""
        return http('GET', '/api-key', self._key())

    # ── inference ────────────────────────────────────────────────

    def payload(self, model=None, prompt=None, system=None, messages=None,
                temperature=None, max_tokens=None, search=None, bot=None,
                tools=None, stream=False, **opts):
        """Build the chat body without sending it — the one place shape lives."""
        persona = get_bot(self.address, bot) if bot else {}
        if persona and not self.address:
            raise GrokError('bots are per account — sign in first', status=401)
        msgs = list(messages or [])
        if not msgs:
            if not prompt:
                raise GrokError('send prompt or messages', status=400)
            msgs = [{'role': 'user', 'content': prompt}]
        sys_prompt = system or persona.get('system')
        if sys_prompt and not any(m.get('role') == 'system' for m in msgs):
            msgs = [{'role': 'system', 'content': sys_prompt}] + msgs
        body = {'model': model or persona.get('model') or DEFAULT_MODEL,
                'messages': msgs}
        temp = temperature if temperature is not None else persona.get('temperature')
        if temp is not None:
            body['temperature'] = float(temp)
        if max_tokens:
            body['max_tokens'] = int(max_tokens)
        if tools:
            body['tools'] = tools
        want_search = search if search is not None else persona.get('search')
        if want_search not in (None, '', False, 'false', 0, '0'):
            mode = want_search if isinstance(want_search, str) else 'auto'
            body['search_parameters'] = {'mode': 'auto' if mode in ('1', 'true', True)
                                         else mode}
        if stream:
            body['stream'] = True
        body.update({k: v for k, v in opts.items() if v is not None})
        return body

    def chat(self, **kwargs):
        """One completion. Spends the resolved key's xAI credits."""
        body = self.payload(**kwargs)
        out = http('POST', '/chat/completions', self._key(), body=body,
                   timeout=CHAT_TIMEOUT)
        choice = ((out.get('choices') or [{}])[0].get('message') or {})
        return {'text': choice.get('content'),
                'model': out.get('model', body['model']),
                'id': out.get('id'),
                'usage': out.get('usage'),
                'citations': out.get('citations'),
                'finish_reason': (out.get('choices') or [{}])[0].get('finish_reason'),
                'raw': out}

    def stream(self, **kwargs):
        """SSE passthrough — yields raw `data: …` frames as xAI sends them."""
        body = self.payload(stream=True, **kwargs)
        req, url = _request('POST', BASE.rstrip('/') + '/chat/completions',
                            self._key(), body=body, stream=True)
        try:
            with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT) as r:
                for line in r:
                    yield line
        except urllib.error.HTTPError as e:
            _raise('POST', url, e)
        except Exception as e:
            raise GrokError(f'stream → {type(e).__name__}: {e}')

    def images(self, prompt, model='grok-2-image', n=1, **opts):
        return http('POST', '/images/generations', self._key(),
                    body={'model': model, 'prompt': prompt, 'n': int(n), **opts},
                    timeout=CHAT_TIMEOUT)

    def raw(self, path, method='GET', body=None, params=None):
        """Escape hatch: any xAI route, with the resolved key attached."""
        if not path.startswith('/'):
            path = '/' + path
        return http(method, path, self._key(), body=body, params=params,
                    timeout=CHAT_TIMEOUT)


def card(m):
    """One model row, normalized. Prices are USD per MILLION tokens."""
    def per_m(v):
        try:
            # xAI quotes cents per 100M tokens: 30000 → $3.00 / 1M.
            return round(float(v) / 10_000.0, 4)
        except (TypeError, ValueError):
            return None
    return {'id': m.get('id') or m.get('name'),
            'aliases': m.get('aliases') or [],
            'input_modalities': m.get('input_modalities') or [],
            'output_modalities': m.get('output_modalities') or [],
            'prompt_usd_m': per_m(m.get('prompt_text_token_price')),
            'completion_usd_m': per_m(m.get('completion_text_token_price')),
            'raw': m}
