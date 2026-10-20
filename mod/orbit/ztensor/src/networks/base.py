"""The contract every network adapter implements.

mod.py never branches on a chain: it resolves `network=` to an adapter
(networks/__init__.py) and calls the methods below. Signatures always come
from the shared Cairo signer (core/signer.py); an adapter only knows how to
put the subnet contract on its chain and hand it a signature.

Return shapes (what mod.py, simulate() and the console rely on):
    deploy  -> {'ok', 'tempo', 'min_ring', 'emission', ...} (+ 'payee' if the
               adapter does not override payee())
    register-> {'tx', ...cost}
    submit  -> {'ok', 'epoch', 'tag', 'ring_size', 'tx', ...cost}
    score   -> {'ok', 'epoch', 'tag', 'score', 'nullifier', 'tx', ...cost}
    settle  -> {'ok', 'epoch', 'tag', 'consensus', 'rank', 'tx', ...cost}
               (Yuma for one tag, see core/yuma.py; a re-settle must raise
               RuntimeError)
    claim   -> {'ok', 'epoch', 'tag', 'recipient', 'earned', 'tx', ...cost}
               (raises RuntimeError until every tag of the epoch is settled)
    epoch   -> {'epoch', 'block', 'tempo', 'blocks_left'}
    tally   -> {'epoch', 'total'?, 'validators', 'rank_total', 'settled',
                'tags': [{tag, score_sum, scores, claimed, consensus, rank,
                          settled}]}
    test    -> {'ok', 'passed', 'failed', 'tests': [[PASS|FAIL, name]], 'log'}
`...cost` = whichever of `cost_fields` the chain measures (steps, tgas, gas,
cu, ...); a claim that is replayed must raise RuntimeError.
"""
import os
import random
import sys

from ..core.util import ROLES

# every adapter must override these (tests/test_networks.py checks it)
REQUIRED = ('health', 'build', 'test', 'up', 'block', 'advance', 'deploy',
            'register', 'set_open', 'members', 'epoch', 'mine_scope', 'submit',
            'score', 'settle', 'claim', 'tally', 'earned', 'board')


class Network:
    chain = ''            # id: simulate(chain=), tests cache, console switch
    title = ''            # 'Starknet'
    nets = ()             # network names served; nets[0] = the local chain
    order = 100           # position in lists and the console switch
    max_ring = 32         # biggest ring the verifier can afford
    sim_tempo = 60        # blocks per epoch for simulate()
    sweep_tempo = 5000    # blocks per epoch for gas_sweep()
    cost_fields = ()      # cost keys an action reports, in display order
    deploy_fields = ()    # deploy() keys worth showing in the event log
    default_costs = None  # cost table to show before any gas_sweep
    sweep_meta = {}       # extra keys stored with a gas_sweep table
    ui = {}               # console: name, local (node), verifier, deploying, color
    sources = ()          # CODE tab: (group, [(path from this dir, note)])
    token = ''            # coingecko id of the fee token ('' = no USD view)
    symbol = ''           # 'ETH'
    rounds = 1            # txs per action that must land one after another

    def __init__(self, signer):
        self.signer = signer

    @property
    def dir(self):
        return os.path.dirname(os.path.abspath(
            sys.modules[type(self).__module__].__file__))

    @property
    def local(self):
        return self.nets[0]

    def describe(self):
        return {'chain': self.chain, 'title': self.title, 'nets': list(self.nets),
                'net': self.local, 'max_ring': self.max_ring,
                'cost_fields': list(self.cost_fields), **self.ui}

    # ---- lifecycle ---------------------------------------------------------

    def health(self):
        raise NotImplementedError

    def build(self):
        raise NotImplementedError

    def test(self):
        """The chain's own native suite (snforge, cargo, forge, ...)."""
        raise NotImplementedError

    def up(self, action='status'):
        """start | stop | reset | status of the local chain -> {'up': bool}."""
        raise NotImplementedError

    def block(self, net):
        raise NotImplementedError

    def advance(self, net, to_block):
        """Move the epoch clock to `to_block` (warp locally, else wait)."""
        raise NotImplementedError

    # ---- the subnet contract -----------------------------------------------

    def deploy(self, net, tempo=None, min_ring=3, emission=1000, **kw):
        raise NotImplementedError

    def register(self, net, role, key):
        """role: 1 miner | 2 validator; key: {x, y} (hex)."""
        raise NotImplementedError

    def set_open(self, net, flag):
        raise NotImplementedError

    def members(self, net, role):
        raise NotImplementedError

    def epoch(self, net):
        raise NotImplementedError

    def mine_scope(self, net, epoch):
        """The scope a miner signs in for `epoch` (its key image = its tag)."""
        raise NotImplementedError

    def submit(self, net, name, work, ring):
        raise NotImplementedError

    def score(self, net, name, tag, score, ring):
        raise NotImplementedError

    def settle(self, net, epoch, tag):
        """Anyone, after `epoch` closed: run Yuma for `tag` on chain."""
        raise NotImplementedError

    def claim(self, net, name, epoch, recipient, ring):
        raise NotImplementedError

    def tally(self, net, epoch=None):
        raise NotImplementedError

    def earned(self, net, recipient):
        raise NotImplementedError

    def board(self, net, epochs=6):
        """Everything a chain reader sees: params, clock, sets, recent tags."""
        raise NotImplementedError

    # ---- shared --------------------------------------------------------------

    def ring(self, net, name, role, ring_size=None):
        """Your key plus a random sample of the other members (default:
        everyone, capped at max_ring). Bigger ring = bigger anonymity set."""
        me = self.signer.pubkey(name)
        everyone = self.members(net, ROLES[role])
        others = [p for p in everyone if int(p['x'], 16) != int(me['x'], 16)]
        if len(others) == len(everyone):
            raise ValueError(f'{name!r} is not a registered {role}')
        size = min(int(ring_size or self.max_ring), self.max_ring,
                   len(others) + 1)
        return random.sample(others, size - 1) + [me]

    def cost(self, r):
        return {k: r[k] for k in self.cost_fields if r.get(k) is not None}

    # ---- mainnet economics (core/fees.py) -----------------------------------

    def mainnet(self):
        """Live from a public mainnet RPC: {'block_seconds', 'gas': {...}
        whatever fee() needs, 'block_capacity': cost units one block holds
        (None = not the binding limit), 'capacity_unit', 'note'}."""
        return {}

    def fee(self, cost, m):
        """Native tokens one action costs on mainnet, from the cost it
        reported locally (a cost_fields dict) and mainnet() as `m`."""
        return None

    def load(self, cost):
        """How much of `block_capacity` one action uses (same unit)."""
        return None

    # ---- simulate() hooks (override when the chain differs) ----------------

    def payee(self, net, dep):
        return dep['payee']

    def contract_id(self, dep):
        return dep.get('address') or dep.get('account') or dep.get('subnet')

    def open_epoch(self, net, tempo, ev):
        """Start the actions at the top of a fresh epoch."""
        b = self.block(net)
        self.advance(net, (b // tempo + 1) * tempo)

    def close_epoch(self, net, epoch, tempo, ev):
        self.advance(net, (epoch + 1) * tempo)
