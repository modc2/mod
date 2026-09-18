#!/usr/bin/env python3
"""trustedstake — run a TrustedStake index from the shell.

TrustedStake is non-custodial Bittensor staking. A delegator signs one
`addProxy(<index proxy>, Staking)` extrinsic and from then on the index's
manager rebalances that stake across subnets — without ever being able to move
the principal. The delegator revokes the proxy to walk away; the TAO never
leaves their coldkey.

This module is the MANAGER side of that platform: the Manager API at
https://api.app.trustedstake.ai/api/v1/manager-api. List your strategies, set
the subnet weights, pause and resume, queue a rebalance, read what the trading
engine actually did, and see who is delegated behind you.

    m trustedstake                          # what this is, and whether a key is set
    m trustedstake/strategies               # your strategies
    m trustedstake/strategy <id>            # one, in full
    m trustedstake/weights 8:70,120:30      # validate a basket — no network, no key
    m trustedstake/update <id> weights=8:70,120:30
    m trustedstake/rebalance <id>           # queue one now
    m trustedstake/activity <id>            # what the engine did, newest first
    m trustedstake/delegators <id>          # who is staked behind you
    m trustedstake/pause <id> | m trustedstake/resume <id>
    m trustedstake/fees                     # the documented economics
    m trustedstake/raw GET /strategies      # any Manager API route

BYOK — this module holds no house key. Every call spends the caller's own
rate limit against their own strategies. `m trustedstake/set_key ts_mk_…`
stores a key at ~/.mod/trustedstake/key.json (0600, off-tree); keys are minted
in the platform dashboard (Manager → Settings → API Keys) and are scoped, so a
call that needs a scope you did not grant returns 403 rather than acting.

Nothing here signs a chain extrinsic or moves a delegator's TAO. The only
writes possible are to your own strategy definitions.

Python stdlib only.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

BASE = os.environ.get('TRUSTEDSTAKE_BASE_URL', 'https://api.app.trustedstake.ai')
PREFIX = '/api/v1/manager-api'
KEY_DIR = os.path.expanduser('~/.mod/trustedstake')
KEY_FILE = os.path.join(KEY_DIR, 'key.json')
TIMEOUT = float(os.environ.get('TRUSTEDSTAKE_TIMEOUT', 30))
RETRIES = 2

# Documented on 2026-09-10. Numbers the platform publishes, not numbers this
# module enforces — the API is always the authority, this is the sanity check
# that saves a round trip (and a 400) when a basket is obviously malformed.
DOCS = 'https://trustedstake.gitbook.io/trustedstake'
SCOPES = {
    'strategies:read': 'list strategies, strategy detail, delegators',
    'strategies:write': 'create, update, delete, pause, resume, whitelist',
    'rebalances:trigger': 'queue manual rebalances',
    'operations:read': 'strategy transaction history and activity',
}
LIMITS = {
    'active_strategies_per_wallet': 3,
    'active_api_keys_per_wallet': 5,
    'api_key_lifetime_days_default': 90,
    'api_key_lifetime_days_max': 365,
    'requests_per_minute_per_key': 130,
    'weights_must_sum_to': 100,
}
# Minimum delegator balance by constituent count. The published table stops at
# 12 with an "etc." — so do we, rather than inventing the tail.
MIN_BALANCE_TIERS = ((3, 0.5), (6, 1.0), (12, 2.0))


class TrustedStakeError(Exception):
    """An upstream refusal, carried with its status and a plain-language hint."""

    def __init__(self, message, status=None, code=None, hint=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.hint = hint


# ── key (BYOK, off-tree) ─────────────────────────────────────────

def _keystore():
    try:
        with open(KEY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_key(key=None):
    """The caller's Manager API key, or None. Never logged, never returned."""
    return (key
            or os.environ.get('TRUSTEDSTAKE_API_KEY')
            or os.environ.get('TRUSTEDSTAKE_MANAGER_KEY')
            or _keystore().get('api_key')
            or None)


def _mask(key):
    if not key:
        return None
    return f'{key[:8]}…{key[-4:]}' if len(key) > 16 else '…'


# ── weights ──────────────────────────────────────────────────────

