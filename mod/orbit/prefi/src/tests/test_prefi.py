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

    def _fake_hl(self, mids=None, perps=None, spot=None, tokens=None,
                 ctxs=None, spot_ctxs=None):
        """Stand in for the whole Hyperliquid feed.

        The universe is assembled from three calls (allMids, the perp meta, the
        spot meta); patching them together is the only way to test the join
        that turns an '@index' key into a pair name.
        """
        mids = mids if mids is not None else {}
        payloads = {
            'allMids': mids,
            'metaAndAssetCtxs': [{'universe': perps or []}, ctxs or []],
            'spotMetaAndAssetCtxs': [{'universe': spot or [], 'tokens': tokens or []},
                                     spot_ctxs or []],
        }

        def post(body, *a, **kw):
            try:
                return payloads[body['type']]
            except KeyError:
                raise RuntimeError(f"unexpected info call {body}")

        return (patch.object(Mod, '_hl_mod_get', return_value=None),
                patch.object(Mod, '_hl_post', side_effect=post))

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
        mod_get, post = self._fake_hl(mids={'SOL': 74.0}, perps=[{'name': 'SOL'}])
        with mod_get, post:
            ok = self.prefi.add_hl_market('sol')
            self.assertEqual(ok['status'], 'added')
            self.assertEqual(ok['market']['token'], 'hl:SOL')
            self.assertEqual(ok['market']['source'], 'hyperliquid')
            self.assertEqual(ok['market']['hl_kind'], 'perp')
            self.assertIn('error', self.prefi.add_hl_market('NOTACOIN'))

    def test_add_hl_market_reports_an_unreachable_feed(self):
        mod_get, post = self._fake_hl(mids={})
        with mod_get, post:
            self.assertIn('error', self.prefi.add_hl_market('SOL'))

    def test_hl_assets_flags_already_listed(self):
        mod_get, post = self._fake_hl(
            mids={'SOL': 74.0, 'BTC': 64000.0},
            perps=[{'name': 'SOL', 'maxLeverage': 20},
                   {'name': 'BTC', 'maxLeverage': 40},
                   {'name': 'OLD', 'maxLeverage': 5, 'isDelisted': True}])
        with mod_get, post:
            self.prefi.add_hl_market('SOL')
            assets = {a['coin']: a for a in self.prefi.hl_assets()}
            self.assertTrue(assets['SOL']['listed'])
            self.assertFalse(assets['BTC']['listed'])
            self.assertNotIn('OLD', assets)   # delisted coins have no price

    def test_price_dispatches_on_the_market_source(self):
        self._market('SOL', 'hyperliquid')
        with patch.object(Mod, '_hl_mids', return_value={'SOL': 74.0}):
            self.assertEqual(self.prefi._get_token_price('SOL'), 74.0)

    def test_hl_named_keeps_spot_pairs_and_drops_prediction_legs(self):
        # Spot pairs are quoted under an '@index' key and are perfectly
        # tradeable; '#10010' is an event leg, which is odds, not a price.
        named = Mod._hl_named({'BTC': '64000', '0G': '0.14', '@1': '16.4',
                               '#10010': '0.97', 'BAD': 'x', 'ZERO': '0'})
        self.assertEqual(set(named), {'BTC', '0G', '@1'})



