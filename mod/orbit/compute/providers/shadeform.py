"""Shadeform — one API over ~20 GPU clouds.

Included as the fiat benchmark: it is the honest comparison point for what the
crypto-native markets charge, and its catalog is public. It is *not* no-KYC —
`kyc: 'account'`, `pay: ('card',)` — so a `compute_search kyc=none` run leaves
it out on purpose.
"""

from .base import Provider, num, offer, instance

BASE = 'https://api.shadeform.ai/v1'


class Shadeform(Provider):
    name = 'shadeform'
    title = 'Shadeform (multi-cloud)'
    upstream = BASE
    docs = 'https://docs.shadeform.ai'
    signup = 'https://platform.shadeform.ai'
    kyc = 'account'
    pay = ('card',)
    caps = ('search', 'rent', 'instances', 'status', 'stop')
    key_env = ('SHADEFORM_API_KEY',)
    key_hint = 'platform.shadeform.ai → API keys. The catalog is public.'

    def headers(self):
        return {'X-API-KEY': self.key()}

    def search(self, f):
        rows = (self.get('/instances/types') or {}).get('instance_types') or []
        out = []
        for t in rows:
            regions = [r for r in (t.get('availability') or []) if r.get('available')]
            cfg = t.get('configuration') or {}
            out.append(offer(
                self.name, f"{t.get('cloud')}/{t.get('shade_instance_type')}",
                # hourly_price is in cents.
                usd_hr=num(t.get('hourly_price'), 0) / 100,
                gpu=t.get('gpu_type'), gpus=int(num(t.get('num_gpus'), 1) or 1),
                vram_gb=num(cfg.get('vram_per_gpu_in_gb')),
                cpu=num(t.get('vcpus')), ram_gb=num(t.get('memory_in_gb')),
                disk_gb=num(t.get('storage_in_gb')),
                region=(regions[0].get('display_name') if regions else None),
                available=bool(regions),
                note=f"{t.get('cloud')} · {t.get('interconnect')} · "
                     f"{len(regions)} regions available",
                raw=t))
        return [o for o in out if f.match(o)]

    def rent(self, ref, name='mod', region=None, ssh_key=None, **opts):
        cloud, itype = (ref.split('/', 1) + [''])[:2]
        body = {'cloud': cloud, 'shade_instance_type': itype, 'name': name,
                'region': region or 'us-east', 'shade_cloud': True}
        if ssh_key:
            body['launch_configuration'] = {'type': 'ssh', 'ssh_key': ssh_key}
        r = self.post('/instances/create', body)
        return instance(self.name, r.get('id'), name=name, status='creating', raw=r)

    def instances(self):
        r = self.get('/instances', auth=True)
        return [self._inst(i) for i in (r.get('instances') or [])]

    def status(self, ref):
        return self._inst(self.get(f'/instances/{ref}/info', auth=True))

    def stop(self, ref):
        return {'stopped': ref, 'result': self.post(f'/instances/{ref}/delete', {})}

    def _inst(self, i):
        return instance(self.name, i.get('id'), name=i.get('name'),
                        status=i.get('status'), usd_hr=num(i.get('cost_estimate')),
                        gpu=i.get('shade_instance_type'), ssh=i.get('ssh_user') and
                        f"ssh {i['ssh_user']}@{i.get('ip')}",
                        created=i.get('created_at'), raw=i)
