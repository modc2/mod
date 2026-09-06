"""Fluence — a decentralized CPU marketplace: enterprise racks from
independent datacenter operators, priced per configuration in USD, paid in
USDC or by card.

No GPUs here — Fluence sells dedicated CPU VMs — so it fills the `cpu` kind
the way Shadeform fills fiat GPUs: a real market with real prices for the
boxes that run scrapers, indexers and bots next to the GPU fleet. The catalog
sits behind an API key (`X-API-KEY`), so an unkeyed fan-out reports this
market as missing rather than empty.

Creating a VM on the current API is a three-step draft → configure →
provision flow, so `rent` returns that plan with the caller's numbers filled
in rather than half-doing it.
"""

from .base import Provider, instance, num, offer

BASE = 'https://api.fluence.dev'


class Fluence(Provider):
    name = 'fluence'
    title = 'Fluence'
    upstream = BASE
    docs = 'https://fluence.dev/docs/build/api/overview'
    signup = 'https://console.fluence.network'
    chain = 'fluence l2'
    kyc = 'email'
    pay = ('USDC', 'card')
    caps = ('search', 'instances', 'status', 'stop', 'balance')
    key_env = ('FLUENCE_API_KEY',)
    key_files = ('~/.mod/fluence/api_key',)
    key_hint = ('console.fluence.network → Settings → API keys. '
                'Even the catalog needs one.')

    def headers(self):
        # The scheme is an X-API-KEY header; the docs' own curl examples also
        # spell it Authorization: X-API-KEY <key>, so both are sent.
        k = self.key()
        return {'x-api-key': k, 'authorization': f'X-API-KEY {k}'}

    def search(self, f):
        configs = (self.get('/v1/configurations/virtual_machines', auth=True)
                   .get('items') or [])
        best, clusters_of = {}, {}
        for row in (self.get('/v1/prices/vm', auth=True).get('items') or []):
            cid = (row.get('vmTypeId') or {}).get('vmConfigurationId')
            if not cid:
                continue
            clusters_of[cid] = clusters_of.get(cid, 0) + 1
            p = num((row.get('priceInfo') or {}).get('pricePerHourPerQty'))
            if p is not None and (cid not in best or p < best[cid]):
                best[cid] = p
        out = []
        for c in configs:
            p, clusters = best.get(c.get('id')), clusters_of.get(c.get('id'), 0)
            slug = c.get('slug') or ''
            disk = None
            if 'storage-' in slug:
                disk = num(slug.split('storage-')[-1].replace('gb', ''))
            out.append(offer(
                self.name, slug or c.get('id'),
                usd_hr=p, kind='cpu',
                cpu=num(c.get('vcpu')), ram_gb=num(c.get('ramGb')), disk_gb=disk,
                available=clusters > 0,
                note=f"{'dedicated' if c.get('dedicated') else 'shared'} · "
                     f"{'/'.join(c.get('cpuFamilies') or [])} · "
                     f"{clusters} datacenter{'s' if clusters != 1 else ''} price it"
                     f" · {c.get('name') or ''}".strip(' ·'),
                raw=c))
        return [o for o in out if f.match(o)]

    def rent(self, ref, name='mod', hours=None, **opts):
        """The current API lands VMs through a draft → patch → provision flow;
        the plan spells it out instead of half-doing it blind."""
        return {
            'provider': self.name,
            'action': 'plan',
            'reason': 'a Fluence VM is a three-step draft/configure/provision '
                      'flow on the current API — run it, or use the console.',
            'configuration': ref,
            'steps': [
                "curl -X POST https://api.fluence.dev/v3/vms "
                "-H 'X-API-KEY: <key>'                        # a Draft VM, no billing yet",
                f"curl -X PATCH https://api.fluence.dev/v3/vms/<vm_id> "
                f"-H 'X-API-KEY: <key>' -H 'content-type: application/json' "
                f"-d '{{\"name\":\"{name}\",\"configurationSlug\":\"{ref}\"}}'",
                "curl -X POST https://api.fluence.dev/v3/vms/<vm_id>/provision "
                "-H 'X-API-KEY: <key>'                        # billing starts here",
            ],
            'console': 'https://console.fluence.network',
            'docs': self.docs,
        }

    def instances(self):
        r = self.get('/v2/vms', auth=True)
        return [self._inst(v) for v in (r.get('items') or [])]

    def stop(self, ref):
        return {'stopped': ref, 'result': self.delete(f'/v3/vms/{ref}') or
                {'note': 'deleted — billing ends with the VM'}}

    def balance(self):
        rows = self.get('/v2/users/balances', auth=True)
        rows = rows if isinstance(rows, list) else [rows]
        usd = None
        for b in rows:
            if isinstance(b, dict):
                for k in ('availableBalance', 'balance', 'amount', 'value'):
                    if usd is None and num(b.get(k)) is not None:
                        usd = num(b.get(k))
        return {'provider': self.name, 'balance_usd': usd, 'unit': 'USD',
                'raw': rows}

    def _inst(self, v):
        return instance(self.name, v.get('id'), name=v.get('name'),
                        status=v.get('status'),
                        usd_hr=num(v.get('pricePerHour')),
                        created=v.get('createdAt'), raw=v)