class TestHyperliquidUniverse(PrefiTestBase):
    """Every pair Hyperliquid quotes — perps and spot — is listable here.

    Spot is the half that needs the work: HL quotes a spot pair under an
    '@index' key and only the spot meta says which token that index is, so a
    market listed as 'HYPE/USDC' has to remember it is '@107' to the feed.
    """

    MIDS = {'BTC': 64000.0, 'SOL': 74.0, 'DEAD': 3.0,
            '@1': 21.0, '@7': 0.5, '@9': 1.25, 'PURR/USDC': 0.11,
            '#10010': 0.97}
    PERPS = [{'name': 'BTC', 'maxLeverage': 40}, {'name': 'SOL', 'maxLeverage': 20},
             {'name': 'DEAD', 'maxLeverage': 5, 'isDelisted': True}]
    TOKENS = [{'index': 0, 'name': 'USDC'}, {'index': 1, 'name': 'PURR'},
              {'index': 2, 'name': 'HYPE'}, {'index': 3, 'name': 'FUN'}]
    SPOT = [{'name': 'PURR/USDC', 'tokens': [1, 0], 'index': 0},
            {'name': '@1', 'tokens': [2, 0], 'index': 1},
            {'name': '@7', 'tokens': [3, 0], 'index': 7}]
    CTXS = [{'dayNtlVlm': '1000', 'prevDayPx': '60000', 'markPx': '64000'},
            {'dayNtlVlm': '9000', 'prevDayPx': '80', 'markPx': '74'}]
    # Spot contexts are keyed by coin, not by position — that is the exchange's
    # own shape, and the '@9' pair deliberately has none.
    SPOT_CTXS = [{'coin': 'PURR/USDC', 'dayNtlVlm': '1'},
                 {'coin': '@1', 'dayNtlVlm': '5000'},
                 {'coin': '@7', 'dayNtlVlm': '10'}]

    def _feed(self):
        return self._fake_hl(mids=self.MIDS, perps=self.PERPS, spot=self.SPOT,
                             tokens=self.TOKENS, ctxs=self.CTXS,
                             spot_ctxs=self.SPOT_CTXS)

    def test_universe_carries_perps_and_spot_under_one_shape(self):
        mod_get, post = self._feed()
        with mod_get, post:
            rows = {a['coin']: a for a in self.prefi.hl_assets(limit=0)}
        self.assertEqual(rows['SOL']['kind'], 'perp')
        self.assertEqual(rows['HYPE/USDC']['key'], '@1')
        self.assertEqual(rows['HYPE/USDC']['kind'], 'spot')
        self.assertEqual(rows['HYPE/USDC']['price'], 21.0)
        self.assertEqual(rows['PURR/USDC']['key'], 'PURR/USDC')
        self.assertNotIn('DEAD', rows)        # delisted: quoted, not tradeable
        self.assertNotIn('#10010', rows)      # an event leg is not a pair

    def test_spot_pairs_the_meta_does_not_name_stay_addressable(self):
        """HL quotes ~700 spot pairs and names ~330. Dropping the rest would
        make a priceable, settleable pair unreachable."""
        mod_get, post = self._feed()
        with mod_get, post:
            rows = {a['coin']: a for a in self.prefi.hl_assets(limit=0)}
            self.assertIn('@9', rows)
            self.assertFalse(rows['@9']['named'])
            listed = self.prefi.add_hl_market('@9')
        self.assertEqual(listed['market']['hl_key'], '@9')

    def test_kind_filter_and_limit(self):
        mod_get, post = self._feed()
        with mod_get, post:
            perps = self.prefi.hl_assets(kind='perp', limit=0)
            spot = self.prefi.hl_assets(kind='spot', limit=0)
            self.assertEqual({a['coin'] for a in perps}, {'BTC', 'SOL'})
            self.assertEqual(len(spot), 4)
            self.assertEqual(len(self.prefi.hl_assets(limit=2)), 2)

    def test_rows_come_back_liquid_end_first(self):
        mod_get, post = self._feed()
        with mod_get, post:
            perps = self.prefi.hl_assets(kind='perp', limit=0)
        self.assertEqual([a['coin'] for a in perps], ['SOL', 'BTC'])   # 9000 > 1000
        self.assertEqual(perps[0]['change_24h'], -7.5)

    def test_search_matches_the_pair_name_or_the_hl_key(self):
        mod_get, post = self._feed()
        with mod_get, post:
            self.assertEqual([a['coin'] for a in self.prefi.hl_assets(search='hype')],
                             ['HYPE/USDC'])
            self.assertEqual([a['coin'] for a in self.prefi.hl_assets(search='@7')],
                             ['FUN/USDC'])

    def test_a_spot_market_prices_and_settles_under_its_hl_key(self):
        mod_get, post = self._feed()
        with mod_get, post:
            self.prefi.add_hl_market('HYPE/USDC')
            market = self.prefi._market('HYPE/USDC')
            self.assertEqual(market['hl_key'], '@1')
            self.assertEqual(self.prefi._get_token_price('HYPE/USDC'), 21.0)

        ts = time.time() - 90000
        bucket = int(ts // 3600 * 3600) * 1000
        seen = {}

        def candles(path, timeout=10):
            seen['path'] = path
            return [{'t': bucket, 'c': '19.5'}]

        with patch.object(Mod, '_hl_mod_get', side_effect=candles):
            quote = self.prefi._price_at('HYPE/USDC', ts, 'hyperliquid')
        self.assertEqual(quote, {'price': 19.5, 'mode': 'historical'})
        self.assertIn('%401', seen['path'])   # '@1', not 'HYPE/USDC'

    def test_a_bare_token_resolves_to_its_canonical_pair(self):
        mod_get, post = self._feed()
        with mod_get, post:
            self.assertEqual(self.prefi.add_hl_market('fun')['market']['symbol'],
                             'FUN/USDC')

    def test_a_perp_wins_a_name_it_shares_with_a_spot_token(self):
        """'BTC' is a perp and could also be a spot base token — the perp is
        what someone typing BTC means."""
        mod_get, post = self._fake_hl(
            mids={'BTC': 64000.0, '@3': 63000.0},
            perps=[{'name': 'BTC'}],
            spot=[{'name': '@3', 'tokens': [1, 0], 'index': 3}],
            tokens=[{'index': 0, 'name': 'USDC'}, {'index': 1, 'name': 'BTC'}])
        with mod_get, post:
            self.assertEqual(self.prefi._hl_find('BTC')['kind'], 'perp')
            self.assertEqual(self.prefi._hl_find('BTC/USDC')['key'], '@3')

    def test_a_stale_list_beats_an_empty_one_when_the_feed_429s(self):
        mod_get, post = self._feed()
        with mod_get, post:
            warm = len(self.prefi.hl_assets(limit=0))

        cold = Mod({})                       # fresh process, cold memory cache
        cold.store_dir = self.prefi.store_dir
        cold.markets_path = self.prefi.markets_path
        with patch.object(Mod, '_hl_mod_get', return_value=None), \
             patch.object(Mod, '_hl_post', side_effect=RuntimeError('429')):
            self.assertEqual(len(cold.hl_assets(limit=0)), warm)
            self.assertFalse(cold.hl_stats()['reachable'] is None)

    def test_seed_lists_the_busiest_pairs_in_one_call(self):
        """Standing a pool up should not mean 20 clicks through a 900-row list."""
        mod_get, post = self._feed()
        with mod_get, post:
            out = self.prefi.seed_hl(limit=3)
            listed = [m['symbol'] for m in self.prefi.list_markets()]
        self.assertEqual(out['added'], ['SOL', 'BTC', 'HYPE/USDC'])  # by volume
        self.assertEqual(listed, ['SOL', 'BTC', 'HYPE/USDC'])

    def test_seed_is_idempotent_and_can_be_narrowed(self):
        """`limit` is the top of the ranking, not a count of new listings."""
        mod_get, post = self._feed()
        with mod_get, post:
            self.prefi.seed_hl(limit=2)
            again = self.prefi.seed_hl(limit=2)
            self.assertEqual(again['added'], [])
            self.assertEqual(again['existing'], ['SOL', 'BTC'])
            spot = self.prefi.seed_hl(limit=2, kind='spot')
            self.assertEqual(spot['added'], ['HYPE/USDC', 'FUN/USDC'])

    def test_seed_skips_pairs_under_a_volume_floor(self):
        mod_get, post = self._feed()
        with mod_get, post:
            out = self.prefi.seed_hl(limit=10, kind='perp', min_volume=5000)
        self.assertEqual(out['added'], ['SOL'])   # BTC trades $1000

    def test_seed_reports_an_unreachable_feed_rather_than_listing_nothing(self):
        with patch.object(Mod, '_hl_mod_get', return_value=None), \
             patch.object(Mod, '_hl_post', side_effect=RuntimeError('429')):
            self.assertIn('error', self.prefi.seed_hl())

    def test_stats_counts_what_is_quoted_and_what_is_listed(self):
        mod_get, post = self._feed()
        with mod_get, post:
            self.prefi.add_hl_market('SOL')
            stats = self.prefi.hl_stats()
        self.assertEqual(stats['perps'], 2)
        self.assertEqual(stats['spot'], 4)
        self.assertEqual(stats['pairs'], 6)
        self.assertEqual(stats['listed'], 1)
        self.assertTrue(stats['reachable'])

    def test_the_module_is_asked_before_the_public_endpoint(self):
        """One HL client per box: the local hyperliquid module answers first,
        and only a miss falls through to the rate-limited public API."""
        with patch.object(Mod, '_hl_mod_get', return_value={'BTC': '64000'}) as mod_get, \
             patch.object(Mod, '_hl_post', side_effect=AssertionError('public API used')):
            self.assertEqual(self.prefi._hl_mids(), {'BTC': 64000.0})
        mod_get.assert_called_with('/mids')

    def test_the_activator_door_is_tried_when_the_direct_port_is_dead(self):
        """The hyperliquid module is scale-to-zero: a refused call on its own
        port means asleep, and only the activator hop wakes it."""
        seen = []

        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {'BTC': '64000'}

        def get(url, timeout=None):
            seen.append(url)
            if url.startswith(Mod.HL_MOD_URL):
                raise ConnectionError('refused')
            return Resp()

        with patch('mod.requests.get', side_effect=get):
            self.assertEqual(self.prefi._hl_mod_get('/mids'), {'BTC': '64000'})
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[1].startswith(Mod.HL_WAKE_URL))
        # ...and the door that answered is the one tried first next time.
        with patch('mod.requests.get', side_effect=get):
            self.prefi._hl_mod_get('/mids')
        self.assertTrue(seen[2].startswith(Mod.HL_WAKE_URL))


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


