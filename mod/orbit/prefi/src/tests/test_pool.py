"""Stake-pool test suite — settlement math, deposits, signatures, solvency.

Hermetic: the chain is a fake that returns canned receipts and logs, so nothing
here touches HyperEVM, Hyperliquid or the live ~/.mod/prefi ledger. The one
thing that is real is the cryptography — signature tests sign with a genuine
eth_account key, because a signature check that is only tested against a mock
is not tested at all.

    python3 -m pytest tests/ -q        # from src/
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import hyperevm
import pool as pool_mod


def F(model, tol):
    """A score-function snapshot — what the settlement helpers now take."""
    return pool_mod.fn_for(model, tol)

from mod import Mod
from pool import (Pool, settle_asset, settle_free, shadow_payout, split_pot,
                  accuracy, usd_to_units, units_to_usd)

ALICE = '0xaaaa000000000000000000000000000000000001'
BOB = '0xbbbb000000000000000000000000000000000002'
CAROL = '0xcccc000000000000000000000000000000000003'
USDC = '0xb88339cb7199b77e23db6e890353e22632ba630f'
VAULT = '0x1234000000000000000000000000000000005678'


# ── A chain that never leaves the process ────────────────────────────

class FakeChain:
    """Enough of HyperEVM to exercise every path that touches money."""

    chain = {'name': 'FakeEVM', 'explorer': 'https://example.invalid',
             'currency': 'HYPE', 'rpc': 'fake://', 'testnet': True}
    chain_id = 999
    rpc_url = 'fake://'

    def __init__(self):
        self.receipts = {}          # tx hash → list of transfer dicts
        self.logs = []              # transfers discoverable by scanning
        self.head = 1_000_000
        self.sent = []
        self.fail_send = None
        self.balances = {USDC: 10_000_000_000}

    # reads
    def block_number(self):
        return self.head

    def erc20_meta(self, token):
        return {'address': token, 'symbol': 'USDC', 'name': 'USD Coin', 'decimals': 6}

    def erc20_balance(self, token, owner):
        return self.balances.get(hyperevm.normalize(token), 0)

    def native_balance(self, owner):
        return 10 ** 18

    def transfers_in_tx(self, tx_hash, to):
        if tx_hash not in self.receipts:
            return {'confirmed': False, 'reason': 'not mined yet', 'transfers': []}
        want = hyperevm.normalize(to)
        return {'confirmed': True, 'block': self.head,
                'transfers': [t for t in self.receipts[tx_hash]
                              if hyperevm.normalize(t['to']) == want]}

    def scan_transfers(self, token, to, from_block, to_block=None, max_chunks=20):
        head = to_block if to_block is not None else self.head
        want = hyperevm.normalize(to)
        found = [t for t in self.logs
                 if hyperevm.normalize(t['token']) == hyperevm.normalize(token)
                 and hyperevm.normalize(t['to']) == want
                 and from_block <= t['block'] <= head]
        return {'transfers': found, 'cursor': head + 1, 'head': head,
                'done': True, 'chunks': 1, 'blocks_behind': 0}

    # writes
    def send_erc20(self, key, token, to, units):
        if self.fail_send:
            raise RuntimeError(self.fail_send)
        tx = '0x' + f'{len(self.sent) + 1:064x}'
        self.sent.append({'tx': tx, 'token': token, 'to': to, 'units': units})
        return {'tx': tx, 'from': VAULT, 'to': to, 'units': units}

    def tx_url(self, tx):
        return f"{self.chain['explorer']}/tx/{tx}"

    def address_url(self, addr):
        return f"{self.chain['explorer']}/address/{addr}"

    # helpers the tests drive it with
    def deposit_tx(self, tx, sender, amount, token=USDC, to=VAULT, log_index=0):
        transfer = {'token': token, 'from': sender, 'to': to,
                    'units': int(amount * 10 ** 6), 'tx': tx,
                    'log_index': log_index, 'block': self.head}
        self.receipts.setdefault(tx, []).append(transfer)
        return transfer

    def broadcast(self, tx, sender, amount, block=None, token=USDC, to=VAULT):
        """A deposit that only the log scanner will find. Placed behind the tip
        because the scanner deliberately stops short of it."""
        self.logs.append({'token': token, 'from': sender, 'to': to,
                          'units': int(amount * 10 ** 6), 'tx': tx,
                          'log_index': 0, 'block': block or (self.head - 100)})


# ── Pure math ────────────────────────────────────────────────────────

class TestSplitPot(unittest.TestCase):
    """The pot must come out to the last micro-dollar."""

    def test_exact_conservation(self):
        for scores in ([1, 1, 1], [7, 3], [1e-9, 1, 500], [0.1] * 7, [2, 2, 2, 1]):
            for pot in (1, 999_999, 100_000_000, 3):
                parts = split_pot(pot, scores)
                self.assertEqual(sum(parts), pot,
                                 f'{scores} @ {pot} lost or invented units')
                self.assertTrue(all(p >= 0 for p in parts))

    def test_proportional(self):
        parts = split_pot(1_000_000, [3, 1])
        self.assertEqual(parts, [750_000, 250_000])

    def test_zero_scores_pay_nothing(self):
        self.assertEqual(split_pot(1_000_000, [0, 0]), [0, 0])

    def test_remainder_goes_to_the_closest(self):
        # 10 units, equal thirds — someone has to get the odd unit, and it is
        # never nobody.
        parts = split_pot(10, [1, 1, 1])
        self.assertEqual(sum(parts), 10)
        self.assertEqual(sorted(parts), [3, 3, 4])


class TestAccuracy(unittest.TestCase):
    """linear @ tolerance 1.0 is a pure relative-L1 score: a = 1 − e."""

    def test_perfect_call(self):
        self.assertEqual(accuracy(100.0, 100.0, F('linear', 1.0))['accuracy'], 1.0)

    def test_is_one_minus_relative_error(self):
        got = accuracy(90.0, 100.0, F('linear', 1.0))
        self.assertAlmostEqual(got['rel_error'], 0.1)
        self.assertAlmostEqual(got['accuracy'], 0.9)

    def test_direction_does_not_matter(self):
        self.assertAlmostEqual(accuracy(110.0, 100.0, F('linear', 1.0))['accuracy'],
                               accuracy(90.0, 100.0, F('linear', 1.0))['accuracy'])

    def test_double_is_worthless(self):
        self.assertEqual(accuracy(200.0, 100.0, F('linear', 1.0))['accuracy'], 0.0)

    def test_tolerance_sharpens_it(self):
        loose = accuracy(102.0, 100.0, F('linear', 1.0))['accuracy']
        tight = accuracy(102.0, 100.0, F('linear', 0.05))['accuracy']
        self.assertGreater(loose, tight)


class TestSettleAsset(unittest.TestCase):

    def _entries(self, *pairs):
        return [{'id': i + 1, 'address': a, 'amount': amt, 'predicted_price': px}
                for i, (a, amt, px) in enumerate(pairs)]

    def test_dollars_times_accuracy(self):
        # Alice: $100 at 1% off → score 99. Bob: $100 at 10% off → score 90.
        out = settle_asset(self._entries((ALICE, 100.0, 101.0), (BOB, 100.0, 110.0)),
                           100.0, F('linear', 1.0), 0)
        alice, bob = out['entries']
        self.assertAlmostEqual(alice['score'], 99.0)
        self.assertAlmostEqual(bob['score'], 90.0)
        self.assertAlmostEqual(alice['payout'], 200 * 99 / 189, places=4)
        self.assertAlmostEqual(alice['payout'] + bob['payout'], 200.0, places=6)
        self.assertEqual(out['winner']['address'], ALICE)

    def test_more_dollars_wins_a_tie_on_accuracy(self):
        out = settle_asset(self._entries((ALICE, 300.0, 101.0), (BOB, 100.0, 101.0)),
                           100.0, F('linear', 1.0), 0)
        alice, bob = out['entries']
        self.assertAlmostEqual(alice['payout'], 300.0, places=4)
        self.assertAlmostEqual(bob['payout'], 100.0, places=4)
        self.assertEqual(out['winner']['address'], ALICE)

    def test_accuracy_beats_size(self):
        # $10 dead on vs $1000 half wrong.
        out = settle_asset(self._entries((ALICE, 10.0, 100.0), (BOB, 1000.0, 150.0)),
                           100.0, F('linear', 1.0), 0)
        alice, bob = out['entries']
        self.assertAlmostEqual(alice['score'], 10.0)
        self.assertAlmostEqual(bob['score'], 500.0)
        self.assertGreater(bob['payout'], alice['payout'])   # size still counts
        self.assertGreater(alice['payout'], alice['amount'])  # but she profits
        self.assertLess(bob['net'], 0)

    def test_sole_correct_caller_takes_the_pot(self):
        out = settle_asset(self._entries((ALICE, 50.0, 100.0), (BOB, 150.0, 300.0)),
                           100.0, F('linear', 1.0), 0)
        alice, bob = out['entries']
        self.assertAlmostEqual(alice['payout'], 200.0, places=4)
        self.assertEqual(bob['payout'], 0.0)
        self.assertAlmostEqual(alice['net'], 150.0, places=4)

    def test_everyone_wrong_is_a_refund_not_a_confiscation(self):
        out = settle_asset(self._entries((ALICE, 100.0, 500.0), (BOB, 50.0, 900.0)),
                           100.0, F('threshold', 0.01), 300)
        self.assertEqual(out['mode'], 'refund')
        self.assertEqual(out['fee'], 0.0)
        self.assertEqual([e['payout'] for e in out['entries']], [100.0, 50.0])

    def test_fee_comes_off_the_pot(self):
        out = settle_asset(self._entries((ALICE, 100.0, 100.0)), 100.0,
                           F('linear', 1.0), 250)          # 2.5%
        self.assertAlmostEqual(out['fee'], 2.5)
        self.assertAlmostEqual(out['pot'], 97.5)
        self.assertAlmostEqual(out['entries'][0]['payout'], 97.5)
        self.assertAlmostEqual(out['gross'], out['pot'] + out['fee'])

    def test_pot_is_conserved_to_the_unit(self):
        entries = self._entries((ALICE, 33.33, 101.0), (BOB, 66.67, 99.5),
                                (CAROL, 0.01, 100.2))
        out = settle_asset(entries, 100.0, F('l2', 0.02), 0)
        paid = sum(usd_to_units(e['payout']) for e in out['entries'])
        self.assertEqual(paid, usd_to_units(out['pot']))


class TestShadowPayout(unittest.TestCase):
    """The counterfactual a free call is scored against."""

    def _entries(self, *pairs):
        return [{'id': i + 1, 'address': a, 'amount': amt, 'predicted_price': px}
                for i, (a, amt, px) in enumerate(pairs)]

    def _paid(self, entries, actual, fn=None):
        fn = fn or F('linear', 1.0)
        return (sum(float(e['amount']) * accuracy(e['predicted_price'], actual,
                                                  fn)['accuracy']
                    for e in entries),
                sum(float(e['amount']) for e in entries))

    def test_matches_an_actual_settlement_to_the_micro_dollar(self):
        """The number shown to a free player has to be the number they would
        have been paid — so it is checked against a real settlement run."""
        paid = self._entries((ALICE, 100.0, 101_000.0), (BOB, 250.0, 97_000.0))
        actual, notional = 100_000.0, 100.0
        for model, tol, fee in (('linear', 1.0, 0), ('l2', 0.05, 250),
                                ('exponential', 0.02, 100), ('linear', 1.0, 500),
                                ('cushion', 0.1, 0), ('hinge', 0.05, 250)):
            fn = F(model, tol)
            score, gross = self._paid(paid, actual, fn)
            shadow = shadow_payout(99_500.0, actual, fn, fee, notional,
                                   score, gross)
            real = settle_asset(
                paid + [{'id': 99, 'address': CAROL, 'amount': notional,
                         'predicted_price': 99_500.0}],
                actual, fn, fee)
            carol = next(e for e in real['entries'] if e['id'] == 99)
            self.assertAlmostEqual(shadow['would_win'], carol['payout'], places=5,
                                   msg=f'{model}/{tol}/{fee} counterfactual drifted')

    def test_the_free_caller_funds_their_own_counterfactual(self):
        # Alone in an empty pot, you get your paper stake back, less the fee —
        # not a windfall out of a pot nobody funded.
        shadow = shadow_payout(100_000.0, 100_000.0, F('linear', 1.0), 0, 100.0)
        self.assertAlmostEqual(shadow['would_win'], 100.0, places=5)
        self.assertAlmostEqual(shadow['would_net'], 0.0, places=5)

    def test_a_better_call_would_have_taken_the_pot(self):
        paid = self._entries((ALICE, 100.0, 110_000.0))       # 10% off
        score, gross = self._paid(paid, 100_000.0)
        shadow = shadow_payout(100_000.0, 100_000.0, F('linear', 1.0), 0, 100.0,
                               score, gross)
        self.assertGreater(shadow['would_net'], 0)
        self.assertLess(shadow['would_win'], 200.0)           # Alice keeps a share

    def test_missing_everything_is_a_refund_not_a_loss(self):
        shadow = shadow_payout(500_000.0, 100_000.0, F('threshold', 0.01), 300, 100.0)
        self.assertEqual(shadow['would_mode'], 'refund')
        self.assertEqual(shadow['would_win'], 100.0)
        self.assertEqual(shadow['would_net'], 0.0)

    def test_free_calls_are_priced_against_the_pot_not_each_other(self):
        paid = self._entries((ALICE, 100.0, 100_000.0))
        free = [{'id': 10, 'address': BOB, 'predicted_price': 100_000.0,
                 'amount': 0.0, 'free': True, 'notional': 100.0},
                {'id': 11, 'address': CAROL, 'predicted_price': 100_000.0,
                 'amount': 0.0, 'free': True, 'notional': 100.0}]
        out = settle_free(free, paid, 100_000.0, F('linear', 1.0), 0)
        # Two identical free calls each see the same $200 two-way split. Neither
        # dilutes the other, because neither is in the pot.
        self.assertAlmostEqual(out[0]['would_win'], 100.0, places=5)
        self.assertAlmostEqual(out[1]['would_win'], 100.0, places=5)


class TestConfigValidation(unittest.TestCase):

    def test_weekly_is_the_default(self):
        self.assertEqual(pool_mod.DEFAULT_CONFIG['interval'], 604800)

    def test_rejects_absurd_intervals(self):
        for bad in (60, 0, 10 ** 12):
            with self.assertRaises(ValueError):
                pool_mod.validate_config({'interval': bad})

    def test_cutoff_must_fit_inside_the_round(self):
        with self.assertRaises(ValueError):
            pool_mod.validate_config({'interval': 7200, 'entry_cutoff': 7200})

    def test_fee_is_capped(self):
        with self.assertRaises(ValueError):
            pool_mod.validate_config({'fee_bps': 5000})
        self.assertEqual(pool_mod.validate_config({'fee_bps': 500})['fee_bps'], 500)

    def test_unknown_model_refused(self):
        with self.assertRaises(ValueError):
            pool_mod.validate_config({'model': 'vibes'})

    def test_free_play_is_on_by_default(self):
        self.assertGreater(pool_mod.DEFAULT_CONFIG['free_per_round'], 0)

    def test_free_calls_are_bounded(self):
        with self.assertRaises(ValueError):
            pool_mod.validate_config({'free_per_round': 500})
        with self.assertRaises(ValueError):
            pool_mod.validate_config({'free_per_round': -1})
        self.assertEqual(pool_mod.validate_config({'free_per_round': 0})['free_per_round'], 0)

    def test_notional_must_be_a_real_stake(self):
        with self.assertRaises(ValueError):
            pool_mod.validate_config({'free_notional': 0})


# ── The pool, end to end ─────────────────────────────────────────────

class PoolTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='prefi_pool_')
        os.environ['PREFI_UNSAFE_NO_SIG'] = '1'
        self.chain = FakeChain()
        self.prices = {'BTC': 100_000.0}
        self.markets = [{'symbol': 'BTC', 'token': 'BTC', 'source': 'hyperliquid',
                         'active': True},
                        {'symbol': 'WETH', 'token': '0xweth', 'source': 'coingecko',
                         'active': True},
                        {'symbol': 'SN64', 'token': 'bt:64', 'source': 'bittensor',
                         'bt_netuid': 64, 'quote': 'TAO', 'active': True}]
        self.prices['SN64'] = 0.085
        self.fees = []
        # Every price lookup records which feed it was asked for, so a test
        # can assert a subnet pot never settles against Hyperliquid.
        self.price_calls = []

        def price_at(sym, ts, src=None):
            self.price_calls.append(('at', sym, src))
            return {'price': self.prices.get(sym), 'mode': 'historical'}

        def price_now(sym, src=None):
            self.price_calls.append(('now', sym, src))
            return self.prices.get(sym)

        self.pool = Pool(
            self.tmp,
            price_at=price_at,
            price_now=price_now,
            markets=lambda: self.markets,
            on_fee=self.fees.append,
        )
        self.patcher = patch.object(Pool, 'chain', lambda _self: self.chain)
        self.patcher.start()
        self.pool.set_vault(VAULT)

    def tearDown(self):
        self.patcher.stop()
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fund(self, address, amount, tx=None):
        tx = tx or '0x' + os.urandom(32).hex()
        self.chain.deposit_tx(tx, address, amount)
        return self.pool.deposit(tx)

    def rewind(self, seconds):
        """Move the schedule back so the current round has already closed."""
        st = self.pool.state()
        st['schedule']['anchor'] -= seconds
        for record in self.pool._rounds():
            pass
        self.pool._save_state(st)


class TestDeposits(PoolTestBase):

    def test_credits_a_transfer_into_the_vault(self):
        out = self.fund(ALICE, 250.0)
        self.assertEqual(out['total'], 250.0)
        self.assertEqual(self.pool.balance(ALICE)['available'], 250.0)

    def test_same_tx_twice_credits_once(self):
        tx = '0x' + 'ab' * 32
        self.chain.deposit_tx(tx, ALICE, 100.0)
        self.assertEqual(self.pool.deposit(tx)['total'], 100.0)
        again = self.pool.deposit(tx)
        self.assertEqual(again['credited'], [])
        self.assertIn('already credited', str(again))
        self.assertEqual(self.pool.balance(ALICE)['available'], 100.0)

    def test_scan_does_not_double_credit_a_hashed_deposit(self):
        tx = '0x' + 'cd' * 32
        self.chain.deposit_tx(tx, ALICE, 40.0)
        self.chain.broadcast(tx, ALICE, 40.0)          # the same transfer, again
        self.pool.deposit(tx)
        self.pool.sync()
        self.assertEqual(self.pool.balance(ALICE)['available'], 40.0)

    def test_scan_finds_a_deposit_nobody_reported(self):
        self.chain.broadcast('0x' + 'ef' * 32, BOB, 75.5)
        out = self.pool.sync()
        self.assertEqual(out['total'], 75.5)
        self.assertEqual(self.pool.balance(BOB)['available'], 75.5)

    def test_unmined_hash_is_a_retry_not_a_failure(self):
        out = self.pool.deposit('0x' + '11' * 32)
        self.assertTrue(out.get('retry'))

    def test_transfer_to_someone_else_is_not_a_deposit(self):
        self.chain.deposit_tx('0x' + '22' * 32, ALICE, 500.0, to=BOB)
        out = self.pool.deposit('0x' + '22' * 32)
        self.assertIn('no supported token', out['error'])
        self.assertEqual(self.pool.balance(ALICE)['available'], 0.0)

    def test_unsupported_token_is_refused(self):
        self.chain.deposit_tx('0x' + '33' * 32, ALICE, 10.0,
                              token='0xdead00000000000000000000000000000000dead')
        out = self.pool.deposit('0x' + '33' * 32)
        self.assertEqual(out['credited'], [])
        self.assertIn('unsupported token', str(out['skipped']))

    def test_malformed_hash_rejected(self):
        self.assertIn('error', self.pool.deposit('nope'))


class TestStaking(PoolTestBase):

    def test_stake_moves_dollars_into_the_pot(self):
        self.fund(ALICE, 500.0)
        out = self.pool.stake(ALICE, 'BTC', 105_000, 200.0)
        self.assertEqual(out['staked'], 200.0)
        bal = self.pool.balance(ALICE)
        self.assertEqual(bal['available'], 300.0)
        self.assertEqual(bal['at_stake'], 200.0)

    def test_cannot_stake_money_you_do_not_have(self):
        self.fund(ALICE, 10.0)
        out = self.pool.stake(ALICE, 'BTC', 105_000, 50.0)
        self.assertIn('insufficient balance', out['error'])

    def test_minimum_stake_enforced(self):
        self.fund(ALICE, 100.0)
        self.assertIn('minimum stake', self.pool.stake(ALICE, 'BTC', 1.0, 1.0)['error'])

    def test_maximum_stake_enforced_when_set(self):
        self.pool.set_config(max_stake=100.0)
        self.fund(ALICE, 500.0)
        self.assertIn('maximum stake',
                      self.pool.stake(ALICE, 'BTC', 105_000, 200.0)['error'])

    def test_only_hyperliquid_priced_markets(self):
        self.fund(ALICE, 100.0)
        out = self.pool.stake(ALICE, 'WETH', 3000, 50.0)
        self.assertIn('Hyperliquid', out['error'])

    def test_a_bittensor_subnet_is_stakeable_in_tao(self):
        self.fund(ALICE, 100.0)
        out = self.pool.stake(ALICE, 'SN64', 0.09, 50.0)
        self.assertNotIn('error', out)
        self.assertEqual(out['quote'], 'TAO')
        self.assertEqual(out['mark_at_entry'], 0.085)
        self.assertIn(('now', 'SN64', 'bittensor'), self.price_calls)
        self.assertNotIn(('now', 'SN64', 'hyperliquid'), self.price_calls)

    def test_unknown_market_refused(self):
        self.fund(ALICE, 100.0)
        self.assertIn('no market', self.pool.stake(ALICE, 'DOGE', 1.0, 50.0)['error'])

    def test_negative_price_refused(self):
        self.fund(ALICE, 100.0)
        self.assertIn('positive', self.pool.stake(ALICE, 'BTC', -1, 50.0)['error'])

    def test_entries_close_before_the_round_does(self):
        self.fund(ALICE, 100.0)
        st = self.pool.state()
        # Wind the anchor forward so we sit inside the cutoff window.
        st['schedule']['anchor'] -= st['schedule']['interval'] - 60
        self.pool._save_state(st)
        out = self.pool.stake(ALICE, 'BTC', 105_000, 50.0)
        self.assertIn('closed', out['error'])

    def test_bad_address_refused(self):
        self.assertIn('error', self.pool.stake('alice', 'BTC', 1, 10.0))


class TestSettlement(PoolTestBase):

    def _closed_round_with(self, *stakes, rewind_extra=0, asset='BTC'):
        for addr, amount, price in stakes:
            self.fund(addr, amount)
            out = self.pool.stake(addr, asset, price, amount)
            self.assertNotIn('error', out)
        st = self.pool.state()
        st['schedule']['anchor'] -= st['schedule']['interval'] + 10 + rewind_extra
        self.pool._save_state(st)
        rounds = self.pool._rounds()
        for record in rounds:
            window = self.pool.window(record['index'])
            record.update({'opens': window['opens'], 'closes': window['closes'],
                           'entry_deadline': window['entry_deadline']})
        self.pool._write(self.pool.rounds_path, rounds)

    def test_pot_is_split_by_dollars_times_accuracy(self):
        self.prices['BTC'] = 100_000.0
        self._closed_round_with((ALICE, 100.0, 101_000.0),    # 1% off
                                (BOB, 100.0, 110_000.0))      # 10% off
        out = self.pool.settle()
        self.assertEqual(len(out['settled']), 1)
        self.assertEqual(out['settled'][0]['winner']['address'], ALICE)
        alice = self.pool.balance(ALICE)['available']
        bob = self.pool.balance(BOB)['available']
        self.assertAlmostEqual(alice + bob, 200.0, places=5)
        self.assertGreater(alice, 100.0)
        self.assertLess(bob, 100.0)

    def test_a_subnet_pot_settles_against_the_bittensor_feed_in_tao(self):
        self.prices['SN64'] = 0.085
        self._closed_round_with((ALICE, 100.0, 0.086),    # ~1% off
                                (BOB, 100.0, 0.100),      # ~18% off
                                asset='SN64')
        out = self.pool.settle()
        self.assertEqual(len(out['settled']), 1)
        pot = out['settled'][0]
        self.assertEqual(pot['winner']['address'], ALICE)
        self.assertIn(('at', 'SN64', 'bittensor'), self.price_calls)
        self.assertFalse([c for c in self.price_calls if c[1] == 'SN64' and c[2] == 'hyperliquid'])
        record = self.pool.rounds(limit=5)[0]
        self.assertEqual(record['assets']['SN64']['quote'], 'TAO')
        self.assertEqual(record['assets']['SN64']['actual_price'], 0.085)
        # Balances still move in dollars — the quote unit only scores accuracy.
        alice = self.pool.balance(ALICE)['available']
        bob = self.pool.balance(BOB)['available']
        self.assertAlmostEqual(alice + bob, 200.0, places=5)
        self.assertGreater(alice, bob)

    def test_each_asset_gets_its_own_pot(self):
        self.markets.append({'symbol': 'HYPE', 'token': 'HYPE',
                             'source': 'hyperliquid', 'active': True})
        self.prices.update({'BTC': 100_000.0, 'HYPE': 40.0})
        self.fund(ALICE, 100.0)
        self.fund(BOB, 100.0)
        self.pool.stake(ALICE, 'BTC', 100_000.0, 100.0)   # perfect
        self.pool.stake(BOB, 'HYPE', 80.0, 100.0)         # 100% off
        st = self.pool.state()
        st['schedule']['anchor'] -= st['schedule']['interval'] + 10
        self.pool._save_state(st)
        rounds = self.pool._rounds()
        for record in rounds:
            record.update(self.pool.window(record['index']))
        self.pool._write(self.pool.rounds_path, rounds)

        self.pool.settle()
        # Alice cannot reach into the HYPE pot with a perfect BTC call, and
        # Bob's hopeless HYPE call is refunded from its own pot, not hers.
        self.assertAlmostEqual(self.pool.balance(ALICE)['available'], 100.0, places=5)
        self.assertAlmostEqual(self.pool.balance(BOB)['available'], 100.0, places=5)

    def test_no_winner_refunds_everyone(self):
        self.pool.set_config(model='threshold', tolerance=0.001)
        self.prices['BTC'] = 100_000.0
        self._closed_round_with((ALICE, 100.0, 50_000.0), (BOB, 60.0, 300_000.0))
        self.pool.settle()
        self.assertEqual(self.pool.balance(ALICE)['available'], 100.0)
        self.assertEqual(self.pool.balance(BOB)['available'], 60.0)

    def test_fee_reaches_the_treasury_hook(self):
        self.pool.set_config(fee_bps=200)
        self.prices['BTC'] = 100_000.0
        self._closed_round_with((ALICE, 100.0, 100_000.0))
        self.pool.settle()
        self.assertAlmostEqual(sum(self.fees), 2.0, places=6)
        self.assertAlmostEqual(self.pool.balance(ALICE)['available'], 98.0, places=5)

    def test_ledger_conserves_every_dollar(self):
        self.prices['BTC'] = 100_000.0
        self._closed_round_with((ALICE, 120.0, 99_000.0), (BOB, 80.0, 101_500.0),
                                (CAROL, 33.33, 100_020.0))
        self.pool.settle()
        credited = self.pool.total_credited()
        fees = sum(self.fees)
        deposited = 120.0 + 80.0 + 33.33
        self.assertAlmostEqual(credited + fees, deposited, places=5)

    def test_settling_twice_pays_once(self):
        self.prices['BTC'] = 100_000.0
        self._closed_round_with((ALICE, 100.0, 100_000.0))
        self.pool.settle()
        first = self.pool.balance(ALICE)['available']
        self.pool.settle()
        self.assertEqual(self.pool.balance(ALICE)['available'], first)

    def test_unpriceable_round_waits_rather_than_guessing(self):
        self._closed_round_with((ALICE, 100.0, 100_000.0))
        self.prices['BTC'] = None
        out = self.pool.settle()
        self.assertEqual(out['settled'], [])
        self.assertEqual(len(out['waiting']), 1)
        self.assertEqual(self.pool.balance(ALICE)['at_stake'], 100.0)

    def test_fresh_spot_settles_inside_the_grace_window(self):
        self.pool._price_at = lambda sym, ts, src=None: {'price': 100_000.0,
                                                         'mode': 'spot'}
        self._closed_round_with((ALICE, 100.0, 100_000.0))
        out = self.pool.settle()
        self.assertEqual(len(out['settled']), 1)

    def test_stale_spot_is_refused_as_a_settlement_price(self):
        self.pool._price_at = lambda sym, ts, src=None: {'price': 100_000.0,
                                                         'mode': 'spot'}
        self._closed_round_with((ALICE, 100.0, 100_000.0), rewind_extra=3600)
        out = self.pool.settle()
        self.assertEqual(out['settled'], [])
        self.assertIn('stale-spot', str(out['waiting']))

    def test_owner_can_settle_a_stuck_pot_by_hand(self):
        self.pool._price_at = lambda sym, ts, src=None: {'price': None, 'mode': 'none'}
        self._closed_round_with((ALICE, 100.0, 100_000.0))
        self.pool.settle()
        index = self.pool._rounds()[0]['index']
        out = self.pool.settle_manual(index, 'BTC', 100_000.0)
        self.assertEqual(out['price_mode'], 'manual')
        self.assertAlmostEqual(self.pool.balance(ALICE)['available'], 100.0, places=5)


class TestFreePlay(PoolTestBase):
    """A free call is scored like a stake and touches no money at all."""

    def _close_round(self):
        st = self.pool.state()
        st['schedule']['anchor'] -= st['schedule']['interval'] + 10
        self.pool._save_state(st)
        rounds = self.pool._rounds()
        for record in rounds:
            record.update(self.pool.window(record['index']))
        self.pool._write(self.pool.rounds_path, rounds)

    def test_a_free_call_needs_no_balance(self):
        out = self.pool.free_stake(ALICE, 'BTC', 101_000.0)
        self.assertNotIn('error', out)
        self.assertTrue(out['free'])
        self.assertEqual(out['staked'], 0.0)
        self.assertEqual(self.pool.balance(ALICE)['available'], 0.0)

    def test_it_writes_nothing_to_the_ledger(self):
        self.pool.free_stake(ALICE, 'BTC', 101_000.0)
        self.assertEqual(self.pool.ledger(ALICE), [])
        self.assertEqual(self.pool.liabilities()['total'], 0.0)

    def test_quota_runs_out_per_round(self):
        self.pool.set_config(free_per_round=2)
        self.markets.append({'symbol': 'ETH', 'token': 'ETH',
                             'source': 'hyperliquid', 'active': True})
        self.markets.append({'symbol': 'SOL', 'token': 'SOL',
                             'source': 'hyperliquid', 'active': True})
        self.prices.update({'ETH': 3000.0, 'SOL': 200.0})
        self.assertNotIn('error', self.pool.free_stake(ALICE, 'BTC', 101_000.0))
        self.assertNotIn('error', self.pool.free_stake(ALICE, 'ETH', 3_100.0))
        self.assertIn('out of free calls',
                      self.pool.free_stake(ALICE, 'SOL', 210.0)['error'])
        self.assertEqual(self.pool.free_quota(ALICE)['remaining'], 0)
        # Someone else's allowance is their own.
        self.assertEqual(self.pool.free_quota(BOB)['remaining'], 2)

    def test_one_call_per_asset_so_the_board_means_something(self):
        self.pool.free_stake(ALICE, 'BTC', 101_000.0)
        out = self.pool.free_stake(ALICE, 'BTC', 99_000.0)
        self.assertIn('already have a free call', out['error'])

    def test_the_owner_can_switch_it_off(self):
        self.pool.set_config(free_per_round=0)
        self.assertIn('switched off', self.pool.free_stake(ALICE, 'BTC', 1.0)['error'])
        self.assertFalse(self.pool.free_quota(ALICE)['enabled'])

    def test_free_calls_close_with_the_entries(self):
        st = self.pool.state()
        st['schedule']['anchor'] -= st['schedule']['interval'] - 60
        self.pool._save_state(st)
        self.assertIn('closed', self.pool.free_stake(ALICE, 'BTC', 101_000.0)['error'])

    def test_a_free_call_cannot_dilute_the_pot(self):
        """The load-bearing test: identical paid and free calls, and the paid
        staker takes the whole pot regardless."""
        self.prices['BTC'] = 100_000.0
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 100_000.0, 100.0)
        for addr in (BOB, CAROL):
            self.pool.free_stake(addr, 'BTC', 100_000.0)
        self._close_round()
        self.pool.settle()

        self.assertAlmostEqual(self.pool.balance(ALICE)['available'], 100.0, places=5)
        self.assertEqual(self.pool.balance(BOB)['available'], 0.0)
        self.assertEqual(self.pool.balance(CAROL)['available'], 0.0)
        pot = self.pool._rounds()[0]['assets']['BTC']
        self.assertEqual(pot['gross'], 100.0)          # only Alice's dollars
        self.assertEqual(pot['entries'], 1)
        self.assertEqual(pot['free_entries'], 2)
        self.assertEqual(pot['winner']['address'], ALICE)

    def test_a_settled_free_call_reports_what_it_would_have_won(self):
        self.prices['BTC'] = 100_000.0
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 110_000.0, 100.0)     # 10% off
        self.pool.free_stake(BOB, 'BTC', 100_000.0)         # dead on
        self._close_round()
        self.pool.settle()

        bob = self.pool.entries(BOB)[0]
        self.assertEqual(bob['status'], 'settled')
        self.assertEqual(bob['payout'], 0.0)
        self.assertEqual(bob['accuracy'], 1.0)
        self.assertGreater(bob['would_win'], bob['notional'])
        self.assertAlmostEqual(bob['would_net'],
                               bob['would_win'] - bob['notional'], places=5)

    def test_the_notional_is_snapshotted_at_placement(self):
        self.pool.set_config(free_notional=50.0)
        self.pool.free_stake(ALICE, 'BTC', 101_000.0)
        self.pool.set_config(free_notional=5000.0)          # retune after the fact
        self.assertEqual(self.pool.entries(ALICE)[0]['notional'], 50.0)

    def test_a_free_only_pot_settles_without_paying_anyone(self):
        self.prices['BTC'] = 100_000.0
        self.pool.free_stake(ALICE, 'BTC', 100_000.0)
        self._close_round()
        out = self.pool.settle()
        self.assertEqual(out['paid'], 0.0)
        self.assertEqual(self.pool._rounds()[0]['assets']['BTC']['mode'], 'free-only')
        self.assertEqual(self.pool.entries(ALICE)[0]['accuracy'], 1.0)

    def test_manual_settlement_scores_free_calls_too(self):
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 101_000.0, 100.0)
        self.pool.free_stake(BOB, 'BTC', 100_000.0)
        self._close_round()
        self.prices['BTC'] = None                       # oracle cannot answer
        self.pool.settle()
        self.assertEqual(self.pool.entries(BOB)[0]['status'], 'open')
        self.pool.settle_manual(self.pool._rounds()[0]['index'], 'BTC', 100_000.0)
        bob = self.pool.entries(BOB)[0]
        self.assertEqual(bob['status'], 'settled')
        self.assertEqual(bob['accuracy'], 1.0)
        self.assertEqual(bob['payout'], 0.0)

    def test_the_round_view_lists_them_apart_from_the_pot(self):
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 101_000.0, 100.0)
        self.pool.free_stake(BOB, 'BTC', 100_500.0)
        pot = self.pool.round()['assets'][0]
        self.assertEqual(pot['stakers'], 1)
        self.assertEqual(pot['free_callers'], 1)
        self.assertEqual(len(pot['entries']), 1)
        self.assertEqual(pot['entries'][0]['address'], ALICE)
        self.assertEqual(pot['gross'], 100.0)
        self.assertEqual(len(pot['free']), 1)
        self.assertIsNotNone(pot['free'][0]['would_win'])

    def test_free_players_get_their_own_board(self):
        self.prices['BTC'] = 100_000.0
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 101_000.0, 100.0)
        self.pool.free_stake(BOB, 'BTC', 100_000.0)       # perfect
        self.pool.free_stake(CAROL, 'BTC', 130_000.0)     # miles off
        self._close_round()
        self.pool.settle()

        board = self.pool.free_leaderboard()
        self.assertEqual([r['address'] for r in board], [BOB, CAROL])
        self.assertEqual(board[0]['avg_accuracy'], 1.0)
        self.assertGreater(board[0]['would_net'], 0)
        self.assertFalse(board[0]['staker'])
        # and they are not on the money board, which ranks dollars
        self.assertEqual([r['address'] for r in self.pool.leaderboard()], [ALICE])

    def test_stats_count_free_play_separately(self):
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 101_000.0, 100.0)
        self.pool.free_stake(BOB, 'BTC', 100_000.0)
        stats = self.pool.stats()
        self.assertEqual(stats['stakers'], 1)
        self.assertEqual(stats['entries_total'], 1)
        self.assertEqual(stats['free_calls'], 1)
        self.assertEqual(stats['free_callers'], 1)
        self.assertEqual(stats['at_stake'], 100.0)

    def test_free_play_works_on_a_spot_pair(self):
        """Spot symbols carry a slash, which travels through a signed message
        and a URL — so the pair a market is listed under has to survive both."""
        self.markets.append({'symbol': 'HYPE/USDC', 'token': 'hl:@107',
                             'source': 'hyperliquid', 'hl_key': '@107',
                             'hl_kind': 'spot', 'active': True})
        self.prices['HYPE/USDC'] = 81.0
        out = self.pool.free_stake(ALICE, 'HYPE/USDC', 85.0)
        self.assertEqual(out['asset'], 'HYPE/USDC')
        self.assertEqual(self.pool.free_quota(ALICE)['assets_used'], ['HYPE/USDC'])
        self.assertIn('asset: HYPE/USDC',
                      self.pool.sign_request('free_stake', ALICE, asset='HYPE/USDC',
                                             price='85.00000000', round='0')['message'])
        pot = next(a for a in self.pool.round()['assets'] if a['asset'] == 'HYPE/USDC')
        self.assertIsNotNone(pot['free'][0]['would_win'])

    def test_bad_input_refused(self):
        self.assertIn('error', self.pool.free_stake('alice', 'BTC', 1.0))
        self.assertIn('positive', self.pool.free_stake(ALICE, 'BTC', -1)['error'])
        self.assertIn('Hyperliquid', self.pool.free_stake(ALICE, 'WETH', 3000)['error'])
        self.assertIn('no market', self.pool.free_stake(ALICE, 'DOGE', 1.0)['error'])


class TestConfigMigration(PoolTestBase):
    """A pool older than a setting still has to be able to read its config."""

    def test_a_config_predating_free_play_still_loads(self):
        st = self.pool.state()
        st['config'].pop('free_per_round')
        st['config'].pop('free_notional')
        st['config']['min_stake'] = 25.0            # a value the owner did set
        self.pool._save_state(st)

        cfg = self.pool.config()
        self.assertEqual(cfg['free_per_round'], pool_mod.DEFAULT_CONFIG['free_per_round'])
        self.assertEqual(cfg['min_stake'], 25.0)    # not clobbered by the default
        self.assertNotIn('error', self.pool.free_stake(ALICE, 'BTC', 101_000.0))
        self.assertIn('free_calls', self.pool.stats())


class TestRoundSchedule(PoolTestBase):

    def test_default_round_is_a_week(self):
        window = self.pool.window()
        self.assertEqual(window['closes'] - window['opens'], 604800)

    def test_owner_changes_the_interval(self):
        out = self.pool.set_config(interval=86400)
        self.assertEqual(out['interval'], 86400)
        self.assertEqual(out['interval_days'], 1.0)

    def test_new_interval_starts_at_the_next_boundary(self):
        before = self.pool.window()
        self.pool.set_config(interval=86400)
        after = self.pool.window(before['index'])
        # The round already open keeps the length it was sold with.
        self.assertEqual(self.pool.state()['schedule']['anchor'], before['closes'])
        self.assertEqual(self.pool.state()['schedule']['anchor_index'],
                         before['index'] + 1)

    def test_round_numbers_never_go_backwards(self):
        first = self.pool.current_index()
        self.pool.set_config(interval=3600)
        self.assertGreaterEqual(self.pool.current_index(), first)

    def test_open_round_keeps_its_snapshotted_params(self):
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 100_000.0, 50.0)
        self.pool.set_config(tolerance=0.001, model='threshold')
        record = self.pool._rounds()[0]
        self.assertEqual(record['model'], 'linear')
        self.assertEqual(record['tolerance'], 1.0)


class TestWithdrawals(PoolTestBase):

    def test_queued_when_there_is_no_hot_key(self):
        self.fund(ALICE, 100.0)
        out = self.pool.withdraw(ALICE, 40.0, 'USDC')
        self.assertEqual(out['status'], 'pending')
        self.assertEqual(self.pool.balance(ALICE)['available'], 60.0)
        self.assertEqual(self.pool.balance(ALICE)['pending_withdrawal'], 40.0)

    def test_cannot_withdraw_staked_money(self):
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 100_000.0, 90.0)
        self.assertIn('insufficient', self.pool.withdraw(ALICE, 50.0, 'USDC')['error'])

    def test_minimum_withdrawal_enforced(self):
        self.fund(ALICE, 100.0)
        self.assertIn('minimum', self.pool.withdraw(ALICE, 0.01, 'USDC')['error'])

    def test_paying_it_sends_on_chain(self):
        self.fund(ALICE, 100.0)
        wid = self.pool.withdraw(ALICE, 25.0, 'USDC')['withdrawal_id']
        with patch.object(Pool, 'vault_key', lambda _self: '0x' + '11' * 32):
            out = self.pool.pay_withdrawal(wid)
        self.assertEqual(out['status'], 'sent')
        self.assertEqual(self.chain.sent[0]['units'], 25_000_000)
        self.assertEqual(self.pool.balance(ALICE)['available'], 75.0)

    def test_a_failed_send_gives_the_money_back(self):
        self.fund(ALICE, 100.0)
        wid = self.pool.withdraw(ALICE, 25.0, 'USDC')['withdrawal_id']
        self.chain.fail_send = 'out of gas'
        with patch.object(Pool, 'vault_key', lambda _self: '0x' + '11' * 32):
            out = self.pool.pay_withdrawal(wid)
        self.assertEqual(out['status'], 'failed')
        self.assertEqual(self.pool.balance(ALICE)['available'], 100.0)

    def test_paying_twice_is_refused(self):
        self.fund(ALICE, 100.0)
        wid = self.pool.withdraw(ALICE, 10.0, 'USDC')['withdrawal_id']
        with patch.object(Pool, 'vault_key', lambda _self: '0x' + '11' * 32):
            self.pool.pay_withdrawal(wid)
            again = self.pool.pay_withdrawal(wid)
        self.assertIn('already', again['error'])

    def test_manual_payout_can_be_recorded(self):
        self.fund(ALICE, 100.0)
        wid = self.pool.withdraw(ALICE, 10.0, 'USDC')['withdrawal_id']
        out = self.pool.mark_paid(wid, '0x' + 'aa' * 32)
        self.assertEqual(out['status'], 'sent')

    def test_auto_pay_sends_immediately(self):
        self.pool.set_config(auto_pay=True)
        self.fund(ALICE, 100.0)
        with patch.object(Pool, 'vault_key', lambda _self: '0x' + '11' * 32):
            with patch.object(Pool, 'has_hot_key', lambda _self: True):
                out = self.pool.withdraw(ALICE, 30.0, 'USDC')
        self.assertEqual(out['status'], 'sent')
        self.assertEqual(len(self.chain.sent), 1)


class TestSignatures(unittest.TestCase):
    """Real keys, real personal_sign — the part that guards real money."""

    def setUp(self):
        from eth_account import Account
        self.tmp = tempfile.mkdtemp(prefix='prefi_sig_')
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        self.acct = Account.create()
        self.other = Account.create()
        self.addr = self.acct.address.lower()
        self.chain = FakeChain()
        self.pool = Pool(
            self.tmp,
            price_at=lambda sym, ts, src=None: {'price': 100_000.0, 'mode': 'historical'},
            price_now=lambda sym, src=None: 100_000.0,
            markets=lambda: [{'symbol': 'BTC', 'token': 'BTC',
                              'source': 'hyperliquid', 'active': True}],
        )
        self.patcher = patch.object(Pool, 'chain', lambda _self: self.chain)
        self.patcher.start()
        self.pool.set_vault(VAULT)
        tx = '0x' + 'fe' * 32
        self.chain.deposit_tx(tx, self.addr, 1000.0)
        self.pool.deposit(tx)

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sign(self, account, message):
        from eth_account import Account
        from eth_account.messages import encode_defunct
        signed = Account.sign_message(encode_defunct(text=message), account.key)
        return signed.signature.hex()

    def test_unsigned_stake_is_refused(self):
        out = self.pool.stake(self.addr, 'BTC', 100_000.0, 50.0)
        self.assertIn('signature required', out['error'])
        self.assertIn('PreFi Pool', out['sign_message'])

    def test_signed_stake_is_accepted(self):
        req = self.pool.sign_request('stake', self.addr, amount='50.000000',
                                     asset='BTC', price='100000.00000000',
                                     round=str(self.pool.current_index()))
        out = self.pool.stake(self.addr, 'BTC', 100_000.0, 50.0,
                              signature=self._sign(self.acct, req['message']))
        self.assertEqual(out['staked'], 50.0)

    def test_a_free_call_is_signed_too(self):
        """It costs nothing, but it writes to a public accuracy record — and
        nobody else gets to write to yours."""
        unsigned = self.pool.free_stake(self.addr, 'BTC', 100_000.0)
        self.assertIn('signature required', unsigned['error'])

        req = self.pool.sign_request('free_stake', self.addr, asset='BTC',
                                     price='100000.00000000',
                                     round=str(self.pool.current_index()))
        out = self.pool.free_stake(self.addr, 'BTC', 100_000.0,
                                   signature=self._sign(self.acct, req['message']))
        self.assertTrue(out['free'])
        # ...and a stranger cannot place one under your address.
        forged = self.pool.free_stake(self.addr, 'BTC', 90_000.0,
                                      signature=self._sign(self.other, req['message']))
        self.assertIn('error', forged)

    def test_someone_elses_signature_is_refused(self):
        req = self.pool.sign_request('stake', self.addr, amount='50.000000',
                                     asset='BTC', price='100000.00000000',
                                     round=str(self.pool.current_index()))
        out = self.pool.stake(self.addr, 'BTC', 100_000.0, 50.0,
                              signature=self._sign(self.other, req['message']))
        self.assertIn('not', out['error'])

    def test_a_signature_cannot_be_replayed(self):
        req = self.pool.sign_request('withdraw', self.addr, amount='10.000000',
                                     token='USDC')
        sig = self._sign(self.acct, req['message'])
        first = self.pool.withdraw(self.addr, 10.0, 'USDC', signature=sig)
        self.assertEqual(first['status'], 'pending')
        second = self.pool.withdraw(self.addr, 10.0, 'USDC', signature=sig)
        self.assertIn('error', second)

    def test_a_stake_signature_cannot_be_spent_as_a_withdrawal(self):
        req = self.pool.sign_request('stake', self.addr, amount='10.000000',
                                     asset='BTC', price='100000.00000000',
                                     round=str(self.pool.current_index()))
        sig = self._sign(self.acct, req['message'])
        out = self.pool.withdraw(self.addr, 10.0, 'USDC', signature=sig)
        self.assertIn('error', out)

    def test_amount_cannot_be_altered_after_signing(self):
        req = self.pool.sign_request('withdraw', self.addr, amount='10.000000',
                                     token='USDC')
        sig = self._sign(self.acct, req['message'])
        out = self.pool.withdraw(self.addr, 900.0, 'USDC', signature=sig)
        self.assertIn('error', out)


class TestOwnership(PoolTestBase):

    def test_unclaimed_pool_is_configurable(self):
        self.assertEqual(self.pool.set_config(fee_bps=100)['fee_bps'], 100)

    def test_claimed_pool_rejects_strangers(self):
        self.pool.claim_owner(ALICE)
        self.assertIn('owner only', self.pool.set_config(fee_bps=100)['error'])

    def test_owner_secret_authorises(self):
        secret = self.pool.claim_owner(ALICE)['secret']
        self.assertEqual(self.pool.set_config(fee_bps=100, secret=secret)['fee_bps'], 100)

    def test_wrong_secret_refused(self):
        self.pool.claim_owner(ALICE)
        self.assertIn('error', self.pool.set_config(fee_bps=100, secret='nope'))

    def test_second_claim_needs_the_secret(self):
        self.pool.claim_owner(ALICE)
        self.assertIn('already has an owner', self.pool.claim_owner(BOB)['error'])

    def test_env_owner_overrides(self):
        self.pool.claim_owner(ALICE)
        os.environ['PREFI_OWNER'] = BOB
        try:
            self.assertIsNone(self.pool._require_owner(BOB))
        finally:
            os.environ.pop('PREFI_OWNER')

    def test_only_the_owner_pays_withdrawals(self):
        self.pool.claim_owner(ALICE)
        self.fund(BOB, 50.0)
        wid = self.pool.withdraw(BOB, 20.0, 'USDC')['withdrawal_id']
        self.assertIn('owner only', self.pool.pay_withdrawal(wid)['error'])


class TestViews(PoolTestBase):

    def test_round_shows_provisional_scores_before_it_closes(self):
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 101_000.0, 100.0)
        view = self.pool.round()
        self.assertEqual(len(view['assets']), 1)
        asset = view['assets'][0]
        self.assertTrue(asset['provisional'])
        self.assertEqual(asset['price_mode'], 'live-mark')
        self.assertAlmostEqual(asset['entries'][0]['payout'], 100.0, places=4)

    def test_round_without_a_price_still_renders(self):
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 101_000.0, 100.0)
        self.prices['BTC'] = None
        view = self.pool.round()
        self.assertEqual(view['assets'][0]['mode'], 'unpriced')

    def test_leaderboard_ranks_by_realised_pnl(self):
        self.prices['BTC'] = 100_000.0
        for addr, amount, price in ((ALICE, 100.0, 100_000.0), (BOB, 100.0, 130_000.0)):
            self.fund(addr, amount)
            self.pool.stake(addr, 'BTC', price, amount)
        st = self.pool.state()
        st['schedule']['anchor'] -= st['schedule']['interval'] + 10
        self.pool._save_state(st)
        rounds = self.pool._rounds()
        for record in rounds:
            record.update(self.pool.window(record['index']))
        self.pool._write(self.pool.rounds_path, rounds)
        self.pool.settle()

        board = self.pool.leaderboard()
        self.assertEqual(board[0]['address'], ALICE)
        self.assertGreater(board[0]['pnl'], 0)
        self.assertLess(board[-1]['pnl'], 0)

    def test_stats_report_tvl_and_stakes(self):
        self.fund(ALICE, 300.0)
        self.pool.stake(ALICE, 'BTC', 100_000.0, 120.0)
        stats = self.pool.stats()
        self.assertEqual(stats['tvl'], 300.0)
        self.assertEqual(stats['at_stake'], 120.0)
        self.assertEqual(stats['stakers'], 1)
        self.assertTrue(stats['enabled'])

    def test_vault_reports_solvency(self):
        self.fund(ALICE, 100.0)
        self.chain.balances[USDC] = 100_000_000        # $100
        vault = self.pool.vault()
        self.assertEqual(vault['credited'], 100.0)
        self.assertTrue(vault['solvent'])
        self.chain.balances[USDC] = 1_000_000          # $1 — a hole
        self.assertFalse(self.pool.vault()['solvent'])

    def test_ledger_shows_the_history_behind_a_balance(self):
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 100_000.0, 25.0)
        kinds = [row['kind'] for row in self.pool.ledger(ALICE)]
        self.assertEqual(sorted(kinds), ['deposit', 'stake'])


class TestModIntegration(unittest.TestCase):
    """The pool as `mod.py` exposes it — names, dispatch, config surface."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='prefi_mod_')
        os.environ['PREFI_UNSAFE_NO_SIG'] = '1'
        self.mod = Mod({})
        self.mod.store_dir = Path(self.tmp)
        for name in ('positions', 'stakes', 'treasury', 'markets',
                     'predictions', 'scoring'):
            setattr(self.mod, f'{name}_path', self.mod.store_dir / f'{name}.json')

    def tearDown(self):
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_pool_fn_is_declared_in_config(self):
        import json
        config = json.loads(
            (Path(__file__).resolve().parents[2] / 'config.json').read_text())
        for name in dir(self.mod):
            if name.startswith('pool_') or name in ('set_pool_config', 'hyperevm_status'):
                self.assertIn(name, config['fns'], f'{name} missing from config.json fns')

    def test_forward_exposes_the_pool_actions(self):
        actions = self.mod.forward()['actions']
        for action in ('pool', 'stake', 'deposit', 'withdraw', 'settle',
                       'round', 'rounds', 'pool-set', 'pool-vault',
                       'free-stake', 'free-quota', 'free-board'):
            self.assertIn(action, actions)

    def test_pool_config_round_trips_through_the_mod(self):
        out = self.mod.set_pool_config(interval=86400)
        self.assertEqual(out['interval'], 86400)
        self.assertEqual(self.mod.pool_config()['interval_days'], 1.0)

    def test_fee_lands_in_the_shared_treasury(self):
        before = self.mod.treasury()['balance']
        self.mod._pool_fee_to_treasury(12.5)
        self.assertAlmostEqual(self.mod.treasury()['balance'], before + 12.5)

    def test_status_carries_the_pool(self):
        with patch.object(Mod, '_get_token_price', lambda *a, **k: None):
            status = self.mod.status()
        self.assertIn('pool', status)
        self.assertIn('round', status['pool'])


if __name__ == '__main__':
    unittest.main()
