"""One shape for every inference router.

`compute` does this for GPU rental; this does it for inference calls. An adapter
maps one router's catalog and chat surface onto the same nouns:

    Offering   a model you can call, priced in USD per million tokens
    Provider   the router that serves it, and how it takes money

Everything else — HTTP, key resolution, error shape, modality vocabulary, price
units — lives here, so an adapter stays a mapping file.

Two fields exist on every adapter that do not exist in `compute`, because the
brief for this module is narrower than "every market":

    kyc    what the provider demands before it will serve you
    pay    which coins it takes, and whether an account is needed at all

Both are *declared data with a verification date*, never inferred. `kyc='none'`
is a claim about somebody else's onboarding policy, and policies change without
telling us, so an adapter that has not been checked says `unknown` and is
filtered out by default rather than being optimistically included. Being wrong
in that direction hands someone's passport to a provider they were avoiding.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = 'mod-infer/0.2 (mod protocol; inference router)'
TIMEOUT = 45
KEYS_FILE = os.path.expanduser('~/.mod/infer/router-keys.json')

# The canonical modality vocabulary. Adapters map into this and nothing outside
# it reaches the catalog, so `modality=audio` means the same thing on every
# router even though no two of them spell it the same way.
MODALITIES = ('text', 'image', 'audio', 'video', 'file', 'embedding')

# What a provider can be asked to do.
CAPS = ('catalog', 'chat', 'image', 'audio', 'embedding', 'balance', 'topup')

# KYC ladder, weakest demand first. `unknown` is not on it: it is the absence of
# a reading, and sorts as "excluded" rather than as a rung.
KYC_LEVELS = ('none', 'email', 'account', 'full')


class RouterError(Exception):
    """Anything the caller should read and act on."""

    def __init__(self, message, provider=None, status=None, hint=None):
        super().__init__(message)
        self.provider, self.status, self.hint = provider, status, hint

    def dict(self):
        d = {'error': str(self), 'provider': self.provider}
        if self.status:
            d['status'] = self.status
        if self.hint:
            d['hint'] = self.hint
        return d


class NeedsKey(RouterError):
    """No key for this provider — the caller brings their own, or funds one."""


class Unsupported(RouterError):
    """The provider genuinely cannot do this. Not transient, do not retry."""


def _read_keys():
    try:
        with open(KEYS_FILE) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def set_key(provider, key):
    """Keys live off the tree at 0600, never in config, never in a response."""
    keys = _read_keys()
    if key:
        keys[provider] = str(key)
    else:
        keys.pop(provider, None)
    os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
    tmp = KEYS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(keys, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, KEYS_FILE)
    return {'provider': provider, 'key': bool(key)}


def model_key(name):
    """The cross-router identity of a model.

    Delegates to `proofs.model_key`, which the receipts board already uses to
    decide that `openai/gpt-4o` and `gpt-4o` are one question. Sharing it is the
    point: a model grouped one way in the catalog and another way on the board
    would make "cheapest provider for the model I benchmarked" unanswerable.
    The fallback only runs if proofs cannot be imported, and matches its rules.
    """
    try:
        from proofs import model_key as _mk
        return _mk(name)
    except Exception:
        s = str(name or '').strip().lower()
        head, _, tail = s.partition('/')
        if tail and head in _VENDORS:
            s = tail
        return s.split(':')[0] if s.count(':') == 1 and s.endswith(
            (':free', ':beta', ':extended', ':nitro', ':floor')) else s


_VENDORS = ('openai', 'anthropic', 'google', 'meta-llama', 'meta', 'mistralai',
            'mistral', 'deepseek', 'qwen', 'x-ai', 'cohere', 'ai21', 'amazon',
            'microsoft', 'nvidia', 'perplexity', 'nousresearch', 'openrouter')


def http(method, url, headers=None, params=None, body=None, timeout=TIMEOUT,
         provider=None, raw=False):
    """One JSON request. Raises RouterError carrying the upstream's own words."""
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, '')}
        if clean:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(clean)
    hdrs = {'user-agent': USER_AGENT, 'accept': 'application/json'}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs['content-type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:400]
        except Exception:
            pass
        raise RouterError(f'{provider or url} returned {e.code}: {detail or e.reason}',
                          provider=provider, status=e.code)
    except urllib.error.URLError as e:
        raise RouterError(f'{provider or url} unreachable: {e.reason}',
                          provider=provider, status=0)
    except Exception as e:
        raise RouterError(f'{provider or url} failed: {e}', provider=provider)
    if raw:
        return payload
    try:
        return json.loads(payload)
    except Exception:
        raise RouterError(f'{provider or url} did not return JSON',
                          provider=provider)


class Offering:
    """One model on one router, normalized.

    `key` is the cross-router identity (`proofs.model_key`), so the same weights
    reached through four routers collapse to four offerings of one model and the
    cheapest is a well-posed question.
    """

    __slots__ = ('provider', 'id', 'key', 'name', 'price', 'inputs', 'outputs',
                 'context', 'max_output', 'tags', 'extra')

    def __init__(self, provider, id, key, name=None, price=None, inputs=(),
                 outputs=('text',), context=None, max_output=None, tags=(),
                 extra=None):
        self.provider, self.id, self.key = provider, id, key
        self.name = name or id
        self.price = price
        self.inputs = tuple(inputs) or ('text',)
        self.outputs = tuple(outputs) or ('text',)
        self.context, self.max_output = context, max_output
        self.tags = tuple(tags)
        self.extra = extra or {}

    @property
    def multimodal(self):
        return len(set(self.inputs) | set(self.outputs)) > 1

    def dict(self):
        d = {'provider': self.provider, 'id': self.id, 'model': self.key,
             'name': self.name, 'inputs': list(self.inputs),
             'outputs': list(self.outputs), 'multimodal': self.multimodal,
             'context': self.context}
        if self.max_output:
            d['max_output'] = self.max_output
        if self.price is not None:
            d.update(self.price.dict())
        if self.tags:
            d['tags'] = list(self.tags)
        d.update(self.extra)
        return d


class Provider:
    """Subclass this, map the catalog, declare how the provider takes money."""

    name = 'provider'
    base = ''
    caps = ('catalog',)
    style = 'openai'          # the chat wire format: 'openai' or its own

    # ── the money and identity declaration ───────────────────────────────
    kyc = 'unknown'           # one of KYC_LEVELS, or 'unknown' if not checked
    pay = ()                  # coins accepted, e.g. ('USDC', 'BTC', 'XMR')
    pay_note = ''             # how funding actually works, in one line
    account = True            # is an account needed at all before a first call
    checked = ''              # ISO date the kyc/pay claim above was last read
    price_unit = 'per_million_tokens'

    def __init__(self, key=None):
        self._explicit = key

    # ── keys ─────────────────────────────────────────────────────────────
    @property
    def env(self):
        return (self.name.upper() + '_API_KEY',)

    def key(self):
        if self._explicit:
            return self._explicit
        for var in self.env:
            if os.environ.get(var):
                return os.environ[var]
        return _read_keys().get(self.name)

    def require_key(self):
        k = self.key()
        if not k:
            raise NeedsKey(
                f'no key for {self.name} — POST /router/key {{provider, key}}, '
                f'or set {self.env[0]}',
                provider=self.name,
                hint=self.pay_note or None)
        return k

    @property
    def ready(self):
        return bool(self.key())

    def headers(self):
        return {'authorization': 'Bearer ' + self.require_key()}

    # ── what an adapter implements ───────────────────────────────────────
    def catalog(self):
        """Every model this router serves, as Offerings."""
        raise Unsupported(f'{self.name} has no catalog', provider=self.name)

    def chat(self, model, messages, **kw):
        """An OpenAI-shaped chat completion. Default works for openai-style."""
        if self.style != 'openai':
            raise Unsupported(f'{self.name} needs its own chat adapter',
                              provider=self.name)
        body = {'model': model, 'messages': messages}
        for k, v in kw.items():
            if v is not None:
                body[k] = v
        return http('POST', self.chat_url(), headers=self.headers(), body=body,
                    provider=self.name)

    def chat_url(self):
        return self.base.rstrip('/') + '/chat/completions'

    def balance(self):
        raise Unsupported(f'{self.name} exposes no balance', provider=self.name)

    # ── self-description ─────────────────────────────────────────────────
    def describe(self):
        return {'provider': self.name, 'base': self.base, 'caps': list(self.caps),
                'kyc': self.kyc, 'pay': list(self.pay), 'account': self.account,
                'pay_note': self.pay_note, 'checked': self.checked,
                'ready': self.ready, 'key_env': list(self.env)}
