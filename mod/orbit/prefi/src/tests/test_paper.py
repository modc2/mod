"""Paper pool test suite — the wasm contract, the state machine, the
hash-chained log, and the host around them.

Hermetic: markets and the price oracle are injected callables, so nothing
here touches a feed or the live ~/.mod/prefi store. The cryptography is
real (eth_account), same policy as the pool suite — a signature check
tested only against a mock is not tested at all.

    python3 -m pytest tests/test_paper.py -q      # from src/
"""

import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import paper as paper_mod
import paper_machine as pm
import watvm
from paper import Paper, WasmSplit

ALICE = '0xaaaa000000000000000000000000000000000001'
BOB = '0xbbbb000000000000000000000000000000000002'
CAROL = '0xcccc000000000000000000000000000000000003'

M = pm.MICRO


# ── The interpreter itself ───────────────────────────────────────────

class TestWatVM(unittest.TestCase):

    def test_arithmetic_and_memory(self):
        mod = watvm.Module("""
        (module (memory 1)
          (func (export "f") (param $a i64) (param $b i64) (result i64)
            (i64.store (i32.const 8) (i64.add (local.get $a) (local.get $b)))
            (i64.mul (i64.load (i32.const 8)) (i64.const 2))))
        """)
        self.assertEqual(mod.invoke('f', 3, 4), 14)
        self.assertEqual(mod.read_u64(8), 7)

    def test_loop_and_branch(self):
        mod = watvm.Module("""
        (module (memory 1)
          (func (export "sum") (param $n i64) (result i64)
            (local $i i64) (local $acc i64)
            (block $done
              (loop $l
                (br_if $done (i64.ge_u (local.get $i) (local.get $n)))
                (local.set $acc (i64.add (local.get $acc) (local.get $i)))
                (local.set $i (i64.add (local.get $i) (i64.const 1)))
                (br $l)))
            (local.get $acc)))
        """)
        self.assertEqual(mod.invoke('sum', 10), 45)

    def test_div_by_zero_traps(self):
        mod = watvm.Module("""
        (module (memory 1)
          (func (export "d") (param $a i64) (param $b i64) (result i64)
            (i64.div_u (local.get $a) (local.get $b))))
        """)
        with self.assertRaises(watvm.Trap):
            mod.invoke('d', 1, 0)

    def test_signed_comparison(self):
        mod = watvm.Module("""
        (module (memory 1)
          (func (export "gt") (param $a i64) (param $b i64) (result i64)
            (i64.gt_s (local.get $a) (local.get $b))))
        """)
        self.assertEqual(mod.invoke('gt', -1, 0), 0)   # -1 <s 0
        self.assertEqual(mod.invoke('gt', 0, -1), 1)

    def test_unknown_instruction_refused(self):
        mod = watvm.Module("""
        (module (memory 1)
          (func (export "f") (result i64) (i64.popcnt (i64.const 3))))
        """)
        with self.assertRaises(watvm.WatError):
            mod.invoke('f')


# ── The wasm kernel is the reference, bit for bit ────────────────────

class TestKernelParity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.wasm = WasmSplit()

    def both(self, pot, weights):
        got = self.wasm(pot, weights)
        ref = pm.split_weighted(pot, weights)
        self.assertEqual(got, ref, f'wasm != reference for {pot} {weights}')
        payouts, leftover = got
        self.assertEqual(sum(payouts) + leftover, pot,
                         'a unit went missing')
        return got

    def test_edges(self):
        self.both(0, [])
        self.both(100, [])
        self.both(100, [0, 0, 0])          # all-zero → all leftover
        self.both(100, [1])                # sole winner takes all
        self.both(1, [3, 7])               # one unit of dust
        self.both(10, [1, 1, 1])           # even split with remainder

    def test_scaling_path(self):
        # Weights big enough to force the halving loop, and a pot big
        # enough that naive pot*w would overflow i64.
        self.both(10**15, [10**18, 3 * 10**18, 7])
        self.both(982_451_653, [2**62, 2**61, 2**60, 1])

    def test_randomized(self):
        rng = random.Random(1729)
        for _ in range(200):
            n = rng.randint(0, 30)
            weights = [rng.choice([0, rng.randint(1, 10 ** rng.randint(0, 18))])
                       for _ in range(n)]
            self.both(rng.randint(0, 10**14), weights)

    def test_tie_break_is_first_index(self):
        # Two identical remainders: the dust goes to the earlier entry,
        # deterministically, in both implementations.
        payouts, _ = self.both(3, [1, 1])
        self.assertEqual(payouts, [2, 1])


