import sys
from pathlib import Path

import pytest

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from charitao_subnet.chain import MockChain
from charitao_subnet.incentive import DonationIncentive
from charitao_subnet.miner import CharitaoMiner
from charitao_subnet.registry import CharityRegistry
from charitao_subnet.validator import CharitaoValidator


@pytest.fixture
def registry(tmp_path):
    return CharityRegistry(path=str(tmp_path / 'registry.json'))


@pytest.fixture
def chain(tmp_path):
    return MockChain(path=str(tmp_path / 'mockchain.json'))


@pytest.fixture
def validator(tmp_path, registry, chain):
    return CharitaoValidator(
        local=True, coverage_ratio=0.8, epoch_emission=1.0,
        storage_path=str(tmp_path / 'store'), chain=chain, registry=registry)


def make_miner(uid, validator):
    m = CharitaoMiner(uid=uid, local=True, chain=validator.chain,
                      registry=validator.registry)
    validator.add_miner(m)
    return m


# ── registry ─────────────────────────────────────────────────────

def test_registry_seeds_defaults(registry):
    ids = {c['id'] for c in registry.charities()}
    assert 'cleanwater' in ids and len(ids) >= 4


def test_registry_add_remove_verify(registry):
    c = registry.add('x', 'X Charity', 'demo://x')
    assert c['verified'] is False
    assert 'demo://x' not in registry.addresses()  # unverified excluded
    registry.verify('x')
    assert registry.addresses()['demo://x'] == 'x'
    assert registry.add('x', 'dup', 'demo://y').get('error')
    assert registry.add('y', 'dup addr', 'demo://x').get('error')
    registry.remove('x')
    assert registry.get('x') is None


def test_registry_persists(tmp_path):
    p = str(tmp_path / 'r.json')
    CharityRegistry(path=p).add('x', 'X', 'demo://x', verified=True)
    assert CharityRegistry(path=p).get('x')['verified'] is True


# ── mock chain ───────────────────────────────────────────────────

def test_mockchain_transfer_and_scan(chain):
    t1 = chain.transfer('a', 'b', 1.0)
    t2 = chain.transfer('a', 'c', 2.0)
    assert t1.txid != t2.txid
    assert [t.txid for t in chain.scan(0)] == [t1.txid, t2.txid]
    assert [t.txid for t in chain.scan(t1.block)] == [t2.txid]


def test_mockchain_persists(tmp_path):
    p = str(tmp_path / 'c.json')
    MockChain(path=p).transfer('a', 'b', 1.0)
    assert MockChain(path=p).height == 1


def test_mockchain_rejects_nonpositive(chain):
    with pytest.raises(ValueError):
        chain.transfer('a', 'b', 0)


# ── incentive: the profit guarantee ─────────────────────────────

def test_exact_payout_rate():
    im = DonationIncentive(coverage_ratio=0.8)
    r = im.settle([{'uid': 1, 'amount': 0.4, 'txid': 't1'}], epoch_emission=1.0)
    # payout = donation / rho, exactly
    assert r['payouts']['1'] == pytest.approx(0.5)
    assert r['weights']['1'] == pytest.approx(0.5)  # 0.4 / 0.8


def test_burn_absorbs_unfilled_budget():
    im = DonationIncentive(coverage_ratio=0.8, burn_uid=0)
    r = im.settle([{'uid': 1, 'amount': 0.08, 'txid': 't1'}], epoch_emission=1.0)
    # weight 0.1 to the miner, 0.9 burned — rate stays 1/rho, not a lottery
    assert r['weights']['1'] == pytest.approx(0.1)
    assert r['weights']['0'] == pytest.approx(0.9)
    assert sum(r['weights'].values()) == pytest.approx(1.0)


