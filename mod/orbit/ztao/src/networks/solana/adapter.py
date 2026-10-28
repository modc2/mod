"""ztao on Solana: contract/ (the sBPF program). One STARK-curve double
scalar multiply costs more than a transaction's 1.4M compute units, so the
relayer opens a SESSION, uploads the signature plus HINTS (derived in
Python by core/stark.py), and sends independent UNITS that each verify one
slice; `finish` applies the action once every unit has passed. client.py is
the wire (txs, validator, sessions, units).

Networks:
    solana         local solana-test-validator on :51510 (data/solana/)
    solana-devnet  api.devnet.solana.com with ZTAO_SOLANA_KEYPAIR (funded)

The field + Poseidon Rust code is shared with NEAR (near/contract/src).
"""
import os

from ...core import stark
from ...core.util import ROLES, felt
from ..base import Network
from .client import SolZTao, b58decode, felt_of


UNIT_CU = 971_000   # measured: ring 4 verifies in ~41 units of ~0.97M CU

# the last sweep, as frozen in paper/generated/fits.json:
# CU = 2.08e6 + 9.44e6 x ring (r2 = 0.99999 over rings 4, 8, 16, 32), cut
# into units of UNIT_CU (each one transaction, 5000 lamports of signature).
# `m ztao gas_sweep chain=solana` overwrites this with a fresh measurement.
def _row(r):
    cu = round(2_080_926 + 9_440_882 * r)
    n = max(1, round(cu / UNIT_CU))
    return {'ring': r, 'cu': cu, 'units': n, 'txs': n, 'cu_max': 1_400_000}


MEASURED = {'at': None, 'cap_cu': 1_400_000, 'max_ring': 32,
            'from': 'solana sweep, frozen in paper/generated/fits.json',
            'rows': [_row(r) for r in (4, 8, 16, 32)]}


