"""PreFi Test Suite — Native Token (ETH) vs USDC/USDT Pilot

Comprehensive tests for the prediction market module covering:
- Market lifecycle (create → predict → resolve → claim)
- L2 distance scoring math
- ETH/USDC and ETH/USDT pair markets
- Multi-player reward distribution
- Edge cases and error paths
"""

import unittest
import tempfile
import shutil
import time
import math
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mod import Mod


class PrefiTestBase(unittest.TestCase):
    """Base class with temp storage isolation"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='prefi_test_')
        self.prefi = Mod()
        self.prefi.store_dir = Path(self.tmp)
        self.prefi.markets_path = self.prefi.store_dir / 'markets.json'
        self.prefi.predictions_path = self.prefi.store_dir / 'predictions.json'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_eth_usdc_market(self, duration=3600):
        return self.prefi.create_market('ETH/USDC', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', duration)

    def _create_eth_usdt_market(self, duration=3600):
        return self.prefi.create_market('ETH/USDT', '0xdAC17F958D2ee523a2206206994597C13D831ec7', duration)


class TestL2Scoring(PrefiTestBase):
    """Verify the L2 distance scoring formula: score = stake / (1 + (diff/actual)^2)"""

    def _score(self, predicted, actual, stake):
        diff = abs(predicted - actual)
        distance_sq = (diff ** 2) / (actual ** 2) if actual > 0 else 0
        return stake / (1 + distance_sq)

    def test_perfect_prediction_gets_full_stake_score(self):
        score = self._score(3500.0, 3500.0, 100.0)
        self.assertEqual(score, 100.0)

    def test_score_decreases_with_distance(self):
        actual = 3500.0
        s_close = self._score(3510.0, actual, 100.0)
        s_mid = self._score(3600.0, actual, 100.0)
        s_far = self._score(4000.0, actual, 100.0)
        self.assertGreater(s_close, s_mid)
        self.assertGreater(s_mid, s_far)
        self.assertGreater(s_far, 0)

    def test_score_scales_with_stake(self):
        actual = 3500.0
        s10 = self._score(3600.0, actual, 10.0)
        s100 = self._score(3600.0, actual, 100.0)
        self.assertAlmostEqual(s100 / s10, 10.0, places=6)

    def test_symmetric_error(self):
        """Equally wrong above/below should score identically"""
        actual = 3500.0
        s_above = self._score(3600.0, actual, 50.0)
        s_below = self._score(3400.0, actual, 50.0)
        self.assertAlmostEqual(s_above, s_below, places=6)

    def test_normalized_scoring_scale_invariant(self):
        """Same % error on different price levels yields same score"""
        # 10% off on ETH at 3500 vs ETH at 2000
        s1 = self._score(3850.0, 3500.0, 100.0)
        s2 = self._score(2200.0, 2000.0, 100.0)
        self.assertAlmostEqual(s1, s2, places=6)

    def test_extreme_miss_score_near_zero(self):
        score = self._score(100000.0, 3500.0, 100.0)
        self.assertLess(score, 1.0)

    def test_1pct_error_score(self):
        """1% error should retain ~99.99% of stake as score"""
        actual = 3500.0
        predicted = actual * 1.01  # 1% off
        score = self._score(predicted, actual, 100.0)
        # score = 100 / (1 + 0.01^2) = 100 / 1.0001 ≈ 99.99
        self.assertGreater(score, 99.9)


class TestMarketCreation(PrefiTestBase):
    """Market creation for ETH/USDC and ETH/USDT pairs"""

    def test_create_eth_usdc_market(self):
        m = self._create_eth_usdc_market()
        self.assertEqual(m['asset'], 'ETH/USDC')
        self.assertIn('market_id', m)
        self.assertEqual(m['market_id'], 1)
        self.assertEqual(m['duration'], 3600)

    def test_create_eth_usdt_market(self):
        m = self._create_eth_usdt_market()
        self.assertEqual(m['asset'], 'ETH/USDT')
        self.assertIn('market_id', m)

    def test_create_multiple_markets(self):
        m1 = self._create_eth_usdc_market()
        m2 = self._create_eth_usdt_market()
        self.assertEqual(m1['market_id'], 1)
        self.assertEqual(m2['market_id'], 2)

    def test_market_has_time_window(self):
        m = self._create_eth_usdc_market(duration=7200)
        self.assertAlmostEqual(m['end_time'] - m['start_time'], 7200, delta=2)

    def test_list_markets_returns_active(self):
        self._create_eth_usdc_market()
        self._create_eth_usdt_market()
        markets = self.prefi.list_markets()
        self.assertEqual(len(markets), 2)
        self.assertTrue(markets[0]['is_active'])
        self.assertTrue(markets[1]['is_active'])

    def test_get_market_by_id(self):
        self._create_eth_usdc_market()
        m = self.prefi.get_market(1)
        self.assertEqual(m['asset'], 'ETH/USDC')
        self.assertTrue(m['is_active'])

    def test_get_nonexistent_market(self):
        result = self.prefi.get_market(999)
        self.assertIn('error', result)

    def test_market_token_address_stored(self):
        self._create_eth_usdc_market()
        m = self.prefi.get_market(1)
        self.assertEqual(m['token_address'], '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48')


class TestPredictions(PrefiTestBase):
    """Predicting ETH price against stablecoins"""

    def setUp(self):
        super().setUp()
        self._create_eth_usdc_market(duration=3600)

    def test_record_prediction(self):
        p = self.prefi.record_prediction(1, '0xAlice', '3500.00', '10')
        self.assertEqual(p['status'], 'active')
        self.assertEqual(p['predicted_price'], '3500.00')

    def test_prediction_updates_market_stake(self):
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(1, '0xBob', '3600', '20')
        m = self.prefi.get_market(1)
        self.assertEqual(m['total_staked'], 30.0)

    def test_prediction_updates_player_count(self):
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(1, '0xBob', '3600', '20')
        m = self.prefi.get_market(1)
        self.assertEqual(m['players_count'], 2)

    def test_duplicate_prediction_blocked(self):
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        dup = self.prefi.record_prediction(1, '0xAlice', '3700', '5')
        self.assertIn('error', dup)

    def test_duplicate_case_insensitive(self):
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        dup = self.prefi.record_prediction(1, '0xalice', '3700', '5')
        self.assertIn('error', dup)

    def test_prediction_on_nonexistent_market(self):
        p = self.prefi.record_prediction(999, '0xAlice', '3500', '10')
        self.assertIn('error', p)

    def test_get_user_predictions(self):
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        preds = self.prefi.get_user_predictions('0xAlice')
        self.assertEqual(len(preds), 1)
        self.assertEqual(preds[0]['asset'], 'ETH/USDC')
        self.assertEqual(preds[0]['predicted_price'], '3500')

    def test_predictions_across_markets(self):
        """Alice predicts ETH/USDC and ETH/USDT"""
        self._create_eth_usdt_market(duration=3600)
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(2, '0xAlice', '3505', '15')
        preds = self.prefi.get_user_predictions('0xAlice')
        self.assertEqual(len(preds), 2)
        assets = {p['asset'] for p in preds}
        self.assertEqual(assets, {'ETH/USDC', 'ETH/USDT'})


class TestMarketResolution(PrefiTestBase):
    """Resolve ETH/USDC and ETH/USDT markets, verify reward math"""

    def _setup_eth_usdc_with_players(self):
        self._create_eth_usdc_market(duration=3600)
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')    # perfect
        self.prefi.record_prediction(1, '0xBob', '3600', '20')      # 2.86% off
        self.prefi.record_prediction(1, '0xCharlie', '4000', '5')   # 14.3% off
        self.prefi.record_prediction(1, '0xDave', '3000', '15')     # 14.3% off

    def test_resolve_with_actual_price(self):
        self._setup_eth_usdc_with_players()
        res = self.prefi.resolve_market(1, '3500')
        self.assertIn('rewards', res)
        self.assertEqual(res['actual_price'], 3500.0)
        self.assertEqual(res['predictions_count'], 4)

    def test_2pct_platform_fee(self):
        self._setup_eth_usdc_with_players()
        res = self.prefi.resolve_market(1, '3500')
        total = 10 + 20 + 5 + 15  # 50
        self.assertAlmostEqual(res['fee'], total * 0.02, places=4)
        self.assertAlmostEqual(res['reward_pool'], total * 0.98, places=4)

    def test_rewards_sum_to_pool(self):
        self._setup_eth_usdc_with_players()
        res = self.prefi.resolve_market(1, '3500')
        total_rewards = sum(res['rewards'].values())
        self.assertAlmostEqual(total_rewards, res['reward_pool'], places=4)

    def test_perfect_predictor_gets_most_per_stake(self):
        """Alice (perfect, stake=10) should have higher reward/stake ratio than others"""
        self._setup_eth_usdc_with_players()
        res = self.prefi.resolve_market(1, '3500')
        alice_ratio = res['rewards']['0xAlice'] / 10
        bob_ratio = res['rewards']['0xBob'] / 20
        charlie_ratio = res['rewards']['0xCharlie'] / 5
        self.assertGreater(alice_ratio, bob_ratio)
        self.assertGreater(alice_ratio, charlie_ratio)

    def test_all_players_get_positive_reward(self):
        self._setup_eth_usdc_with_players()
        res = self.prefi.resolve_market(1, '3500')
        for addr, reward in res['rewards'].items():
            self.assertGreater(reward, 0, f'{addr} should get positive reward')

    def test_resolve_already_settled(self):
        self._setup_eth_usdc_with_players()
        self.prefi.resolve_market(1, '3500')
        dup = self.prefi.resolve_market(1, '3600')
        self.assertIn('error', dup)

    def test_resolve_nonexistent_market(self):
        res = self.prefi.resolve_market(999, '3500')
        self.assertIn('error', res)

    def test_resolve_eth_usdt_market(self):
        """ETH/USDT market resolves independently"""
        self._create_eth_usdt_market(duration=3600)
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(1, '0xBob', '3510', '10')
        res = self.prefi.resolve_market(1, '3500')
        self.assertIn('rewards', res)
        # Alice was perfect, Bob was close — Alice gets more
        self.assertGreater(res['rewards']['0xAlice'], res['rewards']['0xBob'])


class TestRewardDistribution(PrefiTestBase):
    """Detailed reward scenarios for ETH/stablecoin markets"""

    def test_single_player_gets_full_pool(self):
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '100')
        res = self.prefi.resolve_market(1, '3500')
        self.assertAlmostEqual(res['rewards']['0xAlice'], 98.0, places=4)  # 100 - 2% fee

    def test_single_player_wrong_still_gets_pool(self):
        """Only player gets full pool regardless of accuracy"""
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '5000', '100')
        res = self.prefi.resolve_market(1, '3500')
        self.assertAlmostEqual(res['rewards']['0xAlice'], 98.0, places=4)

    def test_equal_stakes_equal_predictions(self):
        """Two players with same stake and same prediction split evenly"""
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '50')
        self.prefi.record_prediction(1, '0xBob', '3500', '50')
        res = self.prefi.resolve_market(1, '3500')
        self.assertAlmostEqual(res['rewards']['0xAlice'], res['rewards']['0xBob'], places=4)

    def test_equal_accuracy_higher_stake_wins_more(self):
        """Same distance but Alice stakes 2x — gets 2x reward"""
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3600', '100')
        self.prefi.record_prediction(1, '0xBob', '3400', '50')  # symmetric distance
        res = self.prefi.resolve_market(1, '3500')
        self.assertAlmostEqual(
            res['rewards']['0xAlice'] / res['rewards']['0xBob'], 2.0, places=4
        )

    def test_whale_vs_shrimp_accuracy_matters(self):
        """Shrimp with perfect prediction can beat whale with bad prediction on ROI"""
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xWhale', '5000', '1000')   # way off
        self.prefi.record_prediction(1, '0xShrimp', '3500', '10')    # perfect
        res = self.prefi.resolve_market(1, '3500')
        whale_roi = res['rewards']['0xWhale'] / 1000
        shrimp_roi = res['rewards']['0xShrimp'] / 10
        self.assertGreater(shrimp_roi, whale_roi)

    def test_realistic_eth_price_scenario(self):
        """Simulate realistic ETH/USDC predictions around $3,500"""
        self._create_eth_usdc_market()
        # Realistic spread of predictions
        players = [
            ('0xTrader1', '3480', '50'),   # -0.57%
            ('0xTrader2', '3520', '30'),   # +0.57%
            ('0xTrader3', '3550', '100'),  # +1.43%
            ('0xTrader4', '3400', '25'),   # -2.86%
            ('0xTrader5', '3700', '75'),   # +5.71%
        ]
        for addr, price, stake in players:
            self.prefi.record_prediction(1, addr, price, stake)

        res = self.prefi.resolve_market(1, '3500')
        rewards = res['rewards']

        # Trader1 and Trader2 are equally close (symmetric), but Trader1 has higher stake
        self.assertGreater(rewards['0xTrader1'], rewards['0xTrader2'])
        # Trader3 has big stake but further off — still high reward due to stake weight
        self.assertGreater(rewards['0xTrader3'], rewards['0xTrader4'])
        # Trader5 is furthest off
        trader5_roi = rewards['0xTrader5'] / 75
        trader1_roi = rewards['0xTrader1'] / 50
        self.assertGreater(trader1_roi, trader5_roi)

    def test_two_parallel_markets_independent_rewards(self):
        """ETH/USDC and ETH/USDT resolve independently"""
        self._create_eth_usdc_market()
        self._create_eth_usdt_market()

        # Same player, different predictions per pair
        self.prefi.record_prediction(1, '0xAlice', '3500', '50')
        self.prefi.record_prediction(2, '0xAlice', '3510', '50')
        self.prefi.record_prediction(1, '0xBob', '3600', '50')
        self.prefi.record_prediction(2, '0xBob', '3400', '50')

        res1 = self.prefi.resolve_market(1, '3500')
        res2 = self.prefi.resolve_market(2, '3500')

        # Alice perfect in USDC market, close in USDT
        self.assertGreater(res1['rewards']['0xAlice'], res1['rewards']['0xBob'])
        # Alice closer in USDT market too
        self.assertGreater(res2['rewards']['0xAlice'], res2['rewards']['0xBob'])


class TestClaims(PrefiTestBase):
    """Claiming rewards after settlement"""

    def setUp(self):
        super().setUp()
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(1, '0xBob', '3600', '20')
        self.prefi.resolve_market(1, '3500')

    def test_claim_reward(self):
        claim = self.prefi.record_claim(1, '0xAlice')
        self.assertTrue(claim['claimed'])
        self.assertGreater(claim['reward'], 0)

    def test_double_claim_blocked(self):
        self.prefi.record_claim(1, '0xAlice')
        dup = self.prefi.record_claim(1, '0xAlice')
        self.assertIn('error', dup)

    def test_claim_before_settlement(self):
        self._create_eth_usdt_market()
        self.prefi.record_prediction(2, '0xAlice', '3500', '10')
        claim = self.prefi.record_claim(2, '0xAlice')
        self.assertIn('error', claim)

    def test_claim_nonexistent_prediction(self):
        claim = self.prefi.record_claim(1, '0xNobody')
        self.assertIn('error', claim)

    def test_claim_reflects_in_user_predictions(self):
        self.prefi.record_claim(1, '0xAlice')
        preds = self.prefi.get_user_predictions('0xAlice')
        self.assertTrue(preds[0]['claimed'])


class TestLeaderboard(PrefiTestBase):
    """Leaderboard rankings across ETH/stablecoin markets"""

    def test_leaderboard_ranked_by_reward(self):
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(1, '0xBob', '3600', '20')
        self.prefi.record_prediction(1, '0xCharlie', '4000', '5')
        self.prefi.resolve_market(1, '3500')

        board = self.prefi.leaderboard()
        self.assertEqual(len(board), 3)
        # Verify descending order
        for i in range(len(board) - 1):
            self.assertGreaterEqual(board[i]['total_reward'], board[i + 1]['total_reward'])

    def test_leaderboard_has_pnl(self):
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.resolve_market(1, '3500')
        board = self.prefi.leaderboard()
        self.assertIn('pnl', board[0])
        # Single player: reward = 9.8 (98% of 10), pnl = 9.8 - 10 = -0.2 (fee)
        self.assertAlmostEqual(board[0]['pnl'], -0.2, places=4)

    def test_leaderboard_filter_by_market(self):
        self._create_eth_usdc_market()
        self._create_eth_usdt_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(2, '0xBob', '3500', '10')
        self.prefi.resolve_market(1, '3500')
        self.prefi.resolve_market(2, '3500')

        board_m1 = self.prefi.leaderboard(market_id=1)
        self.assertEqual(len(board_m1), 1)
        self.assertEqual(board_m1[0]['address'], '0xAlice')

    def test_leaderboard_aggregates_across_markets(self):
        self._create_eth_usdc_market()
        self._create_eth_usdt_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(2, '0xAlice', '3500', '10')
        self.prefi.resolve_market(1, '3500')
        self.prefi.resolve_market(2, '3500')

        board = self.prefi.leaderboard()
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]['predictions_count'], 2)
        self.assertAlmostEqual(board[0]['total_staked'], 20.0, places=4)


class TestRewards(PrefiTestBase):
    """Get rewards detail for specific addresses"""

    def test_get_rewards_for_address(self):
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.resolve_market(1, '3500')

        r = self.prefi.get_rewards('0xAlice')
        self.assertGreater(r['total_reward'], 0)
        self.assertEqual(len(r['rewards']), 1)

    def test_get_rewards_filter_by_market(self):
        self._create_eth_usdc_market()
        self._create_eth_usdt_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.record_prediction(2, '0xAlice', '3500', '10')
        self.prefi.resolve_market(1, '3500')
        self.prefi.resolve_market(2, '3500')

        r = self.prefi.get_rewards('0xAlice', market_id=1)
        self.assertEqual(len(r['rewards']), 1)

    def test_get_rewards_no_predictions(self):
        r = self.prefi.get_rewards('0xNobody')
        self.assertEqual(r['total_reward'], 0)
        self.assertEqual(len(r['rewards']), 0)


class TestStatus(PrefiTestBase):
    """Module status reporting"""

    def test_status_empty(self):
        s = self.prefi.status()
        self.assertEqual(s['service'], 'prefi')
        self.assertEqual(s['markets_total'], 0)
        self.assertEqual(s['markets_active'], 0)
        self.assertEqual(s['predictions_total'], 0)

    def test_status_with_markets(self):
        self._create_eth_usdc_market()
        self._create_eth_usdt_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        s = self.prefi.status()
        self.assertEqual(s['markets_total'], 2)
        self.assertEqual(s['markets_active'], 2)
        self.assertEqual(s['predictions_total'], 1)

    def test_status_after_settlement(self):
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '10')
        self.prefi.resolve_market(1, '3500')
        s = self.prefi.status()
        self.assertEqual(s['markets_total'], 1)
        self.assertEqual(s['markets_active'], 0)


class TestForwardCLI(PrefiTestBase):
    """CLI entry point routing"""

    def test_forward_no_action_returns_info(self):
        res = self.prefi.forward()
        self.assertEqual(res['module'], 'prefi')
        self.assertIn('actions', res)

    def test_forward_status(self):
        res = self.prefi.forward('status')
        self.assertEqual(res['service'], 'prefi')

    def test_forward_create_market(self):
        res = self.prefi.forward('create', asset='ETH/USDC', token='0xUSDC', duration='3600')
        self.assertIn('market_id', res)

    def test_forward_markets(self):
        self.prefi.forward('create', asset='ETH/USDC', token='0xUSDC')
        res = self.prefi.forward('markets')
        self.assertEqual(len(res), 1)

    def test_forward_predict(self):
        self.prefi.forward('create', asset='ETH/USDC', token='0xUSDC')
        res = self.prefi.forward('predict', market_id='1', address='0xAlice', price='3500', stake='10')
        self.assertEqual(res['status'], 'active')

    def test_forward_invalid_action(self):
        res = self.prefi.forward('nonexistent')
        self.assertIn('module', res)

    def test_forward_test(self):
        res = self.prefi.forward('test')
        self.assertIn('passed', res)
        self.assertEqual(res['failed'], 0)


class TestEdgeCases(PrefiTestBase):
    """Edge cases and boundary conditions"""

    def test_very_small_stake(self):
        self._create_eth_usdc_market()
        p = self.prefi.record_prediction(1, '0xAlice', '3500', '0.000001')
        self.assertEqual(p['status'], 'active')
        res = self.prefi.resolve_market(1, '3500')
        self.assertGreater(res['rewards']['0xAlice'], 0)

    def test_large_stake(self):
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500', '1000000')
        res = self.prefi.resolve_market(1, '3500')
        self.assertAlmostEqual(res['rewards']['0xAlice'], 980000.0, places=2)

    def test_many_players(self):
        self._create_eth_usdc_market()
        n = 50
        for i in range(n):
            price = str(3400 + i * 4)  # spread from 3400 to 3596
            self.prefi.record_prediction(1, f'0xPlayer{i}', price, '10')
        res = self.prefi.resolve_market(1, '3500')
        self.assertEqual(res['predictions_count'], n)
        total_rewards = sum(res['rewards'].values())
        self.assertAlmostEqual(total_rewards, res['reward_pool'], places=2)

    def test_price_string_precision(self):
        """Predictions with decimal precision"""
        self._create_eth_usdc_market()
        self.prefi.record_prediction(1, '0xAlice', '3500.123456', '10')
        preds = self.prefi.get_user_predictions('0xAlice')
        self.assertEqual(preds[0]['predicted_price'], '3500.123456')

    def test_deployment_info(self):
        info = self.prefi.get_deployment_info()
        self.assertIn('network', info)
        self.assertIn('api_port', info)
        self.assertIn('app_port', info)


class TestPriceAPI(PrefiTestBase):
    """Price fetching (mocked)"""

    @patch('requests.get')
    def test_get_prices(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'ethereum': {'usd': 3500.0, 'usd_24h_change': 2.5},
            'bitcoin': {'usd': 65000.0, 'usd_24h_change': -1.2},
            'usd-coin': {'usd': 1.0, 'usd_24h_change': 0.01},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        prices = self.prefi.get_prices()
        self.assertEqual(prices['ETH/USD']['price'], 3500.0)
        self.assertEqual(prices['BTC/USD']['price'], 65000.0)
        self.assertEqual(prices['USDC/USD']['price'], 1.0)

    @patch('requests.get')
    def test_get_asset_price_eth(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'ethereum': {'usd': 3500.0, 'usd_24h_change': 2.5}}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        p = self.prefi.get_asset_price('ETH')
        self.assertEqual(p['asset'], 'ETH')
        self.assertEqual(p['price'], 3500.0)

    @patch('requests.get')
    def test_get_asset_price_error(self, mock_get):
        mock_get.side_effect = Exception('Network error')
        p = self.prefi.get_asset_price('ETH')
        self.assertIn('error', p)


if __name__ == '__main__':
    unittest.main()
