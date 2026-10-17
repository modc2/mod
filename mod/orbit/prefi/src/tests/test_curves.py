"""Score functions as programs — the language, the library, sharing, and the
pool settling under a custom rule.

Hermetic like the rest of the suite. The one thing worth saying twice: the
sandbox tests are the security boundary. A score function decides how real
dollars split, and it is user-supplied text; every construct the language does
not name must be refused at compile time.

    python3 -m pytest tests/test_curves.py -q      # from src/
"""

import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import curves
import scoring
import pool as pool_mod
from mod import Mod
from test_pool import PoolTestBase, ALICE, BOB, CAROL


def old_l2(e, tol):
    return 1.0 / (1.0 + (e / tol) ** 2)


def old_linear(e, tol):
    return max(0.0, 1.0 - e / tol)


def old_exponential(e, tol):
    return math.exp(-e / tol)


def old_threshold(e, tol):
    return 1.0 if e <= tol else 0.0


# ── The language ─────────────────────────────────────────────────────

class TestLanguage(unittest.TestCase):

    def test_defaults_are_the_curves_they_replaced(self):
        """The four historical models must score identically to their old
        Python — every open round and prediction was sold under them."""
        pairs = {'l2': old_l2, 'linear': old_linear,
                 'exponential': old_exponential, 'threshold': old_threshold}
        for name, old in pairs.items():
            for tol in (1.0, 0.05, 0.02, 0.001):
                for e in curves.GRID:
                    want = min(1.0, max(0.0, old(e, tol)))
                    got = curves.evaluate(curves.BUILTINS[name], e, {'tol': tol})
                    self.assertAlmostEqual(got, want, places=12,
                                           msg=f'{name} tol={tol} e={e}')

    def test_scoring_models_are_the_same_programs(self):
        for name in ('l2', 'linear', 'exponential', 'threshold'):
            self.assertIn(name, scoring.MODELS)
            self.assertAlmostEqual(scoring.MODELS[name](0.01, 0.02),
                                   curves.evaluate(curves.BUILTINS[name], 0.01, {'tol': 0.02}))

    def test_every_default_is_perfect_at_zero_and_monotone(self):
        for name, spec in curves.BUILTINS.items():
            rep = curves.report(spec)
            self.assertEqual(rep['at_zero'], 1.0, name)
            self.assertTrue(rep['monotone'], name)

    def test_arithmetic_and_functions(self):
        f = curves.compile_expr('clamp(1 - sqrt(e) * k, 0, 1)', {'k': 2.0})
        self.assertAlmostEqual(f(0.25), 0.0)
        self.assertAlmostEqual(f(0.01), 0.8)
        self.assertAlmostEqual(f(0.01, {'k': 1.0}), 0.9)

    def test_conditionals(self):
        f = curves.compile_expr('1 if e < a else (0.5 if e < b else 0)', {'a': 0.01, 'b': 0.05})
        self.assertEqual(f(0.0), 1.0)
        self.assertEqual(f(0.02), 0.5)
        self.assertEqual(f(0.1), 0.0)
        g = curves.compile_expr('where(e <= tol and not e > 2*tol, 1, 0)', {'tol': 0.1})
        self.assertEqual(g(0.05), 1.0)
        self.assertEqual(g(0.5), 0.0)

    def test_err_is_an_alias_for_e(self):
        f = curves.compile_expr('1 - err', {})
        self.assertAlmostEqual(f(0.25), 0.75)

    def test_output_is_clamped_and_never_nan(self):
        spec = {'name': 'xx', 'expr': '5 - 100*e', 'params': {}}
        self.assertEqual(curves.evaluate(spec, 0.0), 1.0)          # 5 → 1
        self.assertEqual(curves.evaluate(spec, 1.0), 0.0)          # -95 → 0
        div = {'name': 'dd', 'expr': '1 / e', 'params': {}}
        self.assertEqual(curves.evaluate(div, 0.0), 0.0)           # inf → 0
        self.assertEqual(curves.evaluate(div, 2.0), 0.5)
        boom = {'name': 'bb', 'expr': 'exp(1000 * e) * 0 + 10 ** (1000 * e)', 'params': {}}
        self.assertEqual(curves.evaluate(boom, 1.0), 0.0)          # overflow → 0
        self.assertEqual(curves.evaluate(div, float('nan')), 0.0)
        self.assertEqual(curves.evaluate(div, -1.0), 0.0)

    def test_sandbox_refuses_everything_outside_the_language(self):
        bad = [
            "__import__('os').system('id')",
            "e.__class__",
            "(1).__class__.__bases__",
            "[1, 2][0]",
            "{'a': 1}['a']",
            "lambda: 1",
            "x + 1",                      # undeclared name
            "exp",                        # bare function
            "'a' * 3",
            "min(e, key=1)",
            "e := 1",
            "open('/etc/passwd')",
            "e if e else print(1)",
            "f'{e}'",
            "e @ e",
            "e << 1",
            "e is 1",
            "e in (1, 2)",
            "",
            "1\n+1",
            "1 +" * 300,
        ]
        for expr in bad:
            with self.assertRaises(curves.ExprError, msg=expr):
                curves.compile_expr(expr, {})

    def test_sandbox_never_calls_eval(self):
        """Belt and braces: even if something got through `_check`, the
        evaluator only knows the node types it implements."""
        import ast
        with patch('curves._check'):
            with self.assertRaises(curves.ExprError):
                curves._eval(ast.parse("__import__('os')", mode='eval'), {})

    def test_limits(self):
        with self.assertRaises(curves.ExprError):
            curves.compile_expr('e' + ' + 0.001' * 200, {})        # node count
        with self.assertRaises(curves.ExprError):
            curves.compile_expr('e' + ' ' * curves.MAX_EXPR_LEN + '+ 1', {})
        with self.assertRaises(curves.ExprError):
            curves.validate_spec({'name': 'pp', 'expr': 'e',
                                  'params': {f'p{i}': 1 for i in range(curves.MAX_PARAMS + 1)}})

    def test_params_are_validated(self):
        with self.assertRaises(curves.ExprError):
            curves.validate_spec({'name': 'pp', 'expr': 'e', 'params': {'exp': 1}})     # reserved
        with self.assertRaises(curves.ExprError):
            curves.validate_spec({'name': 'pp', 'expr': 'e', 'params': {'e': 1}})
        with self.assertRaises(curves.ExprError):
            curves.validate_spec({'name': 'pp', 'expr': 'e', 'params': {'Tol': 1}})
        with self.assertRaises(curves.ExprError):
            curves.validate_spec({'name': 'pp', 'expr': 'e', 'params': {'k': 'nan'}})
        with self.assertRaises(curves.ExprError):
            curves.validate_spec({'name': 'pp', 'expr': 'e', 'params': {'k': float('inf')}})
        ok = curves.validate_spec({'name': 'pp', 'expr': 'e * k', 'params': '{"k": "2"}'})
        self.assertEqual(ok['params'], {'k': 2.0})

    def test_names_are_validated(self):
        for bad in ('', 'X', '1abc', 'a-b', 'exp', 'e', 'a' * 40, 'If', 'x'):
            with self.assertRaises(curves.ExprError, msg=repr(bad)):
                curves.validate_spec({'name': bad, 'expr': 'e', 'params': {}})
        self.assertEqual(curves.validate_spec({'name': 'My_Fn2', 'expr': 'e'})['name'], 'my_fn2')

    def test_report_warns_about_the_things_that_matter(self):
        rep = curves.report({'name': 'xx', 'expr': '0.5 - e', 'params': {}})
        self.assertLess(rep['at_zero'], 1.0)
        self.assertTrue(any('perfect call' in w for w in rep['warnings']))
        rep = curves.report({'name': 'xx', 'expr': 'e', 'params': {}})
        self.assertFalse(rep['monotone'])
        rep = curves.report(curves.BUILTINS['l2'])
        self.assertIsNone(rep['zero_from'])
        self.assertTrue(any('never reaches 0' in w for w in rep['warnings']))
        rep = curves.report(curves.BUILTINS['linear'])
        self.assertEqual(rep['zero_from'], 1.0)
        self.assertEqual(rep['warnings'], [])


