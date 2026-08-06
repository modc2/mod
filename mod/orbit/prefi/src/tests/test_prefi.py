"""PreFi test suite — ledger, predictions, scoring, price sources.

Hermetic: every price lookup is patched, so this never touches CoinGecko,
Hyperliquid, or the live ~/.mod/prefi ledger.

    python3 -m pytest tests/ -q        # from src/
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mod import Mod
import scoring


class PrefiTestBase(unittest.TestCase):
    """Every test gets its own empty ledger"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='prefi_test_')
        self.prefi = Mod({})
        self.prefi.store_dir = Path(self.tmp)
        for name in ('positions', 'stakes', 'treasury', 'markets',
                     'predictions', 'scoring'):
            setattr(self.prefi, f'{name}_path', self.prefi.store_dir / f'{name}.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _market(self, symbol='WETH', source='coingecko'):
        return self.prefi.add_market(f'0x{symbol}', symbol, 3000, source)

    def _fund(self, address, prefi_amount):
        """Give an address PREFI the only way it exists — a winning trade"""
        positions = self.prefi._load_json(self.prefi.positions_path, [])
        positions.append({
            'id': len(positions) + 1, 'trader': address, 'asset': 'WETH',
            'token': '0xWETH', 'usdc_in': 100.0, 'asset_amount': 1.0,
            'entry_price': 100.0, 'open_time': time.time(), 'closed': True,
            'close_time': time.time(), 'exit_price': 100.0 + prefi_amount,
            'usdc_out': 100.0 + prefi_amount, 'profit': prefi_amount,
            'prefi_earned': prefi_amount,
        })
        self.prefi._save_json(self.prefi.positions_path, positions)


# ── scoring.py ───────────────────────────────────────────────────────

class TestScoringModels(unittest.TestCase):
    """The score is a pure function of normalized dollar error"""

    def test_exact_call_scores_one(self):
        for model in scoring.MODELS:
            with self.subTest(model=model):
                r = scoring.score(100.0, 100.0, {'model': model})
                self.assertEqual(r['score'], 1.0)

    def test_normalized_error_is_dollars_over_price(self):
        r = scoring.score(64640.0, 64000.0)
        self.assertEqual(r['abs_error'], 640.0)
        self.assertAlmostEqual(r['normalized_error'], 0.01)

    def test_score_is_scale_free(self):
        """A 1% miss on a $0.41 token and on $64k BTC score identically —
        the whole reason the dollar difference is normalized."""
        cheap = scoring.score(0.4141, 0.41)['score']
        dear = scoring.score(64640.0, 64000.0)['score']
        self.assertAlmostEqual(cheap, dear, places=6)

    def test_score_decreases_with_error(self):
        for model in scoring.MODELS:
            with self.subTest(model=model):
                close = scoring.score(100.5, 100.0, {'model': model})['score']
                far = scoring.score(130.0, 100.0, {'model': model})['score']
                self.assertGreaterEqual(close, far)

    def test_l2_matches_the_solidity_contract(self):
        """ScoreL2.sol is 1/(1+d²) — tolerance 1 reproduces it exactly"""
        r = scoring.score(200.0, 100.0, {'model': 'l2', 'tolerance': 1})
        self.assertAlmostEqual(r['score'], 0.5)

    def test_linear_hits_zero_at_tolerance(self):
        p = {'model': 'linear', 'tolerance': 0.02}
        self.assertAlmostEqual(scoring.score(101.0, 100.0, p)['score'], 0.5)
        self.assertEqual(scoring.score(102.0, 100.0, p)['score'], 0.0)
        self.assertEqual(scoring.score(150.0, 100.0, p)['score'], 0.0)

    def test_exponential_is_one_over_e_at_tolerance(self):
        r = scoring.score(102.0, 100.0, {'model': 'exponential', 'tolerance': 0.02})
        self.assertAlmostEqual(r['score'], 1 / 2.718281828, places=6)

    def test_threshold_is_all_or_nothing(self):
        p = {'model': 'threshold', 'tolerance': 0.01}
        self.assertEqual(scoring.score(100.9, 100.0, p)['score'], 1.0)
        self.assertEqual(scoring.score(101.1, 100.0, p)['score'], 0.0)

    def test_tighter_tolerance_scores_harder(self):
        loose = scoring.score(102.0, 100.0, {'tolerance': 0.05})['score']
        tight = scoring.score(102.0, 100.0, {'tolerance': 0.005})['score']
        self.assertGreater(loose, tight)

    def test_zero_actual_price_scores_zero(self):
        self.assertEqual(scoring.score(100.0, 0.0)['score'], 0.0)

    def test_payout_scales_burn_by_multiplier_and_score(self):
        self.assertEqual(scoring.payout(10.0, 1.0, {'multiplier': 3.0}), 30.0)
        self.assertEqual(scoring.payout(10.0, 0.5, {'multiplier': 3.0}), 15.0)
        self.assertEqual(scoring.payout(10.0, 0.0, {'multiplier': 3.0}), 0.0)

    def test_models_are_self_describing(self):
        described = scoring.describe_models()
        self.assertEqual(set(described), set(scoring.MODELS))
        self.assertTrue(all(described.values()))


class TestScoringParams(unittest.TestCase):

    def test_defaults_fill_in(self):
        self.assertEqual(scoring.validate({})['model'], 'l2')
        self.assertEqual(scoring.validate({})['horizon'], 86400)

    def test_unknown_keys_ignored(self):
        self.assertNotIn('nonsense', scoring.validate({'nonsense': 1}))

    def test_rejects_unknown_model(self):
        with self.assertRaises(ValueError):
            scoring.validate({'model': 'astrology'})

    def test_rejects_non_positive_tolerance(self):
        for bad in (0, -0.01):
            with self.assertRaises(ValueError):
                scoring.validate({'tolerance': bad})

    def test_rejects_out_of_range_horizon(self):
        for bad in (60, scoring.MAX_HORIZON + 1):
            with self.assertRaises(ValueError):
                scoring.validate({'horizon': bad})

    def test_rejects_negative_multiplier(self):
        with self.assertRaises(ValueError):
            scoring.validate({'multiplier': -1})


class TestScoringConfig(PrefiTestBase):

    def test_set_and_get_roundtrip(self):
        r = self.prefi.set_scoring(model='linear', tolerance=0.05)
        self.assertEqual(r['status'], 'updated')
        active = self.prefi.get_scoring()
        self.assertEqual(active['model'], 'linear')
        self.assertEqual(active['tolerance'], 0.05)
        self.assertEqual(active['multiplier'], scoring.DEFAULT_PARAMS['multiplier'])

    def test_bad_params_rejected_and_not_persisted(self):
        self.assertIn('error', self.prefi.set_scoring(model='astrology'))
        self.assertEqual(self.prefi.get_scoring()['model'], 'l2')

    def test_empty_update_rejected(self):
        self.assertIn('error', self.prefi.set_scoring())

    def test_corrupt_config_falls_back_to_defaults(self):
        self.prefi._save_json(self.prefi.scoring_path, {'model': 'bogus'})
        self.assertEqual(self.prefi.get_scoring()['model'], 'l2')

    def test_preview_uses_the_settlement_math(self):
        r = self.prefi.score_preview(102.0, 100.0, model='linear',
                                     tolerance=0.04, burn=10)
        self.assertAlmostEqual(r['score'], 0.5)
        self.assertAlmostEqual(r['payout'], 15.0)
        self.assertAlmostEqual(r['net'], 5.0)


# ── Markets & price sources ──────────────────────────────────────────

class TestMarkets(PrefiTestBase):

    def test_add_records_source(self):
        r = self._market('WETH')
        self.assertEqual(r['market']['source'], 'coingecko')

    def test_duplicate_token_rejected(self):
        self._market('WETH')
        self.assertIn('error', self.prefi.add_market('0xWETH', 'OTHER', 3000))

    def test_duplicate_symbol_rejected_across_sources(self):
        """Positions resolve by symbol, so two markets can't share one"""
        self._market('ETH', 'coingecko')
        self.assertIn('error', self.prefi.add_market('hl:ETH', 'ETH', 0, 'hyperliquid'))

    def test_unknown_source_rejected(self):
        self.assertIn('error', self.prefi.add_market('0xX', 'X', 3000, 'oracle-vibes'))

    def test_add_hl_market_verifies_against_the_universe(self):
        with patch.object(Mod, '_hl_mids', return_value={'SOL': 74.0}):
            ok = self.prefi.add_hl_market('sol')
            self.assertEqual(ok['status'], 'added')
            self.assertEqual(ok['market']['token'], 'hl:SOL')
            self.assertEqual(ok['market']['source'], 'hyperliquid')
            self.assertIn('error', self.prefi.add_hl_market('NOTACOIN'))

    def test_add_hl_market_reports_an_unreachable_feed(self):
        with patch.object(Mod, '_hl_mids', return_value={}):
            self.assertIn('error', self.prefi.add_hl_market('SOL'))

    def test_hl_assets_flags_already_listed(self):
        with patch.object(Mod, '_hl_mids', return_value={'SOL': 74.0, 'BTC': 64000.0}), \
             patch.object(Mod, '_hl_post', return_value={'universe': [
                 {'name': 'SOL', 'maxLeverage': 20},
                 {'name': 'BTC', 'maxLeverage': 40},
                 {'name': 'OLD', 'maxLeverage': 5, 'isDelisted': True},
             ]}), patch.object(Mod, '_hl_mod_get', return_value=None):
            self.prefi.add_hl_market('SOL')
            assets = {a['coin']: a for a in self.prefi.hl_assets()}
            self.assertTrue(assets['SOL']['listed'])
            self.assertFalse(assets['BTC']['listed'])
            self.assertNotIn('OLD', assets)   # delisted coins have no price

    def test_price_dispatches_on_the_market_source(self):
        self._market('SOL', 'hyperliquid')
        with patch.object(Mod, '_hl_mids', return_value={'SOL': 74.0}):
            self.assertEqual(self.prefi._get_token_price('SOL'), 74.0)

    def test_hl_named_drops_spot_and_index_legs(self):
        named = Mod._hl_named({'BTC': '64000', '0G': '0.14', '@1': '16.4',
                               '#10010': '0.97', 'BAD': 'x', 'ZERO': '0'})
        self.assertEqual(set(named), {'BTC', '0G'})


class TestHistoricalPrice(PrefiTestBase):
    """Resolution scores the price at the resolve time, not at read time"""

    def test_hyperliquid_picks_the_candle_containing_the_moment(self):
        ts = time.time() - 90000
        bucket = int(ts // 3600 * 3600) * 1000
        candles = [{'t': bucket - 3600_000, 'c': '10'}, {'t': bucket, 'c': '73.85'}]
        with patch.object(Mod, '_hl_mod_get', return_value=candles):
            q = self.prefi._price_at('SOL', ts, 'hyperliquid')
        self.assertEqual(q, {'price': 73.85, 'mode': 'historical'})

    def test_coingecko_picks_the_nearest_sample(self):
        ts = 1_700_000_000
        payload = {'prices': [[(ts - 1800) * 1000, 1800.0], [(ts + 60) * 1000, 1865.0]]}
        with patch('mod.requests.get') as get:
            get.return_value.json.return_value = payload
            get.return_value.raise_for_status.return_value = None
            q = self.prefi._price_at('WETH', ts, 'coingecko')
        self.assertEqual(q, {'price': 1865.0, 'mode': 'historical'})

    def test_falls_back_to_spot_and_says_so(self):
        with patch.object(Mod, '_hl_mod_get', return_value=None), \
             patch.object(Mod, '_hl_post', side_effect=RuntimeError('429')), \
             patch.object(Mod, '_get_token_price', return_value=74.0):
            q = self.prefi._price_at('SOL', time.time() - 90000, 'hyperliquid')
        self.assertEqual(q, {'price': 74.0, 'mode': 'spot'})

    def test_no_price_at_all_is_reported_not_guessed(self):
        with patch.object(Mod, '_hl_mod_get', return_value=None), \
             patch.object(Mod, '_hl_post', side_effect=RuntimeError('429')), \
             patch.object(Mod, '_get_token_price', return_value=None):
            q = self.prefi._price_at('SOL', time.time() - 90000, 'hyperliquid')
        self.assertEqual(q['mode'], 'none')


# ── PREFI balance ────────────────────────────────────────────────────

class TestBalance(PrefiTestBase):

    def test_trading_profit_is_the_mint(self):
        self._fund('0xA', 250.0)
        b = self.prefi.prefi_balance('0xA')
        self.assertEqual(b['from_trades'], 250.0)
        self.assertEqual(b['available'], 250.0)

    def test_locking_removes_it_from_available(self):
        self._fund('0xA', 250.0)
        self.prefi.lock_prefi(100.0, 604800, '0xA')
        b = self.prefi.prefi_balance('0xA')
        self.assertEqual(b['locked'], 100.0)
        self.assertEqual(b['available'], 150.0)

    def test_cannot_lock_prefi_you_never_earned(self):
        r = self.prefi.lock_prefi(50.0, 604800, '0xBroke')
        self.assertIn('error', r)
        self.assertIn('Insufficient PREFI', r['error'])

    def test_cannot_lock_the_same_prefi_twice(self):
        self._fund('0xA', 100.0)
        self.assertEqual(self.prefi.lock_prefi(80.0, 604800, '0xA')['status'], 'locked')
        self.assertIn('error', self.prefi.lock_prefi(80.0, 604800, '0xA'))

    def test_unlocking_returns_it_to_available(self):
        self._fund('0xA', 100.0)
        self.prefi.lock_prefi(100.0, 604800, '0xA')
        stakes = self.prefi._load_json(self.prefi.stakes_path, [])
        stakes[0]['lock_end'] = time.time() - 1
        self.prefi._save_json(self.prefi.stakes_path, stakes)
        self.prefi.unlock_prefi(1, '0xA')
        self.assertEqual(self.prefi.prefi_balance('0xA')['available'], 100.0)

    def test_balance_is_case_insensitive_on_address(self):
        self._fund('0xAbC', 10.0)
        self.assertEqual(self.prefi.prefi_balance('0xabc')['available'], 10.0)


# ── Predictions ──────────────────────────────────────────────────────

class TestPredictions(PrefiTestBase):

    def setUp(self):
        super().setUp()
        self._market('WETH')
        self._fund('0xA', 500.0)
        self.price_patch = patch.object(Mod, '_get_token_price', return_value=2000.0)
        self.price_patch.start()
        self.addCleanup(self.price_patch.stop)

    def _settle_at(self, actual, prediction_id=1):
        """Backdate the prediction and settle it at a known price"""
        preds = self.prefi._load_json(self.prefi.predictions_path, [])
        for p in preds:
            if p['id'] == prediction_id:
                p['resolve_at'] = time.time() - 10
        self.prefi._save_json(self.prefi.predictions_path, preds)
        with patch.object(Mod, '_price_at',
                          return_value={'price': actual, 'mode': 'historical'}):
            return self.prefi.resolve_predictions()

    def test_burn_leaves_the_balance_immediately(self):
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        b = self.prefi.prefi_balance('0xA')
        self.assertEqual(b['burned'], 50.0)
        self.assertEqual(b['available'], 450.0)

    def test_prediction_records_the_entry_price_and_move(self):
        r = self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        self.assertEqual(r['entry_price'], 2000.0)
        self.assertEqual(r['implied_move_pct'], 2.5)
        self.assertEqual(r['max_payout'], 150.0)

    def test_defaults_to_tomorrow(self):
        r = self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        p = self.prefi._load_json(self.prefi.predictions_path, [])[0]
        self.assertEqual(p['horizon'], 86400)
        self.assertAlmostEqual(p['resolve_at'] - p['created_at'], 86400, places=3)
        self.assertEqual(r['status'], 'open')

    def test_rejects_a_burn_beyond_the_balance(self):
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, 501.0, '0xA'))

    def test_rejects_a_burn_below_the_minimum(self):
        self.prefi.set_scoring(min_burn=5.0)
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, 1.0, '0xA'))

    def test_rejects_unknown_market(self):
        self.assertIn('error', self.prefi.predict('DOGE', 1.0, 10.0, '0xA'))

    def test_rejects_non_positive_price(self):
        self.assertIn('error', self.prefi.predict('WETH', 0, 10.0, '0xA'))

    def test_rejects_out_of_range_horizon(self):
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, 10.0, '0xA', horizon=60))
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, 10.0, '0xA',
                                                  horizon=scoring.MAX_HORIZON + 1))

    def test_rejects_missing_address(self):
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, 10.0, ''))

    def test_nothing_resolves_before_its_horizon(self):
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        self.assertEqual(self.prefi.resolve_predictions()['resolved'], [])

    def test_settlement_scores_the_dollar_error(self):
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        self.assertEqual(self._settle_at(2000.0)['resolved'], [1])

        p = self.prefi.get_predictions('0xA')[0]
        self.assertEqual(p['actual_price'], 2000.0)
        self.assertEqual(p['abs_error'], 50.0)
        self.assertAlmostEqual(p['normalized_error'], 0.025)
        expected = 1 / (1 + (0.025 / 0.02) ** 2)
        self.assertAlmostEqual(p['score'], expected, places=6)
        self.assertAlmostEqual(p['payout'], 50.0 * 3.0 * expected, places=4)

    def test_a_perfect_call_pays_the_full_multiplier(self):
        self.prefi.predict('WETH', 2000.0, 50.0, '0xA')
        self._settle_at(2000.0)
        p = self.prefi.get_predictions('0xA')[0]
        self.assertEqual(p['score'], 1.0)
        self.assertEqual(p['payout'], 150.0)
        self.assertEqual(p['net'], 100.0)

    def test_a_bad_call_loses_the_burn(self):
        self.prefi.set_scoring(model='linear', tolerance=0.01)
        self.prefi.predict('WETH', 3000.0, 50.0, '0xA')
        self._settle_at(2000.0)
        p = self.prefi.get_predictions('0xA')[0]
        self.assertEqual(p['payout'], 0.0)
        self.assertEqual(p['net'], -50.0)
        self.assertEqual(self.prefi.prefi_balance('0xA')['available'], 450.0)

    def test_payout_is_credited_to_the_balance(self):
        self.prefi.predict('WETH', 2000.0, 50.0, '0xA')
        self._settle_at(2000.0)
        b = self.prefi.prefi_balance('0xA')
        self.assertEqual(b['from_predictions'], 150.0)
        self.assertEqual(b['available'], 500.0 - 50.0 + 150.0)

    def test_resolution_is_idempotent(self):
        self.prefi.predict('WETH', 2000.0, 50.0, '0xA')
        self._settle_at(2000.0)
        minted = self.prefi.treasury()['total_prefi_minted']
        self.assertEqual(self.prefi.resolve_predictions()['resolved'], [])
        self.assertEqual(self.prefi.treasury()['total_prefi_minted'], minted)

    def test_params_are_snapshotted_at_placement(self):
        """Retuning the score can't re-price a bet already on the table"""
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        self.prefi.set_scoring(model='threshold', tolerance=0.0001)
        self._settle_at(2000.0)
        p = self.prefi.get_predictions('0xA')[0]
        self.assertEqual(p['params']['model'], 'l2')
        self.assertGreater(p['score'], 0)

    def test_an_unpriceable_prediction_stays_open(self):
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        preds = self.prefi._load_json(self.prefi.predictions_path, [])
        preds[0]['resolve_at'] = time.time() - 10
        self.prefi._save_json(self.prefi.predictions_path, preds)
        with patch.object(Mod, '_price_at', return_value={'price': None, 'mode': 'none'}):
            r = self.prefi.resolve_predictions()
        self.assertEqual(r['resolved'], [])
        self.assertEqual(r['pending'], 1)

    def test_open_predictions_show_a_projected_score(self):
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        p = self.prefi.get_predictions('0xA')[0]
        self.assertFalse(p['resolved'])
        self.assertIn('projected', p)
        self.assertGreater(p['seconds_remaining'], 86000)

    def test_burn_counts_against_supply(self):
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        t = self.prefi.treasury()
        self.assertEqual(t['total_prefi_burned'], 50.0)
        self.assertEqual(t['prefi_supply'], t['total_prefi_minted'] - 50.0)

    def test_predictions_are_listed_newest_first_and_scoped(self):
        self._fund('0xB', 100.0)
        self.prefi.predict('WETH', 2010.0, 10.0, '0xA')
        self.prefi.predict('WETH', 2020.0, 10.0, '0xB')
        self.assertEqual(len(self.prefi.get_predictions('0xA')), 1)
        everyone = self.prefi.get_predictions()
        self.assertEqual(len(everyone), 2)
        self.assertGreaterEqual(everyone[0]['created_at'], everyone[1]['created_at'])


