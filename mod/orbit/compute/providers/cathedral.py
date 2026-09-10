"""Cathedral — confidential compute: attested Intel TDX CPU workers and
confidential GPU executions, each returning a signed receipt.

The odd one out on price shape: sealed workers bill by the hour like everyone
else, but `attest.v1` and the confidential GPU bill per completed execution.
Those come back as `kind='job'` with `usd_hr=None` and the per-run price in
`note`, so nobody's cost model quietly treats $3.00-per-execution as $3.00/hr.
"""

from .base import Provider, num, offer, instance

BASE = 'https://cathedral.computer/v1'


class Cathedral(Provider):
    name = 'cathedral'
    title = 'Cathedral (confidential TDX / CC-GPU)'
    upstream = BASE
    docs = 'https://cathedral.computer/docs/'
    signup = 'https://cathedral.computer/account/'
    kyc = 'email'
    pay = ('credits',)
    caps = ('search', 'rent', 'instances', 'status', 'stop', 'balance')
    key_env = ('CATHEDRAL_API_KEY',)
    key_files = ('~/.mod/cathedral/keys.json',)
    key_hint = ('cathedral.computer/account → cat_sk_ key, then prepay credits. '
                'The catalog is public.')

    def search(self, f):
        prof = self.get('/profiles') or {}
        out = []
        for p in prof.get('profiles') or []:
            live = p.get('availability') in ('available', 'live_testing')
            res = p.get('resources')
            if isinstance(res, list):                       # custom.v1 — hourly workers
                for r in res:
                    out.append(offer(
                        self.name, f"{p['id']}:{r.get('size', '').replace(' ', '-').lower()}",
                        usd_hr=num(r.get('price_usd_per_hour')),
                        kind='confidential',
                        cpu=num(r.get('cpu')), ram_gb=num(r.get('memory_gib')),
                        available=live,
                        note=f"{r.get('size')} · sealed worker · {r.get('hardware_class')}",
                        raw=r))
            elif isinstance(res, dict):                     # attest.v1 — per execution
                pricing = p.get('pricing') or {}
                out.append(offer(
                    self.name, p['id'], usd_hr=None, kind='job',
                    cpu=num(res.get('cpu')), ram_gb=num(res.get('memory_gib')),
                    available=live,
                    note=f"{p.get('name')} · ${num(pricing.get('amount_usd'), 0):.2f} "
                         f"per {pricing.get('unit', 'execution')} (not hourly)",
                    raw=p))
            for hw in p.get('hardware_classes') or []:      # the GPU hides in here
                if hw.get('execution_class') == 'cc_gpu':
                    out.append(offer(
                        self.name, 'cc_gpu', usd_hr=None, kind='job',
                        gpu=hw.get('gpu_type'), gpus=1,
                        available=hw.get('availability') in ('available', 'live_testing')
                        and bool(hw.get('customer_enabled')),
                        note='confidential GPU · $3.00 per execution (not hourly) · '
                             'refused unless the attestation gates PASS',
                        raw=hw))
                    break
        seen, uniq = set(), []
        for o in out:                                       # cc_gpu repeats per profile
            if o['id'] in seen:
                continue
            seen.add(o['id'])
            uniq.append(o)
        return [o for o in uniq if f.match(o)]

    # ── lifecycle ──

    def rent(self, ref, name='mod', hours=None, image=None, cmd=None, **opts):
        """Sealed worker (hourly) for a custom.v1 size; one execution otherwise."""
        if ref.startswith('custom.v1'):
            size = ref.split(':', 1)[1] if ':' in ref else None
            body = {'profile': 'custom.v1', 'name': name}
            if size:
                body['size'] = size.replace('-', ' ').title()
            if hours:
                body['hours'] = num(hours)
            body.update(opts)
            r = self.post('/rent', body)
        elif ref == 'cc_gpu':
            r = self.post('/gpu', {'command': cmd or 'nvidia-smi', **opts})
        else:
            r = self.post('/run', {'command': cmd or 'echo hello', **opts})
        wid = r.get('worker_id') or r.get('id') or r.get('execution_id')
        return instance(self.name, wid, name=name, status=r.get('status') or 'running',
                        raw=r)

    def instances(self):
        r = self.get('/workers', auth=True)
        rows = r if isinstance(r, list) else (r.get('workers') or [])
        return [self._inst(w) for w in rows]

    def status(self, ref):
        return self._inst(self.get(f'/workers/{ref}', auth=True))

    def stop(self, ref):
        return {'stopped': ref, 'result': self.delete(f'/workers/{ref}')}

    def balance(self):
        c = self.get('/credits', auth=True)
        return {'provider': self.name,
                'balance_usd': num(c.get('balance_usd', c.get('credits'))),
                'unit': 'USD', 'raw': c}

    def receipt(self, ref):
        """The signed receipt — Cathedral's whole point. Not part of the shared verbs."""
        return self.get(f'/receipts/{ref}', auth=True)

    def _inst(self, w):
        return instance(self.name, w.get('id') or w.get('worker_id'),
                        name=w.get('name'), status=w.get('status'),
                        usd_hr=num(w.get('price_usd_per_hour')),
                        created=w.get('created_at'), raw=w)
