"""Router tests — the claims the aggregator half makes, checked offline.

Nothing here touches the network. The catalog is a cache file, so a fixture
writes one and every routing decision is then a pure function of it: the tests
that matter are about normalization, filtering and refusal, and none of those
should depend on whether six third-party APIs are up this minute.

The price tests carry real published values from each router, copied from their
live catalogs on 2026-09-14, because the bug they guard against is a
million-fold mis-ranking that looks perfectly reasonable in a synthetic fixture.
"""

import json
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


@pytest.fixture(scope='module')
def store():
    tmp = tempfile.mkdtemp(prefix='infer-router-test-')
    os.environ['INFER_DIR'] = tmp
    return tmp


@pytest.fixture(scope='module')
def mods(store):
    import catalog
    import ledger
    import pick
    import router
    import settle
    for m in (catalog, ledger, settle):
        m.STATE_DIR = store
    catalog.CACHE = os.path.join(store, 'router-catalog.json')
    ledger.LEDGER = os.path.join(store, 'router-ledger.jsonl')
    settle.POLICY = os.path.join(store, 'settle.json')
    settle.PROPOSALS = os.path.join(store, 'settle-proposals.jsonl')
    return catalog, ledger, pick, router, settle


# ── the unit trap ────────────────────────────────────────────────────────

class TestPrice:
    """Six routers, six units, one number downstream."""

    def test_per_token_and_per_million_agree_after_conversion(self):
        from router.price import to_mtok
        # Real values: openrouter deepseek-chat-v3.1 vs a chute, 2026-09-14.
        assert to_mtok('0.00000025', 'per_token') == pytest.approx(0.25)
        assert to_mtok(0.12, 'per_million_tokens') == pytest.approx(0.12)
        # The whole point: these are now comparable, and the chute is cheaper.
        assert to_mtok(0.12, 'per_million_tokens') < to_mtok('0.00000025', 'per_token')

    def test_same_key_name_different_unit(self):
        """openrouter and chutes both ship `pricing.prompt`. They disagree."""
        from router.price import to_mtok
        openrouter = to_mtok('0.00000025', 'per_token')
        chutes = to_mtok(0.12, 'per_million_tokens')
        # Read chutes the way openrouter is read and it is 120,000x too dear.
        naive = to_mtok(0.12, 'per_token')
        assert naive / chutes == pytest.approx(1e6)
        assert naive > openrouter * 100

    def test_unit_must_be_declared(self):
        from router.price import to_mtok
        with pytest.raises(ValueError):
            to_mtok(1.0, 'per_furlong')

    def test_negative_price_is_unknown_not_free(self):
        """openrouter's -1 means 'variable', and -1 must never sort first."""
        from router.price import to_mtok
        assert to_mtok(-1, 'per_token') is None

    def test_sniff_reports_its_own_confidence(self):
        from router.price import sniff
        assert sniff('0.00000025') == (pytest.approx(0.25), True)
        value, confident = sniff(0.12)
        assert value == pytest.approx(0.12) and confident is False

    def test_blended_uses_both_sides(self):
        from router.price import Price
        p = Price(inp=1.0, out=10.0)
        # 1000 in at $1/Mtok + 500 out at $10/Mtok
        assert p.blended(1000, 500) == pytest.approx((1000 * 1 + 500 * 10) / 1e6)

    def test_unpriced_blends_to_none(self):
        from router.price import Price
        assert Price().blended() is None


# ── the registry ─────────────────────────────────────────────────────────