def parse_weights(weights):
    """`"8:70,120:30"`, `{"8": 70}` or a JSON string → a clean subnet→weight map.

    Accepts what a shell makes easy and what an agent already has. Rejects
    anything the API would reject, here, where the error is readable.
    """
    if weights is None:
        raise TrustedStakeError('weights are required', status=400)
    if isinstance(weights, str):
        text = weights.strip()
        if text.startswith('{'):
            try:
                weights = json.loads(text)
            except json.JSONDecodeError as e:
                raise TrustedStakeError(f'weights is not valid JSON: {e}',
                                        status=400) from None
        else:
            pairs = []
            for part in text.replace(';', ',').split(','):
                part = part.strip()
                if not part:
                    continue
                for sep in (':', '=', ' '):
                    if sep in part:
                        netuid, _, weight = part.partition(sep)
                        pairs.append((netuid.strip(), weight.strip()))
                        break
                else:
                    raise TrustedStakeError(
                        f'cannot read "{part}" — write weights as 8:70,120:30',
                        status=400) from None
            weights = dict(pairs)
    if not isinstance(weights, dict) or not weights:
        raise TrustedStakeError('weights must be a non-empty subnet→weight map',
                                status=400)

    out = {}
    for netuid, weight in weights.items():
        try:
            uid = int(str(netuid).strip().lstrip('sn').lstrip('#'))
        except ValueError:
            raise TrustedStakeError(f'"{netuid}" is not a subnet id', status=400) from None
        try:
            value = float(weight)
        except (TypeError, ValueError):
            raise TrustedStakeError(f'weight for subnet {uid} is not a number',
                                    status=400) from None
        if uid < 0:
            raise TrustedStakeError(f'subnet id {uid} is negative', status=400)
        if value <= 0:
            raise TrustedStakeError(
                f'subnet {uid} has weight {value} — drop it instead of weighting it zero',
                status=400)
        out[str(uid)] = int(value) if float(value).is_integer() else value

    total = round(sum(out.values()), 6)
    if total != float(LIMITS['weights_must_sum_to']):
        raise TrustedStakeError(
            f'weights sum to {total}, not 100 — '
            f'{"add" if total < 100 else "remove"} {abs(round(100 - total, 6))}',
            status=400)
    return out


def min_balance(constituents):
    """The published delegator minimum for a basket of this size, in TAO."""
    for ceiling, tao in MIN_BALANCE_TIERS:
        if constituents <= ceiling:
            return tao
    return None  # published table stops at 12 — the API enforces the real floor


