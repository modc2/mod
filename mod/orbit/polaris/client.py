"""The Polaris REST surface, normalized — stdlib only, one class.

Polaris (https://polaris.computer) is a GPU cloud with four distinct things
behind one base URL, and the published quickstart only documents the first:

    catalog      /compute/gpus, /pricing, /templates, /models   — public
    rentals      /compute/instances                             — a box you SSH into
    deployments  /deployments, /templates/deployments           — a template on a box
    account      /auth/me, /keys, /stats, /usage, /billing/*    — who you are, what you owe

Everything here goes through `Polaris.get/post/delete`, which is the only place
that knows about bearer tokens, JSON framing or upstream error shapes. The
verbs above it return mod-protocol rows: prices are USD/hr floats, ids are
strings, and a missing field is absent rather than guessed.

The key is the caller's own. Resolution order is explicit argument, then
POLARIS_KEY / POLARIS_API_KEY in the environment, then ~/.mod/polaris/api_key,
which is 0600 and off-tree — never config.json, never the repo.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('POLARIS_API_BASE', 'https://api.polaris.computer/api')
STATE = os.path.expanduser('~/.mod/polaris')
KEY_FILE = os.path.join(STATE, 'api_key')
TIMEOUT = float(os.environ.get('POLARIS_TIMEOUT', 45))

# Renting spends real money. An estimate above this needs confirm=True.
CONFIRM_USD = float(os.environ.get('POLARIS_CONFIRM_USD', 0.50))

# Catalog reads are public and change slowly; a short TTL keeps a console that
# polls from hammering the upstream on every repaint.
CACHE_TTL = float(os.environ.get('POLARIS_CACHE_TTL', 30))
_CACHE = {}


class PolarisError(Exception):
    """An upstream failure, or a refusal by this module, with a way forward."""

    def __init__(self, message, status=400, hint=None, upstream=None):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.hint = hint
        self.upstream = upstream

    def dict(self):
        out = {'error': self.message, 'status': self.status}
        if self.hint:
            out['hint'] = self.hint
        if self.upstream is not None:
            out['upstream'] = self.upstream
        return out


# ── small helpers ──

def num(v, default=None):
    """A float, or the default — upstream sends prices as strings sometimes."""
    try:
        if v is None or v == '' or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _usd(v, default=None):
    """A dollar figure that may arrive as 0.01, "$0.01" or None."""
    if isinstance(v, str):
        v = v.replace('$', '').replace(',', '').strip()
    return num(v, default)


def truthy(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ('', '0', 'false', 'no', 'none', 'off')


def _match(hay, needle):
    return needle.lower() in (hay or '').lower()


class Polaris:
    """One caller's view of Polaris. Holds a key, holds no state beyond it."""

    def __init__(self, key=None, base=None, timeout=None):
        self._key = key
        self.base = (base or BASE).rstrip('/')
        self.timeout = timeout or TIMEOUT

    # ── key ──

    def key(self, required=True):
        """The caller's key: argument, environment, then the off-tree store."""
        for candidate in (self._key,
                          os.environ.get('POLARIS_KEY'),
                          os.environ.get('POLARIS_API_KEY'),
                          self._file_key()):
            if candidate:
                return candidate.strip()
        if required:
            raise PolarisError(
                'no Polaris API key', status=401,
                hint='export POLARIS_KEY=pi_sk_… or `m polaris/set_key key=pi_sk_…` '
                     f'(stored 0600 at {KEY_FILE}). Get one at https://polaris.computer')
        return None

    def has_key(self):
        return bool(self.key(required=False))

    @staticmethod
    def _file_key():
        try:
            with open(KEY_FILE) as f:
                return f.read().strip()
        except OSError:
            return None

    @staticmethod
    def set_key(key, persist=True):
        """Write the key to the off-tree keystore. Never to the repo."""
        if not key or not str(key).strip():
            raise PolarisError('key is required')
        key = str(key).strip()
        if not persist:
            os.environ['POLARIS_KEY'] = key
            return {'stored': 'process environment', 'persisted': False}
        os.makedirs(STATE, exist_ok=True)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(key)
        os.chmod(KEY_FILE, 0o600)
        return {'stored': KEY_FILE, 'persisted': True, 'mode': '0600',
                'prefix': key[:11] + '…'}

    # ── transport ──

    def request(self, path, method='GET', body=None, params=None, auth=True):
        url = self.base + '/' + str(path).lstrip('/')
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, '')}
            if clean:
                url += ('&' if '?' in url else '?') + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode() if body is not None else None
        headers = {'accept': 'application/json', 'user-agent': 'mod-polaris/1.0'}
        if data is not None:
            headers['content-type'] = 'application/json'
        if auth:
            headers['authorization'] = f'Bearer {self.key()}'
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise self._http_error(e, method, path)
        except urllib.error.URLError as e:
            raise PolarisError(f'cannot reach {self.base}: {e.reason}', status=502,
                               hint='network, or POLARIS_API_BASE points somewhere wrong')
        except TimeoutError:
            raise PolarisError(f'{method} {path} timed out after {self.timeout}s',
                               status=504)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {'raw': raw.decode('utf-8', 'replace')}

    def _http_error(self, e, method, path):
        try:
            detail = json.loads(e.read() or b'{}')
        except Exception:
            detail = None
        msg = None
        if isinstance(detail, dict):
            msg = detail.get('detail') or detail.get('message') or detail.get('error')
        hint = None
        if e.code == 401:
            hint = ('the key was rejected — check `m polaris/account`, or set a new '
                    'one with `m polaris/set_key key=pi_sk_…`')
        elif e.code == 402:
            hint = 'out of credits — `m polaris/packs` lists top-ups'
        elif e.code == 404:
            hint = f'no such route upstream: {method} {path}'
        return PolarisError(msg or f'{method} {path} → HTTP {e.code}',
                            status=e.code, hint=hint, upstream=detail)

    def get(self, path, params=None, auth=True, ttl=0):
        """GET, optionally served from the short-lived catalog cache."""
        if ttl:
            ck = (self.base, path, json.dumps(params or {}, sort_keys=True))
            hit = _CACHE.get(ck)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            got = self.request(path, params=params, auth=auth)
            _CACHE[ck] = (time.time(), got)
            return got
        return self.request(path, params=params, auth=auth)

    def post(self, path, body=None, params=None):
        return self.request(path, method='POST', body=body or {}, params=params)

    def delete(self, path, params=None):
        return self.request(path, method='DELETE', params=params)

    # ── catalog (public) ──

    def gpus(self, gpu=None, min_vram_gb=None, max_usd_hr=None, kind=None,
             available_only=True, sort='price', limit=None):
        """The rentable catalog, normalized and filtered. No key needed."""
        rows = (self.get('/compute/gpus', auth=False, ttl=CACHE_TTL) or {}).get('gpus') or []
        out = []
        for g in rows:
            # available_count is null when the catalog knows a type is up but
            # not how many are free. That is available, not sold out.
            free = num(g.get('available_count'))
            spot = num(g.get('spot_price'))
            row = {
                'id': g.get('id') or g.get('name'),
                # gpu_type in a rent call is the *name*, not the slug id.
                'gpu_type': g.get('name'),
                'name': g.get('display_name') or g.get('name'),
                'arch': g.get('architecture'),
                'memory': g.get('memory'),
                'vram_gb': num(g.get('vram_gb'), 0),
                'is_cpu': bool(g.get('is_cpu')),
                'usd_hr': spot,
                'usd_hr_spot': spot,
                'usd_hr_on_demand': num(g.get('on_demand_price')),
                'available': bool(g.get('available')) and (free is None or free > 0),
                'available_count': free,
            }
            out.append(row)
        if kind in ('gpu', 'cpu'):
            out = [r for r in out if r['is_cpu'] == (kind == 'cpu')]
        if gpu:
            out = [r for r in out if _match(r['name'], gpu) or _match(r['gpu_type'], gpu)]
        if min_vram_gb is not None:
            floor = num(min_vram_gb, 0)
            out = [r for r in out if (r['vram_gb'] or 0) >= floor]
        if max_usd_hr is not None:
            cap = num(max_usd_hr)
            out = [r for r in out if r['usd_hr'] is not None and r['usd_hr'] <= cap]
        if truthy(available_only, True):
            out = [r for r in out if r['available']]
        keyed = {'price': lambda r: (r['usd_hr'] is None, r['usd_hr'] or 0),
                 'vram': lambda r: -(r['vram_gb'] or 0),
                 'name': lambda r: (r['name'] or '').lower()}
        out.sort(key=keyed.get(sort, keyed['price']))
        if limit:
            out = out[:int(limit)]
        return {'gpus': out, 'count': len(out), 'source': self.base + '/compute/gpus'}

    def pricing(self):
        """The billing table — the prices you are actually charged against."""
        got = self.get('/pricing', auth=False, ttl=CACHE_TTL) or {}
        rows = [{'billing_key': g.get('billing_key'),
                 'name': g.get('display_name'),
                 'usd_hr_spot': num(g.get('spot_per_hour')),
                 'usd_hr_on_demand': num(g.get('on_demand_per_hour'))}
                for g in (got.get('gpus') or [])]
        rows.sort(key=lambda r: (r['usd_hr_spot'] is None, r['usd_hr_spot'] or 0))
        out = {'gpus': rows, 'count': len(rows)}
        for k, v in got.items():
            if k != 'gpus':
                out[k] = v
        return out

    def templates(self, category=None, q=None):
        """The one-click images: Ollama, Jupyter, ComfyUI, agents, raw docker."""
        rows = (self.get('/templates', auth=False, ttl=CACHE_TTL) or {}).get('templates') or []
        if category:
            rows = [t for t in rows if _match(t.get('category'), category)]
        if q:
            rows = [t for t in rows
                    if _match(t.get('name'), q) or _match(t.get('id'), q)
                    or _match(t.get('description'), q)]
        slim = [{'id': t.get('id'), 'name': t.get('name'),
                 'category': t.get('category'), 'port': t.get('default_port'),
                 'access': t.get('access_type'), 'default_gpu': t.get('default_gpu'),
                 'deploy_time': t.get('estimated_deploy_time'),
                 'params': [p.get('name') if isinstance(p, dict) else p
                            for p in (t.get('parameters') or [])],
                 'description': t.get('description')}
                for t in rows]
        return {'templates': slim, 'count': len(slim),
                'categories': sorted({t.get('category') for t in rows if t.get('category')})}

    def template(self, template_id):
        """One template in full, including the parameters a deploy accepts."""
        return self.get(f'/templates/{template_id}', auth=False)

    def models(self, q=None, provider=None, max_prompt_price=None,
               min_context=None, sort='name', limit=60):
        """Polaris also fronts an inference catalog. Filtering is done here —
        the upstream ignores query parameters and returns all of them."""
        rows = (self.get('/models', ttl=CACHE_TTL) or {}).get('models') or []
        total = len(rows)
        out = []
        for m in rows:
            price_in, price_out = _token_price(m.get('prompt_price')), \
                _token_price(m.get('completion_price'))
            out.append({
                'id': m.get('id'), 'name': m.get('name'), 'provider': m.get('provider'),
                'context': m.get('context_length'),
                'usd_per_token_in': price_in, 'usd_per_token_out': price_out,
                # A router picks the model, and the price, at request time. Its
                # cost is unknown here — reporting it as a number would be a lie
                # whichever number we picked.
                'pricing': 'routed' if price_in is None and price_out is None
                           and m.get('prompt_price') is not None else 'fixed'})
        if provider:
            wanted = {p.strip().lower() for p in str(provider).split(',') if p.strip()}
            out = [m for m in out if (m['provider'] or '').lower() in wanted]
        if q:
            out = [m for m in out if _match(m['id'], q) or _match(m['name'], q)]
        if max_prompt_price is not None:
            cap = num(max_prompt_price)
            out = [m for m in out if m['usd_per_token_in'] is not None
                   and m['usd_per_token_in'] <= cap]
        if min_context is not None:
            floor = num(min_context, 0)
            out = [m for m in out if num(m['context'], 0) >= floor]
        keyed = {'price': lambda m: (m['usd_per_token_in'] is None, m['usd_per_token_in'] or 0),
                 'context': lambda m: -num(m['context'], 0),
                 'name': lambda m: (m['id'] or '').lower()}
        out.sort(key=keyed.get(sort, keyed['name']))
        matched = len(out)
        if limit:
            out = out[:int(limit)]
        return {'models': out, 'count': len(out), 'matched': matched, 'total': total,
                'providers': sorted({m.get('provider') for m in rows if m.get('provider')})}

    # ── rentals ──

    def quote(self, gpu_type, hours=1, spot=True, quantity=1):
        """What this costs before you commit to it, and what is cheaper."""
        catalog = self.gpus(available_only=False)['gpus']
        row = next((r for r in catalog
                    if (r['gpu_type'] or '').lower() == str(gpu_type).lower()
                    or (r['id'] or '').lower() == str(gpu_type).lower()), None)
        if row is None:
            near = [r['gpu_type'] for r in catalog if _match(r['name'], str(gpu_type))][:5]
            raise PolarisError(f'unknown gpu_type: {gpu_type}', status=404,
                               hint=f'did you mean: {", ".join(near)}' if near
                               else 'see `m polaris/gpus` for the catalog')
        rate = row['usd_hr_spot'] if truthy(spot, True) else row['usd_hr_on_demand']
        hours, quantity = num(hours, 1) or 1, int(num(quantity, 1) or 1)
        total = None if rate is None else round(rate * hours * quantity, 4)
        cheaper = [r for r in catalog
                   if r['available'] and r['usd_hr'] is not None and rate is not None
                   and r['usd_hr'] < rate and not r['is_cpu'] and
                   (r['vram_gb'] or 0) >= (row['vram_gb'] or 0)]
        cheaper.sort(key=lambda r: r['usd_hr'])
        return {'gpu_type': row['gpu_type'], 'name': row['name'],
                'available': row['available'], 'spot': truthy(spot, True),
                'usd_hr': rate, 'hours': hours, 'quantity': quantity,
                'estimate_usd': total,
                'confirm_required': total is not None and total > CONFIRM_USD,
                'confirm_threshold_usd': CONFIRM_USD,
                'cheaper_with_same_vram': cheaper[:3]}

    def rent(self, gpu_type, name='mod', hours=1, confirm=False, ssh_public_key=None,
             spot=True, quantity=1, **extra):
        """Provision an instance. Spends the caller's credits — hence the guard."""
        q = self.quote(gpu_type, hours=hours, spot=spot, quantity=quantity)
        if q['confirm_required'] and not truthy(confirm):
            raise PolarisError(
                f'renting {q["name"]} for {q["hours"]}h is about ${q["estimate_usd"]} '
                f'— above the ${CONFIRM_USD} guard', status=402,
                hint='pass confirm=true to go ahead, or pick a cheaper type from '
                     '`m polaris/gpus`. Billing runs until you call stop.',
                upstream=q)
        if not q['available']:
            raise PolarisError(f'{q["name"]} is not available right now', status=409,
                               hint='`m polaris/gpus` lists what is', upstream=q)
        key = ssh_public_key or ssh_key()
        if not key:
            raise PolarisError(
                'no SSH public key — the box would be unreachable', status=400,
                hint='pass ssh_public_key=, or create ~/.ssh/id_ed25519.pub')
        body = {'name': name, 'gpu_type': q['gpu_type'], 'ssh_public_key': key,
                'use_spot': truthy(spot, True), 'quantity': int(quantity)}
        body.update({k: v for k, v in extra.items() if v is not None})
        got = self.post('/compute/instances', body) or {}
        made = [_instance(i) for i in (got.get('instances') or [])]
        return {'ok': bool(got.get('success', True)), 'message': got.get('message'),
                'instances': made, 'quote': q,
                'next': 'poll `m polaris/instances` until status is running, then '
                        '`m polaris/ssh` — and `m polaris/stop` is what ends the billing'}

    def instances(self, state=None):
        """Everything you are paying for right now, with the burn rate."""
        got = self.get('/compute/instances')
        rows = got if isinstance(got, list) else (got.get('instances') or [])
        out = [_instance(i) for i in rows]
        if state:
            out = [i for i in out if _match(i.get('status'), state)]
        burn = sum(i['usd_hr'] for i in out
                   if i.get('usd_hr') and (i.get('status') or '').lower()
                   in ('running', 'provisioning', 'pending', 'starting'))
        return {'instances': out, 'count': len(out), 'burn_usd_hr': round(burn, 4)}

    def instance(self, instance_id):
        """One instance. Upstream has no by-id route, so this filters the list."""
        for i in self.instances()['instances']:
            if i.get('id') == instance_id or i.get('name') == instance_id:
                return i
        raise PolarisError(f'no instance {instance_id}', status=404,
                           hint='`m polaris/instances` lists them')

    def ssh(self, instance_id=None):
        """The command that gets you a shell, once the box is up."""
        rows = self.instances()['instances']
        if instance_id:
            rows = [i for i in rows
                    if i.get('id') == instance_id or i.get('name') == instance_id]
            if not rows:
                raise PolarisError(f'no instance {instance_id}', status=404)
        live = [i for i in rows if i.get('ssh')]
        if not live:
            pending = [i.get('status') for i in rows]
            raise PolarisError(
                'no instance has an address yet', status=409,
                hint=f'still {", ".join(p for p in pending if p) or "empty"} — '
                     'provisioning usually takes 1-3 minutes')
        return {'ssh': [{'id': i['id'], 'name': i.get('name'), 'cmd': i['ssh'],
                         'access_url': i.get('access_url')} for i in live]}

    def stop(self, instance_id):
        """Terminate. This, and only this, is what stops the billing."""
        got = self.delete(f'/compute/instances/{instance_id}') or {}
        return {'stopped': instance_id, 'ok': bool(got.get('success', True)),
                'message': got.get('message'), 'billing': 'stops immediately'}

    # ── deployments (a template running on a box) ──

    def deployments(self, state=None, kind=None):
        rows = (self.get('/deployments') or {}).get('deployments') or []
        out = [{'id': d.get('id'), 'name': d.get('name'), 'status': d.get('status'),
                'type': d.get('type'), 'template': d.get('template_id'),
                'gpu': d.get('gpu'), 'endpoint': _clean(d.get('endpoint')),
                'cost': d.get('cost'), 'created': d.get('created'),
                'provider_instance_id': d.get('provider_instance_id')}
               for d in rows]
        if state:
            out = [d for d in out if _match(d.get('status'), state)]
        if kind:
            out = [d for d in out if _match(d.get('type'), kind)]
        live = [d for d in out if (d.get('status') or '').lower() in ('running', 'deploying')]
        return {'deployments': out, 'count': len(out), 'running': len(live)}

    def deployment(self, deployment_id):
        """One deployment in full — host, port, progress, parameters."""
        return self.get(f'/templates/deployments/{deployment_id}')

    def deployment_logs(self, deployment_id, tail=None):
        got = self.get(f'/deployments/{deployment_id}/logs') or {}
        logs = got.get('logs') or ''
        if tail:
            logs = '\n'.join(logs.splitlines()[-int(tail):])
        return {'id': deployment_id, 'logs': logs}

    def activity(self, limit=25):
        """The event tape: what deployed, what spoke, what fell over."""
        got = self.get('/activity', params={'limit': limit}) or {}
        rows = got.get('events') or []
        return {'events': rows[:int(limit)] if limit else rows, 'count': len(rows)}

    # ── account and billing ──

    def account(self):
        me = self.get('/auth/me') or {}
        return {'id': me.get('id'), 'email': me.get('email'), 'name': me.get('name'),
                'handle': me.get('handle'), 'tier': me.get('tier'),
                'compute_hours_used': num(me.get('compute_hours_used')),
                'compute_hours_limit': num(me.get('compute_hours_limit')),
                'compute_hours_remaining': num(me.get('compute_hours_remaining')),
                'max_deployments': me.get('max_deployments'),
                'storage_bytes_used': me.get('storage_bytes_used'),
                'storage_bytes_limit': me.get('storage_bytes_limit'),
                'created_at': me.get('created_at')}

    def credits(self):
        """The prepaid balance. `account_status` is what gates new instances."""
        c = self.get('/billing/credits') or {}
        return {'balance_usd': _usd(c.get('balance_usd')),
                'pending_usd': _usd(c.get('pending_usd')),
                'status': c.get('account_status'),
                'can_run_once': c.get('can_run_once'),
                'can_create_sandbox': c.get('can_create_sandbox'),
                'attest_price_usd': _usd(c.get('attest_price_usd')),
                'raw': c}

    balance = credits

    def history(self, limit=20, offset=0):
        return self.get('/billing/credits/history',
                        params={'limit': limit, 'offset': offset})

    def packs(self):
        """Top-up sizes. Buying one is a browser flow, not an API call."""
        return self.get('/billing/credits/packs', auth=False)

    def usage(self, billing=True):
        """Two different meters upstream: request counts, and compute seconds."""
        out = {'requests': self.get('/usage')}
        if truthy(billing, True):
            out['compute'] = self.get('/billing/usage')
        return out

    def stats(self):
        return self.get('/stats')

    def api_keys(self):
        """Key metadata only — the secrets themselves are never returned."""
        got = self.get('/keys') or {}
        rows = got.get('keys') if isinstance(got, dict) else got
        return {'keys': [{'id': k.get('id'), 'name': k.get('name'),
                          'prefix': k.get('key_prefix'), 'created_at': k.get('created_at'),
                          'last_used': k.get('last_used'),
                          'requests': k.get('request_count')}
                         for k in (rows or [])]}

    # ── whole-account snapshot, and the escape hatch ──

    def status(self):
        """One call for the console's first paint: money, boxes, deployments."""
        out = {}
        for name, fn in (('credits', self.credits), ('account', self.account),
                         ('instances', self.instances), ('deployments', self.deployments)):
            try:
                out[name] = fn()
            except PolarisError as e:
                out[name] = e.dict()
        inst = out.get('instances') or {}
        out['burn_usd_hr'] = inst.get('burn_usd_hr', 0) if isinstance(inst, dict) else 0
        bal = (out.get('credits') or {}).get('balance_usd')
        burn = out['burn_usd_hr']
        out['hours_of_runway'] = round(bal / burn, 2) if bal and burn else None
        return out

    def raw(self, path, method='GET', body=None, params=None, auth=True):
        """Polaris's own API, unnormalized — for routes this module has not
        wrapped yet. `path` is relative to /api."""
        return self.request(path, method=str(method).upper(), body=body,
                            params=params, auth=truthy(auth, True))