class TestRegistry:
    def test_every_adapter_declares_its_terms(self, mods):
        _, _, _, R, _ = mods
        for name, cls in R.REGISTRY.items():
            assert cls.kyc in R.KYC_LEVELS or cls.kyc == 'unknown', name
            assert cls.pay, f'{name} declares no accepted coin'
            assert cls.checked, f'{name} has no date on its kyc claim'
            assert cls.price_unit in ('per_token', 'per_million_tokens',
                                      'per_thousand_tokens'), name

    def test_kyc_is_a_ceiling_not_an_equality(self, mods):
        _, _, _, R, _ = mods
        strict = {p.name for p in R.every(kyc='none')}
        loose = {p.name for p in R.every(kyc='account')}
        assert strict < loose
        assert all(R.REGISTRY[n].kyc == 'none' for n in strict)

    def test_unverified_policy_is_excluded_by_default(self, mods):
        """An unread policy must never be treated as a passing one."""
        _, _, _, R, _ = mods

        class Mystery(R.Provider):
            name, kyc, pay = 'mystery', 'unknown', ('BTC',)

        R.REGISTRY['mystery'] = Mystery
        try:
            assert 'mystery' not in {p.name for p in R.every(kyc='none')}
            assert 'mystery' not in {p.name for p in R.every(kyc='full')}
            assert 'mystery' in {p.name for p in R.every(kyc=None)}
        finally:
            del R.REGISTRY['mystery']

    def test_no_adapter_demands_documents(self, mods):
        _, _, _, R, _ = mods
        assert all(cls.kyc != 'full' for cls in R.REGISTRY.values())

    def test_coin_filter_selects_by_what_is_accepted(self, mods):
        _, _, _, R, _ = mods
        assert 'nanogpt' in {p.name for p in R.every(coin='XMR')}
        assert 'chutes' in {p.name for p in R.every(coin='TAO')}

    def test_model_key_matches_the_board(self, mods):
        """The catalog and the receipts board must group models identically."""
        _, _, _, R, _ = mods
        from proofs import model_key as board_key
        for name in ('openai/gpt-4o', 'gpt-4o', 'deepseek/deepseek-chat:free'):
            assert R.model_key(name) == board_key(name)


# ── the catalog ──────────────────────────────────────────────────────────

ROWS = [
    {'provider': 'nanogpt', 'id': 'openai/gpt-oss-120b', 'model': 'gpt-oss-120b',
     'name': 'GPT OSS 120B', 'inputs': ['text'], 'outputs': ['text'],
     'multimodal': False, 'context': 131072, 'usd_per_mtok_in': 0.45,
     'usd_per_mtok_out': 0.55, 'pay': ['XMR', 'BTC'], 'kyc': 'none'},
    {'provider': 'ppq', 'id': 'gpt-oss-120b', 'model': 'gpt-oss-120b',
     'name': 'GPT OSS 120B', 'inputs': ['text'], 'outputs': ['text'],
     'multimodal': False, 'context': 131072, 'usd_per_mtok_in': 0.08,
     'usd_per_mtok_out': 0.1, 'pay': ['BTC', 'USDC'], 'kyc': 'none'},
    {'provider': 'venice', 'id': 'qwen-vision', 'model': 'qwen-vision',
     'name': 'Qwen Vision', 'inputs': ['text', 'image'], 'outputs': ['text'],
     'multimodal': True, 'context': 32768, 'usd_per_mtok_in': 1.0,
     'usd_per_mtok_out': 2.0, 'coin': 'DIEM', 'pay': ['VVV'], 'kyc': 'none'},
    {'provider': 'venice', 'id': 'venice-sd35', 'model': 'venice-sd35',
     'name': 'Venice SD35', 'inputs': ['text'], 'outputs': ['image'],
     'multimodal': True, 'context': None, 'usd_per_image': 0.01,
     'coin': 'DIEM', 'pay': ['VVV'], 'kyc': 'none'},
    {'provider': 'chutes', 'id': 'mystery-model', 'model': 'mystery-model',
     'name': 'Unpriced', 'inputs': ['text'], 'outputs': ['text'],
     'multimodal': False, 'context': 8192, 'pay': ['TAO'], 'kyc': 'none'},
    # PPQ really does publish this: a music model billed per clip, with both
    # token fields set to 0 because tokens are not how it charges.
    {'provider': 'ppq', 'id': 'google/lyria-3-pro-preview', 'model': 'lyria-3-pro',
     'name': 'Lyria 3 Pro', 'inputs': ['text'], 'outputs': ['audio'],
     'multimodal': True, 'context': None, 'usd_per_mtok_in': 0.0,
     'usd_per_mtok_out': 0.0, 'pay': ['BTC'], 'kyc': 'none'},
    {'provider': 'nanogpt', 'id': 'some/model:free', 'model': 'some-model',
     'name': 'Actually Free', 'inputs': ['text'], 'outputs': ['text'],
     'multimodal': False, 'context': 4096, 'usd_per_mtok_in': 0.0,
     'usd_per_mtok_out': 0.0, 'pay': ['XMR'], 'kyc': 'none'},
]


