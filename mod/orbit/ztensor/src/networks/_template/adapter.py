"""ztensor on <Chain>: contract/ (<the verifier project>). Copy of
networks/_template — see networks/README.md for the steps.

Networks:
    <chain>          local node on :<port> (data/<chain>/)
    <chain>-testnet  <how a funded signer is configured>
"""
from ..base import Network


class Chain(Network):
    chain = 'chain'
    title = 'Chain'
    nets = ('chain', 'chain-testnet')   # first = the local chain
    order = 90
    max_ring = 16                       # what one verification can afford
    sim_tempo = 60
    cost_fields = ('gas',)              # what an action's result reports
    deploy_fields = ()
    ui = {'name': 'Chain', 'local': 'chain-node', 'color': '#999999',
          'verifier': 'by a <language> contract',
          'deploying': 'deploying the <language> verifier'}
    sources = (
        ('Chain · <language>', [
            # ('contract/src/lsag.xx', 'the scheme'),
        ]),
        ('Chain · adapter', [
            ('adapter.py', 'the Network'),
            ('tests/test_chain.py', 'adapter tests')]),
    )

    # ---- lifecycle ---------------------------------------------------------

    def health(self):
        return {'up': False, 'built': False}

    def build(self):
        return {'ok': False, 'log': 'not implemented'}

    def test(self):
        return {'ok': False, 'passed': 0, 'failed': None, 'tests': [],
                'log': 'not implemented'}

    def up(self, action='status'):
        return {'up': False, 'error': 'not implemented'}

    def block(self, net):
        raise NotImplementedError

    def advance(self, net, to_block):
        raise NotImplementedError

    # ---- the contract --------------------------------------------------------

    def deploy(self, net, tempo=None, min_ring=3, emission=1000, **kw):
        raise NotImplementedError

    def register(self, net, role, key):
        raise NotImplementedError

    def set_open(self, net, flag):
        raise NotImplementedError

    def members(self, net, role):
        raise NotImplementedError

    def epoch(self, net):
        raise NotImplementedError

    def mine_scope(self, net, epoch):
        # usually: self.signer.scope('mine', <subnet felt from the chain>, epoch)
        raise NotImplementedError

    # ---- anonymous actions: sign with self.signer, post via a relayer ------

    def submit(self, net, name, work, ring):
        raise NotImplementedError

    def score(self, net, name, tag, score, ring):
        raise NotImplementedError

    def settle(self, net, epoch, tag):
        """Yuma for one tag (core/yuma.py): consensus + clipped rank."""
        raise NotImplementedError

    def claim(self, net, name, epoch, recipient, ring):
        raise NotImplementedError

    # ---- reads ---------------------------------------------------------------

    def tally(self, net, epoch=None):
        raise NotImplementedError

    def earned(self, net, recipient):
        raise NotImplementedError

    def board(self, net, epochs=6):
        return {'network': net, 'deployed': False, 'devnet_up': False}


ADAPTER = Chain
