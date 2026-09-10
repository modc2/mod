"""Hyperbolic — idle GPUs from independent operators, rented by the node.

An open marketplace: anyone with cards can list them, anyone with credits can
take them, and the unit you rent is a named node inside a named cluster rather
than an anonymous slot. That pair is what `ref` carries here — `cluster/node` —
so a rented offer round-trips back through `rent` without a second lookup.

The catalog needs a key (there is no public window), so an unkeyed fan-out
reports this market as missing rather than empty.
"""

from .base import Provider, ProviderError, instance, num, offer

BASE = 'https://api.hyperbolic.xyz/v1'


class Hyperbolic(Provider):
    name = 'hyperbolic'
    title = 'Hyperbolic'
    upstream = BASE
    docs = 'https://docs.hyperbolic.xyz/docs/rent-a-gpu'
    signup = 'https://app.hyperbolic.xyz/settings'
    kyc = 'email'
    pay = ('crypto', 'card')
    caps = ('search', 'rent', 'instances', 'status', 'stop', 'balance')
    key_env = ('HYPERBOLIC_API_KEY', 'HYPERBOLIC_KEY')
    key_files = ('~/.mod/hyperbolic/api_key',)
    key_hint = 'app.hyperbolic.xyz → Settings → API key. The catalog needs one too.'

    def search(self, f):
        rows = self.post('/marketplace', body={'filters': {}}).get('instances') or []
        out = []
        for n in rows:
            hw = (n.get('hardware') or {})
            gpus = hw.get('gpus') or []
            g = gpus[0] if gpus else {}
            free = num(n.get('gpus_reserved')) or 0
            total = num(n.get('gpus_total')) or len(gpus) or 1
            # Priced in cents/hr per GPU upstream; quoted here for what you take.
            per_gpu = num((n.get('pricing') or {}).get('price', {}).get('amount'))
            want = max(int(f.min_gpus or 1), 1)
            out.append(offer(
                self.name, f"{n.get('cluster_name')}/{n.get('id')}",
                usd_hr=round(per_gpu / 100 * want, 4) if per_gpu else None,
                gpu=g.get('model'), gpus=want,
                vram_gb=num(g.get('ram')) / 1024 if num(g.get('ram')) else None,
                cpu=num((hw.get('cpus') or [{}])[0].get('virtual_cores')),
                ram_gb=num((hw.get('ram') or {}).get('capacity')) / 1024
                if num((hw.get('ram') or {}).get('capacity')) else None,
                disk_gb=num((hw.get('storage') or [{}])[0].get('capacity')),
                region=n.get('location') or (n.get('network') or {}).get('region'),
                available=(total - free) >= want and n.get('status') != 'unavailable',
                note=f"{int(total - free)}/{int(total)} gpus free · cluster "
                     f"{n.get('cluster_name')} · ${per_gpu / 100 if per_gpu else '?'}/gpu/hr",
                raw=n))
        return [o for o in out if f.match(o)]

    # ── lifecycle ──

    def rent(self, ref, name='mod', hours=None, gpus=1, image=None, **opts):
        cluster, _, node = str(ref).partition('/')
        if not node:
            raise ProviderError(
                'hyperbolic: id must be hyperbolic:<cluster>/<node> — '
                'ids come back from compute_search', provider=self.name)
        body = {'cluster_name': cluster, 'node_name': node,
                'gpu_count': int(num(gpus, 1) or 1)}
        if image:
            body['image'] = {'name': image, 'tag': 'latest',
                             'port': int(opts.get('port') or 22)}
        r = self.post('/marketplace/instances/create', body=body)
        return instance(self.name, r.get('instance_name') or r.get('id') or ref,
                        name=name, status='starting', raw=r)

    def instances(self):
        rows = self.get('/marketplace/instances', auth=True).get('instances') or []
        return [self._inst(i) for i in rows]

    def stop(self, ref):
        return {'stopped': ref,
                'result': self.post('/marketplace/instances/terminate',
                                    body={'id': str(ref)})}

    def balance(self):
        r = self.get('/billing/get_current_balance', auth=True)
        credits = num(r.get('credits'))
        return {'provider': self.name,
                'balance_usd': round(credits / 100, 2) if credits is not None else None,
                'unit': 'USD', 'raw': r}

    def _inst(self, i):
        inst = i.get('instance') or i
        ssh = i.get('sshCommand') or inst.get('ssh_command')
        return instance(self.name, i.get('id') or inst.get('id'),
                        name=inst.get('instance_name') or i.get('id'),
                        status=inst.get('status') or i.get('status'),
                        usd_hr=num((inst.get('pricing') or {}).get('price', {}).get('amount'),
                                   0) / 100 or None,
                        gpu=((inst.get('hardware') or {}).get('gpus') or [{}])[0].get('model'),
                        gpus=len((inst.get('hardware') or {}).get('gpus') or []) or None,
                        ssh=ssh, created=i.get('start') or inst.get('created'), raw=i)