class Solana(Network):
    chain = 'solana'
    title = 'Solana'
    nets = ('solana', 'solana-devnet')
    order = 40
    max_ring = 32  # session layout; verification is split into units anyway
    # a slot every ~0.4 s and ~35 transactions per action: the longest epoch
    sim_tempo = 180
    sweep_tempo = 100000
    cost_fields = ('cu', 'units', 'txs', 'cu_max')
    deploy_fields = ('program', 'program_bytes')
    default_costs = MEASURED
    sweep_meta = {'cap_cu': 1_400_000}
    token, symbol = 'solana', 'SOL'
    rounds = 3  # open session, the units (in parallel), finish
    mainnet_rpc = os.environ.get('ZTAO_RPC_SOLANA_MAINNET',
                                 'https://api.mainnet-beta.solana.com')
    BLOCK_CU = 60_000_000  # SIMD-0256 block limit
    ui = {'name': 'Solana', 'local': 'solana-test-validator', 'color': '#b57bff',
          'verifier': 'by an sBPF program, in compute-budget-sized units',
          'deploying': 'deploying the sBPF verifier to the local validator'}
    sources = (
        ('Solana · Rust (sBPF)', [
            ('contract/src/verify.rs', 'the scheme, cut into 1.4M-CU units'),
            ('contract/src/lib.rs', 'the program: subnet, sessions, finish, Zuma settle'),
            ('contract/src/tests.rs', 'cargo suite on Cairo-made hints + Zuma math'),
            ('contract/Cargo.toml', 'crate + build profile')]),
        ('Solana · adapter', [
            ('adapter.py', 'the Network: sessions, hints, actions'),
            ('client.py', 'validator, txs, sessions, units'),
            ('tools/gen_fixture.py', 'writes the test hints'),
            ('tests/test_solana.py', 'adapter tests')]),
    )

    def __init__(self, signer):
        super().__init__(signer)
        from ... import DATA
        self.c = SolZTao(DATA)

    # ---- mainnet economics ---------------------------------------------------

    def mainnet(self):
        from ...core.fees import rpc
        u = self.mainnet_rpc
        perf = rpc(u, 'getRecentPerformanceSamples', [10])
        slots = sum(p['numSlots'] for p in perf)
        pf = sorted(p['prioritizationFee'] for p in
                    rpc(u, 'getRecentPrioritizationFees', []))
        return {'block_seconds': sum(p['samplePeriodSecs'] for p in perf) / slots,
                'gas': {'lamports_per_sig': 5000,
                        'micro_lamports_per_cu': pf[len(pf) // 2] if pf else 0,
                        'micro_lamports_per_cu_p90':
                            pf[int(len(pf) * .9)] if pf else 0},
                'block_capacity': self.BLOCK_CU, 'capacity_unit': 'CU',
                'tx_cap': 1_400_000,
                'note': '5000 lamports per signature + median priority fee x CU; '
                        'session rent is refunded at finish'}

    def fee(self, cost, m):
        if not cost.get('txs'):
            return None
        g = m['gas']
        # +1 signature: the session account co-signs the tx that creates it
        lam = (cost['txs'] + 1) * g['lamports_per_sig'] + \
            (cost.get('cu') or 0) * g['micro_lamports_per_cu'] / 1e6
        return lam / 1e9

    def load(self, cost):
        return cost.get('cu')


    # ---- lifecycle ---------------------------------------------------------

    def health(self):
        return {'up': self.c._alive(), 'built': not self.c._stale()}

    def build(self):
        return self.c.build()

    def test(self):
        return self.c.test()

    def up(self, action='status'):
        return self.c.validator(action)

    def block(self, net):
        return self.c.slot(net)

    def advance(self, net, to_block):
        # no warp on a live validator: wait the slots out (~0.4 s each)
        self.c.wait_slot(net, to_block)

    # ---- the contract --------------------------------------------------------

    def deploy(self, net, tempo=None, min_ring=3, emission=1000, **_):
        return self.c.deploy(net, tempo=tempo, min_ring=min_ring,
                             emission=emission)

    def register(self, net, role, key):
        return {'tx': self.c.register(net, role, key['x'], key['y'])['sig']}

    def set_open(self, net, flag):
        return self.c.set_open(net, flag)['sig']

    def members(self, net, role):
        return self.c.members(net, role)

    def epoch(self, net):
        p = self.c.params(net)
        return {'epoch': p['epoch'], 'block': p['slot'], 'tempo': p['tempo'],
                'blocks_left': p['slots_left']}

    def mine_scope(self, net, epoch):
        return self.signer.scope('mine', self.c._subnet(net)['subnet_felt'],
                                 epoch)

    # ---- anonymous actions ---------------------------------------------------

    def _act(self, net, kind, name, ring, msg, open_kw, tag=0, recipient=None):
        """Open a session (the program computes the scope), sign it, derive
        the hints, then upload + seal + run every unit + finish."""
        role = 'validator' if kind == 'score' else 'miner'
        index = {int(m['x'], 16): i for i, m in
                 enumerate(self.c.members(net, ROLES[role]))}
        ss, scope, e = self.c.open_session(net, kind, len(ring), **open_kw)
        try:
            sig, ms = self.signer.timed_sign(name, scope, msg, ring)
            I = lambda v: int(v, 16)
            pts = [(I(p['x']), I(p['y'])) for p in sig['ring']]
            tr = stark.trace(pts, scope, msg, I(sig['c0']),
                             [I(x) for x in sig['s']],
                             (I(sig['image']['x']), I(sig['image']['y'])))
            r = self.c.act(net, ss, kind, [index[x] for x, _ in pts], sig, tr,
                           e, tag=tag, recipient=recipient)
        except Exception:
            self.c.abort(net, ss)
            raise
        return e, {'tx': r['sig'], 'units': r['units'], 'txs': r['txs'],
                   'cu': r['cu'], 'cu_max': r['cu_max'], 'sign_ms': ms,
                   'verify_s': r['verify_s']}, r['image_id']

    def submit(self, net, name, work, ring):
        msg = felt(work)
        e, cost, img = self._act(net, 'submit', name, ring, msg, {'msg': msg})
        return {'ok': True, 'epoch': e, 'tag': hex(img),
                'ring_size': len(ring), **cost}

    def score(self, net, name, tag, score, ring):
        msg, tag = int(score), felt(tag)
        e, cost, img = self._act(net, 'score', name, ring, msg,
                                 {'msg': msg, 'tag': tag}, tag=tag)
        return {'ok': True, 'epoch': e, 'tag': hex(tag), 'score': msg,
                'nullifier': hex(img), **cost}

    def settle(self, net, epoch, tag):
        """Yuma for one tag (core/yuma.py): one plain tx, no signature —
        anyone may settle a closed epoch; a re-settle raises SolError."""
        r = self.c.settle(net, int(epoch), felt(tag))
        return {'ok': True, 'epoch': int(epoch), 'tag': hex(felt(tag)),
                'consensus': r['consensus'], 'rank': r['rank'], 'tx': r['sig'],
                'cu': r['cu'], 'cu_max': r['cu'], 'txs': 1, 'units': 0}

    def claim(self, net, name, epoch, recipient, ring):
        msg = felt_of(b58decode(recipient))
        e, cost, img = self._act(net, 'claim', name, ring, msg,
                                 {'epoch': epoch, 'recipient': recipient},
                                 recipient=recipient)
        return {'ok': True, 'epoch': e, 'tag': hex(img), 'recipient': recipient,
                'earned': self.c.earned(net, recipient), **cost}

    # ---- reads ---------------------------------------------------------------

    def tally(self, net, epoch=None):
        return self.c.tally(net, epoch)

    def earned(self, net, recipient):
        return {'recipient': recipient, 'earned': self.c.earned(net, recipient)}

    def board(self, net, epochs=6):
        return self.c.board(net, epochs)

    # ---- simulate hooks --------------------------------------------------------

    def open_epoch(self, net, tempo, ev):
        pass  # a fresh subnet starts its own epoch 0 at deploy

    def close_epoch(self, net, epoch, tempo, ev):
        p = self.c.params(net)
        ev('waiting', epoch=epoch, slots_left=p['slots_left'])
        self.advance(net, p['start_slot'] + (epoch + 1) * tempo + 1)


ADAPTER = Solana