class TestFreePredictions(PrefiTestBase):
    """A free call costs nothing, is scored identically, and still mints"""

    def setUp(self):
        super().setUp()
        self._market('WETH')
        self.price_patch = patch.object(Mod, '_get_token_price', return_value=2000.0)
        self.price_patch.start()
        self.addCleanup(self.price_patch.stop)

    def _settle_at(self, actual):
        preds = self.prefi._load_json(self.prefi.predictions_path, [])
        for p in preds:
            p['resolve_at'] = time.time() - 10
        self.prefi._save_json(self.prefi.predictions_path, preds)
        with patch.object(Mod, '_price_at',
                          return_value={'price': actual, 'mode': 'historical'}):
            return self.prefi.resolve_predictions()

    def test_a_broke_address_can_still_predict(self):
        """The whole point — no PREFI, no trade history, still a forecaster"""
        self.assertEqual(self.prefi.prefi_balance('0xNew')['available'], 0.0)
        r = self.prefi.predict('WETH', 2050.0, address='0xNew')
        self.assertEqual(r['status'], 'open')
        self.assertTrue(r['free'])
        self.assertEqual(r['burned'], 0)

    def test_a_free_call_never_touches_the_balance(self):
        self.prefi.predict('WETH', 2050.0, address='0xNew')
        b = self.prefi.prefi_balance('0xNew')
        self.assertEqual(b['burned'], 0)
        self.assertEqual(b['available'], 0.0)
        self.assertEqual(self.prefi.treasury()['total_prefi_burned'], 0)

    def test_a_good_free_call_mints_free_payout(self):
        self.prefi.predict('WETH', 2000.0, address='0xNew')
        self._settle_at(2000.0)
        p = self.prefi.get_predictions('0xNew')[0]
        self.assertEqual(p['score'], 1.0)
        self.assertEqual(p['payout'], scoring.DEFAULT_PARAMS['free_payout'])
        b = self.prefi.prefi_balance('0xNew')
        self.assertEqual(b['from_free'], 1.0)
        self.assertEqual(b['available'], 1.0)

    def test_a_bad_free_call_costs_nothing(self):
        self.prefi.set_scoring(model='linear', tolerance=0.01)
        self.prefi.predict('WETH', 3000.0, address='0xNew')
        self._settle_at(2000.0)
        p = self.prefi.get_predictions('0xNew')[0]
        self.assertEqual(p['payout'], 0.0)
        self.assertEqual(p['net'], 0.0)
        self.assertEqual(self.prefi.prefi_balance('0xNew')['available'], 0.0)

    def test_free_payout_scales_with_the_score(self):
        self.prefi.set_scoring(free_payout=10.0)
        self.prefi.predict('WETH', 2050.0, address='0xNew')
        self._settle_at(2000.0)
        p = self.prefi.get_predictions('0xNew')[0]
        self.assertAlmostEqual(p['payout'], 10.0 * p['score'], places=6)

    def test_the_allowance_runs_out(self):
        for _ in range(scoring.DEFAULT_PARAMS['free_per_day']):
            self.assertNotIn('error', self.prefi.predict('WETH', 2050.0, address='0xNew'))
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, address='0xNew'))
        self.assertEqual(self.prefi.free_quota('0xNew')['remaining'], 0)

    def test_the_allowance_is_per_address(self):
        for _ in range(scoring.DEFAULT_PARAMS['free_per_day']):
            self.prefi.predict('WETH', 2050.0, address='0xNew')
        self.assertNotIn('error', self.prefi.predict('WETH', 2050.0, address='0xOther'))

    def test_the_allowance_is_case_insensitive_on_address(self):
        for _ in range(scoring.DEFAULT_PARAMS['free_per_day']):
            self.prefi.predict('WETH', 2050.0, address='0xAbC')
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, address='0xabc'))

    def test_the_window_rolls_rather_than_resetting_at_midnight(self):
        self.prefi.set_scoring(free_per_day=1)
        self.prefi.predict('WETH', 2050.0, address='0xNew')
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, address='0xNew'))

        preds = self.prefi._load_json(self.prefi.predictions_path, [])
        preds[0]['created_at'] = time.time() - scoring.FREE_WINDOW - 1
        self.prefi._save_json(self.prefi.predictions_path, preds)
        self.assertEqual(self.prefi.free_quota('0xNew')['remaining'], 1)
        self.assertNotIn('error', self.prefi.predict('WETH', 2050.0, address='0xNew'))

    def test_quota_reports_when_the_next_one_lands(self):
        self.prefi.predict('WETH', 2050.0, address='0xNew')
        q = self.prefi.free_quota('0xNew')
        self.assertEqual(q['used'], 1)
        self.assertEqual(q['remaining'], scoring.DEFAULT_PARAMS['free_per_day'] - 1)
        self.assertAlmostEqual(q['seconds_until_reset'], scoring.FREE_WINDOW, delta=5)

    def test_a_fresh_address_has_the_full_allowance(self):
        q = self.prefi.free_quota('0xNobody')
        self.assertTrue(q['enabled'])
        self.assertEqual(q['used'], 0)
        self.assertEqual(q['remaining'], q['limit'])
        self.assertIsNone(q['resets_at'])

    def test_free_can_be_switched_off_entirely(self):
        self.prefi.set_scoring(free_per_day=0)
        self.assertFalse(self.prefi.free_quota('0xNew')['enabled'])
        r = self.prefi.predict('WETH', 2050.0, address='0xNew')
        self.assertIn('error', r)
        self.assertIn('off', r['error'])

    def test_a_burn_below_the_minimum_is_still_rejected(self):
        """Free is burn == 0 exactly — a token burn doesn't sneak past min_burn"""
        self._fund('0xA', 500.0)
        self.prefi.set_scoring(min_burn=5.0)
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, 1.0, '0xA'))

    def test_a_negative_burn_is_rejected(self):
        self.assertIn('error', self.prefi.predict('WETH', 2050.0, -5.0, '0xNew'))

    def test_free_calls_are_marked_on_the_board(self):
        self.prefi.predict('WETH', 2000.0, address='0xNew')
        self._settle_at(2000.0)
        row = [r for r in self.prefi.prediction_board() if r['address'] == '0xNew'][0]
        self.assertEqual(row['free_calls'], 1)
        self.assertEqual(row['total_burned'], 0.0)
        self.assertEqual(row['avg_score'], 1.0)

    def test_free_calls_are_counted_in_status(self):
        self.prefi.predict('WETH', 2050.0, address='0xNew')
        self.assertEqual(self.prefi.status()['predictions_free'], 1)

    def test_free_through_the_cli(self):
        r = self.prefi.forward('predict', asset='WETH', price=2050, address='0xNew')
        self.assertTrue(r['free'])
        self.assertEqual(self.prefi.forward('free', address='0xNew')['used'], 1)


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