def test_overfilled_epoch_carries_over_pro_rata():
    im = DonationIncentive(coverage_ratio=0.8)
    r = im.settle([
        {'uid': 1, 'amount': 1.2, 'txid': 't1'},
        {'uid': 2, 'amount': 0.4, 'txid': 't2'},
    ], epoch_emission=1.0)  # budget 0.8, donated 1.6 → half credited
    assert r['credited']['1'] == pytest.approx(0.6)
    assert r['credited']['2'] == pytest.approx(0.2)
    assert r['carryover'] == pytest.approx(0.8)
    # next epoch with no new donations settles the carryover at same rate
    r2 = im.settle([], epoch_emission=1.0)
    assert r2['credited']['1'] == pytest.approx(0.6)
    assert r2['credited']['2'] == pytest.approx(0.2)
    assert r2['carryover'] == pytest.approx(0.0)
    # total payout across both epochs = donated / rho, exactly
    lb = {row['uid']: row for row in im.leaderboard()}
    assert lb[1]['projected_payout'] == pytest.approx(1.2 / 0.8)
    assert lb[2]['projected_payout'] == pytest.approx(0.4 / 0.8)


def test_carryover_is_fifo_across_epochs():
    im = DonationIncentive(coverage_ratio=0.8)
    im.settle([{'uid': 1, 'amount': 1.6, 'txid': 't1'}], epoch_emission=1.0)
    r2 = im.settle([{'uid': 2, 'amount': 0.8, 'txid': 't2'}], epoch_emission=1.0)
    # epoch-1 leftover (0.8) fills the whole epoch-2 budget before uid 2
    assert r2['credited'] == {'1': pytest.approx(0.8)}
    r3 = im.settle([], epoch_emission=1.0)
    assert r3['credited'] == {'2': pytest.approx(0.8)}


def test_txid_dedupe_and_dust_filter():
    im = DonationIncentive(coverage_ratio=0.8, min_donation=0.01)
    im.settle([{'uid': 1, 'amount': 0.1, 'txid': 't1'},
               {'uid': 1, 'amount': 0.005, 'txid': 'dust'}], epoch_emission=1.0)
    r = im.settle([{'uid': 1, 'amount': 0.1, 'txid': 't1'}], epoch_emission=1.0)
    assert '1' not in r['credited']  # replayed txid not re-credited
    lb = im.leaderboard()[0]
    assert lb['donated'] == pytest.approx(0.1)  # dust never counted


def test_incentive_persistence_roundtrip(tmp_path):
    p = str(tmp_path / 'im.json')
    im = DonationIncentive(coverage_ratio=0.75)
    im.settle([{'uid': 3, 'amount': 2.0, 'txid': 'a'}], epoch_emission=1.0)
    im.save(p)
    im2 = DonationIncentive.load(p)
    assert im2.coverage_ratio == 0.75
    assert im2.epoch == 1
    assert im2.totals[3]['donated'] == pytest.approx(2.0)
    assert 'a' in im2.processed_txids


def test_projection_math():
    im = DonationIncentive(coverage_ratio=0.8)
    p = im.projection(1.0, epoch_emission=1.0)
    assert p['payout'] == pytest.approx(1.25)
    assert p['profit'] == pytest.approx(0.25)
    assert p['margin_pct'] == pytest.approx(25.0)
    assert p['epochs_to_settle'] == 2  # 1.0 donated vs 0.8 budget/epoch


def test_invalid_coverage_ratio():
    with pytest.raises(ValueError):
        DonationIncentive(coverage_ratio=0)
    with pytest.raises(ValueError):
        DonationIncentive(coverage_ratio=1.5)


# ── validator end-to-end ─────────────────────────────────────────

