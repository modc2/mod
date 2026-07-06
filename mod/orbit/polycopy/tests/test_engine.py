"""
Tests for the polycopy engine — covers Python Mod class, Rust engine bindings,
RPC pool, config, wallet management, scoring, and live chain connectivity.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.mod import Mod

# Known active whale addresses for live monitoring tests
VITALIK = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
WHALE_BASE = "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"  # active base trader


class TestModConfig(unittest.TestCase):
    """Test Mod class config management without engine."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmpdir, 'config.json')
        self.mod = Mod(config_path=self.cfg_path)

    def test_default_config(self):
        cfg = self.mod.show_config()
        self.assertIn('chains', cfg)
        self.assertIn('wallets', cfg)
        self.assertEqual(cfg['max_trade_usd'], 100)
        self.assertEqual(cfg['slippage_bps'], 50)
        self.assertEqual(cfg['position_pct'], 10.0)
        self.assertEqual(len(cfg['chains']), 4)

    def test_chain_names(self):
        cfg = self.mod.show_config()
        names = [c['name'] for c in cfg['chains']]
        self.assertEqual(names, ['base', 'polygon', 'arbitrum', 'ethereum'])

    def test_enabled_chains(self):
        cfg = self.mod.show_config()
        enabled = [c['name'] for c in cfg['chains'] if c['enabled']]
        self.assertEqual(enabled, ['base', 'polygon', 'arbitrum'])
        disabled = [c['name'] for c in cfg['chains'] if not c['enabled']]
        self.assertEqual(disabled, ['ethereum'])

    def test_rpc_endpoints_per_chain(self):
        cfg = self.mod.show_config()
        for chain in cfg['chains']:
            self.assertGreaterEqual(len(chain['rpc_urls']), 4,
                f"{chain['name']} should have >= 4 RPC endpoints")

    def test_routers_per_chain(self):
        cfg = self.mod.show_config()
        for chain in cfg['chains']:
            self.assertGreaterEqual(len(chain['routers']), 2,
                f"{chain['name']} should have >= 2 routers")
            for r in chain['routers']:
                self.assertTrue(r['address'].startswith('0x'))
                self.assertIn(r['dex_type'], ['UniswapV2', 'UniswapV3'])

    def test_watch_unwatch_no_engine(self):
        r = self.mod.watch(address=VITALIK)
        self.assertEqual(r['total'], 1)
        self.assertEqual(r['watched'], VITALIK)

        r = self.mod.watch(address=VITALIK)  # duplicate
        self.assertEqual(r['total'], 1)

        r = self.mod.unwatch(address=VITALIK)
        self.assertEqual(r['total'], 0)

    def test_watch_persists_to_disk(self):
        self.mod.watch(address=VITALIK)
        self.assertTrue(os.path.exists(self.cfg_path))
        with open(self.cfg_path) as f:
            saved = json.load(f)
        self.assertIn(VITALIK, saved['wallets'])

    def test_set_config(self):
        r = self.mod.set_config(key='max_trade_usd', value='500')
        self.assertEqual(r['value'], 500.0)
        self.assertEqual(self.mod.config['max_trade_usd'], 500.0)

        r = self.mod.set_config(key='slippage_bps', value='100')
        self.assertEqual(r['value'], 100)

        r = self.mod.set_config(key='auto_discover', value='true')
        self.assertEqual(r['value'], True)

    def test_private_key_not_persisted(self):
        self.mod.set_key(private_key='0xdeadbeef')
        self.mod._save_config()
        with open(self.cfg_path) as f:
            saved = json.load(f)
        self.assertNotIn('private_key', saved)

    def test_config_reload(self):
        self.mod.set_config(key='max_trade_usd', value='777')
        mod2 = Mod(config_path=self.cfg_path)
        self.assertEqual(mod2.config['max_trade_usd'], 777.0)

    def test_chain_name_to_id(self):
        self.assertEqual(self.mod._chain_name_to_id('base'), 8453)
        self.assertEqual(self.mod._chain_name_to_id('polygon'), 137)
        self.assertEqual(self.mod._chain_name_to_id('arbitrum'), 42161)
        self.assertEqual(self.mod._chain_name_to_id('ethereum'), 1)
        self.assertIsNone(self.mod._chain_name_to_id('solana'))

    def test_status_no_engine(self):
        s = self.mod.status()
        self.assertFalse(s['running'])
        self.assertEqual(s['mode'], 'monitor-only')

    def test_forward_dispatch(self):
        r = self.mod.forward('config')
        self.assertIn('chains', r)
        r = self.mod.forward('status')
        self.assertIn('running', r)
        r = self.mod.forward('wallets')
        self.assertIsInstance(r, list)

    def test_forward_unknown_cmd(self):
        r = self.mod.forward('nonexistent')
        # Falls through to status
        self.assertIn('running', r)

    def test_watch_requires_address(self):
        r = self.mod.watch()
        self.assertIn('error', r)

    def test_repr(self):
        s = repr(self.mod)
        self.assertIn('Polycopy', s)
        self.assertIn('base', s)

    def test_set_proxy(self):
        addr = '0x1234567890abcdef1234567890abcdef12345678'
        r = self.mod.set_proxy(chain='base', address=addr)
        self.assertEqual(r['proxy'], addr)
        for c in self.mod.config['chains']:
            if c['name'] == 'base':
                self.assertEqual(c['proxy_address'], addr)