class TestPredictionBoard(PrefiTestBase):

    def setUp(self):
        super().setUp()
        self._market('WETH')
        self._fund('0xSharp', 500.0)
        self._fund('0xWild', 500.0)
        self.price_patch = patch.object(Mod, '_get_token_price', return_value=2000.0)
        self.price_patch.start()
        self.addCleanup(self.price_patch.stop)

    def test_ranks_by_average_score(self):
        self.prefi.predict('WETH', 2000.0, 50.0, '0xSharp')
        self.prefi.predict('WETH', 2600.0, 50.0, '0xWild')
        preds = self.prefi._load_json(self.prefi.predictions_path, [])
        for p in preds:
            p['resolve_at'] = time.time() - 10
        self.prefi._save_json(self.prefi.predictions_path, preds)
        with patch.object(Mod, '_price_at',
                          return_value={'price': 2000.0, 'mode': 'historical'}):
            self.prefi.resolve_predictions()

        board = self.prefi.prediction_board()
        self.assertEqual(board[0]['address'], '0xSharp')
        self.assertEqual(board[0]['rank'], 1)
        self.assertGreater(board[0]['net_prefi'], 0)
        self.assertLess(board[1]['net_prefi'], 0)

    def test_unresolved_forecasters_rank_last(self):
        self.prefi.predict('WETH', 2000.0, 50.0, '0xWild')   # open, unscored
        board = self.prefi.prediction_board()
        self.assertEqual(board[0]['resolved'], 0)
        self.assertEqual(board[0]['avg_score'], 0)
        self.assertEqual(board[0]['total_burned'], 50.0)