# ── Resolution: names + knobs → a snapshot ───────────────────────────

class TestResolve(unittest.TestCase):

    def test_tolerance_lands_on_tol(self):
        fn = curves.resolve('linear', tolerance=0.05)
        self.assertEqual(fn['params'], {'tol': 0.05})
        self.assertEqual(fn['expr'], curves.BUILTINS['linear']['expr'])

    def test_params_override_by_name_and_refuse_unknowns(self):
        fn = curves.resolve('hinge', tolerance=0.1, params={'power': 4})
        self.assertEqual(fn['params'], {'tol': 0.1, 'power': 4.0})
        with self.assertRaises(ValueError):
            curves.resolve('hinge', params={'nope': 1})
        with self.assertRaises(ValueError):
            curves.resolve('linear', tolerance=0)

    def test_unknown_name_is_an_error_unless_a_snapshot_is_carried(self):
        with self.assertRaises(ValueError):
            curves.resolve('vibes')
        snap = {'name': 'vibes', 'expr': '1 - e', 'params': {}}
        fn = curves.resolve('vibes', fallback=snap)
        self.assertEqual(fn['expr'], '1 - e')
        with self.assertRaises(ValueError):
            curves.resolve('vibes', fallback={'name': 'other', 'expr': 'e', 'params': {}})

    def test_a_function_without_tol_ignores_tolerance(self):
        spec = {'name': 'flat', 'expr': '1 - e', 'params': {}}
        fn = curves.resolve(spec, tolerance=0.02)
        self.assertEqual(fn['params'], {})


