"""Aleph Cloud (twentysix) — permissionless VMs on a network of independent
compute resource nodes, paid in ALEPH as a per-second stream. No account, no
key: a funded wallet is the identity, which puts this market in the same
family as Akash and Nosana.

Everything needed to price it is public. The network publishes its own price
book as an on-chain aggregate — fixed tiers, each a number of compute units at
a PAYG rate in ALEPH per compute-unit-hour — and the CRN list says which GPU
models are actually free on a node right now. The only moving part is the
ALEPH/USD rate, fetched live and cached; when that fetch fails the offers come
back unpriced with the ALEPH/hr figure in the note rather than with a made-up
dollar number.

Renting means signing from an ALEPH-funded wallet, so `rent` returns the
`aleph-client` plan instead of pretending this module can spend for you.
"""

import time

from .base import Provider, http, num, offer

API = 'https://api2.aleph.im'
PRICING = ('/api/v0/aggregates/0xFba561a84A537fCaa567bb7A2257e7142701ae2A.json'
           '?keys=pricing')
CRNS = 'https://crns-list.aleph.sh/crns.json'
COINGECKO = 'https://api.coingecko.com/api/v3/simple/price?ids=aleph&vs_currencies=usd'

_price_cache = {'usd': None, 'at': 0.0}
_crn_cache = {'models': set(), 'at': 0.0}


def aleph_usd():
    """ALEPH/USD, cached 15 min; the last known rate outlives a dead fetch."""
    now = time.time()
    if _price_cache['usd'] is None or now - _price_cache['at'] > 900:
        try:
            r = http('GET', COINGECKO, provider='aleph')
            _price_cache.update(usd=num((r.get('aleph') or {}).get('usd')), at=now)
        except Exception:
            _price_cache['at'] = now      # keep the stale rate, retry later
    return _price_cache['usd']


class Aleph(Provider):
    name = 'aleph'
    title = 'Aleph Cloud'
    upstream = API
    docs = 'https://docs.aleph.cloud'
    signup = 'https://console.twentysix.cloud'
    chain = 'aleph (multi-chain)'
    kyc = 'none'
    pay = ('ALEPH',)
    custody = 'wallet'
    caps = ('search',)
    key_hint = ('no account and no key — an ALEPH-funded wallet pays a '
                'per-second stream. `compute_rent` returns the aleph-client plan.')

    def search(self, f):
        pricing = ((self.get(PRICING).get('data') or {}).get('pricing') or {})
        usd = aleph_usd()
        free_gpus = self._free_gpu_models()
        out = []

        def tier_offer(kind, ref, tier, book, gpu=None, vram_mib=None):
            cu = num(tier.get('compute_units'), 0) or 0
            spec = book.get('compute_unit') or {}
            payg = num((book.get('price') or {}).get('compute_unit', {}).get('payg'))
            aleph_hr = round(payg * cu, 4) if payg else None
            return offer(
                self.name, ref,
                usd_hr=round(aleph_hr * usd, 4) if aleph_hr and usd else None,
                kind=kind, gpu=gpu, gpus=1 if gpu else None,
                vram_gb=round(vram_mib / 1024) if vram_mib else None,
                cpu=num(spec.get('vcpus'), 1) * cu,
                ram_gb=round(num(spec.get('memory_mib'), 0) * cu / 1024, 1),
                disk_gb=round(num(spec.get('disk_mib'), 0) * cu / 1024, 1),
                available=(gpu is None) or (not free_gpus) or
                          any(gpu.lower() in m or m in gpu.lower() for m in free_gpus),
                note=f"{aleph_hr or '?'} ALEPH/hr streamed per second · "
                     f"{tier.get('id')} · {cu} compute units"
                     + ('' if usd else ' · ALEPH/USD rate unavailable, unpriced')
                     + (' · no CRN advertising this card free right now'
                        if gpu and free_gpus and not any(
                            gpu.lower() in m or m in gpu.lower() for m in free_gpus)
                        else ''),
                raw=tier)

        for section, kind in (('instance', 'cpu'),
                              ('instance_confidential', 'confidential')):
            book = pricing.get(section) or {}
            for tier in book.get('tiers') or []:
                cu = int(num(tier.get('compute_units'), 0) or 0)
                out.append(tier_offer(kind, f'{section}-{cu}cu', tier, book))
        for section in ('instance_gpu_standard', 'instance_gpu_premium'):
            book = pricing.get(section) or {}
            for tier in book.get('tiers') or []:
                model = tier.get('model') or '?'
                ref = f"gpu-{model.lower().replace(' ', '-')}"
                out.append(tier_offer('gpu', ref, tier, book,
                                      gpu=model, vram_mib=num(tier.get('vram'))))
        return [o for o in out if f.match(o)]

    def _free_gpu_models(self):
        """The GPU models some CRN advertises as free, lowercased. Empty when
        the CRN list is unreachable — treated as 'unknown', not 'none'.
        The list is ~700 KB, so it is cached for five minutes."""
        if time.time() - _crn_cache['at'] < 300:
            return _crn_cache['models']
        try:
            crns = http('GET', CRNS, provider=self.name, timeout=15).get('crns') or []
        except Exception:
            return _crn_cache['models']
        models = set()
        for c in crns:
            for g in (c.get('compatible_available_gpus') or []):
                name = g.get('device_name') if isinstance(g, dict) else g
                if name:
                    models.add(str(name).lower())
        _crn_cache.update(models=models, at=time.time())
        return models

    def rent(self, ref, name='mod', hours=None, image=None, **opts):
        """A plan, not a rental: an Aleph instance is a signed message paid by
        a Superfluid ALEPH stream, and this module holds no wallet."""
        return {
            'provider': self.name,
            'action': 'plan',
            'reason': 'an Aleph instance is signed by your own ALEPH-funded '
                      'wallet — this module holds no wallet and will not sign.',
            'tier': ref,
            'steps': [
                'pipx install aleph-client',
                'aleph account create            # or import the wallet that holds ALEPH',
                'aleph instance create --payment-type superfluid'
                + (' --gpu' if str(ref).startswith('gpu-') else '')
                + '   # pick the tier interactively',
                'aleph instance list             # ssh command appears when it lands',
            ],
            'console': 'https://console.twentysix.cloud',
            'docs': 'https://docs.aleph.cloud/devhub/computing/instance/',
        }
