"""One shape for every compute provider.

Each provider ships an adapter that subclasses `Provider` and maps its own
API onto the same nouns and verbs the rest of the module speaks:

    offer     something rentable, priced in USD/hr
    instance  something rented and running, priced in USD/hr
    account   whose credits pay, and how much is left

Everything else — HTTP, auth, key resolution, error shape — lives here, so an
adapter is a mapping file and nothing more. An adapter declares what it can
actually do in `caps`; the aggregator never calls a verb a provider hasn't
claimed, and tells the agent why instead of failing at the upstream.

Money and identity are the caller's, always: keys are resolved per request,
never shared between providers, never logged, and never returned by any route.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = 'mod-compute/0.1 (mod protocol)'
TIMEOUT = 25

# The full verb set. An adapter lists the subset it implements; `search` is the
# only one every adapter is expected to have.
CAPS = ('search', 'rent', 'instances', 'status', 'logs', 'exec', 'stop', 'balance')

KEYS_FILE = os.path.expanduser('~/.mod/compute/keys.json')


# ── errors ───────────────────────────────────────────────────────────────

class ProviderError(Exception):
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


class NeedsKey(ProviderError):
    """No key for this provider — the caller has to bring their own."""


class Unsupported(ProviderError):
    """The provider genuinely cannot do this, not a transient failure."""


# ── http ─────────────────────────────────────────────────────────────────

def http(method, url, headers=None, params=None, body=None, timeout=TIMEOUT,
         provider=None):
    """One JSON request. Raises ProviderError with the upstream's own message."""
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ''}
        if clean:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(clean)
    data = None
    hdrs = {'user-agent': USER_AGENT, 'accept': 'application/json'}
    hdrs.update(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs['content-type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = (e.read() or b'')[:600].decode('utf-8', 'replace').strip()
        raise ProviderError(f'{method} {url.split("?")[0]} → {e.code}: {detail}',
                            provider=provider, status=e.code)
    except Exception as e:
        raise ProviderError(f'{method} {url.split("?")[0]} → {type(e).__name__}: {e}',
                            provider=provider)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {'text': raw[:2000].decode('utf-8', 'replace')}


# ── normalized records ───────────────────────────────────────────────────

def offer(provider, ref, usd_hr=None, gpu=None, gpus=None, vram_gb=None,
          cpu=None, ram_gb=None, disk_gb=None, region=None, available=True,
          kind='gpu', note=None, raw=None):
    """A rentable thing. `id` is namespaced so it round-trips through any verb."""
    def r(v):
        return round(v, 1) if isinstance(v, (int, float)) else v

    return {
        'id': f'{provider}:{ref}',
        'provider': provider,
        'ref': str(ref),
        'kind': kind,             # gpu | cpu | confidential | job | storage
        'gpu': gpu,
        'gpus': gpus,
        'vram_gb': r(vram_gb),
        'cpu': r(cpu),
        'ram_gb': r(ram_gb),
        'disk_gb': r(disk_gb),
        'usd_hr': round(usd_hr, 4) if isinstance(usd_hr, (int, float)) else None,
        'region': region,
        'available': available,
        'note': note,
        'raw': raw,
    }


def instance(provider, ref, name=None, status=None, usd_hr=None, gpu=None,
             gpus=None, ssh=None, url=None, created=None, raw=None):
    """A rental that exists upstream and is (probably) costing money."""
    return {
        'id': f'{provider}:{ref}',
        'provider': provider,
        'ref': str(ref),
        'name': name,
        'status': status,
        'usd_hr': round(usd_hr, 4) if isinstance(usd_hr, (int, float)) else None,
        'gpu': gpu,
        'gpus': gpus,
        'ssh': ssh,
        'url': url,
        'created': created,
        'raw': raw,
    }


def num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def gi(v, default=None):
    """'80Gi' / '80GB' / 81920 (MiB) → GB, best effort."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return round(v / 1024) if v > 512 else round(float(v), 1)
    s = str(v).strip().lower().replace('ib', 'b')
    for suffix, mult in (('tb', 1024), ('gb', 1), ('mb', 1 / 1024), ('gi', 1), ('ti', 1024)):
        if s.endswith(suffix):
            return round(num(s[:-len(suffix)], 0) * mult, 1)
    return num(s, default)


# ── the contract ─────────────────────────────────────────────────────────

class Provider:
    """Subclass, set the metadata, implement the verbs you claim in `caps`."""

    name = ''
    title = ''
    upstream = ''
    docs = ''
    signup = ''
    chain = None            # settlement chain, if the money is on one
    kyc = 'unknown'         # none | email | account | full
    pay = ()                # what buys time here: TAO, AKT, NOS, USDC, card…
    custody = 'byok'        # byok | wallet | local
    caps = ('search',)
    key_env = ()            # env vars checked in order
    key_files = ()          # off-tree files checked in order (plain text)
    key_hint = ''           # told to the agent when a key is missing

    def __init__(self, key=None):
        self._key = key or None

    # ── auth ──

    def key(self, required=True):
        """Caller key > env > this module's keystore > the sibling module's file."""
        k = self._key
        if not k:
            for env in self.key_env:
                k = os.environ.get(env)
                if k:
                    break
        if not k:
            k = _keystore().get(self.name)
        if not k:
            for path in self.key_files:
                k = _read_key(path)
                if k:
                    break
        if not k and required:
            raise NeedsKey(
                f'{self.name}: no API key — bring your own',
                provider=self.name,
                hint=self.key_hint or f'get one at {self.signup or self.upstream}, then '
                                      f'`m compute/set_key provider={self.name} key=…`')
        return k or ''

    def has_key(self):
        try:
            return bool(self.key(required=True))
        except NeedsKey:
            return False

    def headers(self):
        """Auth headers for an authenticated call. Override per provider."""
        return {'authorization': f'Bearer {self.key()}'}

    # ── http sugar ──

    def get(self, path, params=None, auth=False, base=None):
        return http('GET', (base or self.upstream) + path, params=params,
                    headers=self.headers() if auth else None, provider=self.name)

    def post(self, path, body=None, params=None, auth=True, base=None):
        return http('POST', (base or self.upstream) + path, body=body, params=params,
                    headers=self.headers() if auth else None, provider=self.name)

    def delete(self, path, auth=True, base=None):
        return http('DELETE', (base or self.upstream) + path,
                    headers=self.headers() if auth else None, provider=self.name)

    # ── verbs (override what you claim) ──

    def search(self, f):
        """`f` is a Filters. Return a list of offer() dicts."""
        raise Unsupported(f'{self.name}: search not implemented', provider=self.name)

    def rent(self, ref, **opts):
        raise Unsupported(f'{self.name}: cannot rent through this module',
                          provider=self.name, hint=self.key_hint)

    def instances(self):
        raise Unsupported(f'{self.name}: no instance list', provider=self.name)

    def status(self, ref):
        for i in self.instances():
            if i['ref'] == str(ref):
                return i
        raise ProviderError(f'{self.name}: no instance {ref}', provider=self.name)

    def logs(self, ref, tail=200):
        raise Unsupported(f'{self.name}: no logs', provider=self.name)

    def exec(self, ref, cmd):
        raise Unsupported(f'{self.name}: no exec — use ssh', provider=self.name)

    def stop(self, ref):
        raise Unsupported(f'{self.name}: cannot stop through this module',
                          provider=self.name)

    def balance(self):
        raise Unsupported(f'{self.name}: no balance endpoint', provider=self.name)

    def raw(self, path, method='GET', body=None, params=None):
        """Escape hatch: anything the normalized surface doesn't cover."""
        return http(method, self.upstream + path, body=body, params=params,
                    headers=self.headers() if self.has_key() else None,
                    provider=self.name)

    # ── description ──

    def key_state(self):
        """'not needed' when the market is permissionless; else set/missing."""
        if not self.key_env and not self.key_files:
            return 'not needed'
        return 'set' if self.has_key() else 'missing'

    def card(self):
        return {
            'name': self.name,
            'title': self.title,
            'upstream': self.upstream,
            'docs': self.docs,
            'signup': self.signup,
            'chain': self.chain,
            'kyc': self.kyc,
            'pay': list(self.pay),
            'custody': self.custody,
            'caps': list(self.caps),
            'key': self.key_state(),
            'key_hint': self.key_hint,
        }


class Filters:
    """What a search means, once, for every provider."""

    # Priced per GB-hour rather than per machine-hour, so it would sort to the
    # top of every cheapest-first search and mean nothing. Ask for it by name.
    HIDDEN_KINDS = ('storage',)

    def __init__(self, gpu=None, min_gpus=None, min_vram_gb=None, max_usd_hr=None,
                 min_usd_hr=None, region=None, available_only=True, limit=40,
                 kind=None, **_):
        self.kind = (kind or '').strip().lower() or None
        self.gpu = (gpu or '').strip().lower() or None
        self.min_gpus = int(min_gpus) if min_gpus else None
        self.min_vram_gb = num(min_vram_gb)
        self.max_usd_hr = num(max_usd_hr)
        # A price band, not just a ceiling: the floor of a market whose cheapest
        # row is $0.001 is where the junk lives, and an agent asking for a real
        # H100 wants to say so. Free (0) rows fall outside any floor above 0.
        self.min_usd_hr = num(min_usd_hr)
        if (self.min_usd_hr is not None and self.max_usd_hr is not None
                and self.min_usd_hr > self.max_usd_hr):
            self.min_usd_hr, self.max_usd_hr = self.max_usd_hr, self.min_usd_hr
        self.region = (region or '').strip().lower() or None
        self.available_only = bool(available_only)
        # 2000 is enough to hand the whole catalogue to a distribution view;
        # adapters that page (vast) clamp their own request size below this.
        self.limit = max(1, min(int(limit or 40), 2000))

    def match(self, o):
        """Applied to every offer after the adapter maps it, so a provider that
        cannot filter server-side still behaves like one that can."""
        if self.available_only and not o.get('available'):
            return False
        if self.kind and self.kind != 'all':
            if o.get('kind') != self.kind:
                return False
        elif not self.kind and o.get('kind') in self.HIDDEN_KINDS:
            return False
        if self.gpu:
            # Only the gpu field, never the note — notes carry CUDA versions and
            # marketing names, and matching them turns "4090" into every row.
            hay = str(o.get('gpu') or '').lower().replace('-', ' ')
            needle = self.gpu.replace('-', ' ')
            if needle not in hay and needle.replace(' ', '') not in hay.replace(' ', ''):
                return False
        if self.min_gpus and (o.get('gpus') or 0) < self.min_gpus:
            return False
        if self.min_vram_gb and (o.get('vram_gb') or 0) < self.min_vram_gb:
            return False
        if self.max_usd_hr is not None or self.min_usd_hr is not None:
            p = o.get('usd_hr')
            if p is None:
                return False
            if self.max_usd_hr is not None and p > self.max_usd_hr:
                return False
            if self.min_usd_hr is not None and p < self.min_usd_hr:
                return False
        if self.region and self.region not in str(o.get('region') or '').lower():
            return False
        return True

    def dict(self):
        return {'gpu': self.gpu, 'kind': self.kind, 'min_gpus': self.min_gpus,
                'min_vram_gb': self.min_vram_gb, 'max_usd_hr': self.max_usd_hr,
                'min_usd_hr': self.min_usd_hr,
                'region': self.region, 'available_only': self.available_only,
                'limit': self.limit}


# ── keystore (off-tree, 0600, never committed) ───────────────────────────

def _keystore():
    try:
        with open(KEYS_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _read_key(path):
    """A sibling module's key file: plain text, or JSON with an obvious key."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return ''
    try:
        with open(path) as f:
            body = f.read().strip()
    except Exception:
        return ''
    if not body.startswith('{'):
        return body.split('\n')[0].strip()
    try:
        d = json.loads(body)
    except Exception:
        return ''
    for field in ('key', 'api_key', 'active', 'default'):
        v = d.get(field)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            for f2 in ('key', 'api_key'):
                if isinstance(v.get(f2), str):
                    return v[f2]
    for v in d.values():                     # {account: {key: …}} shapes
        if isinstance(v, str) and v.startswith(('cat_sk_', 'sk_', 'pk_')):
            return v
        if isinstance(v, dict) and isinstance(v.get('key'), str):
            return v['key']
    return ''


def set_key(provider, key, persist=True):
    """Write one provider's key to the off-tree keystore (0600)."""
    store = _keystore()
    if key:
        store[provider] = key
    else:
        store.pop(provider, None)
    if not persist:
        return {'provider': provider, 'persisted': False}
    os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
    fd = os.open(KEYS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(store, f, indent=2)
    os.chmod(KEYS_FILE, 0o600)
    return {'provider': provider, 'persisted': True, 'file': KEYS_FILE}