# ── Bittensor subnets ────────────────────────────────────────────────

class TestBittensorMarkets(PrefiTestBase):
    """Subnet alpha tokens, read through the bt module's /api/call. The
    screener is the universe, bt_prices_at is the mark — now and at a close."""

    ROWS = [
        {'netuid': 0, 'name': 'root', 'symbol': 'Τ', 'price': 1.0, 'vol_24h': 9e9},
        {'netuid': 64, 'name': 'chutes', 'symbol': 'ᚱ', 'price': 0.085,
         'vol_24h': 5000.0, 'change_24h': 2.5, 'market_cap': 500000.0},
        {'netuid': 51, 'name': 'lium.io', 'symbol': 'ת', 'price': 0.0829,
         'vol_24h': 7000.0, 'change_24h': 0.5},
        {'netuid': 1, 'name': 'Apex', 'symbol': 'α', 'price': 0.0075, 'vol_24h': 100.0},
        {'netuid': 99, 'name': 'dead', 'symbol': 'x', 'price': 0, 'vol_24h': 0},
    ]

    def _fake_bt(self, rows=None, prices=None, prices_at=None):
        rows = self.ROWS if rows is None else rows
        now = prices if prices is not None else {
            str(r['netuid']): r['price'] for r in rows}
        calls = []

        def call(_self, tool, args=None, timeout=15):
            calls.append((tool, args or {}))
            if tool == 'bt_screener':
                return {'rows': rows, 'count': len(rows)}
            if tool == 'bt_prices_at':
                if (args or {}).get('ts'):
                    return {'prices': prices_at if prices_at is not None else now}
                return {'prices': now}
            raise RuntimeError(f'unexpected bt tool {tool}')

        self.bt_calls = calls
        return patch.object(Mod, '_bt_call', call)

    def test_universe_drops_root_and_unpriced_and_sorts_by_volume(self):
        with self._fake_bt():
            rows = self.prefi.bt_assets(limit=0)
        self.assertEqual([r['coin'] for r in rows], ['SN51', 'SN64', 'SN1'])
        self.assertTrue(all(r['kind'] == 'subnet' and r['quote'] == 'TAO' for r in rows))
        self.assertEqual(rows[1]['name'], 'chutes')
        self.assertEqual(rows[1]['change_24h'], 2.5)

    def test_add_bt_market_by_netuid_sn_name_or_glyph(self):
        with self._fake_bt():
            ok = self.prefi.add_bt_market(64)
            self.assertEqual(ok['status'], 'added')
            m = ok['market']
            self.assertEqual((m['symbol'], m['source'], m['bt_netuid'], m['quote']),
                             ('SN64', 'bittensor', 64, 'TAO'))
            self.assertEqual(m['bt_name'], 'chutes')
            self.assertEqual(m['token'], 'bt:64')
            # Already listed, whichever name it is asked by.
            self.assertIn('error', self.prefi.add_bt_market('SN64'))
            self.assertIn('error', self.prefi.add_bt_market('chutes'))
            self.assertEqual(self.prefi.add_bt_market('lium.io')['market']['symbol'], 'SN51')
            self.assertEqual(self.prefi.add_bt_market('α')['market']['symbol'], 'SN1')
            self.assertIn('not a Bittensor subnet', self.prefi.add_bt_market('SN12345')['error'])
            self.assertIn('error', self.prefi.add_bt_market('0'))     # root is not listable
            self.assertIn('error', self.prefi.add_bt_market(''))

    def test_add_bt_market_reports_an_unreachable_feed(self):
        with patch.object(Mod, '_bt_call', return_value=None):
            out = self.prefi.add_bt_market(64)
        self.assertIn('unreachable', out['error'])

    def test_bt_assets_flags_listed_and_searches_every_name(self):
        with self._fake_bt():
            self.prefi.add_bt_market(64)
            rows = {a['coin']: a for a in self.prefi.bt_assets(limit=0)}
            self.assertTrue(rows['SN64']['listed'])
            self.assertFalse(rows['SN51']['listed'])
            self.assertEqual([a['coin'] for a in self.prefi.bt_assets(search='lium')], ['SN51'])
            self.assertEqual([a['coin'] for a in self.prefi.bt_assets(search='64')], ['SN64'])
            self.assertEqual([a['coin'] for a in self.prefi.bt_assets(search='ת')], ['SN51'])
            self.assertEqual(len(self.prefi.bt_assets(limit=2)), 2)
            stats = self.prefi.bt_stats()
        self.assertEqual((stats['subnets'], stats['listed'], stats['reachable']), (3, 1, True))

    def test_seed_bt_lists_the_busiest_and_is_idempotent(self):
        with self._fake_bt():
            first = self.prefi.seed_bt(limit=2)
            self.assertEqual(first['added'], ['SN51', 'SN64'])
            second = self.prefi.seed_bt(limit=2)
            self.assertEqual(second['added'], [])
            self.assertEqual(second['existing'], ['SN51', 'SN64'])
            floor = self.prefi.seed_bt(limit=10, min_volume=6000)
            self.assertEqual(floor['added'], [])          # SN1 is under the floor
            self.assertEqual(len(self.prefi._load_json(self.prefi.markets_path, [])), 2)

    def test_a_subnet_prices_in_tao_now_and_at_a_close(self):
        with self._fake_bt(prices_at={'64': 0.09}):
            self.prefi.add_bt_market(64)
            self.assertEqual(self.prefi._get_token_price('SN64'), 0.085)
            self.assertEqual(self.prefi._get_token_price('SN64', 'bittensor'), 0.085)
            quote = self.prefi._price_at('SN64', time.time() - 90000, 'bittensor')
            self.assertEqual(quote, {'price': 0.09, 'mode': 'historical'})
            asked = [a['ts'] for t, a in self.bt_calls if t == 'bt_prices_at' and a.get('ts')]
            self.assertEqual(len(asked), 1)
            self.assertAlmostEqual(asked[0], time.time() - 90000, delta=5)

    def test_a_subnet_with_no_snapshot_at_the_close_falls_back_to_spot(self):
        with self._fake_bt(prices_at={}):
            self.prefi.add_bt_market(64)
            quote = self.prefi._price_at('SN64', time.time() - 90000, 'bittensor')
        self.assertEqual(quote, {'price': 0.085, 'mode': 'spot'})

    def test_list_markets_quotes_a_subnet_in_tao_with_a_usd_shadow(self):
        with self._fake_bt(), patch.object(Mod, '_hl_mids', return_value={'TAO': 300.0}):
            self.prefi.add_bt_market(64)
            rows = {m['symbol']: m for m in self.prefi.list_markets()}
        self.assertEqual(rows['SN64']['quote'], 'TAO')
        self.assertEqual(rows['SN64']['price'], 0.085)
        self.assertAlmostEqual(rows['SN64']['price_usd'], 25.5)

    def test_list_markets_survives_no_tao_quote(self):
        with self._fake_bt(), patch.object(Mod, '_hl_mids', side_effect=RuntimeError('down')):
            self.prefi.add_bt_market(64)
            rows = {m['symbol']: m for m in self.prefi.list_markets()}
        self.assertEqual(rows['SN64']['price'], 0.085)
        self.assertNotIn('price_usd', rows['SN64'])

    def test_bt_call_treats_a_tool_error_as_an_answer_not_a_closed_door(self):
        """A 200 with ok=false means the module is up and the tool failed —
        trying the activator next would only wake nothing and wait 30s."""
        seen = []

        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {'ok': False, 'error': 'boom'}

        def post(url, json=None, timeout=None):
            seen.append(url)
            return Resp()

        with patch('mod.requests.post', side_effect=post):
            self.assertIsNone(self.prefi._bt_call('bt_screener'))
        self.assertEqual(len(seen), 1)

    def test_bt_call_knocks_on_the_activator_when_the_port_is_closed(self):
        seen = []

        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {'ok': True, 'result': {'rows': []}}

        def post(url, json=None, timeout=None):
            seen.append(url)
            if url.startswith(Mod.BT_MOD_URL):
                raise ConnectionError('refused')
            return Resp()

        with patch('mod.requests.post', side_effect=post):
            self.assertEqual(self.prefi._bt_call('bt_screener'), {'rows': []})
        self.assertEqual(seen, [f'{Mod.BT_MOD_URL}/api/call', f'{Mod.BT_WAKE_URL}/api/call'])
        self.assertEqual(self.prefi._bt_base, Mod.BT_WAKE_URL)

    def test_universe_is_served_from_disk_when_the_feed_is_down(self):
        with self._fake_bt():
            self.assertEqual(len(self.prefi.bt_assets(limit=0)), 3)
        fresh = Mod({})
        fresh.store_dir = self.prefi.store_dir
        fresh.markets_path = self.prefi.markets_path
        with patch.object(Mod, '_bt_call', return_value=None):
            self.assertEqual(len(fresh.bt_assets(limit=0)), 3)
            self.assertFalse(fresh.bt_stats()['source'] != 'cache')


