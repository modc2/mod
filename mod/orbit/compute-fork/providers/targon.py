"""Targon — Bittensor subnet 4. Miners supply GPUs, the Hub rents them by the hour.

Paid in Targon credits topped up with TAO, so there is no card and no KYC.
The Hub API moved v2 → v3 in Aug 2026 (every v2 path now answers 410); this
adapter is on v3. `inventory` is public — pricing the network needs no key.
"""

from .base import Provider, gi, num, offer, instance

BASE = 'https://api.targon.com/tha/v3'
SSH_HOST = 'ssh.deployments.targon.com'


class Targon(Provider):
    name = 'targon'
    title = 'Targon (Bittensor SN4)'
    upstream = BASE
    docs = 'https://docs.targon.com'
    signup = 'https://targon.com'
    chain = 'bittensor'
    kyc = 'none'
    pay = ('TAO', 'credits')
    caps = ('search', 'rent', 'instances', 'status', 'logs', 'exec', 'stop', 'balance')
    key_env = ('TARGON_API_KEY',)
    key_files = ('~/.mod/targon/api_key',)
    key_hint = ('targon.com → API keys; credits are bought with TAO. '
                'Pricing (search) works with no key.')

    def search(self, f):
        out = []
        for t in self.get('/inventory') or []:
            spec = t.get('spec') or {}
            free = int(num(t.get('available'), 0) or 0)
            kind = t.get('type')
            # `rental` and `vm` are machines; `storage` is priced per GB-hour and
            # would otherwise sort to the top of every cheapest-first search.
            kind = ('gpu' if t.get('gpu') else 'cpu') if kind in ('rental', 'vm') else kind
            out.append(offer(
                self.name, t.get('name'),
                usd_hr=num(t.get('cost_per_hour')),
                gpu=spec.get('gpu_type'),
                gpus=int(num(spec.get('gpu_count'), 0) or 0) or None,
                # Targon reports vCPU in millicores and memory in MiB.
                cpu=round(num(spec.get('vcpu'), 0) / 1000, 1) or None,
                ram_gb=gi(num(spec.get('memory'))),
                available=free > 0,
                kind=kind,
                note=f"{t.get('display_name') or ''} · {free} free · {t.get('type')}",
                raw=t))
        return [o for o in out if f.match(o)]

    # ── lifecycle ──

    def rent(self, ref, name='mod', hours=None, image=None, **opts):
        """Register a workload on a tier, then deploy it — billing starts here."""
        body = {'name': name, 'resource_name': ref, 'type': 'RENTAL'}
        if image:
            body['docker_image'] = image
        body.update({k: v for k, v in opts.items()
                     if k in ('type', 'docker_image', 'env', 'ports', 'ssh_key_uid')})
        created = self.post('/workloads', body)
        uid = created.get('uid') or created.get('workload_uid') or created.get('id')
        deployed = self.post(f'/workloads/{uid}/deploy', {}) if uid else {}
        return instance(self.name, uid, name=name, status='deploying',
                        ssh=f'ssh {uid}@{SSH_HOST}',
                        raw={'created': created, 'deployed': deployed})

    def instances(self):
        r = self.get('/workloads', auth=True)
        rows = r if isinstance(r, list) else (r.get('workloads') or r.get('data') or [])
        return [self._inst(w) for w in rows]

    def status(self, ref):
        return self._inst(self.get(f'/workloads/{ref}', auth=True))

    def logs(self, ref, tail=200):
        return self.get(f'/workloads/{ref}/logs', params={'tail': tail}, auth=True)

    def exec(self, ref, cmd):
        return self.post(f'/workloads/{ref}/exec', {'command': cmd})

    def stop(self, ref):
        """Delete, not suspend — deletion is what actually stops the meter."""
        return {'stopped': ref, 'result': self.delete(f'/workloads/{ref}')}

    def balance(self):
        c = self.get('/me/credits', auth=True)
        return {'provider': self.name,
                'balance_usd': num(c.get('credits', c.get('balance'))),
                'unit': 'credits', 'raw': c}

    def _inst(self, w):
        uid = w.get('uid') or w.get('workload_uid') or w.get('id')
        return instance(self.name, uid, name=w.get('name'),
                        status=w.get('state') or w.get('status'),
                        usd_hr=num(w.get('cost_per_hour')),
                        gpu=(w.get('spec') or {}).get('gpu_type'),
                        ssh=f'ssh {uid}@{SSH_HOST}',
                        created=w.get('created_at'), raw=w)