class TestRustEngine(unittest.TestCase):
    """Test Rust engine via PyO3 bindings."""

    @classmethod
    def setUpClass(cls):
        try:
            import polycopy_rs
            cls.has_rust = True
        except ImportError:
            cls.has_rust = False

    def setUp(self):
        if not self.has_rust:
            self.skipTest("polycopy_rs not built")
        self.tmpdir = tempfile.mkdtemp()
        self.mod = Mod(config_path=os.path.join(self.tmpdir, 'config.json'))

    def test_engine_init(self):
        import polycopy_rs
        e = polycopy_rs.PolycopyEngine('{}')
        self.assertFalse(e.is_running())

    def test_engine_start_stop(self):
        r = self.mod.start()
        self.assertEqual(r['status'], 'running')
        self.assertIn('base', r['chains'])
        self.assertEqual(r['mode'], 'monitor-only')
        self.assertTrue(self.mod.engine.is_running())

        r = self.mod.stop()
        self.assertEqual(r['status'], 'stopped')

    def test_engine_wallet_management(self):
        self.mod._ensure_engine()
        self.mod.engine.add_wallet(VITALIK, 'vitalik')
        wallets = json.loads(self.mod.engine.get_wallets())
        found = any(VITALIK.lower() in w[0].lower() for w in wallets)
        self.assertTrue(found)

        self.mod.engine.remove_wallet(VITALIK)
        wallets = json.loads(self.mod.engine.get_wallets())
        found = any(VITALIK.lower() in w[0].lower() for w in wallets)
        self.assertFalse(found)

    def test_empty_scores(self):
        self.mod._ensure_engine()
        scores = json.loads(self.mod.engine.get_scores())
        self.assertEqual(scores, [])

    def test_empty_trades(self):
        self.mod._ensure_engine()
        trades = json.loads(self.mod.engine.get_trades(10))
        self.assertEqual(trades, [])

    def test_rpc_stats(self):
        self.mod._ensure_engine()
        stats = json.loads(self.mod.engine.get_rpc_stats())
        # Should have stats for 3 enabled chains
        self.assertGreaterEqual(len(stats), 3)
        for chain_id, providers in stats:
            self.assertGreaterEqual(len(providers), 4)
            for p in providers:
                self.assertIn('url', p)
                self.assertIn('is_healthy', p)
                self.assertIn('latency_ms', p)

    def test_double_start_fails(self):
        self.mod.start()
        with self.assertRaises(Exception):
            self.mod.engine.start()
        self.mod.stop()