# ── DEX tokens on Solana and Base ────────────────────────────────────

def _pair(chain, pair, symbol, token, liq, price=1.0, vol=1000.0, dex='raydium', name=None):
    """A DexScreener pair, the shape the feed actually returns."""
    return {'chainId': chain, 'dexId': dex, 'pairAddress': pair,
            'baseToken': {'address': token, 'symbol': symbol, 'name': name or symbol},
            'quoteToken': {'address': 'So111', 'symbol': 'SOL'},
            'priceUsd': str(price), 'liquidity': {'usd': liq},
            'volume': {'h24': vol}, 'priceChange': {'h24': 2.5},
            'url': f'https://dexscreener.com/{chain}/{pair}'}


# Real-shaped addresses: a Solana pubkey is 32-44 base58 chars, a Base one is
# 0x + 40 hex. The lookup path decides "address or symbol?" by shape.
def _sol(tag): return (tag + 'x' * 44)[:44]
def _evm(tag): return '0x' + (tag.encode().hex() + 'a' * 40)[:40]
SOL_PAIR_WIF, SOL_PAIR_WIF2, SOL_PAIR_BONK = _sol('PAIRWIF1'), _sol('PAIRWIF2'), _sol('PAIRBONK')
SOL_MINT_WIF, SOL_MINT_BONK, SOL_MINT_OTHER = _sol('MINTWIF'), _sol('MINTBONK'), _sol('MINTOTHER')
BASE_PAIR_BRETT, BASE_PAIR_THIN = _evm('pbrett'), _evm('pthin')
BASE_TOK_BRETT, BASE_TOK_THIN = _evm('tbrett'), _evm('tthin')


