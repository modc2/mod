"""Vast.ai — an open marketplace of other people's GPUs, mostly consumer cards.

Cheapest thing on this list by a wide margin, and searchable with no key at
all. Renting needs an account key; the account itself can be funded with
crypto, so there is no card in the loop unless you want one.
"""

import json

from .base import Provider, gi, num, offer, instance

BASE = 'https://console.vast.ai/api/v0'

# Vast's query language has eq/in but no contains, and its catalog spells cards
# out in full — "RTX 4090", "H100 SXM", "A100 SXM4". A loose "h100" therefore
# matches nothing server-side, and the unfiltered page is capped at 64 rows of
# the cheapest cards on the network, where no H100 has ever appeared. So the
# loose token is expanded into the names Vast actually uses.
_PREFIXES = ('', 'RTX ', 'GTX ', 'Tesla ', 'Quadro ', 'Q RTX ')
_SUFFIXES = ('', ' SXM', ' SXM4', ' SXM5', ' PCIE', ' NVL', ' Ti', ' D', ' S')


def gpu_names(token):
    """'h100' → every full card name Vast might file an H100 under."""
    t = str(token or '').strip().upper()
    seen, out = set(), []
    for pre in _PREFIXES:
        for suf in _SUFFIXES:
            name = f'{pre}{t}{suf}'
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


class Vast(Provider):
    name = 'vast'
    title = 'Vast.ai'
    upstream = BASE
    docs = 'https://docs.vast.ai/api-reference'
    signup = 'https://cloud.vast.ai/account/'
    kyc = 'none'
    pay = ('crypto', 'card')
    caps = ('search', 'rent', 'instances', 'status', 'logs', 'stop', 'balance')
    key_env = ('VAST_API_KEY',)
    key_files = ('~/.vast_api_key',)          # the vast CLI's own location
    key_hint = 'cloud.vast.ai → Account → API key. Search needs no key.'

    def search(self, f):
        # Vast filters server-side with a JSON query; anything it cannot express
        # (region text, our vram floor) is caught by Filters.match afterwards.
        q = {'rentable': {'eq': True}, 'num_gpus': {'gte': f.min_gpus or 1},
             'type': 'on-demand', 'order': [['dph_total', 'asc']],
             'limit': min(max(int(f.limit or 40), 64), 256)}
        if f.max_usd_hr is not None:
            q['dph_total'] = {'lte': f.max_usd_hr}
        if f.min_vram_gb:
            q['gpu_ram'] = {'gte': int(f.min_vram_gb * 1024)}
        if f.gpu:
            q['gpu_name'] = {'in': gpu_names(f.gpu)}
        rows = self.get('/bundles/', params={'q': json.dumps(q)}).get('offers') or []
        if f.gpu and not rows:                 # exact-name match missed — widen
            q.pop('gpu_name', None)
            rows = self.get('/bundles/', params={'q': json.dumps(q)}).get('offers') or []
        out = []
        for o in rows:
            out.append(offer(
                self.name, o.get('id'),
                usd_hr=num(o.get('dph_total')),
                gpu=o.get('gpu_name'), gpus=int(num(o.get('num_gpus'), 1) or 1),
                vram_gb=gi(num(o.get('gpu_ram'))),
                cpu=num(o.get('cpu_cores_effective')),
                ram_gb=gi(num(o.get('cpu_ram'))),
                disk_gb=num(o.get('disk_space')),
                region=o.get('geolocation'),
                available=bool(o.get('rentable')) and not o.get('rented'),
                note=f"{o.get('verification')} · reliability "
                     f"{round(num(o.get('reliability2'), 0) * 100)}% · "
                     f"{round(num(o.get('inet_down'), 0))}Mbps down · cuda "
                     f"{o.get('cuda_max_good')}",
                raw=o))
        return [o for o in out if f.match(o)]

    # ── lifecycle ──

    def rent(self, ref, name='mod', hours=None, image='pytorch/pytorch',
             disk_gb=32, ssh_key=None, cmd=None, **opts):
        body = {'client_id': 'me', 'image': image, 'disk': num(disk_gb, 32),
                'label': name, 'runtype': 'ssh'}
        if cmd:
            body['onstart'] = cmd
        body.update({k: v for k, v in opts.items() if k in ('env', 'args', 'price')})
        r = self._put(f'/asks/{ref}/', body)
        return instance(self.name, r.get('new_contract') or ref, name=name,
                        status='starting', raw=r)

    def instances(self):
        r = self.get('/instances/', params={'owner': 'me'}, auth=True)
        return [self._inst(i) for i in (r.get('instances') or [])]

    def status(self, ref):
        r = self.get(f'/instances/{ref}/', auth=True)
        return self._inst(r.get('instances') or r)

    def logs(self, ref, tail=200):
        return self._put(f'/instances/request_logs/{ref}/', {'tail': str(tail)})

    def stop(self, ref):
        return {'stopped': ref, 'result': self.delete(f'/instances/{ref}/')}

    def balance(self):
        me = self.get('/users/current/', auth=True)
        return {'provider': self.name, 'balance_usd': num(me.get('credit')),
                'unit': 'USD', 'raw': {k: me.get(k) for k in ('id', 'email', 'credit')}}

    def _put(self, path, body):
        from .base import http
        return http('PUT', BASE + path, headers=self.headers(), body=body,
                    provider=self.name)

    def _inst(self, i):
        ports = i.get('ssh_host'), i.get('ssh_port')
        return instance(self.name, i.get('id'), name=i.get('label'),
                        status=i.get('actual_status') or i.get('cur_state'),
                        usd_hr=num(i.get('dph_total')), gpu=i.get('gpu_name'),
                        gpus=int(num(i.get('num_gpus'), 1) or 1),
                        ssh=f'ssh -p {ports[1]} root@{ports[0]}' if all(ports) else None,
                        created=i.get('start_date'), raw=i)
