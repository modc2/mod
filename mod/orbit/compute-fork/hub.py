"""The aggregator: one set of verbs over every compute market.

Three rules the whole module rests on.

  1. **One vocabulary.** Every market answers in the same offer/instance shape,
     priced in USD/hr, addressed by a namespaced id (`lium:b7095b41…`). An
     agent learns eight verbs once instead of nine APIs.
  2. **One caller's money.** Keys are resolved per request and per provider —
     env, this module's off-tree keystore, or the sibling module's key file.
     No house key, no cross-provider bleed, no key in any response.
  3. **Partial answers beat failures.** A fan-out never fails because one
     market is down or unkeyed; the dead ones come back in `providers` with
     their reason, and the rest of the answer is still there.

Nothing here knows a provider's name — that lives in providers/.
"""

import concurrent.futures
import os
import re
import time

import providers as P
from providers.base import ProviderError

# A rental keeps billing after the agent stops paying attention, so anything
# above this per-hour estimate needs an explicit confirm=true.
CONFIRM_USD = float(os.environ.get('COMPUTE_FORK_CONFIRM_USD', '0.50'))
FANOUT_TIMEOUT = float(os.environ.get('COMPUTE_FORK_FANOUT_TIMEOUT', '30'))


def split_id(oid):
    """'lium:b7095b41' → ('lium', 'b7095b41'). Refs may contain ':' themselves."""
    s = str(oid or '').strip()
    if ':' not in s:
        raise ProviderError(
            f'id must be provider:ref (e.g. lium:b7095b41), got "{s}" — '
            f'ids come back from computefork_search')
    name, ref = s.split(':', 1)
    return name.strip().lower(), ref.strip()


# Every market spells the same card differently — "NVIDIA H100 80GB HBM3",
# "NVIDIA-H100", "H100 SXM", "NVIDIA 4090 Community". The model number is the
# only part they agree on, so that is what a cross-market comparison searches.
_MODEL = re.compile(r'^[a-z]{0,3}\d{3,5}[a-z]{0,2}$')
_NOT_MODEL = re.compile(r'^\d+(gb|g|mb|tb)$')


def gpu_model(name):
    """'NVIDIA H100 80GB HBM3' → 'h100'. None when there is no model token."""
    for tok in str(name or '').replace('-', ' ').lower().split():
        if _NOT_MODEL.match(tok):
            continue
        if _MODEL.match(tok):
            return tok
    return None