class DexTestBase(PrefiTestBase):
    """A fake DexScreener + GeckoTerminal. `self.pairs` is the universe the
    fake answers from; every route filters it the way the real one would."""

    def setUp(self):
        super().setUp()
        os.environ['PREFI_UNSAFE_NO_SIG'] = '1'
        self.pairs = [
            _pair('solana', SOL_PAIR_WIF, '$WIF', SOL_MINT_WIF, 5_000_000, 0.2, 200_000),
            _pair('solana', SOL_PAIR_WIF2, 'WIF', SOL_MINT_WIF, 40_000, 0.19, 100),
            _pair('solana', SOL_PAIR_BONK, 'BONK', SOL_MINT_BONK, 900, 0.00001, 50),
            _pair('base', BASE_PAIR_BRETT, 'BRETT', BASE_TOK_BRETT, 1_100_000, 0.005, 9_000,
                  dex='uniswap', name='Brett'),
            _pair('base', BASE_PAIR_THIN, 'THIN', BASE_TOK_THIN, 2_500, 0.001, 10, dex='aerodrome'),
            _pair('ethereum', '0x' + 'e' * 40, 'PEPE', '0x' + 'f' * 40, 9_000_000, 0.00001),
        ]
        self.candles = []          # GeckoTerminal ohlcv_list, [t, o, h, l, c, v]
        self.dex_calls = []

        def dex_get(_self, path, params=None, timeout=10):
            self.dex_calls.append(path)
            if self.dex_down:
                return None
            if path.startswith('/latest/dex/search'):
                q = params['q'].upper()
                return {'pairs': [p for p in self.pairs
                                  if q in p['baseToken']['symbol'].upper()
                                  or q == p['baseToken']['address'].upper()]}
            if path.startswith('/latest/dex/pairs/'):
                chain, addrs = path[len('/latest/dex/pairs/'):].split('/', 1)
                want = {a.lower() for a in addrs.split(',')}
                return {'pairs': [p for p in self.pairs if p['chainId'] == chain
                                  and p['pairAddress'].lower() in want]}
            if path.startswith('/token-pairs/v1/'):
                chain, addr = path[len('/token-pairs/v1/'):].split('/', 1)
                return [p for p in self.pairs if p['chainId'] == chain
                        and p['baseToken']['address'].lower() == addr.lower()]
            raise AssertionError(f'unexpected DexScreener call {path}')

        def gecko_get(_self, path, params=None, timeout=15):
            self.dex_calls.append(path)
            if self.dex_down:
                return None
            if path == '/search/pools':
                chain, q = params['network'], params['query'].upper()
                hits = [p for p in self.pairs if p['chainId'] == chain
                        and q in p['baseToken']['symbol'].upper()]
                return {'data': [self._gecko_pool(p, chain) for p in hits]}
            if path.endswith('/pools'):
                chain = path.split('/')[2]
                if params.get('page', 1) > 1:
                    return {'data': []}
                return {'data': [self._gecko_pool(p, chain)
                                 for p in self.pairs if p['chainId'] == chain]}
            if '/ohlcv/hour' in path:
                return {'data': {'attributes': {'ohlcv_list': self.candles}}}
            raise AssertionError(f'unexpected GeckoTerminal call {path}')

        self.dex_down = False
        for target, fake in (('_dex_get', dex_get), ('_gecko_get', gecko_get)):
            p = patch.object(Mod, target, fake)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _gecko_pool(p, chain):
        """The same pair, the way GeckoTerminal's pools/search routes shape it."""
        return {
            'attributes': {'name': f"{p['baseToken']['symbol']} / SOL",
                           'address': p['pairAddress'],
                           'base_token_price_usd': p['priceUsd'],
                           'reserve_in_usd': str(p['liquidity']['usd']),
                           'volume_usd': {'h24': str(p['volume']['h24'])},
                           'price_change_percentage': {'h24': '1.0'}},
            'relationships': {'base_token': {'data': {'id': f"{chain}_{p['baseToken']['address']}"}},
                              'dex': {'data': {'id': p['dexId']}}},
        }

    def tearDown(self):
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        super().tearDown()


