"""Nosana — a Solana GPU grid where jobs are posted on-chain and paid in NOS.

Markets and jobs are fully public, so pricing and watching work with no key.
Posting a job means signing a Solana transaction with a NOS-funded wallet,
which this module deliberately cannot do — `rent` explains the one command
that can (`npx @nosana/cli job post`) instead of pretending.
"""

from .base import Provider, Unsupported, num, offer

BASE = 'https://dashboard.k8s.prd.nos.ci/api'


class Nosana(Provider):
    name = 'nosana'
    title = 'Nosana (Solana)'
    upstream = BASE
    docs = 'https://docs.nosana.io'
    signup = 'https://dashboard.nosana.com'
    chain = 'solana'
    kyc = 'none'
    pay = ('NOS',)
    custody = 'wallet'
    caps = ('search',)
    key_hint = ('no account — a Solana wallet holding NOS pays. '
                'Markets and job history are public.')

    def search(self, f):
        rows = self.get('/markets')
        rows = rows if isinstance(rows, list) else (rows.get('markets') or [])
        out = []
        for m in rows:
            usd_hr = num(m.get('usd_reward_per_hour'))
            fee = num(m.get('network_fee_percentage'), 0) or 0
            out.append(offer(
                self.name, m.get('slug') or m.get('address'),
                usd_hr=usd_hr * (1 + fee / 100) if usd_hr else None,
                gpu=m.get('name'), gpus=1, kind='job',
                # This endpoint lists markets, not their queues — a market is
                # postable; how many nodes are free is not published here.
                available=bool(usd_hr),
                note=f"{m.get('type')} market · {fee:.0f}% network fee · "
                     f"queue depth not published · address {m.get('address')}",
                raw=m))
        return [o for o in out if f.match(o)]

    def jobs(self, limit=20):
        """Recent jobs on the grid — public."""
        r = self.get('/jobs', params={'limit': limit})
        return r.get('jobs') if isinstance(r, dict) else r

    def stats(self):
        return self.get('/stats')

    def rent(self, ref, image='ubuntu', cmd=None, **opts):
        """A job plan, not a rental: posting means signing on Solana, and this
        module holds no wallet. Same shape Akash answers with."""
        return {
            'provider': self.name,
            'action': 'plan',
            'reason': 'a Nosana job is a Solana transaction paid in NOS — '
                      'this module holds no wallet and will not sign for you.',
            'market': ref,
            'steps': [
                'npm i -g @nosana/cli',
                'nosana address                     # fund it with NOS + a little SOL',
                f'nosana job post --market {ref} --image {image} '
                f'{"--cmd " + repr(cmd) if cmd else ""}'.strip(),
                'nosana job get <job-address>       # result + logs',
            ],
            'wallet': '~/.nosana/nosana_key.json (created by the CLI)',
            'docs': 'https://docs.nosana.io/inference/quick_start.html',
        }
