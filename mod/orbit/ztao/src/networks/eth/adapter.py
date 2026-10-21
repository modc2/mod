"""ztao on Ethereum: contract/ (Foundry project) — LSAG.sol + ZTao.sol over
StarkCurve.sol (the STARK curve in Yul) and a hand-assembled Poseidon
permutation contract (Hades). client.py is the wire (web3, anvil, forge).

Networks:
    eth          local anvil on :51504 (data/anvil/)
    eth-sepolia  ZTAO_RPC_ETH_SEPOLIA + ZTAO_ETH_KEY (funded; owner+relayer)

Gas: ~0.41M per ring member, so rings stop at 32 (inside EIP-7825's 2^24).
Yuma (core/yuma.py): score() keeps a per-tag score histogram, settle(epoch,
tag) walks it once the epoch closes, claim() pays emission * rank / sum rank.
"""
import os

from ...core.util import felt
from ..base import Network
from .client import FOUNDRY, EthZTao


def _pt(p):
    return int(p['x'], 16), int(p['y'], 16)


class Eth(Network):
    chain = 'eth'
    title = 'Ethereum'
    nets = ('eth', 'eth-sepolia')
    order = 30
    max_ring = 32
    sim_tempo = 60
    cost_fields = ('gas',)
    deploy_fields = ('gas', 'code_bytes', 'hades')
    sweep_meta = {'cap': 1 << 24}
    token, symbol = 'ethereum', 'ETH'
    mainnet_rpc = os.environ.get('ZTAO_RPC_ETH_MAINNET',
                                 'https://ethereum-rpc.publicnode.com')
    ui = {'name': 'Ethereum', 'local': 'anvil', 'color': '#8b9cf7',
          'verifier': 'by a Solidity contract (STARK curve in Yul, Poseidon as a contract)',
          'deploying': 'deploying the Solidity verifier (and Hades) to anvil'}
    sources = (
        ('Ethereum · Solidity', [
            ('contract/src/LSAG.sol', 'the same scheme: ring check, Hp hint'),
            ('contract/src/ZTao.sol', 'the subnet contract'),
            ('contract/src/StarkCurve.sol', 'STARK curve in Yul: Jacobian, Straus'),
            ('contract/src/Poseidon.sol', 'poseidon_hash_span over Hades'),
            ('contract/src/Hades.sol', 'calls the Poseidon permutation contract'),
            ('contract/test/ZTao.t.sol', 'forge suite + Cairo fixtures + Zuma + gas'),
            ('contract/foundry.toml', 'build profile')]),
        ('Ethereum · adapter', [
            ('adapter.py', 'the Network: scopes, hints, actions'),
            ('client.py', 'anvil, deploy, txs (web3)'),
            ('tools/gen_consts.py', 'hand-assembles the Hades bytecode'),
            ('tests/test_eth.py', 'adapter tests')]),
    )

    def __init__(self, signer):
        super().__init__(signer)
        from ... import DATA
        self.c = EthZTao(DATA)

    # ---- mainnet economics ---------------------------------------------------

    def mainnet(self):
        from ...core.fees import rpc
        u = self.mainnet_rpc
        head = rpc(u, 'eth_getBlockByNumber', ['latest', False])
        n = int(head['number'], 16)
        old = rpc(u, 'eth_getBlockByNumber', [hex(n - 100), False])
        tip = int(rpc(u, 'eth_maxPriorityFeePerGas'), 16)
        base = int(head['baseFeePerGas'], 16)
        return {'block_seconds': (int(head['timestamp'], 16) -
                                  int(old['timestamp'], 16)) / 100,
                'gas': {'base_gwei': base / 1e9, 'tip_gwei': tip / 1e9,
                        'gwei': (base + tip) / 1e9},
                'block_capacity': int(head['gasLimit'], 16),
                'capacity_unit': 'gas', 'tx_cap': 1 << 24,
                'note': 'gas x (base fee + priority tip), L1 mainnet'}

    def fee(self, cost, m):
        g = cost.get('gas')
        return g and g * m['gas']['gwei'] / 1e9

    def load(self, cost):
        return cost.get('gas')


    # ---- lifecycle ---------------------------------------------------------

    def health(self):
        return {'up': self.c._alive(), 'built': not self.c._stale(),
                'forge': os.path.exists(os.path.join(FOUNDRY, 'forge'))}

    def build(self):
        return self.c.build()

    def test(self):
        """forge test; also refreshes data/eth_gas.json (gas by ring size)."""
        return self.c.test()

    def up(self, action='status'):
        return self.c.anvil(action)

    def block(self, net):
        return self.c.block(net)

    def advance(self, net, to_block):
        gap = to_block - self.c.block(net)
        if gap > 0:
            self.c.mine(net, gap)

    # ---- the contract --------------------------------------------------------

    def deploy(self, net, tempo=None, min_ring=3, emission=1000, **_):
        return self.c.deploy(net, tempo=tempo, min_ring=min_ring,
                             emission=emission)

    def register(self, net, role, key):
        t = self.c.invoke(net, 'register', role, _pt(key), role='owner')
        return {'tx': t['hash'], 'gas': t['gas']}

    def set_open(self, net, flag):
        return self.c.invoke(net, 'setOpen', flag, role='owner')['hash']

    def members(self, net, role):
        return self.c.members(net, role)

    def epoch(self, net):
        t, b = self.c.view(net, 'tempo'), self.c.block(net)
        return {'epoch': b // t, 'block': b, 'tempo': t, 'blocks_left': t - b % t}

    def mine_scope(self, net, epoch):
        return self.c.view(net, 'mineScope', int(epoch))

    # ---- anonymous actions ---------------------------------------------------

    def _sig(self, name, scope, msg, ring):
        """Sign, then the EVM tuple form + Hp(scope) as a checked hint."""
        sig, ms = self.signer.timed_sign(name, scope, msg, ring)
        hp = self.signer.hash_point(scope)
        return sig, ms, [_pt(p) for p in sig['ring']], (
            int(sig['c0'], 16), [int(x, 16) for x in sig['s']],
            _pt(sig['image']), _pt(hp))

    def submit(self, net, name, work, ring):
        e = self.c.view(net, 'epoch')
        msg = felt(work)
        sig, ms, ring_t, sig_t = self._sig(name, self.mine_scope(net, e), msg, ring)
        t = self.c.invoke(net, 'submit', ring_t, msg, sig_t)
        return {'ok': True, 'epoch': e, 'tag': hex(t['result']),
                'ring_size': len(ring_t), 'tx': t['hash'], 'gas': t['gas'],
                'sign_ms': ms}

    def score(self, net, name, tag, score, ring):
        e, tag, msg = self.c.view(net, 'epoch'), felt(tag), int(score)
        sig, ms, ring_t, sig_t = self._sig(
            name, self.c.view(net, 'scoreScope', e, tag), msg, ring)
        t = self.c.invoke(net, 'score', ring_t, tag, msg, sig_t)
        return {'ok': True, 'epoch': e, 'tag': hex(tag), 'score': msg,
                'nullifier': hex(t['result']), 'tx': t['hash'],
                'gas': t['gas'], 'sign_ms': ms}

    def settle(self, net, epoch, tag):
        """Yuma for one tag of a closed epoch (anyone; the relayer pays).
        A re-settle / open epoch / unknown tag reverts -> RuntimeError."""
        e, tag = int(epoch), felt(tag)
        t = self.c.invoke(net, 'settle', e, tag)
        c, r = t['result']
        return {'ok': True, 'epoch': e, 'tag': hex(tag), 'consensus': int(c),
                'rank': int(r), 'tx': t['hash'], 'gas': t['gas']}

    def claim(self, net, name, epoch, recipient, ring):
        e = int(epoch)
        to = self.c.w3(net).to_checksum_address(recipient)
        sig, ms, ring_t, sig_t = self._sig(name, self.mine_scope(net, e),
                                           int(to, 16), ring)
        t = self.c.invoke(net, 'claim', e, ring_t, to, sig_t)
        return {'ok': True, 'epoch': e, 'tag': sig['tag'], 'recipient': to,
                'amount': int(t['result']),
                'earned': self.c.view(net, 'earned', to), 'tx': t['hash'],
                'gas': t['gas'], 'sign_ms': ms}

    # ---- reads ---------------------------------------------------------------

    def tally(self, net, epoch=None):
        return self.c.tally(net, epoch)

    def earned(self, net, recipient):
        to = self.c.w3(net).to_checksum_address(recipient)
        return {'recipient': to, 'earned': self.c.view(net, 'earned', to)}

    def board(self, net, epochs=6):
        return self.c.board(net, epochs)


ADAPTER = Eth