class TestDexListing(DexTestBase):

    def test_a_symbol_takes_the_busiest_exact_match_not_the_deepest_namesake(self):
        # a "WIF" with a fabricated $50m of liquidity and no trade — the shape
        # of the fakes DexScreener search is full of. Volume is what it can't fake.
        self.pairs.append(_pair('solana', _sol('PAIRFAKE'), 'WIF', SOL_MINT_OTHER, 50_000_000, 0.00002, 9))
        self.pairs.append(_pair('solana', _sol('PAIRWIFE'), 'WIFE', _sol('MINTWIFE'), 90_000, 0.0003, 9_000_000))
        out = self.prefi.add_dex_market('solana', 'WIF')
        self.assertEqual(out['market']['dex_pair'], SOL_PAIR_WIF, out)
        rows = self.prefi.dex_assets('solana', search='WIF')
        self.assertEqual([r['coin'] for r in rows], ['WIF', 'WIF', 'WIFE'])   # exact first, then volume
        self.assertEqual(rows[0]['key'], SOL_PAIR_WIF)

    def test_the_default_floor_is_ten_thousand_dollars(self):
        self.assertEqual(self.prefi.dex_min_liquidity(), 10_000.0)

    def test_lists_a_solana_token_by_symbol_as_its_deepest_pool(self):
        out = self.prefi.add_dex_market('solana', 'wif')
        self.assertEqual(out['status'], 'added', out)
        m = out['market']
        self.assertEqual(m['symbol'], 'WIF.sol')
        self.assertEqual(m['source'], 'dex')
        self.assertEqual(m['chain'], 'solana')
        self.assertEqual(m['dex_pair'], SOL_PAIR_WIF)    # the $5m pool, not the $40k one
        self.assertEqual(m['dex_token'], SOL_MINT_WIF)
        self.assertEqual(m['quote'], 'USD')
        self.assertEqual(m['liquidity_usd'], 5_000_000)

    def test_lists_a_base_token_by_token_address(self):
        out = self.prefi.add_dex_market('base', '0x' + '9' * 40)
        self.assertIn('error', out)                      # nothing at that address
        # a token address resolves through token-pairs to its deepest pool
        out = self.prefi.add_dex_market('base', BASE_TOK_BRETT.upper().replace('0X', '0x'))
        self.assertEqual(out['status'], 'added', out)
        self.assertEqual(out['market']['symbol'], 'BRETT.base')
        self.assertEqual(out['market']['dex_pair'], BASE_PAIR_BRETT)
        self.assertEqual(out['market']['dex_name'], 'Brett')

    def test_lists_by_pool_address(self):
        out = self.prefi.add_dex_market('base', BASE_PAIR_BRETT)
        self.assertEqual(out['status'], 'added', out)
        self.assertEqual(out['market']['dex_pair'], BASE_PAIR_BRETT)
        self.assertEqual(out['market']['symbol'], 'BRETT.base')

    def test_a_thin_pool_is_refused_with_the_numbers(self):
        out = self.prefi.add_dex_market('solana', 'BONK')
        self.assertIn('error', out)
        self.assertIn('$900', out['error'])
        self.assertIn('$10,000', out['error'])
        self.assertEqual(out['min_liquidity_usd'], 10_000.0)
        self.assertEqual(self.prefi._load_json(self.prefi.markets_path, []), [])

    def test_the_owner_moves_the_floor(self):
        self.prefi.set_pool_config(min_liquidity_usd=500)
        self.assertEqual(self.prefi.dex_min_liquidity(), 500.0)
        self.assertEqual(self.prefi.add_dex_market('solana', 'BONK')['status'], 'added')
        self.prefi.set_pool_config(min_liquidity_usd=2_000_000)
        self.assertIn('error', self.prefi.add_dex_market('base', 'BRETT'))
        self.assertEqual(self.prefi.add_dex_market('solana', 'WIF')['status'], 'added')

    def test_zero_is_no_floor(self):
        self.prefi.set_pool_config(min_liquidity_usd=0)
        self.assertEqual(self.prefi.add_dex_market('base', 'THIN')['status'], 'added')

    def test_the_floor_cannot_be_negative(self):
        self.assertIn('error', self.prefi.set_pool_config(min_liquidity_usd=-1))

    def test_only_solana_and_base(self):
        out = self.prefi.add_dex_market('ethereum', 'PEPE')
        self.assertIn('error', out)
        self.assertIn('solana', out['error'])
        self.assertIn('error', self.prefi.add_dex_market('solana', 'PEPE'))

    def test_the_same_pool_lists_once(self):
        self.prefi.add_dex_market('solana', 'WIF')
        out = self.prefi.add_dex_market('solana', SOL_PAIR_WIF)
        self.assertIn('already listed', out['error'])
        self.assertEqual(len(self.prefi._load_json(self.prefi.markets_path, [])), 1)

    def test_a_ticker_clash_on_the_same_chain_gets_a_suffix(self):
        self.prefi.add_dex_market('solana', 'WIF')
        self.pairs.append(_pair('solana', _sol('PAIRWIFX'), 'WIF', SOL_MINT_OTHER, 60_000, 0.01))
        self.prefi._price_cache.clear()
        out = self.prefi.add_dex_market('solana', SOL_MINT_OTHER)
        self.assertEqual(out['status'], 'added', out)
        self.assertEqual(out['market']['symbol'], f'WIF.sol.{SOL_MINT_OTHER[:4]}')

    def test_the_same_ticker_on_hyperliquid_and_a_chain_are_different_markets(self):
        self.prefi.add_market('hl:WIF', 'WIF', 0, source='hyperliquid', hl_key='WIF')
        out = self.prefi.add_dex_market('solana', 'WIF')
        self.assertEqual(out['status'], 'added')
        self.assertEqual({m['symbol'] for m in self.prefi._load_json(self.prefi.markets_path, [])},
                         {'WIF', 'WIF.sol'})

    def test_feed_down_is_said_not_guessed(self):
        self.dex_down = True
        out = self.prefi.add_dex_market('solana', 'WIF')
        self.assertIn('unreachable', out['error'])

    def test_seed_lists_the_busiest_eligible_tokens(self):
        out = self.prefi.seed_dex('solana', limit=5)
        self.assertEqual(out['added'], ['WIF'])            # BONK is under the floor
        again = self.prefi.seed_dex('solana', limit=5)
        self.assertEqual(again['added'], [])
        self.assertEqual(again['existing'], ['WIF'])


