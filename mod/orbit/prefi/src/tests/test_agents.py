"""The local economy, the strategy sandbox, vibecoded agents and the MCP
dispatch. Hermetic: a temp store, a seeded feed, templates only (no
claudecode call — PREFI_VIBECODE_LLM=0), a simulated clock."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import agents as agents_mod                       # noqa: E402
import economy as economy_mod                     # noqa: E402
from agents import (Agents, Loop, StrategyError, check_strategy,   # noqa: E402
                    compile_strategy, extract_code, normalize_actions,
                    run_strategy, template_for, vibecode, TEMPLATES)
from economy import Economy, EconomyError         # noqa: E402


class EconomyBase(unittest.TestCase):

    def setUp(self):
        os.environ['PREFI_UNSAFE_NO_SIG'] = '1'
        os.environ['PREFI_VIBECODE_LLM'] = '0'
        self.dir = tempfile.mkdtemp(prefix='prefi-eco-')
        self.eco = Economy(self.dir, config={'seed': 42},
                           assets={'BTC': 100.0, 'ETH': 10.0})
        self.agents = Agents(self.dir, self.eco)
        self.loop = Loop(self.eco, self.agents)
        # 1s into the NEXT minute: always after genesis, so the
        # simulated clock never writes history behind the real one
        self.t0 = (int(time.time()) // 60 + 1) * 60 + 1

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        os.environ.pop('PREFI_VIBECODE_LLM', None)

    def advance(self, seconds, step=5):
        """Tick the economy through `seconds` of simulated time."""
        last = None
        for i in range(0, seconds + 1, step):
            last = self.loop.step(self.t0 + i)
        return last


# ── Economy ──────────────────────────────────────────────────────────

class TestFeed(EconomyBase):

    def test_seeded_feed_is_reproducible(self):
        a = Economy(self.dir + '/a', config={'seed': 7}, assets={'X': 1.0})
        b = Economy(self.dir + '/b', config={'seed': 7}, assets={'X': 1.0})
        for i in range(10):
            a.tick(self.t0 + i * 5)
            b.tick(self.t0 + i * 5)
        self.assertEqual(a.history('X'), b.history('X'))

    def test_tick_moves_prices_and_keeps_history(self):
        p0 = self.eco.price_now('BTC')
        for i in range(1, 20):
            self.eco.tick(self.t0 + i * 5)
        self.assertNotEqual(p0, self.eco.price_now('BTC'))
        self.assertEqual(len(self.eco.history('BTC', 1000)), 20)
        self.assertGreater(self.eco.price_now('BTC'), 0)

    def test_price_at_is_the_last_tick_at_or_before(self):
        for i in range(1, 5):
            self.eco.tick(self.t0 + i * 5)
        h = self.eco.history('BTC', 100)
        t, px = h[2]
        self.assertEqual(self.eco.price_at('BTC', t)['price'], px)
        self.assertEqual(self.eco.price_at('BTC', t + 3)['price'], px)
        self.assertEqual(self.eco.price_at('BTC', 0)['mode'], 'first-tick')
        self.assertEqual(self.eco.price_at('NOPE', t), {})

    def test_history_is_capped(self):
        self.eco.set_config(history=10)
        for i in range(1, 30):
            self.eco.tick(self.t0 + i * 5)
        self.assertEqual(len(self.eco.history('BTC', 1000)), 10)

    def test_add_asset_and_unknown_asset(self):
        out = self.eco.add_asset('doge', price=0.1)
        self.assertEqual(out, {'symbol': 'DOGE', 'price': 0.1})
        self.assertIn('DOGE', self.eco.prices())
        with self.assertRaises(EconomyError):
            self.eco.add_asset('ZZZ')              # no live quote, no default
        self.assertEqual(self.eco.add_asset('SOL')['symbol'], 'SOL')

    def test_config_validation(self):
        with self.assertRaises(EconomyError):
            self.eco.set_config(nope=1)
        with self.assertRaises(EconomyError):
            self.eco.set_config(feed='moon')
        with self.assertRaises(EconomyError):
            self.eco.set_config(liquidity=0)
        self.assertEqual(self.eco.set_config(vol=0.01)['vol'], 0.01)

    def test_state_survives_reload_and_backfills_config(self):
        self.eco.tick(self.t0 + 5)
        path = self.eco.path
        raw = json.loads(path.read_text())
        del raw['config']['spread_bps']
        path.write_text(json.dumps(raw))
        again = Economy(self.dir)
        self.assertEqual(again.config()['spread_bps'],
                         economy_mod.DEFAULT_CONFIG['spread_bps'])
        self.assertEqual(again.price_now('BTC'), self.eco.price_now('BTC'))

    def test_reset_wipes_everything(self):
        self.eco.faucet('0xa')
        self.eco.trade('0xa', 'BTC', 'buy', notional=10)
        self.eco.reset(assets={'Q': 5.0})
        self.assertEqual(list(self.eco.prices()), ['Q'])
        self.assertEqual(self.eco.status()['accounts'], 0)


class TestExchange(EconomyBase):

    def test_faucet_funds_both_ledgers_and_rate_limits(self):
        out = self.eco.faucet('0xa')
        self.assertEqual(out['granted'], 1000.0)
        self.assertEqual(out['pool_granted'], 1000.0)
        self.assertEqual(out['cash'], 1000.0)
        self.assertEqual(out['pool']['balance'], 1000.0)
        again = self.eco.faucet('0xa')
        self.assertEqual(again['granted'], 0.0)
        self.assertEqual(again['cash'], 1000.0)

    def test_buy_sell_round_trip_pays_the_spread(self):
        self.eco.faucet('0xa')
        buy = self.eco.trade('0xa', 'BTC', 'buy', notional=100)
        self.assertAlmostEqual(buy['cash'], 900.0)
        self.assertGreater(buy['price'], 100.0)          # paid half-spread
        acct = self.eco.account('0xa')
        self.assertIn('BTC', acct['holdings'])
        sell = self.eco.trade('0xa', 'BTC', 'sell', qty=buy['qty'])
        self.assertLess(sell['cash'], 1000.0)            # spread both ways
        self.assertNotIn('BTC', self.eco.account('0xa')['holdings'])
        self.assertLess(self.eco.account('0xa')['realized'], 0)

    def test_order_flow_moves_the_price(self):
        self.eco.faucet('0xa')
        before = self.eco.price_now('BTC')
        self.eco.trade('0xa', 'BTC', 'buy', notional=500)
        self.assertGreater(self.eco.price_now('BTC'), before)
        mid = self.eco.price_now('BTC')
        self.eco.trade('0xa', 'BTC', 'sell', notional=200)
        self.assertLess(self.eco.price_now('BTC'), mid)

    def test_refusals(self):
        with self.assertRaises(EconomyError):
            self.eco.trade('0xa', 'BTC', 'buy', notional=10)   # no cash
        self.eco.faucet('0xa')
        with self.assertRaises(EconomyError):
            self.eco.trade('0xa', 'BTC', 'sell', qty=1)       # nothing held
        with self.assertRaises(EconomyError):
            self.eco.trade('0xa', 'BTC', 'hodl', notional=1)
        with self.assertRaises(EconomyError):
            self.eco.trade('0xa', 'NOPE', 'buy', notional=1)
        with self.assertRaises(EconomyError):
            self.eco.trade('0xa', 'BTC', 'buy', notional=0)
        with self.assertRaises(EconomyError):
            self.eco.trade('0xa', 'BTC', 'buy', notional=5000)

    def test_tape_and_leaderboard(self):
        self.eco.faucet('0xa')
        self.eco.faucet('0xb')
        self.eco.trade('0xa', 'BTC', 'buy', notional=100)
        self.eco.trade('0xb', 'ETH', 'buy', notional=50)
        self.assertEqual(len(self.eco.trades()), 2)
        self.assertEqual(len(self.eco.trades('0xa')), 1)
        self.assertEqual(self.eco.trades()[0]['address'], '0xb')  # newest first
        board = self.eco.leaderboard(names={'0xa': 'alice'})
        self.assertEqual(len(board), 2)
        self.assertEqual([r for r in board if r['address'] == '0xa'][0]['name'],
                         'alice')
        self.assertTrue(all('net_worth' in r for r in board))


class TestMinutePool(EconomyBase):

    def test_round_is_a_minute_and_settles_on_the_stored_tick(self):
        self.assertEqual(self.eco.paper.config()['interval'], 60)
        self.eco.faucet('0xa')
        self.eco.tick(self.t0)
        px = self.eco.price_now('BTC')
        r = self.eco.predict('0xa', 'BTC', px, 10, now=self.t0)
        self.assertNotIn('error', r)
        self.assertEqual(r['round'], self.t0 // 60)
        # tick through the close
        for i in range(5, 70, 5):
            self.eco.tick(self.t0 + i)
        rnd = self.eco.paper.round(r['round'])
        pot = rnd['assets']['BTC']
        self.assertIsNotNone(pot['resolved'])
        close = self.eco.price_at('BTC', r['closes'])['price']
        self.assertAlmostEqual(pot['resolved']['actual'], close, places=5)
        self.assertTrue(self.eco.paper.verify()['ok'])

    def test_entry_cutoff_and_unknown_asset(self):
        self.eco.faucet('0xa')
        late = self.t0 + 57                          # inside the 5s cutoff
        r = self.eco.predict('0xa', 'BTC', 100.0, 1, now=late)
        self.assertIn('closed', r['error'])
        r = self.eco.predict('0xa', 'NOPE', 1.0, 1, now=self.t0)
        self.assertIn('unknown market', r['error'])


# ── Sandbox ──────────────────────────────────────────────────────────

class TestSandbox(unittest.TestCase):

    def test_every_template_compiles_and_runs(self):
        for name, tpl in TEMPLATES.items():
            out = check_strategy(tpl['code'])
            self.assertTrue(out['ok'], name)
            self.assertIsInstance(out['actions'], list)

    def test_refusals(self):
        bad = {
            'import': 'import os\ndef decide(ctx): return []',
            'from': 'from os import path\ndef decide(ctx): return []',
            'dunder': 'def decide(ctx): return ctx.__class__',
            'open': 'def decide(ctx): open("/etc/passwd")',
            'eval': 'def decide(ctx): eval("1")',
            'getattr': 'def decide(ctx): getattr(ctx, "x")',
            'class': 'class A: pass\ndef decide(ctx): return []',
            'nodecide': 'def go(ctx): return []',
            'syntax': 'def decide(ctx) return []',
            'global': 'g = 1\ndef decide(ctx):\n    global g\n    return []',
            'builtins': 'def decide(ctx): return __builtins__',
            'too_long': 'def decide(ctx): return []\n' + '#' * 7000,
        }
        for label, code in bad.items():
            with self.assertRaises(StrategyError, msg=label):
                compile_strategy(code)

    def test_runtime_error_and_timeout(self):
        fn = compile_strategy('def decide(ctx): return 1 / 0')
        with self.assertRaises(StrategyError) as cm:
            run_strategy(fn, agents_mod.mock_ctx())
        self.assertIn('ZeroDivisionError', str(cm.exception))
        slow = compile_strategy('def decide(ctx):\n    while True:\n        pass')
        with self.assertRaises(StrategyError) as cm:
            run_strategy(slow, agents_mod.mock_ctx(), timeout=0.2)
        self.assertIn('ran past', str(cm.exception))

    def test_bad_return_shapes(self):
        with self.assertRaises(StrategyError):
            normalize_actions(42)
        with self.assertRaises(StrategyError):
            normalize_actions([{'op': 'launch', 'asset': 'BTC'}])
        with self.assertRaises(StrategyError):
            normalize_actions([('fly', 'BTC')])
        with self.assertRaises(StrategyError):
            normalize_actions(['BTC'])

    def test_action_normalisation(self):
        acts = normalize_actions([
            ('predict', 'btc', 100, 5), ('buy', 'eth', 10),
            {'op': 'sell', 'asset': 'eth', 'qty': 0.5}, {'op': 'note', 'text': 'x'},
            {'op': 'hold'}, None,
        ][:5])
        self.assertEqual([a['op'] for a in acts], ['predict', 'buy', 'sell', 'note'])
        self.assertEqual(acts[0], {'op': 'predict', 'asset': 'BTC', 'price': 100.0, 'stake': 5.0})
        self.assertEqual(acts[2]['qty'], 0.5)
        self.assertEqual(normalize_actions(None), [])
        self.assertEqual(normalize_actions({'op': 'note', 'text': 'one'})[0]['op'], 'note')
        many = normalize_actions([{'op': 'note', 'text': 'n'}] * 50)
        self.assertEqual(len(many), agents_mod.MAX_ACTIONS)

    def test_math_and_random_in_scope(self):
        fn = compile_strategy(
            'def decide(ctx):\n'
            '    return [{"op": "note", "text": str(round(math.sqrt(16) + random.random() * 0))}]')
        self.assertEqual(run_strategy(fn, agents_mod.mock_ctx())[0]['text'], '4')

    def test_memory_persists_through_ctx(self):
        fn = compile_strategy(
            'def decide(ctx):\n'
            '    ctx["memory"]["n"] = ctx["memory"].get("n", 0) + 1\n'
            '    return []')
        ctx = agents_mod.mock_ctx()
        run_strategy(fn, ctx)
        run_strategy(fn, ctx)
        self.assertEqual(ctx['memory']['n'], 2)


# ── Vibecode ─────────────────────────────────────────────────────────

class TestVibecode(unittest.TestCase):

    def setUp(self):
        os.environ['PREFI_VIBECODE_LLM'] = '0'

    def tearDown(self):
        os.environ.pop('PREFI_VIBECODE_LLM', None)

    def test_template_matching(self):
        self.assertEqual(template_for('ride the trend'), 'momentum')
        self.assertEqual(template_for('fade the dip, contrarian'), 'meanrevert')
        self.assertEqual(template_for('just hodl'), 'hodl')
        self.assertEqual(template_for('random monkey'), 'random')
        self.assertEqual(template_for('yolo whale'), 'whale')
        self.assertEqual(template_for('something else entirely'),
                         agents_mod.DEFAULT_TEMPLATE)

    def test_vibecode_without_a_model_is_a_template(self):
        out = vibecode('fade every dip')
        self.assertEqual(out['source'], 'template')
        self.assertEqual(out['template'], 'meanrevert')
        self.assertIn('def decide', out['code'])
        with self.assertRaises(StrategyError):
            vibecode('')

    def test_llm_off_flag_and_unreachable_fallback(self):
        os.environ['PREFI_VIBECODE_LLM'] = '1'
        old = agents_mod.CLAUDECODE_URL
        agents_mod.CLAUDECODE_URL = 'http://127.0.0.1:9'    # nothing listens
        try:
            out = vibecode('ride momentum', llm=True)
        finally:
            agents_mod.CLAUDECODE_URL = old
        self.assertEqual(out['source'], 'template')
        self.assertTrue(any('unreachable' in n for n in out['notes']))

    def test_extract_code(self):
        self.assertEqual(extract_code('here\n```python\ndef decide(ctx):\n    return []\n```\nbye'),
                         'def decide(ctx):\n    return []')
        self.assertEqual(extract_code('```\ndef decide(ctx): return []\n```'),
                         'def decide(ctx): return []')
        self.assertTrue(extract_code('text def decide(ctx): return []').startswith('def decide'))
        self.assertIsNone(extract_code('no code here'))
        self.assertIsNone(extract_code(''))

    def test_model_code_that_fails_dry_run_falls_back(self):
        old = agents_mod._claudecode_write
        agents_mod._claudecode_write = lambda *a, **k: {'code': 'def decide(ctx): return 1/0'}
        os.environ['PREFI_VIBECODE_LLM'] = '1'
        try:
            out = vibecode('ride momentum')
        finally:
            agents_mod._claudecode_write = old
        self.assertEqual(out['source'], 'template')
        self.assertTrue(any('rejected' in n for n in out['notes']))

    def test_model_code_that_passes_is_used(self):
        old = agents_mod._claudecode_write
        agents_mod._claudecode_write = lambda *a, **k: {
            'code': 'def decide(ctx): return [{"op": "note", "text": "hi"}]',
            'task_id': 't1'}
        os.environ['PREFI_VIBECODE_LLM'] = '1'
        try:
            out = vibecode('say hi')
        finally:
            agents_mod._claudecode_write = old
        self.assertEqual(out['source'], 'claudecode')
        self.assertEqual(out['task_id'], 't1')
        self.assertEqual(out['dry_run'][0]['text'], 'hi')


# ── Agents ───────────────────────────────────────────────────────────

class TestAgents(EconomyBase):

    def test_create_funds_registers_and_derives_address(self):
        a = self.agents.create('mo', prompt='ride the trend')
        self.assertEqual(a['status'], 'running')
        self.assertEqual(a['template'], 'momentum')
        self.assertEqual(a['address'], agents_mod.address_for(a['id']))
        self.assertTrue(a['address'].startswith('0x'))
        self.assertEqual(a['account']['cash'], 1000.0)
        self.assertEqual(a['account']['pool']['balance'], 1000.0)
        self.assertEqual(a['account']['pool']['name'], 'mo')
        self.assertIn('dry_run', a)

    def test_create_refuses_bad_input(self):
        with self.assertRaises(StrategyError):
            self.agents.create('', prompt='x')
        with self.assertRaises(StrategyError):
            self.agents.create('x')                       # no prompt, no code
        with self.assertRaises(StrategyError):
            self.agents.create('x', code='import os\ndef decide(ctx): return []')

    def test_runs_every_round_and_logs_actions(self):
        self.agents.create('mo', prompt='ride the trend')
        self.agents.create('flat', prompt='stay flat and safe')
        self.agents.create('off', prompt='random', start=False)
        res = self.advance(125)
        a = self.agents.get('flat')
        self.assertGreaterEqual(a['runs'], 2)
        self.assertEqual(a['errors'], 0)
        last = a['log'][-1]
        self.assertTrue(all(x['op'] == 'predict' and 'error' not in x
                            for x in last['actions']), last)
        self.assertEqual(self.agents.get('off')['runs'], 0)
        self.assertIsNotNone(res['tick'])
        # the pool scored at least one of flat's calls on the way
        acct = self.agents.get('flat')['account']['pool']
        self.assertGreater(acct['resolved'], 0)
        self.assertIsNotNone(acct['accuracy'])
        self.assertTrue(self.eco.paper.verify()['ok'])

    def test_list_carries_leaderboard_rows_and_names(self):
        self.agents.create('mo', prompt='ride the trend')
        rows = self.agents.list()
        self.assertEqual(rows[0]['name'], 'mo')
        self.assertNotIn('code', rows[0])
        self.assertEqual(rows[0]['board']['name'], 'mo')
        self.assertEqual(rows[0]['board']['net_worth'], 2000.0)

    def test_update_pause_resume_code_and_revibe(self):
        a = self.agents.create('mo', prompt='ride the trend')
        self.agents.update(a['id'], status='paused')
        self.assertEqual(self.agents.get(a['id'])['status'], 'paused')
        with self.assertRaises(StrategyError):
            self.agents.update(a['id'], status='sleeping')
        self.agents.update(a['id'], code='def decide(ctx): return [("note", "k")]')
        self.assertEqual(self.agents.get('mo')['source'], 'user')
        with self.assertRaises(StrategyError):
            self.agents.update(a['id'], code='def decide(ctx): return open("x")')
        out = self.agents.update(a['id'], prompt='fade dips', revibe=True)
        self.assertEqual(out['template'], 'meanrevert')
        self.assertEqual(out['source'], 'template')
        entry = self.agents.run_one(a['id'], now=self.t0)
        self.assertIsNone(entry['error'])

    def test_lookup_by_name_and_address_and_delete(self):
        a = self.agents.create('mo', prompt='ride the trend')
        self.assertEqual(self.agents.get('mo')['id'], a['id'])
        self.assertEqual(self.agents.get(a['address'])['id'], a['id'])
        self.assertEqual(self.agents.delete('mo')['deleted'], a['id'])
        with self.assertRaises(StrategyError):
            self.agents.get('mo')
        with self.assertRaises(StrategyError):
            self.agents.delete('mo')

    def test_strategy_error_is_logged_not_raised(self):
        # passes the dry run (mock ctx is named 'dry-run'), blows up live
        a = self.agents.create('boom', code=(
            "def decide(ctx): return [] if ctx['name'] == 'dry-run' else 1/0"))
        entry = self.agents.run_one(a['id'], now=self.t0)
        self.assertIn('ZeroDivisionError', entry['error'])
        rec = self.agents.get(a['id'])
        self.assertEqual(rec['errors'], 1)
        self.assertEqual(rec['runs'], 1)

    def test_bad_actions_are_recorded_per_action(self):
        a = self.agents.create('greedy', code=(
            'def decide(ctx):\n'
            '    return [{"op": "buy", "asset": "BTC", "notional": 1e9},\n'
            '            {"op": "predict", "asset": "BTC", "price": ctx["prices"]["BTC"], "stake": 2}]'))
        entry = self.agents.run_one(a['id'], now=self.t0)
        self.assertIsNone(entry['error'])
        self.assertIn('insufficient cash', entry['actions'][0]['error'])
        self.assertNotIn('error', entry['actions'][1])

    def test_memory_persists_between_runs(self):
        a = self.agents.create('mem', code=(
            'def decide(ctx):\n'
            '    ctx["memory"]["n"] = ctx["memory"].get("n", 0) + 1\n'
            '    return []'))
        self.agents.run_one(a['id'], now=self.t0)
        self.agents.run_one(a['id'], now=self.t0 + 60)
        self.assertEqual(self.agents.get(a['id'])['memory']['n'], 2)

    def test_registry_survives_reload(self):
        a = self.agents.create('mo', prompt='ride the trend')
        again = Agents(self.dir, self.eco)
        self.assertEqual(again.get(a['id'])['name'], 'mo')

    def test_loop_runs_once_per_round(self):
        self.agents.create('flat', prompt='stay flat')
        runs = []
        for i in range(0, 60, 5):
            r = self.loop.step(self.t0 + i)
            if r['run']:
                runs.append(r['run']['round'])
        self.assertEqual(len(runs), 1)
        r = self.loop.step(self.t0 + 61)
        self.assertIsNotNone(r['run'])
        self.assertEqual(r['run']['round'], runs[0] + 1)

    def test_loop_thread_starts_and_stops(self):
        self.loop.start()
        self.assertTrue(self.loop.status()['alive'])
        self.loop.stop.set()
        self.loop.thread.join(2)
        self.assertFalse(self.loop.status()['alive'])


# ── MCP ──────────────────────────────────────────────────────────────

class TestMCP(EconomyBase):

    def setUp(self):
        super().setUp()
        import mcp_server
        self.mcp = mcp_server

        class FakeMod:
            economy = self.eco
            agents = self.agents
            loop = self.loop

            def economy_loop_step(inner):
                return self.loop.step()
        self._old = mcp_server._mod
        mcp_server._mod = FakeMod()

    def tearDown(self):
        self.mcp._mod = self._old
        super().tearDown()

    def rpc(self, method, params=None, mid=1):
        return self.mcp.handle_message({'jsonrpc': '2.0', 'id': mid,
                                        'method': method, 'params': params or {}})

    def test_initialize_and_tools_list(self):
        init = self.rpc('initialize', {'protocolVersion': '2025-03-26'})
        self.assertEqual(init['result']['protocolVersion'], '2025-03-26')
        self.assertEqual(init['result']['serverInfo']['name'], 'prefi')
        tools = self.rpc('tools/list')['result']['tools']
        names = {t['name'] for t in tools}
        for want in ('prefi_economy', 'prefi_agent_create', 'prefi_trade',
                     'prefi_predict', 'prefi_leaderboard', 'prefi_verify'):
            self.assertIn(want, names)
        for t in tools:
            self.assertIn('inputSchema', t)
            self.assertIn('readOnlyHint', t['annotations'])
        self.assertIsNone(self.mcp.handle_message(
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'}))

    def test_tool_calls_round_trip(self):
        out = self.rpc('tools/call', {'name': 'prefi_agent_create',
                                      'arguments': {'name': 'mo', 'prompt': 'trend', 'llm': False}})
        self.assertNotIn('isError', out['result'])
        self.assertEqual(out['result']['structuredContent']['template'], 'momentum')
        out = self.rpc('tools/call', {'name': 'prefi_agents'})
        self.assertEqual(len(out['result']['structuredContent']['agents']), 1)
        out = self.rpc('tools/call', {'name': 'prefi_faucet', 'arguments': {'address': '0xme'}})
        self.assertEqual(out['result']['structuredContent']['cash'], 1000.0)
        out = self.rpc('tools/call', {'name': 'prefi_trade',
                                      'arguments': {'address': '0xme', 'asset': 'BTC',
                                                    'side': 'buy', 'notional': 50}})
        self.assertEqual(out['result']['structuredContent']['side'], 'buy')
        out = self.rpc('tools/call', {'name': 'prefi_leaderboard', 'arguments': {'limit': 5}})
        self.assertEqual(len(out['result']['structuredContent']['rows']), 2)
        out = self.rpc('tools/call', {'name': 'prefi_strategy_language'})
        self.assertIn('momentum', out['result']['structuredContent']['templates'])

    def test_errors_are_tool_errors_not_protocol_errors(self):
        out = self.rpc('tools/call', {'name': 'prefi_trade',
                                      'arguments': {'address': '0xpoor', 'asset': 'BTC',
                                                    'side': 'buy', 'notional': 50}})
        self.assertTrue(out['result']['isError'])
        self.assertIn('insufficient', out['result']['content'][0]['text'])
        out = self.rpc('tools/call', {'name': 'prefi_predict',
                                      'arguments': {'address': '0xpoor', 'asset': 'NOPE',
                                                    'price': 1, 'stake': 1}})
        self.assertTrue(out['result']['isError'])
        out = self.rpc('tools/call', {'name': 'nope'})
        self.assertTrue(out['result']['isError'])
        out = self.rpc('no/such')
        self.assertEqual(out['error']['code'], -32601)
        out = self.mcp.handle_message({'hello': 1})
        self.assertEqual(out['error']['code'], -32600)

    def test_batch(self):
        out = self.mcp.handle_message([
            {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            {'jsonrpc': '2.0', 'method': 'notifications/x'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'prefi_economy'}},
        ])
        self.assertEqual(len(out), 2)
        self.assertIn('round', out[1]['result']['structuredContent'])


if __name__ == '__main__':
    unittest.main()
