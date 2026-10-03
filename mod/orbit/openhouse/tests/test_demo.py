"""
The demo seed — fake examples on the testnet store.

What these tests pin down: seeding goes through the real accounting paths
(so the books balance), it is deterministic, it refuses to eat data it
didn't write, and unseed removes exactly what seed created.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent
MOD_ROOT = MODULE_DIR.parent.parent.parent
sys.path.insert(0, str(MOD_ROOT))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


openhouse_mod = _load('openhouse_demo_under_test', MODULE_DIR / 'mod.py')
demo = _load('openhouse_demo_book', MODULE_DIR / 'demo.py')


@pytest.fixture
def oh(tmp_path, monkeypatch):
    """A Mod whose ~/.openhouse store is a throwaway directory."""
    monkeypatch.setenv('HOME', str(tmp_path))
    return openhouse_mod.Mod()


def test_seed_populates_every_surface(oh):
    out = oh.seed()
    assert out.get('success'), out
    status = oh.status()
    assert status['deployed'] is True
    assert status['rent']['payments'] == len(demo.PAYMENTS)
    assert status['rent']['renters'] == 2
    assert status['shareholders'] == len(demo.PURCHASES)
    assert status['dividend_count'] == 1
    # The property is labelled as fake, loudly.
    prop = oh.property()
    assert prop['demo'] is True
    assert 'DEMO' in prop['description']


def test_books_balance(oh):
    """Every seeded dollar is fee + equity + owner income — the same split
    the real payer produces, because it IS the real payer."""
    oh.seed()
    stats = oh.rent_stats()
    parts = stats['protocol_fees'] + stats['renter_equity'] + stats['owner_income']
    assert abs(stats['gross_rent'] - parts) < 1e-6
    assert stats['take_pct'] == pytest.approx(demo.FEE_PCT, abs=0.01)
    # The option fee is all equity, so equity share of gross beats credit_pct alone.
    assert stats['renter_equity'] > 0
    assert 0 < stats['owned_pct'] < 100


def test_history_is_backdated_and_pool_accrues(oh):
    oh.seed()
    ledger = oh.rent_ledger()
    stamps = [r['timestamp'] for r in ledger]
    assert stamps == sorted(stamps, reverse=True)   # newest first, real spread
    assert stamps[0] - stamps[-1] > 50 * demo.DAY
    pool = oh.pool()
    assert pool['quarter'] == 0
    assert pool['pool'] > 0                          # fees accrued to the pool
    assert 50 < pool['progress_pct'] < 80            # mid-quarter, not day zero
    addrs = {p['address'] for p in pool['positions']}
    assert demo.CAST['owner'] in addrs
    assert demo.CAST['maya'] in addrs
    assert demo.CAST['ada'] in addrs


def test_deterministic_cast(oh):
    a = oh.seed()['cast']
    oh.seed()
    b = oh.seed()['cast']
    assert a == b
    assert all(len(v) == 42 and v.startswith('0x') for v in a.values())


def test_refuses_foreign_data_unless_forced(oh):
    oh.pay_rent('0x1111111111111111111111111111111111111111', 1.0)
    assert 'error' in oh.seed()
    assert oh.rent_stats()['payments'] == 1          # untouched
    assert oh.seed(force=True).get('success')
    assert oh.rent_stats()['payments'] == len(demo.PAYMENTS)


def test_unseed_removes_everything(oh):
    oh.seed()
    out = oh.unseed()
    assert out.get('success'), out
    status = oh.status()
    assert status['deployed'] is False
    assert status['rent']['payments'] == 0
    assert status['shareholders'] == 0
    assert 'error' in oh.unseed()                    # nothing left to remove
