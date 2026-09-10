"""Lium — Bittensor subnet 51. Providers list nodes, renters take pods by the hour.

Browsing is public. Renting needs the caller's own Lium key and, upstream,
a template plus an SSH key — this adapter resolves both the way the lium
module does, so `compute_rent` stays a one-liner.
"""

from .base import Provider, ProviderError, gi, num, offer, instance

BASE = 'https://lium.io/api'


class Lium(Provider):
    name = 'lium'
    title = 'Lium (Bittensor SN51)'
    upstream = BASE
    docs = 'https://docs.lium.io'
    signup = 'https://lium.io'
    chain = 'bittensor'
    kyc = 'none'
    pay = ('crypto', 'credits')
    caps = ('search', 'rent', 'instances', 'status', 'logs', 'stop', 'balance')
    key_env = ('LIUM_API_KEY',)
    key_files = ('~/.mod/lium/api_key',)
    key_hint = 'lium.io → Settings → API Keys. Browsing needs no key.'

    def headers(self):
        return {'x-api-key': self.key()}

    def search(self, f):
        rows = self.get('/executors', params={'size': 500})
        rows = rows if isinstance(rows, list) else (rows.get('executors') or [])
        out = []
        for e in rows:
            specs = e.get('specs') or {}
            g = (specs.get('gpu') or {})
            det = (g.get('details') or [{}])[0]
            loc = e.get('location') or {}
            free = int(num(e.get('available_gpu_count'), 0) or 0)
            count = int(num(e.get('gpu_count'), 1) or 1)
            # price_per_gpu is per GPU per hour; a pod takes the whole node.
            per_gpu = num(e.get('price_per_gpu'))
            out.append(offer(
                self.name, e.get('id'),
                usd_hr=(per_gpu * count) if per_gpu is not None else None,
                gpu=e.get('machine_name') or det.get('name'),
                gpus=count,
                vram_gb=gi(num(det.get('capacity'))),
                cpu=num((specs.get('cpu') or {}).get('count')),
                ram_gb=round(num((specs.get('ram') or {}).get('total'), 0) / 1048576, 1) or None,
                region=', '.join(x for x in (loc.get('city'), loc.get('country')) if x) or None,
                # `active` is null on most rows rather than absent — only an
                # explicit false means the node is out.
                available=free > 0 and e.get('active') is not False,
                note=f"tier {e.get('tier')} · {free}/{count} free · "
                     f"uptime {e.get('uptime_in_minutes')}m",
                raw=e))
        return [o for o in out if f.match(o)]

    # ── lifecycle ──

    def rent(self, ref, name='mod-pod', hours=None, ssh_key=None, template_id=None,
             gpu_count=None, **opts):
        keys = [ssh_key] if ssh_key else [
            k.get('public_key') for k in (self.get('/ssh-keys', auth=True) or [])
            if isinstance(k, dict) and k.get('public_key')]
        if not keys:
            raise ProviderError(
                'lium: no SSH key — pass ssh_key=<public key> or register one at lium.io',
                provider=self.name)
        if not template_id:
            template_id = self._template(ref)
        body = {'pod_name': name, 'template_id': template_id, 'user_public_key': keys}
        if gpu_count:
            body['gpu_count'] = int(gpu_count)
        if hours:
            body['termination_hours'] = int(hours)
        r = self.post(f'/executors/{ref}/rent', body)
        pod = r.get('pod') if isinstance(r, dict) else None
        return instance(self.name, (pod or {}).get('id') or r.get('id') or ref,
                        name=name, status='starting', raw=r)

    def _template(self, executor_id):
        """Pick a verified template that matches the node's GPU, like lium-rs does."""
        node = self.get(f'/executors/{executor_id}')
        gpu = str((node or {}).get('machine_name') or '').lower()
        best = None
        for t in (self.get('/templates') or []):
            if not isinstance(t, dict):
                continue
            if t.get('status') not in (None, 'VERIFY_SUCCESS', 'verified'):
                continue
            model = str(t.get('gpu_model') or '').lower()
            if model and model.split()[-1] in gpu:
                return t.get('id')
            best = best or t.get('id')
        if not best:
            raise ProviderError('lium: no template available — pass template_id',
                                provider=self.name)
        return best

    def instances(self):
        rows = self.get('/pods', auth=True)
        rows = rows if isinstance(rows, list) else (rows.get('pods') or [])
        return [self._inst(p) for p in rows]

    def status(self, ref):
        return self._inst(self.get(f'/pods/{ref}', auth=True))

    def logs(self, ref, tail=200):
        return self.get(f'/pods/{ref}/logs', params={'tail': tail, 'follow': 'false'},
                        auth=True)

    def stop(self, ref):
        return {'stopped': ref, 'result': self.delete(f'/pods/{ref}')}

    def balance(self):
        me = self.get('/users/me', auth=True)
        return {'provider': self.name,
                'balance_usd': num(me.get('credit_balance', me.get('balance'))),
                'unit': 'USD', 'raw': me}

    def _inst(self, p):
        return instance(self.name, p.get('id'), name=p.get('pod_name') or p.get('name'),
                        status=p.get('status'), usd_hr=num(p.get('price_per_hour')),
                        gpu=(p.get('executor') or {}).get('machine_name'),
                        ssh=p.get('ssh_connect_cmd') or p.get('ssh_command'),
                        created=p.get('created_at'), raw=p)
