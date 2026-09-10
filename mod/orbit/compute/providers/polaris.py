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
    # The sibling `polaris` module owns this file; inherit it rather than
    # making the operator paste the same key into two keystores.
    key_files = ('~/.mod/polaris/api_key',)
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
        # `gpu_type` is the catalog's display name, not its slug id, and the key
        # field is ssh_public_key — ssh_key is silently ignored upstream, which
        # provisions a box nobody can log into.
        body = {'gpu_type': ref, 'name': name, 'use_spot': True}
        if image:
            body['image'] = image
        if ssh_key:
            body['ssh_public_key'] = ssh_key
        body.update(opts)
        r = self.post('/compute/instances', body)
        # The answer is {success, message, instances:[…]}, never a bare instance.
        made = (r.get('instances') or [{}])[0] if isinstance(r, dict) else {}
        return instance(self.name, made.get('id') or r.get('id') or r.get('instance_id'),
                        name=made.get('name') or name,
                        status=made.get('status') or 'provisioning', raw=r)

    def instances(self):
        r = self.get('/compute/instances', auth=True)
        rows = r if isinstance(r, list) else (r.get('instances') or [])
        return [self._inst(i) for i in rows]

    def stop(self, ref):
        return {'stopped': ref, 'result': self.delete(f'/compute/instances/{ref}')}

    def balance(self):
        # /credits does not exist upstream — the balance lives under /billing,
        # and arrives as balance_usd (sometimes as a "$25.00" string).
        c = self.get('/billing/credits', auth=True) or {}
        usd = c.get('balance_usd')
        if isinstance(usd, str):
            usd = usd.replace('$', '').replace(',', '').strip()
        micros = num(c.get('balance_micros'))
        return {'provider': self.name,
                'balance_usd': num(usd, micros / 1e6 if micros is not None else None),
                'unit': 'USD', 'status': c.get('account_status'), 'raw': c}

    def _inst(self, i):
        ip = i.get('ip') or i.get('public_ip') or i.get('ssh_host')
        port = i.get('ssh_port') or 22
        ssh = i.get('ssh_command')
        if not ssh and ip:
            ssh = f"ssh {i.get('ssh_user') or 'root'}@{ip}"
            if str(port) != '22':
                ssh += f' -p {port}'
        return instance(self.name, i.get('id'), name=i.get('name'),
                        status=i.get('status'),
                        usd_hr=num(i.get('hourly_cost'), num(i.get('price_per_hour'))),
                        gpu=i.get('gpu_type'), ssh=ssh,
                        url=i.get('access_url') if i.get('access_url') not in ('N/A', '') else None,
                        created=i.get('created_at'), raw=i)