# ── The state machine ────────────────────────────────────────────────

class TestMachine(unittest.TestCase):

    def setUp(self):
        self.st = pm.genesis({}, time=0)

    def go(self, msg):
        self.st, events = pm.apply(self.st, msg)
        return events

    def fund(self, addr, t=100):
        self.go({'type': 'faucet', 'address': addr, 'time': t})

    def test_faucet_grants_and_rate_limits(self):
        self.fund(ALICE, t=100)
        acct = self.st['accounts'][ALICE]
        self.assertEqual(acct['balance'], 1000 * M)
        self.assertEqual(self.st['minted'], 1000 * M)
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'faucet', 'address': ALICE, 'time': 200})
        self.fund(ALICE, t=100 + 86400)        # a day later it opens again
        self.assertEqual(self.st['accounts'][ALICE]['balance'], 2000 * M)

    def test_predict_needs_account_and_balance(self):
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                     'price_e6': M, 'stake': M, 'time': 100})
        self.fund(ALICE)
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                     'price_e6': M, 'stake': 2000 * M, 'time': 101})

    def test_predict_caps_and_cutoff(self):
        self.fund(ALICE)
        self.go({'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                 'price_e6': M, 'stake': M, 'time': 100})
        with self.assertRaises(pm.MachineError):   # one call per asset
            self.go({'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                     'price_e6': M, 'stake': M, 'time': 101})
        # Inside the cutoff window (round 0 closes at 3600, cutoff 300).
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'predict', 'address': ALICE, 'asset': 'ETH',
                     'price_e6': M, 'stake': M, 'time': 3550})

    def test_per_round_cap(self):
        self.fund(ALICE)
        for i in range(10):
            self.go({'type': 'predict', 'address': ALICE,
                     'asset': f'A{i}', 'price_e6': M, 'stake': M,
                     'time': 100 + i})
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'predict', 'address': ALICE, 'asset': 'A10',
                     'price_e6': M, 'stake': M, 'time': 120})

    def test_resolve_splits_by_stake_times_accuracy(self):
        self.fund(ALICE)
        self.fund(BOB)
        self.go({'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                 'price_e6': 100 * M, 'stake': 10 * M, 'time': 100})
        self.go({'type': 'predict', 'address': BOB, 'asset': 'BTC',
                 'price_e6': 150 * M, 'stake': 10 * M, 'time': 101})
        with self.assertRaises(pm.MachineError):   # round still open
            self.go({'type': 'resolve', 'round': 0, 'asset': 'BTC',
                     'actual_e6': 100 * M, 'time': 1000})
        self.go({'type': 'resolve', 'round': 0, 'asset': 'BTC',
                 'actual_e6': 100 * M, 'time': 3600})
        alice = self.st['accounts'][ALICE]
        bob = self.st['accounts'][BOB]
        # Alice exact (score 1.0), Bob 50% off (score 0.5): 2:1 on equal
        # stakes, and the pot conserves to the unit.
        self.assertEqual(alice['won'] + bob['won'] + self.st['reserve'],
                         20 * M)
        self.assertAlmostEqual(alice['won'] / bob['won'], 2.0, places=5)
        with self.assertRaises(pm.MachineError):   # no double settle
            self.go({'type': 'resolve', 'round': 0, 'asset': 'BTC',
                     'actual_e6': 100 * M, 'time': 3700})

    def test_all_wrong_pot_lands_in_reserve(self):
        # Under the linear score a call ≥100% off earns exactly zero —
        # calling $1000 on a $1 close is a 99900% miss.
        self.fund(ALICE)
        self.go({'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                 'price_e6': 1000 * M, 'stake': 5 * M, 'time': 100})
        self.go({'type': 'resolve', 'round': 0, 'asset': 'BTC',
                 'actual_e6': M, 'time': 3600})
        self.assertEqual(self.st['reserve'], 5 * M)
        self.assertEqual(self.st['accounts'][ALICE]['won'], 0)

    def test_tolerance_snapshots_onto_the_round(self):
        self.fund(ALICE)
        self.go({'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                 'price_e6': 90 * M, 'stake': 5 * M, 'time': 100})
        # Retune after entry: must not re-price the open round.
        self.go({'type': 'config', 'patch': {'tol_ppm': 1}, 'time': 200})
        self.go({'type': 'resolve', 'round': 0, 'asset': 'BTC',
                 'actual_e6': 100 * M, 'time': 3600})
        entry = self.st['rounds']['0']['assets']['BTC']['entries'][0]
        self.assertEqual(entry['score_ppm'], 900_000)   # old tol, not new

    def test_transfer_moves_balance(self):
        self.fund(ALICE)
        self.go({'type': 'transfer', 'address': ALICE, 'to': BOB,
                 'amount': 400 * M, 'time': 200})
        self.assertEqual(self.st['accounts'][ALICE]['balance'], 600 * M)
        self.assertEqual(self.st['accounts'][BOB]['balance'], 400 * M)
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'transfer', 'address': ALICE, 'to': ALICE,
                     'amount': M, 'time': 201})

    def test_signed_messages_consume_nonces(self):
        self.fund(ALICE)
        self.go({'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                 'price_e6': M, 'stake': M, 'time': 100,
                 'signed': True, 'nonce': 0})
        with self.assertRaises(pm.MachineError):   # replay = stale nonce
            self.go({'type': 'predict', 'address': ALICE, 'asset': 'ETH',
                     'price_e6': M, 'stake': M, 'time': 101,
                     'signed': True, 'nonce': 0})
        self.assertEqual(self.st['accounts'][ALICE]['nonce'], 1)

    def test_time_never_runs_backwards(self):
        self.fund(ALICE, t=1000)
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'faucet', 'address': BOB, 'time': 500})

    def test_interval_fixed_at_genesis(self):
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'config', 'patch': {'interval': 7200},
                     'time': 10})
        with self.assertRaises(pm.MachineError):
            self.go({'type': 'config', 'patch': {'bogus': 1}, 'time': 10})

    def test_determinism(self):
        """Same messages, same root — the property everything rests on."""
        msgs = [{'type': 'faucet', 'address': ALICE, 'time': 100},
                {'type': 'predict', 'address': ALICE, 'asset': 'BTC',
                 'price_e6': 42 * M, 'stake': 3 * M, 'time': 101},
                {'type': 'resolve', 'round': 0, 'asset': 'BTC',
                 'actual_e6': 40 * M, 'time': 3600}]
        roots = []
        for split in (None, WasmSplit()):
            st = pm.genesis({}, time=0)
            for msg in msgs:
                st, _ = pm.apply(st, msg, split)
            roots.append(pm.state_root(st))
        self.assertEqual(roots[0], roots[1])


