"""Akash — a Cosmos chain where providers bid to run your container.

Permissionless and no-KYC in the strongest sense: you never make an account,
you hold AKT in a wallet and sign. That is also why `rent` is not a REST call
here — a deployment is a chain transaction, then a bid you accept, then a
manifest you push to the winning provider.

So this adapter does what it honestly can from an API key-less process:
prices the whole network live (public, no wallet), and turns a rent request
into a ready-to-run SDL + the exact command sequence, which `provider-services`
signs locally if the operator has a wallet. Nothing here can move AKT.
"""

import json

from .base import Provider, Unsupported, gi, num, offer

CONSOLE = 'https://console-api.akash.network'


def _price(price):
    """What an Akash lease actually costs per hour.

    The network reports a min/max/median band per card because providers bid.
    The floor is regularly 0 — a provider listing capacity at nothing, which no
    one has leased — and taking that as the price would put a card you cannot
    get at the top of every cheapest-first search across every market. So the
    floor is used when it is real, and the median stands in when it is not.
    The full band stays in the note either way.
    """
    floor = num(price.get('min'))
    if floor:
        return floor
    for key in ('med', 'weightedAverage', 'avg', 'max'):
        v = num(price.get(key))
        if v:
            return v
    return floor


class Akash(Provider):
    name = 'akash'
    title = 'Akash Network'
    upstream = CONSOLE
    docs = 'https://akash.network/docs/'
    signup = 'https://console.akash.network'
    chain = 'akash (cosmos)'
    kyc = 'none'
    pay = ('AKT', 'USDC')
    custody = 'wallet'
    caps = ('search',)
    key_hint = ('no account and no key — you need an AKT wallet. '
                '`compute_rent` returns a signed-by-you deployment plan.')

    def search(self, f):
        data = self.get('/v1/gpu-prices') or {}
        # (see _price: a $0 floor is a listing, not a lease)
        out = []
        for m in data.get('models') or []:
            avail = m.get('availability') or {}
            price = m.get('price') or {}
            free = int(num(avail.get('available'), 0) or 0)
            model = f"{m.get('vendor', '')} {m.get('model', '')}".strip()
            out.append(offer(
                self.name, f"{m.get('model')}-{m.get('ram', '')}".strip('-'),
                usd_hr=_price(price),
                gpu=model, gpus=1, vram_gb=gi(m.get('ram')),
                available=free > 0,
                note=f"{free}/{avail.get('total')} free across "
                     f"{(m.get('providerAvailability') or {}).get('available')} providers · "
                     f"{m.get('interface') or ''} · "
                     f"${num(price.get('min'), 0):.2f}–${num(price.get('max'), 0):.2f}/hr",
                raw=m))
        return [o for o in out if f.match(o)]

    def capacity(self):
        """Network-wide free CPU/GPU/memory/storage — public."""
        return self.get('/v1/network-capacity')

    def providers(self, limit=25):
        rows = self.get('/v1/providers')
        rows = rows if isinstance(rows, list) else []
        rows = [p for p in rows if (p.get('isOnline') or p.get('uptime1d'))]
        return rows[:limit]

    def rent(self, ref, name='mod', hours=None, image='ubuntu:22.04', cmd=None,
             gpus=1, cpu=2, ram_gb=8, disk_gb=50, **opts):
        """A deployment plan: the SDL to deploy and the four commands that do it.

        Executing it needs a funded AKT wallet, so it is left to the operator
        rather than done here — this module never holds a key that can spend."""
        model = ref.split('-')[0]
        sdl = _sdl(name, image, cmd, model, gpus, cpu, ram_gb, disk_gb)
        return {
            'provider': self.name,
            'action': 'plan',
            'reason': 'Akash deployments are chain transactions — this module '
                      'holds no wallet and will not sign for you.',
            'sdl': sdl,
            'steps': [
                f'save the sdl above as {name}.yml',
                f'provider-services tx deployment create {name}.yml --from <key> '
                f'--node https://rpc.akashnet.net:443 --chain-id akashnet-2 --fees 5000uakt',
                'provider-services query market bid list --owner <addr> --dseq <dseq> '
                '--state open   # pick a provider',
                'provider-services tx market lease create --dseq <dseq> --provider <prov> '
                '--from <key>',
                f'provider-services send-manifest {name}.yml --dseq <dseq> '
                '--provider <prov> --from <key>',
            ],
            'wallet': 'https://console.akash.network (browser, Keplr/Leap) or '
                      '`provider-services keys add`',
            'estimate_usd_hr': None,
            'hint': 'or paste the SDL into console.akash.network and click deploy',
        }

    def instances(self):
        raise Unsupported(
            'akash: leases live on-chain under your address — '
            '`provider-services query market lease list --owner <addr>`',
            provider=self.name)


def _sdl(name, image, cmd, gpu_model, gpus, cpu, ram_gb, disk_gb):
    """A minimal SDL v2.0 for one GPU service. Deliberately plain text: it is
    meant to be read, edited and deployed by hand or by the Akash console."""
    gpu_block = ''
    if gpus:
        gpu_block = (f'\n          gpu:\n            units: {gpus}\n'
                     f'            attributes:\n              vendor:\n'
                     f'                nvidia:\n                  - model: {gpu_model}')
    command = f'\n      command:\n        - "sh"\n        - "-c"\n      args:\n        - {json.dumps(cmd)}' if cmd else ''
    return f"""---
version: "2.0"
services:
  {name}:
    image: {image}{command}
    expose:
      - port: 8080
        as: 80
        to:
          - global: true
profiles:
  compute:
    {name}:
      resources:
        cpu:
          units: {cpu}
        memory:
          size: {ram_gb}Gi
        storage:
          size: {disk_gb}Gi{gpu_block}
  placement:
    dcloud:
      pricing:
        {name}:
          denom: uakt
          amount: 100000
deployment:
  {name}:
    dcloud:
      profile: {name}
      count: 1
"""