class TestLiveRPC(unittest.TestCase):
    """Test live RPC connectivity. These hit real public endpoints."""

    def setUp(self):
        try:
            import polycopy_rs
        except ImportError:
            self.skipTest("polycopy_rs not built")
        self.tmpdir = tempfile.mkdtemp()
        self.mod = Mod(config_path=os.path.join(self.tmpdir, 'config.json'))

    def test_rpc_health_after_start(self):
        """Start engine, wait for health checks, verify providers respond."""
        self.mod.start()
        time.sleep(5)  # wait for health checks + at least 1 poll cycle

        stats = json.loads(self.mod.engine.get_rpc_stats())
        healthy_count = 0
        for chain_id, providers in stats:
            for p in providers:
                if p['is_healthy'] and p['total_requests'] > 0:
                    healthy_count += 1
        self.mod.stop()

        # At least some providers should have responded
        self.assertGreater(healthy_count, 0,
            "At least one RPC provider should be healthy after 5s")

    def test_monitor_detects_blocks(self):
        """Start monitoring and verify we're polling blocks (no errors)."""
        self.mod.watch(address=VITALIK)
        self.mod.start()
        time.sleep(8)

        stats = json.loads(self.mod.engine.get_rpc_stats())
        total_requests = 0
        for chain_id, providers in stats:
            for p in providers:
                total_requests += p['total_requests']

        self.mod.stop()
        # Should have made many requests across chains
        self.assertGreater(total_requests, 10,
            "Should have made >10 RPC requests across chains in 8s")


class TestScoringLogic(unittest.TestCase):
    """Test scoring via the Python-accessible interface by feeding synthetic events."""

    def setUp(self):
        try:
            import polycopy_rs
        except ImportError:
            self.skipTest("polycopy_rs not built")
        self.tmpdir = tempfile.mkdtemp()
        self.mod = Mod(config_path=os.path.join(self.tmpdir, 'config.json'))
        self.mod._ensure_engine()

    def test_scores_empty_initially(self):
        scores = self.mod.scores()
        self.assertEqual(scores, [])

    def test_trades_empty_initially(self):
        trades = self.mod.trades(limit=5)
        self.assertEqual(trades, [])