def test_epoch_credits_only_verified_and_bound(validator):
    m = make_miner(1, validator)
    m.donate(0.2, 'cleanwater')
    # donation to an unverified charity address
    validator.registry.add('shady', 'Shady', 'demo://shady', verified=False)
    validator.chain.transfer(m.address, 'demo://shady', 0.5)
    # donation from an unbound stranger
    validator.chain.transfer('stranger://x', 'demo://cleanwater', 0.5)

    r = validator.epoch()
    assert r['donations_seen'] == 1
    assert r['credited'] == {'1': pytest.approx(0.2)}
    reasons = {s['reason'] for s in r['skipped']}
    assert reasons == {'recipient not whitelisted', 'sender not a registered miner'}


def test_epoch_scan_cursor_no_double_credit(validator):
    m = make_miner(1, validator)
    m.donate(0.2)
    r1 = validator.epoch()
    assert r1['credited'] == {'1': pytest.approx(0.2)}
    r2 = validator.epoch()  # nothing new on chain
    assert r2['credited'] == {}
    assert r2['donations_seen'] == 0


def test_epoch_weights_sum_to_one_and_payout_rate(validator):
    m1, m2 = make_miner(1, validator), make_miner(2, validator)
    m1.donate(0.4)
    m2.donate(0.2)
    r = validator.epoch()
    assert sum(r['weights'].values()) == pytest.approx(1.0)
    # emissions cover donations at exactly 1/rho for every miner
    assert r['payouts']['1'] == pytest.approx(0.4 / 0.8)
    assert r['payouts']['2'] == pytest.approx(0.2 / 0.8)
    for uid in ('1', '2'):
        assert r['payouts'][uid] > r['credited'][uid]


def test_validator_state_survives_restart(tmp_path, registry, chain):
    store = str(tmp_path / 'store')
    v1 = CharitaoValidator(local=True, storage_path=store, chain=chain,
                           registry=registry)
    m = make_miner(1, v1)
    m.donate(0.2)
    v1.epoch()

    v2 = CharitaoValidator(local=True, storage_path=store, chain=chain,
                           registry=registry)
    assert v2.incentive.epoch == 1
    assert v2.last_block == 1
    assert v2.bindings[m.address] == 1
    r = v2.epoch()  # restart must not re-credit the old transfer
    assert r['credited'] == {}


def test_miner_refuses_unverified_charity(validator):
    m = make_miner(1, validator)
    validator.registry.add('shady', 'Shady', 'demo://shady', verified=False)
    with pytest.raises(ValueError):
        m.donate(0.1, 'shady')


def test_miner_suggest_donation():
    m = CharitaoMiner(local=True)
    s = m.suggest_donation(epoch_emission=1.0, coverage_ratio=0.8,
                           expected_donors=4)
    assert s['suggested'] == pytest.approx(0.2)
    assert s['payout_if_credited'] == pytest.approx(0.25)
    assert s['margin_pct'] == pytest.approx(25.0)


# ── mod-level smoke ──────────────────────────────────────────────

def test_mod_simulate_smoke(tmp_path):
    # load the anchor by path — plain `import mod` could hit the mod framework
    import importlib.util
    import charitao_subnet.chain as chain_mod
    import charitao_subnet.registry as reg_mod
    spec = importlib.util.spec_from_file_location(
        'charitao_anchor', Path(ROOT) / 'mod.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m = mod.Mod(storage_path=str(tmp_path / 'store'))
    # route the sim through tmp paths
    from charitao_subnet.validator import CharitaoValidator
    m._validator = CharitaoValidator(
        local=True, storage_path=str(tmp_path / 'store'),
        chain=chain_mod.MockChain(path=str(tmp_path / 'mc.json')),
        registry=reg_mod.CharityRegistry(path=str(tmp_path / 'reg.json')))

    out = m.simulate(n_miners=3, epochs=2)
    assert out['epochs_run'] == 2
    lb = out['leaderboard']
    assert len(lb) == 3
    for row in lb:
        assert row['projected_payout'] == pytest.approx(row['credited'] / 0.8)
        assert row['projected_profit'] > 0
    st = m.status()
    assert st['epoch'] == 2
    assert st['margin_pct'] == pytest.approx(25.0)