# ── module-level helpers ──

def _clean(v):
    return None if v in ('N/A', '', None) else v


def _token_price(v):
    """A per-token price, or None when there is not really one.

    The upstream carries OpenRouter's catalog, where a router model prices at
    -1 to mean "decided when the request is routed". Passed through as a
    number it reads as a $1,000,000-per-million-token credit and sorts to the
    top of every cheapest-first list.
    """
    p = num(v)
    return None if p is None or p < 0 else p


def _instance(i):
    """One upstream instance row → the shape every verb here returns."""
    ip = i.get('ip') or i.get('public_ip') or i.get('ssh_host')
    user = i.get('ssh_user') or 'root'
    port = i.get('ssh_port') or 22
    cmd = i.get('ssh_command')
    if not cmd and ip:
        cmd = f'ssh {user}@{ip}' + ('' if str(port) == '22' else f' -p {port}')
    return {'id': i.get('id'), 'name': i.get('name'), 'status': i.get('status'),
            'gpu_type': i.get('gpu_type'), 'ip': ip,
            'usd_hr': num(i.get('hourly_cost'), num(i.get('price_per_hour'))),
            'ssh': cmd, 'access_url': _clean(i.get('access_url')),
            'provider': i.get('provider'), 'created_at': i.get('created_at'),
            'provider_instance_id': i.get('provider_instance_id')}


def ssh_key():
    """The public half this box would hand to Polaris at rent time."""
    for name in ('id_ed25519.pub', 'id_rsa.pub', 'id_ecdsa.pub'):
        path = os.path.expanduser(f'~/.ssh/{name}')
        try:
            with open(path) as f:
                got = f.read().strip()
            if got:
                return got
        except OSError:
            continue
    return None
