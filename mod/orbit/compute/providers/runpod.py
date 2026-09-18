"""RunPod — two lanes on one catalog: `community`, a marketplace of vetted
independent hosts, and `secure`, RunPod's own datacenters.

The whole GPU catalog is public through the GraphQL endpoint, priced per lane,
so search costs nothing and needs no account. Both lanes of every card are
quoted — the ref carries the lane (`community/NVIDIA GeForce RTX 4090`) so an
offer round-trips through `rent` with the right cloudType. Lifecycle goes
through the documented REST surface (Bearer key); the account can be funded
with card or crypto.
"""

from .base import Provider, http, instance, num, offer

GQL = 'https://api.runpod.io/graphql'
REST = 'https://rest.runpod.io/v1'

_CATALOG = ('{ gpuTypes { id displayName memoryInGb secureCloud communityCloud '
            'maxGpuCount communityPrice communitySpotPrice securePrice '
            'secureSpotPrice lowestPrice(input:{gpuCount:1}) { stockStatus } } }')


class Runpod(Provider):
    name = 'runpod'
    title = 'RunPod'
    upstream = REST
    docs = 'https://docs.runpod.io/api-reference'
    signup = 'https://console.runpod.io'
    kyc = 'email'
    pay = ('card', 'crypto')
    caps = ('search', 'rent', 'instances', 'status', 'stop', 'balance')
    key_env = ('RUNPOD_API_KEY', 'RUNPOD_KEY')
    key_files = ('~/.mod/runpod/api_key',)
    key_hint = 'console.runpod.io → Settings → API Keys. The catalog is public.'

    def search(self, f):
        r = http('POST', GQL, body={'query': _CATALOG}, provider=self.name)
        rows = (r.get('data') or {}).get('gpuTypes') or []
        want = max(int(f.min_gpus or 1), 1)
        out = []
        for g in rows:
            # stockStatus is the network's word for whether a 1-GPU pod of this
            # card exists right now; None with a price means listed, none free.
            stock = (g.get('lowestPrice') or {}).get('stockStatus')
            lanes = (('community', g.get('communityPrice'), g.get('communitySpotPrice')),
                     ('secure', g.get('securePrice'), g.get('secureSpotPrice')))
            for lane, price, spot in lanes:
                if not g.get(f'{lane}Cloud'):
                    continue
                p = num(price)
                out.append(offer(
                    self.name, f"{lane}/{g.get('id')}",
                    usd_hr=round(p * want, 4) if p else None,
                    gpu=g.get('displayName') or g.get('id'), gpus=want,
                    vram_gb=num(g.get('memoryInGb')),
                    available=bool(p) and stock is not None,
                    note=f"{lane} cloud · stock {stock or 'none'} · "
                         f"spot ${num(spot, 0) or 0:.2f}/hr · "
                         f"up to {g.get('maxGpuCount')}x · ${p or '?'}/gpu/hr",
                    raw=g))
        return [o for o in out if f.match(o)]

    # ── lifecycle (REST v1, Bearer) ──

    def rent(self, ref, name='mod', hours=None, gpus=1,
             image='runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04',
             disk_gb=40, **opts):
        lane, _, gpu_id = str(ref).partition('/')
        if not gpu_id:
            lane, gpu_id = 'community', lane
        body = {'name': name, 'imageName': image, 'gpuTypeIds': [gpu_id],
                'cloudType': lane.upper(), 'gpuCount': int(num(gpus, 1) or 1),
                'containerDiskInGb': int(num(disk_gb, 40) or 40),
                'ports': ['22/tcp'], 'supportPublicIp': True}
        if opts.get('env'):
            body['env'] = opts['env']
        r = self.post('/pods', body)
        return self._inst(r)

    def instances(self):
        r = self.get('/pods', auth=True)
        rows = r if isinstance(r, list) else (r.get('pods') or r.get('items') or [])
        return [self._inst(p) for p in rows]

    def status(self, ref):
        return self._inst(self.get(f'/pods/{ref}', auth=True))

    def stop(self, ref):
        return {'stopped': ref, 'result': self.delete(f'/pods/{ref}') or
                {'note': 'terminated — a stopped pod still bills its disk, '
                         'terminate is what ends the billing'}}

    def balance(self):
        r = http('POST', GQL, params={'api_key': self.key()},
                 body={'query': '{ myself { clientBalance currentSpendPerHr } }'},
                 provider=self.name)
        me = ((r.get('data') or {}).get('myself') or {})
        return {'provider': self.name, 'balance_usd': num(me.get('clientBalance')),
                'unit': 'USD', 'burn_usd_hr': num(me.get('currentSpendPerHr')),
                'raw': me}

    def _inst(self, p):
        machine = p.get('machine') or {}
        ip = p.get('publicIp') or machine.get('podHostId')
        return instance(self.name, p.get('id'), name=p.get('name'),
                        status=p.get('desiredStatus') or p.get('status'),
                        usd_hr=num(p.get('costPerHr')),
                        gpu=machine.get('gpuDisplayName') or
                        (p.get('gpu') or {}).get('displayName'),
                        gpus=num(p.get('gpuCount')),
                        ssh=f'ssh root@{ip}' if p.get('publicIp') else None,
                        created=p.get('createdAt'), raw=p)