# ── The host: log, oracle, restart, audit ────────────────────────────

class PaperBase(unittest.TestCase):

    def setUp(self):
        os.environ['PREFI_UNSAFE_NO_SIG'] = '1'
        self.dir = tempfile.mkdtemp(prefix='prefi-paper-')
        self.prices = {'BTC': 100.0, 'ETH': 10.0}
        self.paper = self.make_paper()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)

    def make_paper(self, **kw):
        return Paper(
            self.dir,
            price_at=lambda sym, ts: {'price': self.prices[sym],
                                      'mode': 'historical'},
            markets=lambda: [{'symbol': s} for s in self.prices],
            **kw)


class TestPaperHost(PaperBase):

    def test_full_cycle(self):
        self.assertNotIn('error', self.paper.faucet(ALICE))
        self.assertNotIn('error', self.paper.faucet(BOB))
        a = self.paper.predict(ALICE, 'btc', 100.0, 10.0)   # case-folded
        self.assertNotIn('error', a)
        self.assertEqual(a['asset'], 'BTC')
        b = self.paper.predict(BOB, 'BTC', 150.0, 10.0)
        self.assertNotIn('error', b)

        out = self.paper.resolve(now=a['closes'] + 1)
        self.assertEqual(len(out['resolved']), 1)
        board = self.paper.leaderboard()
        self.assertEqual(board[0]['address'], ALICE)
        self.assertGreater(board[0]['profit'], 0)
        self.assertLess(board[1]['profit'], 0)
        acct = self.paper.account(ALICE)
        self.assertEqual(acct['accuracy'], 1.0)
        self.assertEqual(self.paper.verify()['ok'], True)

    def test_unknown_market_refused(self):
        self.paper.faucet(ALICE)
        out = self.paper.predict(ALICE, 'DOGE', 1.0, 1.0)
        self.assertIn('error', out)

    def test_oracle_failure_skips_not_loses(self):
        self.paper.faucet(ALICE)
        entry = self.paper.predict(ALICE, 'BTC', 100.0, 5.0)
        del self.prices['BTC']
        broken = Paper(self.dir,
                       price_at=lambda sym, ts: (_ for _ in ()).throw(
                           RuntimeError('feed down')),
                       markets=lambda: [{'symbol': 'BTC'}])
        out = broken.resolve(now=entry['closes'] + 1)
        self.assertEqual(out['resolved'], [])
        self.assertEqual(len(out['skipped']), 1)
        # The pot is still there for whoever asks once the feed is back.
        self.prices['BTC'] = 100.0
        out = self.make_paper().resolve(now=entry['closes'] + 1)
        self.assertEqual(len(out['resolved']), 1)

    def test_restart_resumes_identical_state(self):
        self.paper.faucet(ALICE)
        self.paper.predict(ALICE, 'BTC', 100.0, 5.0)
        root = pm.state_root(self.paper.state())
        again = self.make_paper()
        self.assertEqual(pm.state_root(again.state()), root)

    def test_snapshot_loss_replays_the_log(self):
        self.paper.faucet(ALICE)
        self.paper.predict(ALICE, 'ETH', 10.0, 2.0)
        root = pm.state_root(self.paper.state())
        os.remove(self.paper.state_path)
        again = self.make_paper()
        self.assertEqual(pm.state_root(again.state()), root)
        self.assertTrue(again.verify()['ok'])

    def test_tampered_log_fails_verification(self):
        self.paper.faucet(ALICE)
        self.paper.faucet(BOB)
        lines = self.paper.log_path.read_text().splitlines()
        doctored = json.loads(lines[1])
        doctored['msg']['address'] = CAROL       # rewrite history
        lines[1] = pm.canonical(doctored)
        self.paper.log_path.write_text('\n'.join(lines) + '\n')
        out = self.make_paper().verify()
        self.assertFalse(out['ok'])

    def test_python_engine_matches_wasm_engine(self):
        self.paper.faucet(ALICE)
        self.paper.predict(ALICE, 'BTC', 99.0, 7.0)
        self.paper.resolve(now=self.paper.state()['config']['interval'] * 2)
        root = pm.state_root(self.paper.state())
        os.environ['PREFI_PAPER_WASM'] = '0'
        try:
            os.remove(self.paper.state_path)
            pure = self.make_paper()
            self.assertEqual(pure.engine, 'python-reference')
            self.assertEqual(pm.state_root(pure.state()), root)
            self.assertTrue(pure.verify()['ok'])
        finally:
            os.environ.pop('PREFI_PAPER_WASM', None)

    def test_status_names_the_contract(self):
        status = self.paper.status()
        self.assertEqual(status['engine'], 'wasm (watvm)')
        self.assertEqual(status['contract'], WasmSplit().sha256)
        self.assertIn('root', status)