class Hub:
    def __init__(self, keys=None):
        # keys: {provider: key} from the caller — headers, CLI arg, or nothing.
        self.keys = {k.lower(): v for k, v in (keys or {}).items() if v}

    # ── who is plugged in ─────────────────────────────────────────────

    def providers(self, kyc=None, cap=None, names=None):
        return {'providers': [p.card() for p in
                              P.every(self.keys, names=names, cap=cap, kyc=kyc)],
                'count': len(P.REGISTRY),
                'verbs': list(P.CAPS),
                'confirm_usd': CONFIRM_USD}

    # ── search ────────────────────────────────────────────────────────

    def search(self, provider=None, kyc=None, sort='price', **filters):
        f = P.Filters(**filters)
        pool = P.every(self.keys, names=provider, cap='search', kyc=kyc)
        if not pool:
            raise ProviderError(f'no provider matches provider={provider} kyc={kyc}')
        started = time.time()
        offers, report = [], {}

        def one(p):
            return p.name, p.search(f)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(pool))) as ex:
            futures = {ex.submit(one, p): p for p in pool}
            for fut in concurrent.futures.as_completed(futures, timeout=FANOUT_TIMEOUT + 5):
                p = futures[fut]
                try:
                    _, rows = fut.result(timeout=FANOUT_TIMEOUT)
                    offers.extend(rows)
                    report[p.name] = len(rows)
                except ProviderError as e:
                    report[p.name] = e.dict()
                except Exception as e:
                    report[p.name] = {'error': f'{type(e).__name__}: {e}',
                                      'provider': p.name}

        priced = [o for o in offers if o.get('usd_hr') is not None]
        unpriced = [o for o in offers if o.get('usd_hr') is None]
        if sort == 'vram':
            priced.sort(key=lambda o: -(o.get('vram_gb') or 0))
        elif sort == 'gpus':
            priced.sort(key=lambda o: -(o.get('gpus') or 0))
        else:
            priced.sort(key=lambda o: o['usd_hr'])
        rows = (priced + unpriced)[:f.limit]
        return {
            'filters': f.dict(),
            'count': len(rows),
            'total_found': len(offers),
            'cheapest': priced[0] if priced else None,
            'offers': rows,
            'providers': report,
            'took_ms': round((time.time() - started) * 1000),
        }

    def offer(self, oid):
        """One offer, re-read from its own provider."""
        name, ref = split_id(oid)
        p = P.get(name, self.keys)
        for o in p.search(P.Filters(available_only=False, limit=200)):
            if o['ref'] == ref:
                return o
        raise ProviderError(f'{name}: no offer {ref} — it may have been rented',
                            provider=name)

    # ── quote ─────────────────────────────────────────────────────────

    def quote(self, oid, hours=1.0):
        """What it costs, and what the same shape costs everywhere else."""
        hours = float(hours or 1)
        o = self.offer(oid)
        alts = []
        model = gpu_model(o.get('gpu'))
        if model:
            found = self.search(gpu=model, min_gpus=o.get('gpus') or 1, limit=8)
            alts = [a for a in found['offers'] if a['id'] != o['id']][:5]
        usd_hr = o.get('usd_hr')
        return {
            'offer': o,
            'hours': hours,
            'usd_hr': usd_hr,
            'total_usd': round(usd_hr * hours, 4) if usd_hr is not None else None,
            'needs_confirm': bool(usd_hr is not None and usd_hr * hours > CONFIRM_USD),
            'confirm_usd': CONFIRM_USD,
            'alternatives': alts,
            'cheaper_elsewhere': [a for a in alts
                                  if usd_hr is not None and (a.get('usd_hr') or 1e9) < usd_hr],
        }

    # ── rent ──────────────────────────────────────────────────────────

    def rent(self, oid, hours=1.0, confirm=False, **opts):
        """Start a rental. Refuses above the spend guard without confirm=true."""
        name, ref = split_id(oid)
        p = P.get(name, self.keys)
        if 'rent' not in p.caps:
            # The provider still gets asked: a chain-native market answers with
            # a deployment plan or the exact command that would work, which is
            # more useful than this layer inventing a refusal.
            return p.rent(ref, hours=hours, **opts)
        if not confirm:
            try:
                q = self.quote(oid, hours)
            except ProviderError:
                q = None
            if q and q['needs_confirm']:
                return {'rented': False, 'needs_confirm': True, 'quote': q,
                        'why': f"est ${q['total_usd']} for {hours}h is over the "
                               f"${CONFIRM_USD} guard — call again with confirm=true",
                        'id': oid}
        inst = p.rent(ref, hours=hours, **opts)
        inst['rented_at'] = time.time()
        inst['hint'] = ('billing runs until computefork_stop — '
                        f'`m compute-fork/stop id={inst["id"]}`')
        return inst

    # ── running things ────────────────────────────────────────────────

    def instances(self, provider=None):
        pool = [p for p in P.every(self.keys, names=provider, cap='instances')
                if p.has_key()]
        rows, report = [], {}

        def one(p):
            return p.name, p.instances()

        if not pool:
            return {'instances': [], 'count': 0, 'providers': {},
                    'note': 'no provider has a key — `m compute-fork/set_key provider=… key=…`'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(pool))) as ex:
            futures = {ex.submit(one, p): p for p in pool}
            for fut in concurrent.futures.as_completed(futures, timeout=FANOUT_TIMEOUT + 5):
                p = futures[fut]
                try:
                    _, got = fut.result(timeout=FANOUT_TIMEOUT)
                    rows.extend(got)
                    report[p.name] = len(got)
                except ProviderError as e:
                    report[p.name] = e.dict()
                except Exception as e:
                    report[p.name] = {'error': f'{type(e).__name__}: {e}'}
        burn = sum(r['usd_hr'] for r in rows if r.get('usd_hr'))
        return {'instances': rows, 'count': len(rows),
                'burn_usd_hr': round(burn, 4), 'providers': report}

    def status(self, iid):
        name, ref = split_id(iid)
        return P.get(name, self.keys).status(ref)

    def logs(self, iid, tail=200):
        name, ref = split_id(iid)
        out = P.get(name, self.keys).logs(ref, tail=tail)
        return out if isinstance(out, dict) else {'logs': out}

    def exec(self, iid, cmd):
        name, ref = split_id(iid)
        out = P.get(name, self.keys).exec(ref, cmd)
        return out if isinstance(out, dict) else {'output': out}

    def stop(self, iid):
        name, ref = split_id(iid)
        return P.get(name, self.keys).stop(ref)

    # ── money ─────────────────────────────────────────────────────────

    def balance(self, provider=None):
        pool = [p for p in P.every(self.keys, names=provider, cap='balance')
                if p.has_key()]
        out = {}
        for p in pool:
            try:
                out[p.name] = p.balance()
            except ProviderError as e:
                out[p.name] = e.dict()
            except Exception as e:
                out[p.name] = {'error': f'{type(e).__name__}: {e}'}
        total = sum(v.get('balance_usd') or 0 for v in out.values()
                    if isinstance(v, dict) and isinstance(v.get('balance_usd'), (int, float)))
        return {'balances': out, 'total_usd': round(total, 2),
                'note': 'each balance is on that provider\'s own account'}

    # ── keys / escape hatch ───────────────────────────────────────────

    def set_key(self, provider, key, persist=True):
        if provider not in P.REGISTRY:
            raise ProviderError(f'unknown provider: {provider}')
        r = P.set_key(provider, key, persist=persist)
        self.keys[provider] = key
        return {**r, 'ok': True}

    def raw(self, provider, path, method='GET', body=None, params=None):
        return {'provider': provider, 'path': path,
                'result': P.get(provider, self.keys).raw(path, method, body, params)}
