"""ztao on Substrate: contract/ (ink! 5 crate ztao_ink, pallet-contracts)
verifies the SAME prover output as every other chain — lsag.rs, fp.rs and
poseidon.rs are near/contract's files, pulled in verbatim by build.rs.
client.py is the wire (node, cargo-contract build, SCALE ABI, extrinsics).

Networks:
    substrate          local substrate-contracts-node --dev on :51507
                       (data/substrate/; owner //Alice, relayer //Bob,
                       payee //Charlie)
    substrate-testnet  any pallet-contracts chain (Astar Shibuya, Pop, Aleph
                       Zero testnet, ...): ZTAO_SUBSTRATE_RPC +
                       ZTAO_SUBSTRATE_SEED

Weight: ~5.7 ms of ref_time per ring member + ~6 ms fixed, against a
1.3 s per-extrinsic cap (2 s blocks, 75 % normal class) -> rings stop at 64
by choice (Starknet's cap), not by the limit. USD is priced on Astar
mainnet, the largest public pallet-contracts chain: its own WeightToFee
and fee multiplier, read live.
"""
import os

from ...core.util import felt
from ..base import Network
from . import client as C
from .client import WASM, SubstrateError, SubstrateZTao

# measured 2026-10-20 on substrate-contracts-node 0.42 (pallet-contracts,
# wasmi): submit, fresh deploy, Hp hint passed. ref_time in ms, proof in KB
MEASURED = {'at': None, 'limit_ms': 1300, 'max_ring': 64, 'rows': [
    {'ring': 4, 'weight_ms': 29.3, 'proof_kb': 286.6, 'bytes': 583},
    {'ring': 8, 'weight_ms': 52.1, 'proof_kb': 307.1, 'bytes': 967},
    {'ring': 16, 'weight_ms': 97.5, 'proof_kb': 389.2, 'bytes': 1735},
    {'ring': 32, 'weight_ms': 189.3, 'proof_kb': 553.3, 'bytes': 3271},
    {'ring': 48, 'weight_ms': 281.0, 'proof_kb': 717.5, 'bytes': 4807},
    {'ring': 64, 'weight_ms': 371.6, 'proof_kb': 881.6, 'bytes': 6345},
    {'ring': 72, 'error': 'RingAboveMax'}]}

# what an extrinsic adds around the call data (signature, era, nonce, tip,
# dest, value, gas limit): length fee only, so an estimate is enough
EXTRINSIC_OVERHEAD = 150


