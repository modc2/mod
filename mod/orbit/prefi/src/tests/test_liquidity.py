"""The liquidity layer — list the whole universe behind the owner's floors,
pause/resume what crosses them, owner-only pause/remove, stake-time volume
floor. Hermetic: HL and bt feeds are the fakes from test_prefi.

    python3 -m pytest tests/test_liquidity.py -q      # from src/
"""

import os
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from mod import Mod
import pool as pool_mod
from pool import Pool
from test_prefi import PrefiTestBase, TestHyperliquidUniverse, TestBittensorMarkets
from test_pool import PoolTestBase, ALICE

OWNER = '0x' + 'ab' * 20


class LiquidityBase(PrefiTestBase):
    """A Mod with both the HL and bt fakes live and the pool unclaimed."""

    def setUp(self):
        super().setUp()
        os.environ['PREFI_UNSAFE_NO_SIG'] = '1'
        hl = TestHyperliquidUniverse
        mod_get, post = self._fake_hl(mids=hl.MIDS, perps=hl.PERPS, spot=hl.SPOT,
                                      tokens=hl.TOKENS, ctxs=hl.CTXS,
                                      spot_ctxs=hl.SPOT_CTXS)
        bt_rows = TestBittensorMarkets.ROWS
        now = {str(r['netuid']): r['price'] for r in bt_rows}

        def call(_self, tool, args=None, timeout=15):
            if tool == 'bt_screener':
                return {'rows': bt_rows, 'count': len(bt_rows)}
            if tool == 'bt_prices_at':
                return {'prices': now}
            raise RuntimeError(f'unexpected bt tool {tool}')

        self.patches = [mod_get, post, patch.object(Mod, '_bt_call', call),
                        patch.object(Mod, '_dex_get', return_value=None),
                        patch.object(Mod, '_gecko_get', return_value=None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        super().tearDown()

    def set_policy(self, **kw):
        out = self.prefi.pool.set_config(**kw)
        self.assertNotIn('error', out, out)
        return out

    def markets(self):
        return {m['symbol']: m for m in self.prefi._load_json(self.prefi.markets_path, [])}


class TestSyncListsTheUniverse(LiquidityBase):

    def test_zero_floors_list_every_pair_and_subnet(self):
        out = self.prefi.sync_markets()
        self.assertEqual(out['dry_run'], False)
        hl, bt = out['sources']['hyperliquid'], out['sources']['bittensor']
        # 2 live perps + 4 spot (DEAD is delisted, #10010 is an event leg).
        self.assertEqual(hl['universe'], 6)
        self.assertEqual(sorted(hl['added']), ['@9', 'BTC', 'FUN/USDC', 'HYPE/USDC', 'PURR/USDC', 'SOL'])
        self.assertEqual(sorted(bt['added']), ['SN1', 'SN51', 'SN64'])
        self.assertEqual(out['added'], 9)
        self.assertEqual(out['markets'], 9)
        m = self.markets()
        self.assertEqual(m['HYPE/USDC']['hl_key'], '@1')
        self.assertEqual(m['SN64']['bt_netuid'], 64)
        self.assertTrue(all(x['active'] for x in m.values()))
        # DEX feed is down in this fixture: reported unreachable, nothing listed.
        self.assertFalse(out['sources']['dex']['reachable'])

    def test_sync_is_idempotent(self):
        self.prefi.sync_markets()
        again = self.prefi.sync_markets()
        self.assertEqual((again['added'], again['paused'], again['resumed']), (0, 0, 0))
        self.assertEqual(again['markets'], 9)

    def test_dry_run_writes_nothing(self):
        out = self.prefi.sync_markets(dry_run=True)
        self.assertEqual(out['added'], 9)
        self.assertEqual(self.markets(), {})

    def test_floors_narrow_the_listing(self):
        self.set_policy(hl_min_volume_usd=2000, bt_min_volume_tao=6000)
        out = self.prefi.sync_markets()
        self.assertEqual(sorted(out['sources']['hyperliquid']['added']), ['HYPE/USDC', 'SOL'])
        self.assertEqual(out['sources']['bittensor']['added'], ['SN51'])

    def test_sources_and_limit_narrow_the_pass(self):
        out = self.prefi.sync_markets(sources='bittensor', limit=2)
        self.assertEqual(list(out['sources']), ['bittensor'])
        self.assertEqual(out['sources']['bittensor']['added'], ['SN51', 'SN64'])   # busiest first
        self.assertIn('error', self.prefi.sync_markets(sources='coingecko'))

    def test_seeded_and_hand_listed_markets_are_recognised(self):
        self.prefi.add_hl_market('HYPE')              # listed as HYPE/USDC, key @1
        self.prefi.add_bt_market(64)
        out = self.prefi.sync_markets()
        self.assertNotIn('HYPE/USDC', out['sources']['hyperliquid']['added'])
        self.assertNotIn('SN64', out['sources']['bittensor']['added'])
        self.assertEqual(out['markets'], 9)


class TestSyncPausesAndResumes(LiquidityBase):

    def test_a_market_under_the_floor_is_paused_then_resumed(self):
        self.prefi.sync_markets()
        self.set_policy(hl_min_volume_usd=2000)
        out = self.prefi.sync_markets()
        self.assertEqual(sorted(out['sources']['hyperliquid']['paused']),
                         ['@9', 'BTC', 'FUN/USDC', 'PURR/USDC'])
        btc = self.markets()['BTC']
        self.assertFalse(btc['active'])
        self.assertEqual(btc['paused_by'], 'sync')
        self.assertIn('under the $2,000 volume floor', btc['paused_reason'])
        # The pool refuses a stake on it, naming the pause.
        self.assertIn('paused', self.prefi.pool._pool_market('BTC')['error'])
        # Floor comes back down: the sync resumes what it paused.
        self.set_policy(hl_min_volume_usd=0)
        out = self.prefi.sync_markets()
        self.assertEqual(sorted(out['sources']['hyperliquid']['resumed']),
                         ['@9', 'BTC', 'FUN/USDC', 'PURR/USDC'])
        self.assertTrue(self.markets()['BTC']['active'])

    def test_an_owner_pause_survives_the_sync(self):
        self.prefi.sync_markets()
        self.assertEqual(self.prefi.pause_market('SOL', 'manual')['status'], 'paused')
        out = self.prefi.sync_markets()
        self.assertEqual(out['resumed'], 0)
        sol = self.markets()['SOL']
        self.assertFalse(sol['active'])
        self.assertEqual(sol['paused_by'], 'owner')

    def test_a_pair_the_feed_dropped_is_paused(self):
        self.prefi.add_market('hl:GONE', 'GONE', 0, source='hyperliquid', hl_key='GONE')
        out = self.prefi.sync_markets(sources='hyperliquid', do_list=False)
        self.assertEqual(out['sources']['hyperliquid']['paused'], ['GONE'])
        self.assertIn('no longer quotes', self.markets()['GONE']['paused_reason'])

    def test_an_unreachable_feed_pauses_nothing(self):
        self.prefi.sync_markets()
        self.set_policy(hl_min_volume_usd=1e12)
        for key in ('_hl_universe', '_hl_index', '_hl_mids'):
            self.prefi._price_cache.pop(key, None)
        self.prefi.hl_universe_path.unlink(missing_ok=True)
        with patch.object(Mod, '_hl_post', side_effect=RuntimeError('429')), \
                patch.object(Mod, '_hl_mod_get', return_value=None):
            out = self.prefi.sync_markets(sources='hyperliquid')
        self.assertFalse(out['sources']['hyperliquid']['reachable'])
        self.assertEqual(out['paused'], 0)
        self.assertTrue(self.markets()['BTC']['active'])

    def test_disabling_pause_only_lists(self):
        self.prefi.sync_markets()
        self.set_policy(hl_min_volume_usd=2000)
        out = self.prefi.sync_markets(do_pause=False)
        self.assertEqual(out['paused'], 0)
        self.assertTrue(self.markets()['BTC']['active'])


class TestOwnerMarketActions(LiquidityBase):

    def test_pause_resume_remove_are_owner_only_once_claimed(self):
        self.prefi.sync_markets()
        self.prefi.pool.claim_owner(OWNER)
        self.assertEqual(self.prefi.pause_market('BTC')['error'], 'owner only')
        self.assertEqual(self.prefi.resume_market('BTC')['error'], 'owner only')
        self.assertEqual(self.prefi.remove_market('BTC')['error'], 'owner only')
        secret = self.prefi.pool.state()['owner_secret']
        self.assertEqual(self.prefi.pause_market('BTC', 'thin', secret=secret)['status'], 'paused')
        self.assertEqual(self.prefi.pause_market('BTC', secret=secret)['status'], 'unchanged')
        self.assertEqual(self.prefi.resume_market('BTC', secret=secret)['status'], 'resumed')
        self.assertTrue(self.markets()['BTC']['active'])
        self.assertNotIn('paused_reason', self.markets()['BTC'])
        out = self.prefi.remove_market('btc', secret=secret)       # case-insensitive
        self.assertEqual(out['status'], 'removed')
        self.assertNotIn('BTC', self.markets())
        self.assertIn('error', self.prefi.pause_market('NOPE', secret=secret))

    def test_remove_refuses_a_market_with_an_open_pool_entry(self):
        self.prefi.sync_markets()
        entries = [{'status': 'open', 'asset': 'SOL', 'round': 1, 'amount': 5.0}]
        self.prefi.pool._write(self.prefi.pool.entries_path, entries)
        out = self.prefi.remove_market('SOL')
        self.assertIn('1 open pool entries', out['error'])
        self.assertIn('SOL', self.markets())
        # A settled one does not block.
        entries[0]['status'] = 'settled'
        self.prefi.pool._write(self.prefi.pool.entries_path, entries)
        self.assertEqual(self.prefi.remove_market('SOL')['status'], 'removed')


class TestLiquidityReport(LiquidityBase):

    def test_report_ranks_thinnest_first_and_counts_per_source(self):
        self.prefi.sync_markets()
        self.set_policy(hl_min_volume_usd=2000)
        rep = self.prefi.liquidity()
        self.assertEqual(rep['policy']['hl_min_volume_usd'], 2000.0)
        hl = rep['sources']['hyperliquid']
        self.assertEqual((hl['universe'], hl['listed'], hl['active'], hl['under_floor']), (6, 6, 6, 4))
        self.assertEqual(rep['sources']['bittensor']['listed'], 3)
        # Under-floor rows first, thinnest first.
        rows = rep['markets']
        self.assertFalse(rows[0]['eligible'])
        # '@9' has no context at all (volume reads 0), PURR/USDC traded $1.
        self.assertEqual([r['symbol'] for r in rows[:2]], ['@9', 'PURR/USDC'])
        self.assertEqual(rows[0]['floor'], 2000.0)
        self.assertTrue(all(r['eligible'] for r in rows[4:]))
        self.assertEqual(len(self.prefi.liquidity(source='bittensor')['markets']), 3)
        self.assertEqual(len(self.prefi.liquidity(limit=2)['markets']), 2)

    def test_list_markets_carries_volume_and_eligibility(self):
        self.prefi.sync_markets()
        self.set_policy(bt_min_volume_tao=6000)
        rows = {m['symbol']: m for m in self.prefi.list_markets()}
        self.assertEqual(rows['SOL']['volume_24h'], 9000.0)
        self.assertEqual(rows['SOL']['change_24h'], -7.5)
        self.assertTrue(rows['SOL']['eligible'])
        self.assertEqual(rows['SN64']['volume_24h'], 5000.0)
        self.assertFalse(rows['SN64']['eligible'])
        self.assertEqual(rows['SN64']['volume_floor'], 6000.0)

    def test_status_counts_paused(self):
        self.prefi.sync_markets()
        self.prefi.pause_market('SOL')
        with patch.object(Mod, 'resolve_predictions', return_value=None):
            st = self.prefi.status()
        self.assertEqual((st['markets'], st['markets_listed'], st['markets_paused']), (8, 9, 1))

    def test_cli_doors(self):
        self.assertEqual(self.prefi.forward('sync', dry_run='true')['dry_run'], True)
        self.assertEqual(self.prefi.forward('policy')['auto_pause'], True)
        self.assertEqual(self.prefi.forward('sync', sources='bittensor')['added'], 3)
        self.assertEqual(self.prefi.forward('pause', symbol='SN64')['status'], 'paused')
        self.assertEqual(self.prefi.forward('resume', symbol='SN64')['status'], 'resumed')
        self.assertEqual(self.prefi.forward('liquidity', source='bittensor')['listed'], 3)
        self.assertEqual(self.prefi.forward('remove', symbol='SN1')['status'], 'removed')


class TestPolicyValidation(unittest.TestCase):

    def test_defaults_list_everything(self):
        cfg = pool_mod.DEFAULT_CONFIG
        self.assertEqual((cfg['hl_min_volume_usd'], cfg['bt_min_volume_tao'],
                          cfg['dex_min_volume_usd']), (0.0, 0.0, 0.0))
        self.assertFalse(cfg['auto_list'])
        self.assertTrue(cfg['auto_pause'])
        self.assertEqual(cfg['sync_interval'], 900)

    def test_rejects_bad_values(self):
        for bad in ({'hl_min_volume_usd': -1}, {'bt_min_volume_tao': -5},
                    {'dex_min_volume_usd': 'x'}, {'sync_interval': 30}):
            with self.assertRaises(ValueError):
                pool_mod.validate_config(bad)
        self.assertEqual(pool_mod.validate_config({'sync_interval': 0})['sync_interval'], 0)

    def test_volume_floor_by_source(self):
        cfg = {'hl_min_volume_usd': 10, 'bt_min_volume_tao': 20, 'dex_min_volume_usd': 30}
        self.assertEqual([pool_mod.volume_floor(cfg, s) for s in
                          ('hyperliquid', 'bittensor', 'dex', 'coingecko', None)],
                         [10.0, 20.0, 30.0, 0.0, 0.0])


class TestStakeTimeVolumeFloor(PoolTestBase):
    """The pool checks the per-source volume floor at stake time, like the
    DEX liquidity floor — no reading means no stake."""

    def setUp(self):
        super().setUp()
        self.volumes = {'BTC': 5_000_000.0, 'SN64': 100.0}
        self.pool._volume_now = lambda sym: self.volumes.get(sym)
        self.fund(ALICE, 100.0)

    def test_no_floor_means_no_check(self):
        self.volumes.clear()
        self.assertIn('market', self.pool._pool_market('BTC'))

    def test_floor_refuses_thin_books_and_missing_readings(self):
        self.pool.set_config(hl_min_volume_usd=1_000_000, bt_min_volume_tao=500)
        self.assertIn('market', self.pool._pool_market('BTC'))
        out = self.pool._pool_market('SN64')
        self.assertIn('τ100 in 24h', out['error'])
        self.assertIn('τ500 volume floor', out['error'])
        self.volumes.pop('BTC')
        self.assertIn('no volume reading', self.pool._pool_market('BTC')['error'])
        # And a real stake obeys it.
        out = self.pool.stake(ALICE, 'SN64', 0.09, 10.0)
        self.assertIn('volume floor', out['error'])

    def test_string_bools_from_the_cli_are_coerced(self):
        out = self.pool.set_config(auto_list='true', auto_pause='0')
        self.assertEqual((out['auto_list'], out['auto_pause']), (True, False))


if __name__ == '__main__':
    unittest.main()
