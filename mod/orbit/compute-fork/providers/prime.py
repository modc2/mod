"""Prime Intellect — a broker across decentralized and traditional GPU clouds.

Everything here, including the availability catalog, is behind the caller's own
API key; without one the adapter reports `missing` rather than guessing at
prices. Signup is email + key, no identity check.
"""

from .base import Provider, num, offer, instance

BASE = 'https://api.primeintellect.ai/api/v1'


class Prime(Provider):
    name = 'prime'
    title = 'Prime Intellect'
    upstream = BASE
    docs = 'https://docs.primeintellect.ai'
    signup = 'https://app.primeintellect.ai/dashboard/tokens'
    kyc = 'email'
    pay = ('crypto', 'card')
    caps = ('search', 'rent', 'instances', 'status', 'stop')
    key_env = ('PRIME_API_KEY', 'PRIME_INTELLECT_API_KEY')
    key_files = ('~/.prime/api_key',)
    key_hint = 'app.primeintellect.ai → API tokens. Even the catalog needs a key.'

    def search(self, f):
        params = {'gpu_type': f.gpu.upper().replace(' ', '_') if f.gpu else None,
                  'gpu_count': f.min_gpus}
        data = self.get('/availability/', params=params, auth=True)
        rows = []
        if isinstance(data, dict):                # {GPU_TYPE: [offers…]}
            for group in data.values():
                rows.extend(group if isinstance(group, list) else [])
        elif isinstance(data, list):
            rows = data
        out = []
        for o in rows:
            if not isinstance(o, dict):
                continue
            out.append(offer(
                self.name, o.get('cloudId') or o.get('id'),
                usd_hr=num(o.get('prices', {}).get('onDemand') if isinstance(
                    o.get('prices'), dict) else o.get('price')),
                gpu=o.get('gpuType'), gpus=int(num(o.get('gpuCount'), 1) or 1),
                vram_gb=num(o.get('gpuMemory')),
                cpu=num(o.get('vcpu')), ram_gb=num(o.get('memory')),
                disk_gb=num(o.get('disk', {}).get('maxCount') if isinstance(
                    o.get('disk'), dict) else None),
                region=o.get('country') or o.get('region'),
                available=str(o.get('stockStatus', 'Available')).lower() != 'unavailable',
                note=f"{o.get('provider')} · {o.get('socket') or ''} · "
                     f"{o.get('stockStatus') or ''}".strip(' ·'),
                raw=o))
        return [o for o in out if f.match(o)]

    def rent(self, ref, name='mod', hours=None, image=None, ssh_key=None, **opts):
        body = {'pod': {'name': name, 'cloudId': ref, 'gpuCount': opts.get('gpus', 1)},
                'provider': {'type': opts.get('provider_type', 'runpod')}}
        if image:
            body['pod']['image'] = image
        if ssh_key:
            body['pod']['sshKey'] = ssh_key
        r = self.post('/pods/', body)
        return instance(self.name, r.get('id') or r.get('podId'), name=name,
                        status=r.get('status') or 'creating', raw=r)

    def instances(self):
        r = self.get('/pods/', auth=True)
        rows = r if isinstance(r, list) else (r.get('data') or r.get('pods') or [])
        return [self._inst(p) for p in rows]

    def status(self, ref):
        return self._inst(self.get(f'/pods/{ref}', auth=True))

    def stop(self, ref):
        return {'stopped': ref, 'result': self.delete(f'/pods/{ref}')}

    def _inst(self, p):
        return instance(self.name, p.get('id'), name=p.get('name'),
                        status=p.get('status'), usd_hr=num(p.get('priceHr')),
                        gpu=p.get('gpuName') or p.get('gpuType'),
                        gpus=int(num(p.get('gpuCount'), 1) or 1),
                        ssh=p.get('sshConnection'), created=p.get('createdAt'), raw=p)
