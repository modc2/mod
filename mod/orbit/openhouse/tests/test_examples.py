"""
The testnet walkthroughs must all play clean, never touch the live store,
and never leak the operator key into a transcript.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent))


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


openhouse_mod = _load('openhouse_under_test', MODULE_DIR / 'mod.py')


@pytest.fixture
def oh(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return openhouse_mod.Mod()


def _names():
    return [e['name'] for e in openhouse_mod.Mod(store='/tmp/openhouse-examples-names').examples()]


def test_catalog_describes_every_example(oh):
    cat = oh.examples()
    assert len(cat) >= 8
    for e in cat:
        assert e['name'] and e['title'] and e['shows'] and 'fn' not in e


@pytest.mark.parametrize('name', _names())
def test_every_example_passes(oh, name):
    r = oh.example(name)
    assert r['ok'], (r['error'], r['steps'][-1:])
    assert r['steps'] and r['checks'] and all(c['ok'] for c in r['checks'])
    assert all(s['tool'].startswith('openhouse_') for s in r['steps'])
    json.dumps(r, default=str)                      # a transcript must serialize


def test_examples_leave_the_live_store_alone(oh):
    before = sorted(p.name for p in oh.store_dir.iterdir())
    for name in ('first_rent', 'bank_transfer', 'bank_statement'):
        oh.example(name)
    assert sorted(p.name for p in oh.store_dir.iterdir()) == before
    assert oh.rent_stats()['payments'] == 0
    assert not (oh.secrets_dir / 'bank_key').exists()


def test_transcripts_never_carry_the_key(oh):
    r = oh.example('bank_statement')
    keyed = [s for s in r['steps'] if 'key' in s['args']]
    assert keyed and all(s['args']['key'] == '‹operator bank key›' for s in keyed)
    assert 'ohbk_' not in json.dumps(r, default=str)


def test_unknown_example(oh):
    assert 'no example' in oh.example('nope')['error']