class TestPortfolioAndStatus(PrefiTestBase):

    def setUp(self):
        super().setUp()
        self._market('WETH')
        self._fund('0xA', 500.0)
        self.price_patch = patch.object(Mod, '_get_token_price', return_value=2000.0)
        self.price_patch.start()
        self.addCleanup(self.price_patch.stop)

    def test_portfolio_reports_predictions_and_balance(self):
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        port = self.prefi.portfolio('0xA')
        self.assertEqual(port['predictions']['open'], 1)
        self.assertEqual(port['predictions']['burned'], 50.0)
        self.assertEqual(port['prefi']['available'], 450.0)

    def test_status_counts_forecasters(self):
        self.prefi.predict('WETH', 2050.0, 50.0, '0xA')
        s = self.prefi.status()
        self.assertEqual(s['predictions_total'], 1)
        self.assertEqual(s['predictions_open'], 1)
        self.assertEqual(s['forecasters'], 1)
        self.assertEqual(s['scoring']['model'], 'l2')


class TestForwardCLI(PrefiTestBase):

    def test_null_action_lists_what_it_can_do(self):
        info = self.prefi.forward()
        for action in ('predict', 'resolve', 'scoring', 'add-hl', 'balance'):
            self.assertIn(action, info['actions'])

    def test_predict_through_the_cli(self):
        self._market('WETH')
        self._fund('0xA', 100.0)
        with patch.object(Mod, '_get_token_price', return_value=2000.0):
            r = self.prefi.forward('predict', asset='WETH', price=2050,
                                   burn=10, address='0xA')
        self.assertEqual(r['status'], 'open')

    def test_scoring_through_the_cli(self):
        self.assertEqual(self.prefi.forward('set-scoring', model='linear')['status'],
                         'updated')
        self.assertEqual(self.prefi.forward('scoring')['model'], 'linear')
        self.assertIn('l2', self.prefi.forward('models')['models'])


if __name__ == '__main__':
    unittest.main()
