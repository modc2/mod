"""ztensor on NEAR: contract/ (near-sdk 5 crate ztensor_near) — lsag.rs is a port
of lsag.cairo on a fast 9x29-bit field (fp.rs) and verifies the SAME prover
output. client.py is the wire (RPC, borsh txs, sandbox, wasm build).

Networks:
    near          local near-sandbox on :51333 (data/near-sandbox/)
    near-testnet  NEAR testnet, signer from the near module keystore
                  (~/.mod/near/keys.json), account=

Gas: ~7.5 Tgas per ring member under a 300 Tgas call cap -> rings stop at 32.
"""
import os

from ...core.util import felt
from ..base import Network
from .client import WASM, NearZTensor

# measured 2026-10-15 on near-sandbox (neard 2.0): submit, fresh deploy
MEASURED = {'at': None, 'limit_tgas': 300, 'max_ring': 32, 'rows': [
    {'ring': 4, 'tgas': 36.27}, {'ring': 8, 'tgas': 66.51},
    {'ring': 16, 'tgas': 126.33}, {'ring': 24, 'tgas': 186.68},
    {'ring': 32, 'tgas': 246.66},
    {'ring': 40, 'error': 'Exceeded the maximum amount of gas allowed to '
                          'burn per contract.'}]}


class Near(Network):
    chain = 'near'
    title = 'NEAR'
    nets = ('near', 'near-testnet')
    order = 20
    max_ring = 32
    # NEAR makes ~1 block/s on its own, so an epoch must outlast the
    # 3 submits + every score of a simulate()
    sim_tempo = 600
    cost_fields = ('tgas',)
    deploy_fields = ('tgas', 'wasm_bytes')
    default_costs = MEASURED
    sweep_meta = {'limit_tgas': 300}
    token, symbol = 'near', 'NEAR'
    mainnet_rpc = os.environ.get('ZTENSOR_RPC_NEAR_MAINNET',
                                 'https://rpc.mainnet.near.org')
    ui = {'name': 'NEAR', 'local': 'near-sandbox', 'color': '#00c08b',
          'verifier': 'by a Rust/wasm contract on NEAR (same signatures, own curve math)',
          'deploying': 'deploying the wasm verifier to a fresh NEAR account'}
    sources = (
        ('NEAR · Rust', [
            ('contract/src/lsag.rs', 'the same scheme, ported line by line'),
            ('contract/src/lib.rs', 'the subnet contract (near-sdk 5)'),
            ('contract/src/fp.rs', 'fast field + curve for wasm'),
            ('contract/src/poseidon.rs', 'Poseidon on the fast field (generated)'),
            ('contract/src/tests.rs', 'cargo test suite + Cairo fixtures'),
            ('contract/src/bench.rs', 'in-contract gas probes'),
            ('contract/Cargo.toml', 'crate + build profile')]),
        ('NEAR · adapter', [
            ('adapter.py', 'the Network: scopes, hints, actions'),
            ('client.py', 'borsh txs, sandbox, deploy'),
            ('tools/wasmlower/src/main.rs', 'wasm -> MVP lowering for nearcore'),
            ('tools/gen_fp.py', 'generates fp.rs core + poseidon.rs'),
            ('tests/test_near.py', 'adapter tests')]),
    )

    def __init__(self, signer):
        super().__init__(signer)
        from ... import DATA
        self.c = NearZTensor(DATA)

    # ---- mainnet economics ---------------------------------------------------

    def mainnet(self):
        from ...core.fees import rpc
        u = self.mainnet_rpc
        head = rpc(u, 'block', {'finality': 'final'})
        h = head['header']
        old = rpc(u, 'block', {'block_id': h['height'] - 100})['header']
        # a contract lives on one shard: one chunk per block is its ceiling
        chunk = max(c['gas_limit'] for c in head['chunks'])
        return {'block_seconds': (h['timestamp'] - old['timestamp']) / 1e9
                / (h['height'] - old['height']),
                'gas': {'yocto_per_gas': int(h['gas_price'])},
                'block_capacity': chunk / 1e12, 'capacity_unit': 'Tgas',
                'tx_cap': 300,
                'note': 'gas burnt x gas price (unused prepaid gas is refunded)'}

    def fee(self, cost, m):
        t = cost.get('tgas')
        return t and t * 1e12 * m['gas']['yocto_per_gas'] / 1e24

    def load(self, cost):
        return cost.get('tgas')


    # ---- lifecycle ---------------------------------------------------------

    def health(self):
        return {'up': self.c._alive(), 'built': os.path.exists(WASM),
                'sandbox_bin': os.path.exists(os.path.expanduser(
                    '~/.local/bin/near-sandbox'))}

    def build(self):
        return self.c.build()

    def test(self):
        return self.c.test()

    def up(self, action='status'):
        return self.c.sandbox(action)

    def block(self, net):
        return self.c.block(net)

    def advance(self, net, to_block):
        gap = to_block - self.c.block(net)
        if gap > 0:
            self.c.fast_forward(net, gap)

    # ---- the contract --------------------------------------------------------

    def deploy(self, net, tempo=None, min_ring=3, emission=1000, account=None,
               **_):
        return self.c.deploy(net, tempo=tempo, min_ring=min_ring,
                             emission=emission, account=account)

    def register(self, net, role, key):
        t = self.c.invoke(net, 'register', {
            'role': role, 'key': {'x': key['x'], 'y': key['y']}}, as_owner=True)
        return {'tx': t['hash'], 'tgas': t['tgas']}

    def set_open(self, net, flag):
        return self.c.invoke(net, 'set_open', {'open': flag}, as_owner=True)['hash']

    def members(self, net, role):
        return self.c.view(net, 'members', {'role': role})

    def epoch(self, net):
        p, b = self.c.view(net, 'params'), self.c.block(net)
        return {'epoch': b // p['tempo'], 'block': b, 'tempo': p['tempo'],
                'blocks_left': p['tempo'] - b % p['tempo']}

    def mine_scope(self, net, epoch):
        # subnet = felt(sha256(account id)): ask the contract, don't recompute
        return felt(self.c.view(net, 'mine_scope', {'epoch': int(epoch)}))

    # ---- anonymous actions ---------------------------------------------------

    def _wire(self, net, name, scope, msg, ring):
        sig, ms = self.signer.timed_sign(name, scope, msg, ring)
        # Hp(scope) from a (free) view call: the contract checks it instead
        # of taking an on-chain square root (~6 Tgas saved per action)
        hp = self.c.view(net, 'hash_point', {'scope': hex(felt(scope))})
        return sig, ms, {'ring': sig['ring'], 'sig': {
            'c0': sig['c0'], 's': sig['s'], 'image': sig['image'], 'hp': hp}}

    def submit(self, net, name, work, ring):
        e = self.c.view(net, 'epoch')
        scope = self.c.view(net, 'mine_scope', {'epoch': e})
        msg = hex(felt(work))
        sig, ms, wire = self._wire(net, name, scope, msg, ring)
        t = self.c.invoke(net, 'submit', {**wire, 'work': msg})
        return {'ok': True, 'epoch': e, 'tag': t['result'],
                'ring_size': len(sig['ring']), 'tx': t['hash'],
                'tgas': t['tgas'], 'sign_ms': ms}

    def score(self, net, name, tag, score, ring):
        e, tag = self.c.view(net, 'epoch'), hex(felt(tag))
        scope = self.c.view(net, 'score_scope', {'epoch': e, 'tag': tag})
        msg = int(score)
        sig, ms, wire = self._wire(net, name, scope, msg, ring)
        t = self.c.invoke(net, 'score', {**wire, 'tag': tag, 'score': msg})
        return {'ok': True, 'epoch': e, 'tag': tag, 'score': msg,
                'nullifier': t['result'], 'tx': t['hash'], 'tgas': t['tgas'],
                'sign_ms': ms}

    def settle(self, net, epoch, tag):
        """Yuma for one tag of a closed epoch (core/yuma.py). Anyone can
        post it (the relayer does); a re-settle panics -> NearError, which
        is a RuntimeError."""
        e, tag = int(epoch), hex(felt(tag))
        t = self.c.invoke(net, 'settle', {'epoch': e, 'tag': tag})
        y = t['result']
        return {'ok': True, 'epoch': e, 'tag': tag,
                'consensus': int(y['consensus']), 'rank': int(y['rank']),
                'tx': t['hash'], 'tgas': t['tgas']}

    def claim(self, net, name, epoch, recipient, ring):
        e = int(epoch)
        scope = self.c.view(net, 'mine_scope', {'epoch': e})
        msg = self.c.view(net, 'recipient_felt', {'recipient': recipient})
        sig, ms, wire = self._wire(net, name, scope, msg, ring)
        t = self.c.invoke(net, 'claim', {**wire, 'epoch': e,
                                         'recipient': recipient})
        return {'ok': True, 'epoch': e, 'tag': sig['tag'],
                'recipient': recipient, 'amount': int(t['result']),
                'earned': int(self.c.view(net, 'earned', {'who': recipient})),
                'tx': t['hash'], 'tgas': t['tgas'], 'sign_ms': ms}

    # ---- reads ---------------------------------------------------------------

    def tally(self, net, epoch=None):
        return self.c.tally(net, epoch)

    def earned(self, net, recipient):
        return {'recipient': recipient, 'earned': int(self.c.view(
            net, 'earned', {'who': recipient}))}

    def board(self, net, epochs=6):
        return self.c.board(net, epochs)


ADAPTER = Near
