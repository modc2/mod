"""Clore.ai — a blockchain GPU marketplace where the whole catalog is public.

Hosts list their rigs on-chain-adjacent and rent them for CLORE, BTC or USDT.
No account is needed to see the market, which makes it the widest keyless
window on real consumer-GPU pricing this module has.

Two prices exist for every rig: `on-demand` (yours until you cancel) and
`spot` (outbid and you lose it). Only on-demand is quoted here — a spot price
that can be taken away is not a price you can plan a rental around — and the
per-coin rates are all normalized to USD so a Clore rig sorts against an Akash
lease in the same column.
"""

from .base import Provider, ProviderError, gi, instance, num, offer

BASE = 'https://api.clore.ai/v1'


def _usd_hr(row):
    """The cheapest coin you could actually pay, in USD/hr. None if unpriced."""
    usd = (row.get('price') or {}).get('usd') or {}
    cands = [num(usd.get(k)) for k in ('on_demand_usd', 'on_demand_clore', 'on_demand_btc')]
    cands = [c for c in cands if c is not None and c > 0]
    return min(cands) if cands else None


def _gpu_name(row):
    """'3x NVIDIA GeForce RTX 3070 Ti' → ('RTX 3070 Ti', 3)."""
    raw = str((row.get('specs') or {}).get('gpu') or '').strip()
    count = len(row.get('gpu_array') or []) or 1
    name = raw
    if 'x ' in raw[:4]:
        head, _, rest = raw.partition('x ')
        if head.strip().isdigit():
            count, name = int(head.strip()), rest
    for junk in ('NVIDIA GeForce ', 'NVIDIA ', 'AMD '):
        name = name.replace(junk, '')
    return name.strip() or None, count


class Clore(Provider):
    name = 'clore'
    title = 'Clore.ai'
    upstream = BASE
    docs = 'https://docs.clore.ai/'
    signup = 'https://clore.ai/'
    chain = 'clore'
    kyc = 'none'
    pay = ('CLORE', 'BTC', 'USDT')
    caps = ('search', 'rent', 'instances', 'status', 'stop', 'balance')
    key_env = ('CLORE_API_KEY', 'CLORE_KEY')
    key_files = ('~/.mod/clore/api_key',)
    key_hint = 'clore.ai → Settings → API. The marketplace itself needs no key.'

    def headers(self):
        # Not Bearer: Clore reads a bare token out of its own `auth` header.
        return {'auth': self.key()}

    # ── search ──

    def search(self, f):
        rows = self.get('/marketplace').get('servers') or []
        out = []
        for row in rows:
            specs = row.get('specs') or {}
            gpu, gpus = _gpu_name(row)
            net = specs.get('net') or {}
            rating = row.get('rating') or {}
            out.append(offer(
                self.name, row.get('id'),
                usd_hr=_usd_hr(row),
                gpu=gpu, gpus=gpus, vram_gb=num(specs.get('gpuram')),
                cpu=num(str(specs.get('cpus') or '').split('/')[0] or None),
                ram_gb=num(specs.get('ram')),
                disk_gb=gi(str(specs.get('disk') or '').split()[-1] if specs.get('disk') else None),
                region=net.get('cc'),
                available=not row.get('rented'),
                note=f"rel {round(num(row.get('reliability'), 0) * 100)}% · "
                     f"{rating.get('avg') or '-'}* x{rating.get('cnt') or 0} · "
                     f"{round(num(net.get('down'), 0))}Mbps · cuda {row.get('cuda_version')} · "
                     f"max {row.get('mrl') or '?'}h · pays {'/'.join(row.get('allowed_coins') or [])}",
                raw=row))
        return [o for o in out if f.match(o)]

    # ── lifecycle ──

    def rent(self, ref, name='mod', hours=None, image='cloreai/ubuntu-xfce',
             ssh_key=None, cmd=None, currency=None, **opts):
        """Place an on-demand order. Clore bills from its own wallet balance."""
        row = self._server(ref)
        coins = row.get('allowed_coins') or ['CLORE-Blockchain']
        body = {'currency': currency or coins[0], 'image': image,
                'renting_server': int(ref), 'type': 'on-demand',
                'ports': {'22': 'tcp'}, 'env': opts.get('env') or {}}
        if ssh_key:
            body['ssh_key'] = ssh_key
        if cmd:
            body['command'] = cmd
        r = self.post('/create_order', body=body)
        if r.get('code'):
            raise ProviderError(f'clore: create_order → {r}', provider=self.name)
        return instance(self.name, r.get('id') or ref, name=name, status='starting',
                        usd_hr=_usd_hr(row), raw=r)

    def instances(self):
        rows = self.get('/my_orders', auth=True).get('orders') or []
        return [self._inst(o) for o in rows]

    def stop(self, ref):
        r = self.post('/cancel_order', body={'id': int(ref)})
        if r.get('code'):
            raise ProviderError(f'clore: cancel_order → {r}', provider=self.name)
        return {'stopped': ref, 'result': r}

    def balance(self):
        wallets = self.get('/wallets', auth=True).get('wallets') or []
        usd = None
        for w in wallets:
            if str(w.get('name') or '').lower().startswith('usd'):
                usd = num(w.get('balance'))
        return {'provider': self.name, 'balance_usd': usd, 'unit': 'USD',
                'wallets': {w.get('name'): num(w.get('balance')) for w in wallets},
                'note': 'CLORE and BTC balances also buy time — see wallets'}

    # ── helpers ──

    def _server(self, ref):
        for row in self.get('/marketplace').get('servers') or []:
            if str(row.get('id')) == str(ref):
                return row
        raise ProviderError(f'clore: no server {ref} — it may have been rented',
                            provider=self.name)

    def _inst(self, o):
        # An order row carries the rig's specs only sometimes; when it does not,
        # the name is unknown rather than wrong.
        gpu, gpus = _gpu_name(o) if o.get('specs') else (None, None)
        ssh = None
        if o.get('pub_cluster') and (o.get('tcp_ports') or []):
            ssh = f"ssh -p {str(o['tcp_ports'][0]).split(':')[-1]} root@{o['pub_cluster'][0]}"
        return instance(self.name, o.get('id'), name=o.get('image'),
                        status='running' if not o.get('expired') else 'expired',
                        usd_hr=num((o.get('price') or 0)) or None,
                        gpu=gpu, gpus=gpus, ssh=ssh, created=o.get('ct'), raw=o)