@pytest.fixture
def cached(mods):
    catalog = mods[0]
    import time
    doc = {'fetched': time.time(), 'kyc': 'none',
           'providers': ['nanogpt', 'ppq', 'venice', 'chutes'], 'errors': {},
           'offerings': [dict(r) for r in ROWS], 'asked': []}
    catalog._save(doc)
    return catalog


class TestCatalog:
    def test_models_group_across_routers(self, cached):
        got = cached.models(limit=50)
        row = next(m for m in got['models'] if m['model'] == 'gpt-oss-120b')
        assert row['n'] == 2
        assert set(row['providers']) == {'nanogpt', 'ppq'}
        assert row['cheapest'] == 'ppq'

    def test_spread_is_the_case_for_aggregating(self, cached):
        row = next(m for m in cached.models(limit=50)['models']
                   if m['model'] == 'gpt-oss-120b')
        # nanogpt (1000*0.45 + 500*0.55)/1e6 vs ppq (1000*0.08 + 500*0.1)/1e6
        assert row['spread'] == pytest.approx(0.000725 / 0.00013, rel=1e-3)
        assert row['spread'] > 5

    def test_modality_filter_reaches_the_multimodal_half(self, cached):
        assert {m['model'] for m in
                cached.models(limit=50, inp='image')['models']} == {'qwen-vision'}
        assert {m['model'] for m in
                cached.models(limit=50, out='image')['models']} == {'venice-sd35'}

    def test_coin_is_about_funding_not_quoting(self, cached):
        """`coin=XMR` asks who takes Monero, not who quotes in it."""
        xmr = {m['model'] for m in cached.models(limit=50, coin='XMR')['models']}
        assert xmr == {'gpt-oss-120b', 'some-model'}   # both are NanoGPT rows
        # Venice quotes in DIEM but does not take it as a deposit.
        diem = cached.search(limit=50, quote_coin='DIEM')
        assert {o['provider'] for o in diem['offerings']} == {'venice'}
        assert not cached.models(limit=50, coin='DIEM')['models']

    def test_a_published_zero_is_not_a_price(self, cached):
        """PPQ lists Lyria at 0/0 because it bills per clip. It is not free."""
        rows = {o['id']: o for o in cached.search(limit=50)['offerings']}
        assert rows['google/lyria-3-pro-preview']['usd_per_call'] is None

    def test_a_declared_free_model_is_still_free(self, cached):
        rows = {o['id']: o for o in cached.search(limit=50)['offerings']}
        assert rows['some/model:free']['usd_per_call'] == 0.0

    def test_placeholder_zeros_do_not_win_cheapest_first(self, cached):
        rows = cached.search(limit=50)['offerings']
        top = rows[0]
        assert top['id'] != 'google/lyria-3-pro-preview'
        # The genuinely free one may lead; the placeholder must sort with the
        # unpriced tail either way.
        tail = [r['id'] for r in rows[-2:]]
        assert 'google/lyria-3-pro-preview' in tail

    def test_free_filter_means_declared_free(self, cached):
        got = cached.search(limit=50, free=True)
        assert {o['id'] for o in got['offerings']} == {'some/model:free'}

    def test_unpriced_sorts_last_not_first(self, cached):
        """A model with no price must never win a cheapest-first ranking."""
        rows = cached.search(limit=50)['offerings']
        unpriced = {r['model'] for r in rows if r['usd_per_call'] is None}
        assert {r['model'] for r in rows[-len(unpriced):]} == unpriced
        assert 'mystery-model' in unpriced

    def test_a_dead_router_does_not_empty_the_answer(self, mods):
        catalog, _, _, R, _ = mods

        class Dead(R.Provider):
            name, kyc, pay = 'dead', 'none', ('BTC',)

            def catalog(self):
                raise R.RouterError('upstream on fire', provider='dead')

        name, rows, err = catalog._fetch(Dead())
        assert rows == [] and err['error'].startswith('upstream on fire')

    def test_offerings_carry_their_routers_terms(self, mods):
        catalog, _, _, R, _ = mods

        class Tiny(R.Provider):
            name, kyc, pay = 'tiny', 'none', ('XMR',)

            def catalog(self):
                return [R.Offering('tiny', 'm', 'm')]

        _, rows, _ = catalog._fetch(Tiny())
        assert rows[0]['pay'] == ['XMR'] and rows[0]['kyc'] == 'none'