class Substrate(Network):
    chain = 'substrate'
    title = 'Substrate'
    nets = ('substrate', 'substrate-testnet')
    order = 25
    max_ring = 64
    # every extrinsic seals one block locally: an epoch has to hold the 3
    # submits + 12 scores of a simulate() (plus a margin for the relayer)
    sim_tempo = 40
    sweep_tempo = 1000
    cost_fields = ('weight_ms', 'proof_kb', 'bytes')
    deploy_fields = ('wasm_bytes', 'weight_ms')
    default_costs = MEASURED
    sweep_meta = {'limit_ms': 1300}
    token, symbol = 'astar', 'ASTR'
    mainnet_rpc = os.environ.get('ZTAO_RPC_SUBSTRATE_MAINNET',
                                 'wss://rpc.astar.network')
    ui = {'name': 'Substrate', 'local': 'substrate-contracts-node',
          'color': '#e6007a',
          'verifier': 'by an ink! contract on pallet-contracts (same signatures, wasm curve math)',
          'deploying': 'uploading the ink! verifier and instantiating it'}
    sources = (
        ('Substrate · ink!', [
            ('contract/src/lib.rs', 'the subnet contract (ink! 5)'),
            ('contract/src/tests.rs', 'cargo test suite + Cairo fixture'),
            ('contract/build.rs', 'pulls lsag/fp/poseidon from near/contract'),
            ('contract/Cargo.toml', 'crate, build profile, getrandom patch'),
            ('tools/getrandom-stub/src/lib.rs', 'no_std stand-in for lambdaworks')]),
        ('Substrate · adapter', [
            ('adapter.py', 'the Network: scopes, hints, actions, fees'),
            ('client.py', 'node, SCALE ABI, dry-run + extrinsic'),
            ('tests/test_substrate.py', 'adapter tests')]),
    )

    def __init__(self, signer):
        super().__init__(signer)
        from ... import DATA
        self.c = SubstrateZTao(DATA)

    # ---- mainnet economics (Astar) ------------------------------------------

    def mainnet(self):
        from substrateinterface import SubstrateInterface
        a = SubstrateInterface(url=self.mainnet_rpc)
        bw = a.get_constant('System', 'BlockWeights').value
        normal = bw['per_class']['normal']
        one_s = 10 ** 12

        def call(fn, arg):
            r = a.rpc_request('state_call', [fn, '0x' + arg.hex()])['result']
            return int.from_bytes(bytes.fromhex(r[2:]), 'little')
        per_s = call('TransactionPaymentApi_query_weight_to_fee',
                     C.compact(one_s) + C.compact(0))
        per_kb = call('TransactionPaymentApi_query_length_to_fee',
                      (1000).to_bytes(4, 'little'))
        mult = int(a.query('TransactionPayment', 'NextFeeMultiplier').value)
        head = a.get_block_number(None)
        t1 = a.query('Timestamp', 'Now').value
        t0 = a.query('Timestamp', 'Now', block_hash=a.get_block_hash(head - 100)).value
        dec = 10 ** (a.token_decimals or 18)
        ms = lambda w: w['ref_time'] / 1e9  # noqa: E731
        return {'block_seconds': (t1 - t0) / 100 / 1000,
                'gas': {'astr_per_ms': per_s / 1e3 / dec,
                        'astr_per_byte': per_kb / 1000 / dec,
                        'multiplier': mult / 1e18,
                        'base_ms': ms(normal['base_extrinsic'])},
                'block_capacity': ms(normal['max_total']),
                'capacity_unit': 'ms ref_time', 'tx_cap': ms(normal['max_extrinsic']),
                'note': 'Astar: (ref_time + base) x WeightToFee x fee multiplier '
                        '+ length fee; proof_size is not charged; storage '
                        'deposits are refundable and not counted'}

    def fee(self, cost, m):
        w = cost.get('weight_ms')
        if w is None:
            return None
        g = m['gas']
        weight = (w + g.get('base_ms', 0)) * g['astr_per_ms'] * g.get('multiplier', 1)
        return weight + ((cost.get('bytes') or 0) + EXTRINSIC_OVERHEAD) * g['astr_per_byte']

    def load(self, cost):
        return cost.get('weight_ms')

    # ---- lifecycle ---------------------------------------------------------

    def health(self):
        return {'up': self.c._alive(), 'built': os.path.exists(WASM),
                'node_bin': os.path.exists(C.NODE_BIN)}

    def build(self):
        return self.c.build()

    def test(self):
        return self.c.test()

    def up(self, action='status'):
        return self.c.node(action)

    def block(self, net):
        return self.c.block(net)

    def advance(self, net, to_block):
        gap = to_block - self.c.block(net)
        if gap <= 0:
            return
        if not C.NETS[net]['local']:
            import time
            while self.c.block(net) < to_block:
                time.sleep(3)
            return
        self.c.seal(net, gap)

    # ---- the contract --------------------------------------------------------

    def deploy(self, net, tempo=None, min_ring=3, emission=1000, **_):
        tempo = int(tempo or (100 if C.NETS[net]['local'] else 600))
        d = self.c.deploy(net, tempo, int(min_ring), int(emission))
        if d.get('ok') and d.get('ref_time') is not None:
            d['weight_ms'] = round(d['ref_time'] / 1e9, 2)
        return d

    def _cost(self, t):
        return {'weight_ms': round(t['ref_time'] / 1e9, 2),
                'proof_kb': round(t['proof_size'] / 1e3, 1), 'bytes': t['bytes']}

    def register(self, net, role, key):
        t = self.c.call(net, 'register', C.u(role, 8) + C.point(key), who='owner')
        return {'tx': t['tx'], **self._cost(t)}

    def set_open(self, net, flag):
        return self.c.call(net, 'set_open', bytes([1 if flag else 0]), who='owner')['tx']

    def members(self, net, role):
        return self.c.members(net, role)

    def epoch(self, net):
        p, b = self.c.view(net, 'params'), self.c.block(net)
        return {'epoch': b // p['tempo'], 'block': b, 'tempo': p['tempo'],
                'blocks_left': p['tempo'] - b % p['tempo']}

    def mine_scope(self, net, epoch):
        # subnet = felt(blake2_256(contract AccountId)): ask the contract
        return felt(self.c.view(net, 'mine_scope', C.u(int(epoch), 64)))

    def payee(self, net, dep):
        return dep['payee']

    # ---- anonymous actions ---------------------------------------------------

    def _wire(self, net, name, scope, msg, ring):
        sig, ms = self.signer.timed_sign(name, scope, msg, ring)
        # Hp(scope) from a (free) dry-run: the contract checks it instead of
        # taking an on-chain square root
        hp = self.c.view(net, 'hash_point', C.felt32(scope))
        wire = C.vec(sig['ring'], C.point), C.signature({**sig, 'hp': hp})
        return sig, ms, wire

    def submit(self, net, name, work, ring):
        e = self.c.view(net, 'epoch')
        scope = self.c.view(net, 'mine_scope', C.u(e, 64))
        msg = hex(felt(work))
        sig, ms, (r, s) = self._wire(net, name, scope, msg, ring)
        t = self.c.call(net, 'submit', r + C.felt32(msg) + s)
        return {'ok': True, 'epoch': e, 'tag': t['result'],
                'ring_size': len(sig['ring']), 'tx': t['tx'], **self._cost(t),
                'sign_ms': ms}

    def score(self, net, name, tag, score, ring):
        e, tag = self.c.view(net, 'epoch'), hex(felt(tag))
        scope = self.c.view(net, 'score_scope', C.u(e, 64) + C.felt32(tag))
        msg = int(score)
        sig, ms, (r, s) = self._wire(net, name, scope, msg, ring)
        t = self.c.call(net, 'score', r + C.felt32(tag) + C.u(msg, 8) + s)
        return {'ok': True, 'epoch': e, 'tag': tag, 'score': msg,
                'nullifier': t['result'], 'tx': t['tx'], **self._cost(t),
                'sign_ms': ms}

    def settle(self, net, epoch, tag):
        """Zuma consensus for one tag of a closed epoch (core/yuma.py). A
        re-settle comes back as Error::AlreadySettled -> SubstrateError, a
        RuntimeError."""
        e, tag = int(epoch), hex(felt(tag))
        t = self.c.call(net, 'settle', C.u(e, 64) + C.felt32(tag))
        c, r = t['result']
        return {'ok': True, 'epoch': e, 'tag': tag, 'consensus': int(c),
                'rank': int(r), 'tx': t['tx'], **self._cost(t)}

    def claim(self, net, name, epoch, recipient, ring):
        e = int(epoch)
        scope = self.c.view(net, 'mine_scope', C.u(e, 64))
        who = C.account_bytes(recipient)
        msg = self.c.view(net, 'recipient_felt', who)
        sig, ms, (r, s) = self._wire(net, name, scope, msg, ring)
        t = self.c.call(net, 'claim', C.u(e, 64) + r + who + s)
        return {'ok': True, 'epoch': e, 'tag': sig['tag'],
                'recipient': recipient, 'amount': int(t['result']),
                'earned': int(self.c.view(net, 'earned', who)),
                'tx': t['tx'], **self._cost(t), 'sign_ms': ms}

    # ---- reads ---------------------------------------------------------------

    def tally(self, net, epoch=None):
        return self.c.tally(net, epoch)

    def earned(self, net, recipient):
        return {'recipient': recipient, 'earned': int(self.c.view(
            net, 'earned', C.account_bytes(recipient)))}

    def board(self, net, epochs=6):
        try:
            return self.c.board(net, epochs)
        except SubstrateError as e:
            return {'network': net, 'chain': 'substrate', 'deployed': True,
                    'devnet_up': self.c._alive(), 'error': str(e)}


ADAPTER = Substrate
