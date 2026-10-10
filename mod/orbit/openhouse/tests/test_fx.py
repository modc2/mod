"""The currency rail — all three layers, no network required.

The live layer is stubbed; what these tests pin down is the local-first
contract: a fresh cache short-circuits, a dead upstream serves the stale
cache honestly, and a machine that has never been online still answers.
"""
import json
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import importlib.util

spec = importlib.util.spec_from_file_location(
    'openhouse_fx', Path(__file__).parent.parent / 'fx.py')
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


def _dead(*a, **k):
    raise urllib.error.URLError('no network in tests')


def test_live_writes_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, '_fetch_live', lambda: {'usd': 2500.0, 'eur': 2200.0})
    p = tmp_path / 'fx.json'
    out = fx.rates(p)
    assert out['source'] == 'live'
    assert out['rates']['usd'] == 2500.0
    assert json.loads(p.read_text())['rates']['usd'] == 2500.0


def test_fresh_cache_short_circuits(tmp_path, monkeypatch):
    p = tmp_path / 'fx.json'
    p.write_text(json.dumps({'rates': {'usd': 2400.0}, 'base': 'ETH', 'fetched': int(time.time())}))
    monkeypatch.setattr(fx, '_fetch_live', _dead)  # must not be reached
    out = fx.rates(p)
    assert out['source'] == 'cache'
    assert out['rates']['usd'] == 2400.0


def test_refresh_bypasses_cache(tmp_path, monkeypatch):
    p = tmp_path / 'fx.json'
    p.write_text(json.dumps({'rates': {'usd': 2400.0}, 'base': 'ETH', 'fetched': int(time.time())}))
    monkeypatch.setattr(fx, '_fetch_live', lambda: {'usd': 2600.0})
    assert fx.rates(p, refresh=True)['rates']['usd'] == 2600.0


def test_dead_upstream_serves_stale_cache(tmp_path, monkeypatch):
    p = tmp_path / 'fx.json'
    p.write_text(json.dumps({'rates': {'usd': 2300.0}, 'base': 'ETH', 'fetched': 1000}))
    monkeypatch.setattr(fx, '_fetch_live', _dead)
    out = fx.rates(p)
    assert out['source'] == 'stale'
    assert out['rates']['usd'] == 2300.0
    assert out['fetched'] == 1000  # the honest timestamp, not now


def test_never_online_still_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, '_fetch_live', _dead)
    out = fx.rates(tmp_path / 'fx.json')
    assert out['source'] == 'fallback'
    assert out['fetched'] == 0
    assert set(out['rates']) == set(fx.CURRENCIES)


def test_menu_and_fallback_agree():
    assert set(fx.FALLBACK_RATES) == set(fx.CURRENCIES)
