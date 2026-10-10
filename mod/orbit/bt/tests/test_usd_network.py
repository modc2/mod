"""bt.usd + bt.network — offline: fake tickers, preloaded chain cache."""
import os
import sys
import time

import pytest

os.environ['BT_NO_SNAPSHOT'] = '1'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bt import network, usd  # noqa: E402


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv('BT_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(usd, '_mem', {})
    return tmp_path


def test_usd_median_of_sources(fresh, monkeypatch):
    monkeypatch.setattr(usd, 'SOURCES', {
        'a': lambda: {'usd': 300.0, 'change_24h': 2.0},
        'b': lambda: {'usd': 310.0, 'change_24h': 4.0},
        'c': lambda: {'usd': 9999.0, 'change_24h': None},   # outlier can't win
    })
    s = usd.spot()
    assert s['usd'] == 310.0                 # median of 300/310/9999
    assert s['change_24h'] == 3.0
    assert s['sources'] == {'a': 300.0, 'b': 310.0, 'c': 9999.0}
    assert s['stale'] is False and s['age_sec'] == 0


def test_usd_partial_failure_and_stale_fallback(fresh, monkeypatch):
    def boom():
        raise OSError('exchange down')
    monkeypatch.setattr(usd, 'SOURCES', {
        'up': lambda: {'usd': 250.0, 'change_24h': 1.0}, 'down': boom})
    s = usd.spot()
    assert s['usd'] == 250.0 and 'down' in (s['errors'] or {})
    # every source dies -> last good answer, flagged stale
    monkeypatch.setattr(usd, 'SOURCES', {'down': boom})
    s2 = usd.spot(max_age=0)
    assert s2['usd'] == 250.0 and s2['stale'] is True and s2['fetch_error']


def test_usd_disk_cache_survives_restart(fresh, monkeypatch):
    monkeypatch.setattr(usd, 'SOURCES', {'a': lambda: {'usd': 123.0, 'change_24h': 0.0}})
    usd.spot()
    monkeypatch.setattr(usd, '_mem', {})     # "restart"
    def boom():
        raise OSError('offline')
    monkeypatch.setattr(usd, 'SOURCES', {'a': boom})
    s = usd.spot(max_age=0)
    assert s['usd'] == 123.0 and s['stale'] is True


def test_halving_math_schedule():
    h0 = network.halving_math(0.0)
    assert h0['halvings'] == 0 and h0['block_emission_tao'] == 1.0
    assert h0['next_halving_at_tao'] == 10_500_000
    assert h0['daily_emission_tao'] == pytest.approx(7200)
    h1 = network.halving_math(10_600_000)
    assert h1['halvings'] == 1 and h1['block_emission_tao'] == 0.5
    assert h1['next_halving_at_tao'] == 15_750_000
    assert h1['est_days_to_halving'] == pytest.approx(
        (15_750_000 - 10_600_000) / 3600)
    assert network.halving_math(10_500_000)['halvings'] == 1    # exact boundary
    hend = network.halving_math(21_000_000)
    assert hend['tao_to_halving'] >= 0 and hend['pct_issued'] == 100


def test_network_serves_cache_and_folds_usd(monkeypatch):
    monkeypatch.setattr(network, '_mem', {
        'block': 7_000_000, 'total_issuance_tao': 10_600_000.0,
        'total_staked_tao': 6_000_000.0, 'staked_pct': 56.6,
        'ts': int(time.time()), **network.halving_math(10_600_000.0)})
    monkeypatch.setattr(usd, 'spot', lambda max_age=60: {'usd': 400.0})
    n = network.network()
    assert n['block'] == 7_000_000 and n['usd']['usd'] == 400.0
    assert n['halvings'] == 1 and n['age_sec'] >= 0