class Mod:
    description = ('TrustedStake — non-custodial Bittensor index staking, as one mod. '
                   'Delegators point a Staking proxy at an index and keep custody of '
                   'their TAO; the index manager rebalances the basket across subnets. '
                   'This is the manager desk: strategies, subnet weights, pause/resume, '
                   'manual rebalances, the engine activity log and the delegator roster, '
                   'over the platform Manager API. BYOK, off-tree key, read-only by '
                   'default — it signs no extrinsic and can touch nobody else\'s stake.')

    fns = [
        'forward', 'info', 'health', 'account',
        'strategies', 'ls', 'strategy',
        'create', 'update', 'set_weights', 'pause', 'resume', 'delete',
        'rebalance', 'activity', 'delegators',
        'weights', 'fees', 'limits', 'scopes', 'docs',
        'set_key', 'raw', 'test', 'readme',
    ]

    def __init__(self, key=None, base=None, timeout=None, **kwargs):
        self.dir = HERE
        self.base = (base or BASE).rstrip('/')
        self.timeout = float(timeout or TIMEOUT)
        self._key = key

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        """This module's config.json."""
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def _request(self, method, path, body=None, params=None, need_key=True):
        """One Manager API call. Returns `data`; raises TrustedStakeError on refusal."""
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += '?' + urllib.parse.urlencode(clean)

        headers = {'Accept': 'application/json', 'User-Agent': 'mod/trustedstake'}
        if need_key:
            key = resolve_key(self._key)
            if not key:
                raise TrustedStakeError(
                    'no Manager API key — run `m trustedstake/set_key ts_mk_…`',
                    status=401,
                    hint='mint one in the dashboard: Manager → Settings → API Keys')
            headers['Authorization'] = f'Bearer {key}'

        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'

        for attempt in range(RETRIES + 1):
            req = urllib.request.Request(url, data=payload, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read().decode('utf-8', 'replace')
                return self._unwrap(raw, path)
            except urllib.error.HTTPError as e:
                text = (e.read() or b'').decode('utf-8', 'replace')
                if e.code in (429, 502, 503, 504) and attempt < RETRIES:
                    time.sleep(float(e.headers.get('Retry-After') or 1.5 * (attempt + 1)))
                    continue
                raise self._error(e.code, text, path) from None
            except urllib.error.URLError as e:
                if attempt < RETRIES:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise TrustedStakeError(f'cannot reach TrustedStake: {e.reason}',
                                        status=502) from None
        raise TrustedStakeError('retries exhausted', status=502)

    @staticmethod
    def _unwrap(raw, path):
        """The API answers {success, data} | {success, error} — return the meat."""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise TrustedStakeError(f'non-JSON answer on {path}: {raw[:200]}',
                                    status=502) from None
        if isinstance(body, dict) and body.get('success') is False:
            err = body.get('error') or {}
            raise TrustedStakeError(err.get('message') or f'refused on {path}',
                                    status=400, code=err.get('code'))
        if isinstance(body, dict) and 'data' in body:
            return body['data']
        return body

    @staticmethod
    def _error(status, text, path):
        code = None
        message = text[:400]
        try:
            err = (json.loads(text) or {}).get('error')
            if isinstance(err, dict):
                code, message = err.get('code'), err.get('message') or message
            elif isinstance(err, str):
                message = message or err
            message = (json.loads(text) or {}).get('message') or message
        except Exception:
            pass
        hints = {
            401: 'key missing, expired or revoked — `m trustedstake/set_key ts_mk_…`',
            403: f'the key lacks the scope this route needs ({", ".join(SCOPES)})',
            404: 'no such strategy, or it is not yours',
            429: f'over {LIMITS["requests_per_minute_per_key"]} requests/minute on this key',
        }
        return TrustedStakeError(f'TrustedStake {status} on {path}: {message}',
                                 status=status, code=code, hint=hints.get(status))

    # ── what this is ─────────────────────────────────────────────

    def info(self):
        """The module, the upstream, the routes, and whether a key is present."""
        key = resolve_key(self._key)
        return {
            'module': 'trustedstake',
            'what': 'non-custodial Bittensor index staking — the manager desk',
            'upstream': self.base + PREFIX,
            'docs': DOCS,
            'app': 'https://app.trustedstake.ai',
            'custody': ('delegators sign addProxy(<index proxy>, Staking) and keep '
                        'their TAO; revoking the proxy ends the relationship'),
            'auth': {'model': 'BYOK (Authorization: Bearer ts_mk_…)',
                     'key_present': bool(key),
                     'key': _mask(key),
                     'order': ['key= argument', 'TRUSTEDSTAKE_API_KEY',
                               'TRUSTEDSTAKE_MANAGER_KEY', KEY_FILE],
                     'scopes': SCOPES},
            'fns': self.fns,
            'limits': LIMITS,
            'keyless': ['info', 'health', 'weights', 'fees', 'limits', 'scopes',
                        'docs', 'test', 'readme'],
        }

    forward = info

    def health(self):
        """Is the platform API up? Keyless."""
        return {'upstream': self.base, **(self._request('GET', '/health', need_key=False) or {})}

    def account(self):
        """Prove the key works, and show what it can see."""
        rows = self.strategies()
        items = rows if isinstance(rows, list) else (rows or {}).get('strategies') or []
        return {'key': _mask(resolve_key(self._key)), 'ok': True,
                'strategies': len(items) if isinstance(items, list) else None}

    # ── strategies ───────────────────────────────────────────────

    def strategies(self):
        """Every strategy you own or are a member of. Needs strategies:read."""
        return self._request('GET', f'{PREFIX}/strategies')

    ls = strategies

    def strategy(self, id):
        """One strategy in full — constituents, weights, AUM, access. strategies:read."""
        return self._request('GET', f'{PREFIX}/strategies/{id}')

    def create(self, name, description, weights, **extra):
        """Launch a strategy from a basket. strategies:write.

        Weights are validated here before the call, so a basket that does not
        sum to 100 costs nothing. Any extra keyword is passed through to the
        API untouched, so platform fields this module has not heard of still work.
        """
        basket = parse_weights(weights)
        body = {'name': name, 'description': description,
                'targetConstituents': {'subnetWeights': basket}, **extra}
        return self._request('POST', f'{PREFIX}/strategies', body=body)

    def update(self, id, weights=None, name=None, description=None, **extra):
        """Change weights, name, description or access on a strategy. strategies:write.

        A weight change takes effect on the engine's next hourly threshold
        check — `rebalance` it if you want the trades now.
        """
        body = dict(extra)
        if weights is not None:
            body['targetConstituents'] = {'subnetWeights': parse_weights(weights)}
        if name is not None:
            body['name'] = name
        if description is not None:
            body['description'] = description
        if not body:
            raise TrustedStakeError('nothing to update', status=400)
        return self._request('PATCH', f'{PREFIX}/strategies/{id}', body=body)

    def set_weights(self, id, weights):
        """Replace the basket, nothing else. strategies:write."""
        return self.update(id, weights=weights)

    def pause(self, id):
        """Stop rebalancing this strategy, keep the stake where it is. strategies:write."""
        return self._request('PATCH', f'{PREFIX}/strategies/{id}/active',
                             body={'isActive': False})

    def resume(self, id):
        """Hand the strategy back to the engine. strategies:write."""
        return self._request('PATCH', f'{PREFIX}/strategies/{id}/active',
                             body={'isActive': True})

    def delete(self, id, confirm=False):
        """Delete a strategy permanently. strategies:write.

        Irreversible and visible to everyone delegated to it, so it asks:
        pass confirm=1. Consider `pause` first.
        """
        if not confirm:
            raise TrustedStakeError(
                f'delete is permanent and ends the strategy for its delegators — '
                f'pass confirm=1 (or `m trustedstake/pause {id}` instead)',
                status=400)
        return self._request('DELETE', f'{PREFIX}/strategies/{id}')

    # ── operations ───────────────────────────────────────────────

    def rebalance(self, id):
        """Queue a manual rebalance now. rebalances:trigger.

        Returns as soon as the job is queued — the engine trades asynchronously,
        so read `activity` for what it actually did. Manual rebalances carry a
        per-strategy daily cap and some cadences are plan-gated
        (REBALANCE_FREQUENCY_GATED).
        """
        return self._request('POST', f'{PREFIX}/strategies/{id}/rebalance', body={})

    def activity(self, id, page=1, limit=20):
        """What the engine did, newest first, paginated. operations:read."""
        return self._request('GET', f'{PREFIX}/strategies/{id}/activity',
                             params={'page': page, 'limit': limit})

    def delegators(self, id, active_only=True):
        """Who is staked behind this strategy. strategies:read."""
        return self._request('GET', f'{PREFIX}/strategies/{id}/delegators',
                             params={'activeOnly': 'true' if active_only else 'false'})

    def raw(self, path, method='GET', body=None, **params):
        """Escape hatch: any Manager API route, with your key attached.

        `m trustedstake/raw /strategies` — a bare path is resolved under the
        Manager API prefix; pass one starting with /api to go anywhere.
        """
        if not path.startswith('/'):
            path = '/' + path
        if not path.startswith('/api'):
            path = PREFIX + path
        if isinstance(body, str):
            body = json.loads(body)
        return self._request(method.upper(), path, body=body, params=params or None)

    # ── reference (keyless) ──────────────────────────────────────

    def weights(self, weights):
        """Validate a basket without spending a call: normalized map, sum, minimum."""
        basket = parse_weights(weights)
        return {'subnetWeights': basket, 'constituents': len(basket),
                'sum': round(sum(basket.values()), 6),
                'min_delegator_balance_tao': min_balance(len(basket)),
                'body': {'targetConstituents': {'subnetWeights': basket}}}

    def fees(self):
        """The published economics. Verified against the docs on 2026-09-10."""
        return {
            'take_rate_pct': 9.0,
            'effective_take_rate_pct': 6.62,
            'effective_note': ('~26.5% fee reduction via the Kraken Institutional '
                               'partnership, quoted by the docs as of 2026-05-07'),
            'basis': 'staking yield (validator dividends) only — never the principal TAO',
            'mechanism': 'each block the validator pays rewards; the platform takes its '
                         'share and the remainder is distributed to stakers',
            'management_or_performance_fee': None,
            'source': f'{DOCS}/basics/lets-talk-fees',
            'verified': '2026-09-10',
        }

    def limits(self):
        """Platform limits worth knowing before a call fails. Verified 2026-09-10."""
        return {**LIMITS,
                'min_delegator_balance_tao': {'1-3': 0.5, '4-6': 1.0, '7-12': 2.0,
                                              '13+': 'published table stops at 12'},
                'rebalance': {'auto': 'hourly, when profitability thresholds are met',
                              'manual': 'immediate, daily cap per strategy'},
                'source': f'{DOCS}/strategies/manager-api'}

    def scopes(self):
        """What each API key scope unlocks."""
        return SCOPES

    def docs(self):
        """Where the authoritative documentation lives."""
        return {'docs': DOCS, 'manager_api': f'{DOCS}/strategies/manager-api',
                'quickstart': f'{DOCS}/getting-started/quickstart',
                'rebalancer': f'{DOCS}/basics/trading-engine-and-rebalancer',
                'fees': f'{DOCS}/basics/lets-talk-fees',
                'risk': f'{DOCS}/basics/risk-classification-system',
                'app': 'https://app.trustedstake.ai', 'site': 'https://trustedstake.ai'}

    # ── key ──────────────────────────────────────────────────────

    def set_key(self, key, persist=True):
        """Store a Manager API key at ~/.mod/trustedstake/key.json (0600, off-tree)."""
        key = (key or '').strip()
        if not key:
            raise TrustedStakeError('key is required', status=400)
        if not key.startswith('ts_mk_'):
            # A warning, not a refusal: the prefix is the platform's convention
            # today and this module should not be the thing that breaks when it
            # changes.
            pass
        if persist:
            os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
            with open(KEY_FILE, 'w') as f:
                json.dump({'api_key': key}, f)
            os.chmod(KEY_FILE, 0o600)
        else:
            os.environ['TRUSTEDSTAKE_API_KEY'] = key
        return {'ok': True, 'key': _mask(key),
                'stored': KEY_FILE if persist else 'process environment',
                'warning': None if key.startswith('ts_mk_') else
                           'expected a ts_mk_… manager key — stored anyway'}

    # ── self-check ───────────────────────────────────────────────

    def test(self):
        """Offline: the weight parser, the tiers, the error unwrapping. No network."""
        checks = []

        def ok(name, got, want):
            checks.append({'check': name, 'ok': got == want, 'got': got, 'want': want})

        ok('pairs', parse_weights('8:70,120:30'), {'8': 70, '120': 30})
        ok('equals+spaces', parse_weights(' 8 = 50 ; 120=50 '), {'8': 50, '120': 50})
        ok('json', parse_weights('{"8": 70, "120": 30}'), {'8': 70, '120': 30})
        ok('dict', parse_weights({8: 25, 120: 75}), {'8': 25, '120': 75})
        ok('sn prefix', parse_weights('sn8:100'), {'8': 100})
        ok('float kept', parse_weights('8:99.5,120:0.5'), {'8': 99.5, '120': 0.5})
        ok('tier 3', min_balance(3), 0.5)
        ok('tier 6', min_balance(6), 1.0)
        ok('tier 12', min_balance(12), 2.0)
        ok('tier 13', min_balance(13), None)

        for name, bad in (('sum<100', '8:70,120:20'), ('sum>100', '8:70,120:40'),
                          ('zero weight', '8:100,120:0'), ('not a subnet', 'eight:100'),
                          ('garbage', '8'), ('empty', {})):
            try:
                parse_weights(bad)
                checks.append({'check': f'rejects {name}', 'ok': False,
                               'got': 'accepted', 'want': 'TrustedStakeError'})
            except TrustedStakeError:
                checks.append({'check': f'rejects {name}', 'ok': True})

        try:
            Mod._unwrap('{"success": false, "error": {"code": "X", "message": "no"}}', '/t')
            checks.append({'check': 'unwrap surfaces error', 'ok': False})
        except TrustedStakeError as e:
            checks.append({'check': 'unwrap surfaces error', 'ok': e.code == 'X'})
        ok('unwrap returns data', Mod._unwrap('{"success": true, "data": [1]}', '/t'), [1])

        failed = [c for c in checks if not c['ok']]
        return {'ok': not failed, 'passed': len(checks) - len(failed),
                'failed': failed, 'checks': checks}

    def readme(self):
        """This module's README."""
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()


if __name__ == '__main__':
    print(json.dumps(Mod().test(), indent=2))