# ── routing ──────────────────────────────────────────────────────────────

class TestPick:
    def test_plan_ranks_cheapest_first_and_prices_the_saving(self, cached, mods):
        pick = mods[2]
        plan = pick.plan('gpt-oss-120b')
        assert [c['provider'] for c in plan['candidates']] == ['ppq', 'nanogpt']
        assert plan['cheapest'] == 'ppq'
        assert plan['saving'] == pytest.approx(1 - 0.00013 / 0.000725, rel=1e-3)

    def test_plan_spends_nothing_when_nothing_is_funded(self, cached, mods):
        pick = mods[2]
        plan = pick.plan('gpt-oss-120b')
        assert plan['chosen'] is None
        assert all(c['funded'] is False for c in plan['candidates'])

    def test_require_drops_models_that_cannot_see(self, cached, mods):
        pick = mods[2]
        rows, _ = pick.candidates(None, require=('image',))
        assert {r['model'] for r in rows} == {'qwen-vision'}

    def test_chat_refuses_when_no_router_is_funded(self, cached, mods):
        pick = mods[2]
        with pytest.raises(pick.PickError, match='none is funded'):
            pick.chat('gpt-oss-120b', prompt='hi')

    def test_unknown_model_names_the_way_out(self, cached, mods):
        pick = mods[2]
        with pytest.raises(pick.PickError, match='no router'):
            pick.chat('no-such-model-anywhere', prompt='hi')

    def test_unpriced_call_counts_as_over_the_limit(self, mods):
        """A router that publishes no price cannot be bounded, so it is not cheap."""
        pick = mods[2]
        assert pick._worst_case({}, 1000) is None

    def test_a_published_zero_bounds_nothing_either(self, mods):
        """The spend guard must not wave through a per-clip model priced 0/token."""
        pick = mods[2]
        assert pick._worst_case(
            {'usd_per_mtok_in': 0.0, 'usd_per_mtok_out': 0.0,
             'id': 'google/lyria-3-pro-preview'}, 1024) is None
        assert pick._worst_case(
            {'usd_per_mtok_in': 0.0, 'usd_per_mtok_out': 0.0,
             'id': 'x/y:free'}, 1024) == 0.0
        assert pick._worst_case({'usd_per_mtok_out': 1.0}, 1_000_000) == \
            pytest.approx(1.0)

    def test_cost_comes_from_reported_usage(self, mods):
        pick = mods[2]
        row = {'usd_per_mtok_in': 1.0, 'usd_per_mtok_out': 10.0}
        got = pick._cost(row, {'prompt_tokens': 1000, 'completion_tokens': 500})
        assert got == pytest.approx((1000 * 1 + 500 * 10) / 1e6)
        assert pick._cost(row, {}) is None


# ── the ledger ───────────────────────────────────────────────────────────

