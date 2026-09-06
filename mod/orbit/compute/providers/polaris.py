"""Polaris — a GPU cloud with a plain REST surface; the `polaris` module here
already speaks it. Catalog is public, renting is BYOK.
"""

from .base import Provider, gi, num, offer, instance

BASE = 'https://api.polaris.computer/api'


class Polaris(Provider):
    name = 'polaris'
    title = 'Polaris Cloud'
    upstream = BASE
    docs = 'https://polaris.computer'
    signup = 'https://polaris.computer'
    kyc = 'email'
    pay = ('crypto', 'card')
    caps = ('search', 'rent', 'instances', 'status', 'stop', 'balance')
    key_env = ('POLARIS_KEY', 'POLARIS_API_KEY')
    key_hint = 'polaris.computer → API key. The GPU catalog is public.'

    def search(self, f):
        rows = (self.get('/compute/gpus') or {}).get('gpus') or []
        out = []
        for g in rows:
            # available_count is null when the catalog knows a type is up but
            # not how many are free — that is available, not sold out.
            free = num(g.get('available_count'))
            out.append(offer(
                self.name, g.get('name'),
                usd_hr=num(g.get('on_demand_price')),
                gpu=g.get('display_name') or g.get('name'), gpus=1,
                vram_gb=gi(g.get('memory')),
                available=bool(g.get('available')) and (free is None or free > 0),
                note=f"{g.get('architecture')} · {free if free is not None else '?'} free · "
                     f"spot ${num(g.get('spot_price'), 0):.2f}/hr",
                raw=g))
        return [o for o in out if f.match(o)]

    def rent(self, ref, name='mod', hours=None, image=None, ssh_key=None, **opts):
        body = {'gpu_type': ref, 'name': name}
        if image:
            body['image'] = image
        if ssh_key:
            body['ssh_key'] = ssh_key
        body.update(opts)
        r = self.post('/compute/instances', body)
        return instance(self.name, r.get('id') or r.get('instance_id'), name=name,
                        status=r.get('status') or 'creating', raw=r)

    def instances(self):
        r = self.get('/compute/instances', auth=True)
        rows = r if isinstance(r, list) else (r.get('instances') or [])
        return [self._inst(i) for i in rows]

    def stop(self, ref):
        return {'stopped': ref, 'result': self.delete(f'/compute/instances/{ref}')}

    def balance(self):
        c = self.get('/credits', auth=True)
        return {'provider': self.name, 'balance_usd': num(c.get('credits', c.get('balance'))),
                'unit': 'USD', 'raw': c}

    def _inst(self, i):
        return instance(self.name, i.get('id'), name=i.get('name'),
                        status=i.get('status'), usd_hr=num(i.get('price_per_hour')),
                        gpu=i.get('gpu_type'), ssh=i.get('ssh_command'),
                        created=i.get('created_at'), raw=i)