class TestSignatures(PaperBase):

    def setUp(self):
        super().setUp()
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        from eth_account import Account
        self.account = Account.create()
        self.addr = self.account.address

    def sign(self, message):
        from eth_account import Account
        from eth_account.messages import encode_defunct
        return Account.sign_message(encode_defunct(text=message),
                                    self.account.key).signature.hex()

    def test_signed_predict_and_replay_refusal(self):
        self.paper.faucet(self.addr)
        req = self.paper.sign_request('paper_predict', self.addr,
                                      asset='BTC',
                                      price_e6=str(100 * M),
                                      stake=str(5 * M),
                                      round=str(self.paper.current_index()))
        sig = self.sign(req['message'])
        out = self.paper.predict(self.addr, 'BTC', 100.0, 5.0,
                                 signature=sig)
        self.assertNotIn('error', out)
        self.assertTrue(out['signed'])
        # The same signature again: the nonce has moved on.
        out = self.paper.predict(self.addr, 'ETH', 100.0, 5.0,
                                 signature=sig)
        self.assertIn('error', out)

    def test_wrong_signature_refused(self):
        self.paper.faucet(self.addr)
        out = self.paper.predict(self.addr, 'BTC', 100.0, 5.0,
                                 signature=self.sign('something else'))
        self.assertIn('error', out)

    def test_closed_deployment_requires_signatures(self):
        shutil.rmtree(self.dir)
        closed = Paper(self.dir, markets=lambda: [{'symbol': 'BTC'}],
                       config={'open': False},
                       price_at=lambda s, t: {'price': 1.0})
        out = closed.faucet(self.addr)
        self.assertIn('error', out)
        req = closed.sign_request('paper_faucet', self.addr)
        out = closed.faucet(self.addr, signature=self.sign(req['message']))
        self.assertNotIn('error', out)

    def test_unsigned_play_is_flagged_not_faked(self):
        self.paper.faucet(self.addr)
        out = self.paper.predict(self.addr, 'BTC', 100.0, 5.0)
        self.assertNotIn('error', out)
        self.assertFalse(out['signed'])


if __name__ == '__main__':
    unittest.main()