class TestDexBrowser(DexTestBase):

    def test_default_list_is_the_chains_busiest_pools_with_eligibility(self):
        rows = self.prefi.dex_assets('base')
        self.assertEqual([r['coin'] for r in rows], ['BRETT', 'THIN'])
        self.assertEqual([r['eligible'] for r in rows], [True, False])
        self.assertTrue(all(r['min_liquidity_usd'] == 10_000 for r in rows))
        self.assertFalse(any(r['listed'] for r in rows))

    def test_search_goes_to_dexscreener_and_stays_on_the_chain(self):
        rows = self.prefi.dex_assets('solana', search='WIF')
        self.assertEqual(len(rows), 1)                     # one row per token: its deepest pool
        self.assertEqual(rows[0]['key'], SOL_PAIR_WIF)
        self.assertEqual(self.prefi.dex_assets('base', search='WIF'), [])

    def test_listed_rows_carry_the_market_symbol(self):
        self.prefi.add_dex_market('base', 'BRETT')
        rows = self.prefi.dex_assets('base', search='BRETT')
        self.assertTrue(rows[0]['listed'])
        self.assertEqual(rows[0]['symbol'], 'BRETT.base')

    def test_stats_report_the_floor(self):
        self.prefi.add_dex_market('solana', 'WIF')
        st = self.prefi.dex_stats('solana')
        self.assertEqual((st['pools'], st['eligible'], st['listed']), (2, 1, 1))
        self.assertEqual(st['min_liquidity_usd'], 10_000.0)
        self.assertTrue(st['reachable'])

    def test_the_universe_survives_the_feed_going_down(self):
        self.assertEqual(len(self.prefi.dex_assets('solana')), 2)
        self.dex_down = True
        self.prefi._price_cache.clear()                    # memory gone; disk copy remains
        self.assertEqual(len(self.prefi.dex_assets('solana')), 2)
        self.assertFalse(self.prefi.dex_stats('solana')['reachable'] is None)


class TestDexPricing(DexTestBase):

    def setUp(self):
        super().setUp()
        self.prefi.add_dex_market('solana', 'WIF')
        self.prefi.add_dex_market('base', 'BRETT')

    def test_markets_price_from_their_own_pool(self):
        markets = {m['symbol']: m for m in self.prefi.list_markets()}
        self.assertEqual(markets['WIF.sol']['price_usd'], 0.2)
        self.assertEqual(markets['BRETT.base']['price_usd'], 0.005)
        self.assertEqual(markets['WIF.sol']['liquidity_usd'], 5_000_000)
        self.assertTrue(markets['WIF.sol']['eligible'])

    def test_one_read_per_chain_prices_every_listed_pool(self):
        self.dex_calls.clear()
        self.prefi._price_cache.clear()
        self.prefi.list_markets()
        pair_calls = [c for c in self.dex_calls if c.startswith('/latest/dex/pairs/')]
        self.assertEqual(len(pair_calls), 2)               # solana, base

    def test_a_drained_pool_shows_as_ineligible_and_blocks_stakes(self):
        self.pairs[0]['liquidity'] = {'usd': 3_000}
        self.prefi._price_cache.clear()
        m = {x['symbol']: x for x in self.prefi.list_markets()}['WIF.sol']
        self.assertIs(m['eligible'], False)
        self.assertEqual(m['liquidity_usd'], 3_000)
        self.assertEqual(self.prefi._dex_liquidity('WIF.sol'), 3_000)
        out = self.prefi.pool.free_stake('0x' + '1' * 40, 'WIF.sol', 0.25)
        self.assertIn('under the $10,000 liquidity floor', out['error'])
        # lift the floor and the same pool takes the call
        self.prefi.set_pool_config(min_liquidity_usd=0)
        out = self.prefi.pool.free_stake('0x' + '1' * 40, 'WIF.sol', 0.25)
        self.assertNotIn('error', out)

    def test_feed_down_means_no_stake_not_a_guess(self):
        self.prefi._price_cache.clear()
        self.dex_down = True
        self.assertIsNone({x['symbol']: x for x in self.prefi.list_markets()}['WIF.sol']['eligible'])
        out = self.prefi.pool.free_stake('0x' + '1' * 40, 'WIF.sol', 0.25)
        self.assertIn('unreachable', out['error'])

    def test_settlement_reads_the_pools_hourly_candle(self):
        ts = 1_700_000_000                                 # on the hour
        self.candles = [[ts + 3600, 0.31, 0, 0, 0, 0], [ts, 0.30, 0, 0, 0, 0],
                        [ts - 3600, 0.29, 0, 0, 0, 0]]
        out = self.prefi._price_at('WIF.sol', ts + 600, 'dex')
        self.assertEqual(out, {'price': 0.30, 'mode': 'historical'})

    def test_snapshots_settle_when_the_candle_feed_cannot(self):
        ts = time.time() - 7200
        hist = {'WIF.sol'.upper(): [[round(ts - 300), 0.21], [round(ts + 120), 0.22]]}
        self.prefi._save_json(self.prefi.dex_history_path, hist)
        self.candles = []
        out = self.prefi._price_at('WIF.sol', ts, 'dex')
        self.assertEqual(out['price'], 0.22)
        self.assertEqual(out['mode'], 'historical')
        self.assertEqual(out['via'], 'snapshot')

    def test_nothing_near_the_close_falls_back_to_spot_and_says_so(self):
        self.candles = []
        out = self.prefi._price_at('WIF.sol', time.time() - 86400 * 3, 'dex')
        self.assertEqual(out, {'price': 0.2, 'mode': 'spot'})

    def test_reading_a_price_records_a_history_point(self):
        hist = self.prefi._load_json(self.prefi.dex_history_path, {})
        self.assertEqual([p[1] for p in hist['WIF.SOL']], [0.2])
        self.prefi.dex_snapshot()                          # too soon: no second point
        hist = self.prefi._load_json(self.prefi.dex_history_path, {})
        self.assertEqual(len(hist['WIF.SOL']), 1)


class TestDexCLI(DexTestBase):

    def test_add_sol_and_add_base_through_the_cli(self):
        self.assertEqual(self.prefi.forward('add-sol', address='WIF')['status'], 'added')
        self.assertEqual(self.prefi.forward('add-base', address='BRETT')['status'], 'added')
        self.assertIn('error', self.prefi.forward('add-dex', chain='base', address='THIN'))
        self.assertEqual(self.prefi.forward('dex-stats', chain='base')['listed'], 1)
        self.assertEqual(len(self.prefi.forward('dex-assets', chain='solana', limit=1)), 1)
        self.assertEqual(self.prefi.forward('pool-set', min_liquidity_usd=1)['min_liquidity_usd'], 1.0)