# ── Library ──────────────────────────────────────────────────────────

class TestLibrary(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='prefi_fns_')
        self.lib = curves.Library(Path(self.tmp) / 'functions.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_are_always_there(self):
        self.assertEqual(self.lib.names()[:8], list(curves.BUILTINS))
        self.assertTrue(self.lib.get('linear')['builtin'])

    def test_save_get_delete(self):
        rec = self.lib.save({'name': 'mine', 'expr': 'max(0, 1 - e/k)', 'params': {'k': 0.2},
                             'description': 'ramp'}, ALICE)
        self.assertEqual(rec['owner'], ALICE)
        self.assertEqual(rec['author'], ALICE)
        self.assertIn('mine', self.lib.names())
        self.assertEqual(self.lib.get('mine')['expr'], 'max(0, 1 - e/k)')
        self.assertEqual(curves.resolve('mine', self.lib, tolerance=0.5)['params'], {'k': 0.2})
        self.lib.delete('mine', ALICE)
        self.assertIsNone(self.lib.get('mine'))

    def test_a_name_belongs_to_whoever_saved_it(self):
        self.lib.save({'name': 'mine', 'expr': '1 - e'}, ALICE)
        with self.assertRaises(curves.ExprError):
            self.lib.save({'name': 'mine', 'expr': '1 - 2*e'}, BOB)
        with self.assertRaises(curves.ExprError):
            self.lib.delete('mine', BOB)
        self.lib.save({'name': 'mine', 'expr': '1 - 2*e'}, ALICE)     # owner may edit
        self.assertEqual(self.lib.get('mine')['expr'], '1 - 2*e')

    def test_defaults_cannot_be_overwritten_or_deleted(self):
        with self.assertRaises(curves.ExprError):
            self.lib.save({'name': 'linear', 'expr': '1'}, ALICE)
        with self.assertRaises(curves.ExprError):
            self.lib.delete('linear', ALICE)

    def test_a_corrupt_file_reads_as_empty(self):
        self.lib.path.write_text('{not json')
        self.assertEqual(self.lib.saved(), {})


# ── Sharing ──────────────────────────────────────────────────────────

class TestSharing(unittest.TestCase):

    def test_share_code_round_trips(self):
        spec = {'name': 'shared', 'expr': 'exp(-(e/tol)**p)', 'params': {'tol': 0.05, 'p': 1.5},
                'description': 'stretched exponential', 'author': ALICE}
        code = curves.to_code(spec)
        self.assertTrue(code.startswith('prefi.fn.'))
        back = curves.from_code(code)
        for key in ('name', 'expr', 'params', 'description', 'author'):
            self.assertEqual(back[key], spec[key])

    def test_a_tampered_bundle_is_refused(self):
        b = curves.bundle({'name': 'xx', 'expr': '1 - e', 'params': {}})
        b['expr'] = '1'
        with self.assertRaises(curves.ExprError):
            curves.from_bundle(b)
        with self.assertRaises(curves.ExprError):
            curves.from_bundle({'kind': 'something/else', 'name': 'xx', 'expr': 'e'})
        with self.assertRaises(curves.ExprError):
            curves.from_code('prefi.fn.!!!')
        with self.assertRaises(curves.ExprError):
            curves.from_code('nope')

    def test_a_shared_program_is_still_sandboxed(self):
        b = curves.bundle({'name': 'xx', 'expr': '1 - e', 'params': {}})
        b['expr'] = "__import__('os')"
        b['digest'] = curves.digest(b)
        with self.assertRaises(curves.ExprError):
            curves.from_bundle(b)


# ── The pool under a custom rule ─────────────────────────────────────

class TestPoolWithCustomFunction(PoolTestBase):

    def _save(self, name, expr, params=None, owner=ALICE):
        return self.pool.library.save({'name': name, 'expr': expr, 'params': params or {}},
                                      owner)

    def _closed_round_with(self, *stakes, asset='BTC'):
        """Stake, then rewind the schedule so the round has closed — the same
        trick test_pool's settlement suite uses."""
        for addr, amount, price in stakes:
            self.fund(addr, amount)
            out = self.pool.stake(addr, asset, price, amount)
            self.assertNotIn('error', out)
        self._rewind()

    def _rewind(self):
        st = self.pool.state()
        st['schedule']['anchor'] -= st['schedule']['interval'] + 10
        self.pool._save_state(st)
        rounds = self.pool._rounds()
        for record in rounds:
            window = self.pool.window(record['index'])
            record.update({'opens': window['opens'], 'closes': window['closes'],
                           'entry_deadline': window['entry_deadline']})
        self.pool._write(self.pool.rounds_path, rounds)

    def test_config_accepts_a_library_function(self):
        self._save('cliff', '1 if e <= tol else base', {'tol': 0.01, 'base': 0.2})
        out = self.pool.set_config(model='cliff', tolerance=0.02)
        self.assertNotIn('error', out)
        self.assertEqual(out['fn'], {'name': 'cliff', 'expr': '1 if e <= tol else base',
                                     'params': {'tol': 0.02, 'base': 0.2}})
        self.assertIn('cliff', out['models'])
        self.assertIn('cliff(e) = 1 if e <= tol else base', out['scoring'])

    def test_model_params_override_by_name(self):
        out = self.pool.set_config(model='hinge', tolerance=0.1, model_params='{"power": 8}')
        self.assertNotIn('error', out)
        self.assertEqual(out['fn']['params'], {'tol': 0.1, 'power': 8.0})
        self.assertEqual(out['model_params'], {'power': 8.0})
        bad = self.pool.set_config(model='hinge', model_params='{"nope": 1}')
        self.assertIn('error', bad)
        bad = self.pool.set_config(model_params='not json')
        self.assertIn('error', bad)

    def test_unknown_function_is_refused(self):
        self.assertIn('error', self.pool.set_config(model='vibes'))

    def test_the_pot_settles_under_the_custom_rule(self):
        # Everyone inside 2% splits evenly; nobody else gets anything.
        self._save('inside', '1 if e <= tol else 0', {'tol': 1.0})
        self.pool.set_config(model='inside', tolerance=0.02)
        self.prices['BTC'] = 100_000.0
        self._closed_round_with((ALICE, 100.0, 101_000.0),    # 1% → in
                                (BOB, 100.0, 101_500.0),      # 1.5% → in
                                (CAROL, 100.0, 110_000.0))    # 10% → out
        out = self.pool.settle()
        self.assertEqual(len(out['settled']), 1)
        self.assertAlmostEqual(self.pool.balance(ALICE)['available'], 150.0, places=5)
        self.assertAlmostEqual(self.pool.balance(BOB)['available'], 150.0, places=5)
        self.assertAlmostEqual(self.pool.balance(CAROL)['available'], 0.0, places=5)

    def test_a_round_snapshots_the_program_not_the_name(self):
        """Editing or deleting the function after the round opened must not
        move a single dollar of that round."""
        self._save('mine', 'max(0, 1 - e/tol)', {'tol': 1.0})
        self.pool.set_config(model='mine', tolerance=0.5)
        self.prices['BTC'] = 100_000.0
        self._closed_round_with((ALICE, 100.0, 101_000.0), (BOB, 100.0, 110_000.0))
        record = self.pool._rounds()[0]
        self.assertEqual(record['fn'], {'name': 'mine', 'expr': 'max(0, 1 - e/tol)',
                                        'params': {'tol': 0.5}})
        # Owner rewrites it into a threshold, then deletes it outright.
        self._save('mine', '1 if e <= 0.001 else 0', {})
        self.pool.library.delete('mine', ALICE)
        out = self.pool.settle()
        self.assertEqual(len(out['settled']), 1)
        alice = self.pool.balance(ALICE)['available']
        bob = self.pool.balance(BOB)['available']
        # linear @ 0.5: alice 0.98, bob 0.8 → 98:80 split of $200
        self.assertAlmostEqual(alice, 200 * 0.98 / 1.78, places=4)
        self.assertAlmostEqual(bob, 200 * 0.80 / 1.78, places=4)
        # The config still names a function that is gone; reads survive.
        cfg = self.pool.config()
        self.assertIn('error', cfg['fn'])
        self.assertEqual(self.pool.round()['fn']['name'], 'linear')

    def test_rounds_from_before_snapshots_still_settle(self):
        self.prices['BTC'] = 100_000.0
        self._closed_round_with((ALICE, 100.0, 101_000.0), (BOB, 100.0, 110_000.0))
        rounds = self.pool._rounds()
        for r in rounds:
            r.pop('fn', None)
            r['model'], r['tolerance'] = 'l2', 0.05
        self.pool._write(self.pool.rounds_path, rounds)
        out = self.pool.settle()
        self.assertEqual(len(out['settled']), 1)
        want = pool_mod.settle_asset(
            [{'id': 1, 'address': ALICE, 'amount': 100.0, 'predicted_price': 101_000.0},
             {'id': 2, 'address': BOB, 'amount': 100.0, 'predicted_price': 110_000.0}],
            100_000.0, pool_mod.fn_for('l2', 0.05), 0)
        self.assertAlmostEqual(self.pool.balance(ALICE)['available'],
                               want['entries'][0]['payout'], places=5)

    def test_free_calls_are_scored_by_the_same_program(self):
        self._save('inside', '1 if e <= tol else 0', {'tol': 1.0})
        self.pool.set_config(model='inside', tolerance=0.02)
        self.prices['BTC'] = 100_000.0
        self.fund(ALICE, 100.0)
        self.pool.stake(ALICE, 'BTC', 101_000.0, 100.0)
        self.pool.free_stake(BOB, 'BTC', 101_500.0)
        self._rewind()
        self.pool.settle()
        free = [e for e in self.pool.entries(BOB) if e.get('free')][0]
        self.assertEqual(free['accuracy'], 1.0)
        self.assertAlmostEqual(free['would_win'], 100.0, places=5)   # even split of $200


# ── Through the mod: sign, save, share, import ───────────────────────

class TestModFunctions(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='prefi_modfn_')
        os.environ['PREFI_UNSAFE_NO_SIG'] = '1'
        self.mod = Mod({})
        self.mod.store_dir = Path(self.tmp)
        for name in ('positions', 'stakes', 'treasury', 'markets',
                     'predictions', 'scoring', 'functions'):
            setattr(self.mod, f'{name}_path', self.mod.store_dir / f'{name}.json')

    def tearDown(self):
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_library_lives_beside_the_ledger(self):
        self.assertEqual(self.mod.fns.path, Path(self.tmp) / 'functions.json')
        self.assertEqual(self.mod.pool.library.path, self.mod.fns.path)

    def test_list_carries_defaults_curves_and_language(self):
        out = self.mod.fn_list()
        names = [f['name'] for f in out['functions']]
        self.assertEqual(names[:8], list(curves.BUILTINS))
        self.assertEqual(len(out['functions'][0]['sample']), len(curves.GRID))
        self.assertIn('functions', out['language'])
        self.assertEqual(out['active'], {'pool': 'linear', 'predict': 'l2'})

    def test_test_runs_a_mock_pot(self):
        out = self.mod.fn_test('1 if e <= tol else 0', {'tol': 0.02})
        self.assertNotIn('error', out)
        self.assertEqual(out['pot']['gross'], 500.0)
        paid = [e for e in out['pot']['entries'] if e['payout'] > 0]
        self.assertEqual(len(paid), 3)                    # 0, 0.5%, 1% are inside 2%
        self.assertIn('error', self.mod.fn_test("__import__('os')"))
        self.assertIn('error', self.mod.fn_test('e', calls='a,b'))
        custom = self.mod.fn_test('max(0, 1 - e)', calls='100,200', stake=50, fee_bps=100)
        self.assertEqual(custom['pot']['fee_bps'], 100)
        self.assertAlmostEqual(custom['pot']['pot'], 99.0)

    def test_save_share_import_round_trip(self):
        sign = self.mod.fn_sign(ALICE, 'mine', 'max(0, 1 - e/tol)', {'tol': 0.1}, 'ramp')
        self.assertEqual(sign['action'], 'fn_save')
        self.assertIn(f"digest: {sign['digest']}", sign['message'])
        saved = self.mod.fn_save(ALICE, 'mine', 'max(0, 1 - e/tol)', {'tol': 0.1}, 'ramp')
        self.assertEqual(saved['status'], 'saved')
        self.assertEqual(saved['function']['owner'], ALICE)

        share = self.mod.fn_share('mine')
        self.assertIsNone(share['cid'])
        preview = self.mod.fn_import(share['code'])
        self.assertEqual(preview['preview']['expr'], 'max(0, 1 - e/tol)')
        self.assertTrue(preview['name_taken'])
        self.assertFalse((Path(self.tmp) / 'functions.json').read_text().count('"bob'))

        # Bob imports it under a new name; authorship is kept, ownership is his.
        got = self.mod.fn_import(share['code'], BOB, name='ramp_by_alice')
        self.assertEqual(got['status'], 'saved')
        self.assertEqual(got['function']['author'], ALICE)
        self.assertEqual(got['function']['owner'], BOB)
        self.assertIn('ramp_by_alice', self.mod.fns.names())

        # Both layers can adopt it.
        self.assertNotIn('error', self.mod.set_pool_config(model='ramp_by_alice', tolerance=0.2))
        self.assertEqual(self.mod.pool_config()['fn']['params'], {'tol': 0.2})
        tuned = self.mod.set_scoring(model='mine', tolerance=0.05)
        self.assertEqual(tuned['scoring']['fn']['params'], {'tol': 0.05})
        self.assertEqual(self.mod.score_preview(101, 100)['score'], 0.8)
        self.assertEqual(self.mod.score_preview(101, 100, model='hinge', tolerance=0.02,
                                                model_params='{"power": 1}')['score'], 0.5)

    def test_save_is_signed_when_signatures_are_on(self):
        os.environ.pop('PREFI_UNSAFE_NO_SIG', None)
        out = self.mod.fn_save(ALICE, 'mine', '1 - e')
        self.assertIn('signature required', out['error'])
        self.assertIn('action: fn_save', out['sign_message'])
        self.assertNotIn('mine', self.mod.fns.names())
        from eth_account import Account
        from eth_account.messages import encode_defunct
        acct = Account.create()
        sign = self.mod.fn_sign(acct.address, 'mine', '1 - e')
        sig = Account.sign_message(encode_defunct(text=sign['message']),
                                   acct.key).signature.hex()
        out = self.mod.fn_save(acct.address, 'mine', '1 - e', signature=sig)
        self.assertEqual(out['status'], 'saved')
        # A signature is single-use: the nonce moved on.
        again = self.mod.fn_save(acct.address, 'mine', '1 - e', signature=sig)
        self.assertIn('error', again)
        # Somebody else's signature cannot write under my address.
        other = Account.create()
        sign = self.mod.fn_sign(ALICE, 'theirs', '1 - e')
        sig = Account.sign_message(encode_defunct(text=sign['message']),
                                   other.key).signature.hex()
        self.assertIn('error', self.mod.fn_save(ALICE, 'theirs', '1 - e', signature=sig))

    def test_delete_is_owner_only_and_refuses_a_live_function(self):
        self.mod.fn_save(ALICE, 'mine', '1 - e')
        self.assertIn('error', self.mod.fn_delete(BOB, 'mine'))
        self.mod.set_scoring(model='mine')
        self.assertIn('prediction layer', self.mod.fn_delete(ALICE, 'mine')['error'])
        self.mod.set_scoring(model='l2')
        self.mod.set_pool_config(model='mine')
        self.assertIn("pool's live", self.mod.fn_delete(ALICE, 'mine')['error'])
        self.mod.set_pool_config(model='linear')
        self.assertEqual(self.mod.fn_delete(ALICE, 'mine')['status'], 'deleted')

    def test_a_prediction_keeps_its_program_after_the_function_is_gone(self):
        self.mod.fn_save(ALICE, 'mine', '1 - e')
        self.mod.set_scoring(model='mine')
        snapshot = self.mod.get_scoring()['fn']
        self.mod.fns.delete('mine', ALICE)              # behind the API's back
        # The saved params carry the program; scoring still resolves.
        self.assertEqual(self.mod.get_scoring()['fn'], snapshot)
        self.assertEqual(scoring.score(100.5, 100.0, self.mod.get_scoring())['score'], 0.995)

    def test_publish_needs_a_token_and_a_store(self):
        self.mod.fn_save(ALICE, 'mine', '1 - e')
        with patch('store_link.local_token', side_effect=RuntimeError('no key')):
            out = self.mod.fn_publish('mine')
        self.assertIn('protocol token', out['error'])
        with patch('store_link.StoreLink.put_json',
                   return_value={'cid': 'QmFake', 'size': 10, 'url': 'u'}):
            out = self.mod.fn_publish('mine', token='t')
        self.assertEqual(out['cid'], 'QmFake')
        self.assertEqual(self.mod.fn_share('mine')['cid'], 'QmFake')
        bundle = curves.bundle(self.mod.fns.get('mine'))
        with patch('store_link.StoreLink.fetch_json', return_value=bundle):
            got = self.mod.fn_import('QmFake', BOB, name='alices')
        self.assertEqual(got['function']['origin_cid'], 'QmFake')

    def test_every_fn_is_declared_in_config(self):
        config = json.loads((Path(__file__).resolve().parents[2] / 'config.json').read_text())
        for name in dir(self.mod):
            if name.startswith('fn_'):
                self.assertIn(name, config['fns'], f'{name} missing from config.json fns')


if __name__ == '__main__':
    unittest.main()