class TestLedger:
    def test_drift_exposes_a_router_billing_off_its_own_prices(self, mods):
        _, ledger, _, _, _ = mods
        open(ledger.LEDGER, 'w').close()
        ledger.record('honest', 'm', usd=0.001, estimated=0.001, ms=10)
        ledger.record('liar', 'm', usd=0.004, estimated=0.001, ms=10)
        by = {b['provider']: b for b in ledger.spend()['providers']}
        assert by['honest']['drift'] == pytest.approx(1.0)
        assert by['liar']['drift'] == pytest.approx(4.0)

    def test_unpriced_calls_are_counted_not_summed_as_zero(self, mods):
        _, ledger, _, _, _ = mods
        open(ledger.LEDGER, 'w').close()
        ledger.record('p', 'm', usd=None, estimated=None)
        b = ledger.spend()['providers'][0]
        assert b['unpriced'] == 1 and b['usd'] == 0.0 and b['calls'] == 1


# ── settlement ───────────────────────────────────────────────────────────

class TestSettle:
    def test_disarmed_by_default(self, mods):
        settle = mods[4]
        if os.path.exists(settle.POLICY):
            os.remove(settle.POLICY)
        assert settle.policy()['armed'] is False
        assert 'nothing moves' in settle.status()['note']

    def test_arming_needs_confirmation(self, mods):
        settle = mods[4]
        got = settle.configure(armed=True)
        assert got.get('needs_confirm') is True
        assert settle.policy()['armed'] is False

    def test_arming_needs_a_rail_and_a_cap(self, mods):
        settle = mods[4]
        with pytest.raises(settle.SettleError, match='rail'):
            settle.configure(armed=True, confirm=True)
        with pytest.raises(settle.SettleError, match='daily_cap'):
            settle.configure(armed=True, confirm=True, rail='eth',
                             daily_cap_usd=0)

    def test_arming_sticks_once_bounded(self, mods):
        settle = mods[4]
        got = settle.configure(armed=True, confirm=True, rail='eth',
                               daily_cap_usd=25.0)
        assert got['armed'] is True and got['rail'] == 'eth'
        settle.configure(armed=False)

    def test_pay_without_confirm_moves_nothing(self, mods):
        settle = mods[4]
        settle.set_address('nanogpt', '0xdeadbeef', coin='USDC')
        got = settle.pay(provider='nanogpt', usd=5, rail='eth')
        assert got['state'] == 'proposed'
        assert got['would_send']['to'] == '0xdeadbeef'
        assert 'dry run' in got['why']

    def test_pay_refuses_without_an_address(self, mods):
        settle = mods[4]
        with pytest.raises(settle.SettleError, match='no deposit address'):
            settle.pay(provider='never-funded', usd=5, rail='eth')

    def test_pay_refuses_to_blow_past_the_configured_size(self, mods):
        settle = mods[4]
        settle.set_address('nanogpt', '0xdeadbeef', coin='USDC')
        with pytest.raises(settle.SettleError, match='4x'):
            settle.pay(provider='nanogpt', usd=10_000, rail='eth', confirm=True)

    def test_daily_cap_is_enforced(self, mods):
        settle = mods[4]
        settle.configure(daily_cap_usd=1.0, topup_usd=10.0)
        settle.set_address('nanogpt', '0xdeadbeef', coin='USDC')
        with pytest.raises(settle.SettleError, match='cap'):
            settle.pay(provider='nanogpt', usd=5, rail='eth', confirm=True)
        settle.configure(daily_cap_usd=25.0)

    def test_an_unreachable_rail_is_named_not_retried(self, mods):
        settle = mods[4]
        settle.RAILS['nowhere'] = {'url': 'http://localhost:1', 'send': '/send',
                                   'coins': ('X',), 'auth': False}
        try:
            with pytest.raises(settle.SettleError, match='not listening'):
                settle._send('nowhere', 'addr', 1, 'X', confirm=True)
        finally:
            del settle.RAILS['nowhere']