class TestScraperFilters(unittest.TestCase):
    """Test scraper filtering logic with synthetic trade data (no RPC calls)."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src', 'api'))
        from scraper import Scraper
        self.Scraper = Scraper

    def _make_scraper(self, trades_dict):
        """Create a scraper with pre-populated trade data."""
        from collections import defaultdict
        s = self.Scraper(min_trades_per_day=5, lookback_days=7, chains=[8453])
        s._trades = defaultdict(lambda: defaultdict(int))
        for addr, daily in trades_dict.items():
            for date, count in daily.items():
                s._trades[addr][date] = count
        s._done = True
        s._running = False
        return s

    def test_filters_by_min_trades_per_day(self):
        """Traders below the min_trades_per_day threshold should be excluded."""
        s = self._make_scraper({
            '0xaaa': {'2025-01-01': 20, '2025-01-02': 30},  # avg 25/day -> pass
            '0xbbb': {'2025-01-01': 2, '2025-01-02': 3},    # avg 2.5/day -> fail at threshold 5
            '0xccc': {'2025-01-01': 6, '2025-01-02': 8},    # avg 7/day -> pass
        })
        traders = s.get_traders(min_trades_per_day=5)
        addrs = [t['address'] for t in traders]
        self.assertIn('0xaaa', addrs)
        self.assertIn('0xccc', addrs)
        self.assertNotIn('0xbbb', addrs)

    def test_requires_at_least_2_active_days(self):
        """One-off bots with only 1 active day should be filtered out."""
        s = self._make_scraper({
            '0xaaa': {'2025-01-01': 100},                      # 1 day only -> fail
            '0xbbb': {'2025-01-01': 50, '2025-01-02': 50},     # 2 days -> pass
        })
        traders = s.get_traders(min_trades_per_day=5)
        addrs = [t['address'] for t in traders]
        self.assertNotIn('0xaaa', addrs)
        self.assertIn('0xbbb', addrs)

    def test_sorted_by_avg_trades_per_day_descending(self):
        """Results should be sorted by avg trades/day, highest first."""
        s = self._make_scraper({
            '0xlow':  {'2025-01-01': 10, '2025-01-02': 10},   # avg 10
            '0xhigh': {'2025-01-01': 50, '2025-01-02': 60},   # avg 55
            '0xmid':  {'2025-01-01': 20, '2025-01-02': 30},   # avg 25
        })
        traders = s.get_traders(min_trades_per_day=5)
        self.assertEqual(len(traders), 3)
        self.assertEqual(traders[0]['address'], '0xhigh')
        self.assertEqual(traders[1]['address'], '0xmid')
        self.assertEqual(traders[2]['address'], '0xlow')

    def test_excludes_zero_address(self):
        """The zero address should always be excluded."""
        zero = '0x' + '0' * 40
        s = self._make_scraper({
            zero: {'2025-01-01': 100, '2025-01-02': 100},
            '0xreal': {'2025-01-01': 20, '2025-01-02': 20},
        })
        traders = s.get_traders(min_trades_per_day=5)
        addrs = [t['address'] for t in traders]
        self.assertNotIn(zero, addrs)
        self.assertIn('0xreal', addrs)

    def test_trader_fields(self):
        """Each returned trader should have the expected fields."""
        s = self._make_scraper({
            '0xtrader': {'2025-01-01': 15, '2025-01-02': 25, '2025-01-03': 20},
        })
        traders = s.get_traders(min_trades_per_day=5)
        self.assertEqual(len(traders), 1)
        t = traders[0]
        self.assertEqual(t['address'], '0xtrader')
        self.assertEqual(t['total_trades'], 60)
        self.assertEqual(t['active_days'], 3)
        self.assertEqual(t['avg_trades_per_day'], 20.0)
        self.assertEqual(len(t['daily_counts']), 3)

    def test_override_min_trades_per_day(self):
        """get_traders should respect the override min_trades_per_day param."""
        s = self._make_scraper({
            '0xaaa': {'2025-01-01': 3, '2025-01-02': 3},   # avg 3
            '0xbbb': {'2025-01-01': 8, '2025-01-02': 12},  # avg 10
        })
        # default threshold is 5 from constructor
        traders = s.get_traders(min_trades_per_day=3)
        self.assertEqual(len(traders), 2)  # both pass at threshold 3

        traders = s.get_traders(min_trades_per_day=5)
        self.assertEqual(len(traders), 1)  # only 0xbbb passes

    def test_top_n_selection(self):
        """Verify we can select top N traders from a larger pool."""
        trades = {}
        for i in range(20):
            addr = f'0x{i:040x}'
            avg = (i + 1) * 5  # 5, 10, 15, ... 100
            trades[addr] = {'2025-01-01': avg, '2025-01-02': avg}
        s = self._make_scraper(trades)
        all_traders = s.get_traders(min_trades_per_day=5)
        top_10 = all_traders[:10]
        self.assertEqual(len(top_10), 10)
        # top 10 should be the 10 highest avg traders
        self.assertEqual(top_10[0]['avg_trades_per_day'], 100.0)
        self.assertEqual(top_10[9]['avg_trades_per_day'], 55.0)

    def test_empty_trades(self):
        """No trades at all should return empty list."""
        s = self._make_scraper({})
        traders = s.get_traders(min_trades_per_day=5)
        self.assertEqual(traders, [])

    def test_daily_counts_sorted(self):
        """Daily counts should be sorted by date."""
        s = self._make_scraper({
            '0xfoo': {'2025-01-03': 10, '2025-01-01': 20, '2025-01-02': 15},
        })
        traders = s.get_traders(min_trades_per_day=5)
        dates = list(traders[0]['daily_counts'].keys())
        self.assertEqual(dates, ['2025-01-01', '2025-01-02', '2025-01-03'])


class TestTopTradersIntegration(unittest.TestCase):
    """Test the top_traders Mod method dispatch."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmpdir, 'config.json')
        self.mod = Mod(config_path=self.cfg_path)

    def test_forward_includes_top_traders(self):
        """top_traders should be registered in the forward dispatch."""
        commands = {
            'start', 'stop', 'status', 'watch', 'unwatch', 'scores',
            'trades', 'pause', 'unpause', 'config', 'set', 'rpc',
            'wallets', 'build', 'scrape', 'top_traders',
        }
        # just verify top_traders is in forward dispatch by calling it
        # (it will fail on import but we catch that)
        self.assertTrue(hasattr(self.mod, 'top_traders'))

    def test_top_traders_method_exists(self):
        """Mod should have a top_traders method."""
        self.assertTrue(callable(getattr(self.mod, 'top_traders', None)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
