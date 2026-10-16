"""Offline tests — every one runs in a throwaway store under tmp_path."""

import importlib.util
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(HERE)
if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import reviews  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = reviews.Store(str(tmp_path / 'r.db'))
    yield s
    s.close()


def test_config_parses_and_matches():
    with open(os.path.join(MODULE_DIR, 'config.json')) as f:
        cfg = json.load(f)
    assert cfg['name'] == 'reviews' and cfg['anchor'] == 'mod.py'
    assert cfg['port'] == 51440
    for fn in cfg['api_fns']:
        assert fn in cfg['fns']


def test_anchor_loads_by_path(tmp_path):
    spec = importlib.util.spec_from_file_location('reviews_anchor', os.path.join(MODULE_DIR, 'mod.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    m = module.Mod(path=str(tmp_path / 'r.db'))
    assert m.health()['ok']
    ids = [a['id'] for a in m.aggregators()['aggregators']]
    assert ids[0] == 'mean'


def test_registry_default_is_mean_and_soon_sorts_last():
    ids = [a['id'] for a in reviews.aggregators()]
    assert ids[0] == 'mean'
    assert ids[-1] == 'staketime'
    assert all('formula' in a and 'status' in a for a in reviews.aggregators())


def test_mean_is_a_plain_average(store):
    store.rate('build:x', '0xA', 5, 'human', ref='r1')
    store.rate('build:x', 'agent', 2, 'agent', ref='r2')
    out = store.score('build:x')
    assert out['aggregator'] == 'mean'
    assert out['score'] == 3.5 and out['n'] == 2
    assert out['by_kind'] == {'human': 1, 'agent': 1}
    assert all(r['weight'] == 1.0 for r in out['breakdown'])


def test_rating_is_one_per_rater_and_ref(store):
    store.rate('s', '0xA', 2, ref='r1')
    store.rate('s', '0xa', 4, ref='r1')  # same rater, case-insensitive → update
    assert store.score('s')['n'] == 1
    assert store.score('s')['score'] == 4.0
    store.rate('s', '0xA', 1, ref='r2')  # a second review by the same rater
    assert store.score('s')['n'] == 2


def test_score_bounds():
    with pytest.raises(ValueError):
        reviews._clamp_score(0)
    with pytest.raises(ValueError):
        reviews._clamp_score(6)
    with pytest.raises(ValueError):
        reviews._clamp_score('x')
    assert reviews._clamp_score('4.5') == 4.5


def test_weighted_quotes_its_table_and_why(store):
    store.rate('s', '0xA', 5, 'human', ref='a')
    store.rate('s', 'bot', 1, 'agent', ref='b')
    store.set_weights('s', {'by_kind': {'agent': 0.5}})
    out = store.score('s', 'weighted')
    # (5·1 + 1·0.5) / 1.5 = 3.67
    assert out['score'] == 3.67
    assert out['weights_source'] == 's'
    assert out['weights']['by_kind'] == {'agent': 0.5}
    whys = {r['rater']: r['why'] for r in out['breakdown']}
    assert whys['bot'].startswith('by_kind[agent]')
    assert whys['0xa'].startswith('default')


def test_weighted_by_rater_beats_by_kind(store):
    store.rate('s', '0xA', 5, 'human', ref='a')
    store.rate('s', '0xB', 1, 'human', ref='b')
    out = store.score('s', 'weighted', weights={'by_kind': {'human': 1}, 'by_rater': {'0xB': 3}})
    assert out['score'] == 2.0  # (5 + 3)/4
    assert out['weights_source'] == 'inline'


def test_star_table_applies_to_every_subject(store):
    store.set_weights('*', {'by_kind': {'agent': 0}})
    store.rate('t', 'bot', 1, 'agent', ref='a')
    store.rate('t', '0xA', 5, 'human', ref='b')
    out = store.score('t', 'weighted')
    assert out['score'] == 5.0 and out['weights_source'] == '*'


def test_all_zero_weights_fall_back_to_mean_and_say_so(store):
    store.rate('z', 'bot', 1, 'agent', ref='a')
    out = store.score('z', 'weighted', weights={'by_kind': {'agent': 0}})
    assert out['fallback']['to'] == 'mean'
    assert out['score'] == 1.0


def test_staketime_without_provider_falls_back(store, monkeypatch):
    monkeypatch.delenv('REVIEWS_STAKE_URL', raising=False)
    store.rate('k', '0xA', 4, ref='a')
    out = store.score('k', 'staketime')
    assert out['status'] == 'soon'
    assert out['fallback']['to'] == 'mean'
    assert 'provider' in out['fallback']['why']
    assert out['score'] == 4.0


def test_staketime_with_provider(store, monkeypatch):
    monkeypatch.setattr(reviews, 'stake_weights', lambda addrs, url=None: {'0xa': 3.0, '0xb': 1.0})
    store.rate('k', '0xA', 5, ref='a')
    store.rate('k', '0xB', 1, ref='b')
    out = store.score('k', 'staketime')
    assert out['score'] == 4.0  # (15 + 1)/4
    assert 'fallback' not in out


def test_stateless_aggregate_and_unknown_aggregator():
    out = reviews.aggregate([{'rater': 'a', 'kind': 'agent', 'score': 3}, {'rater': 'b', 'score': 5}])
    assert out['score'] == 4.0 and out['n'] == 2
    assert reviews.aggregate([])['score'] is None
    with pytest.raises(ValueError):
        reviews.aggregate([], 'nope')


def test_bad_weights_refused():
    with pytest.raises(ValueError):
        reviews.normalize_weights({'default': -1})
    with pytest.raises(ValueError):
        reviews.normalize_weights({'by_kind': {'robot': 1}})


def test_subject_default_aggregator(store):
    store.rate('p', 'a', 3, ref='x')
    store.set_aggregator('p', 'weighted')
    assert store.score('p')['aggregator'] == 'weighted'
    assert store.score('p')['default_aggregator'] == 'weighted'
    assert store.subjects()[0]['subject'] == 'p'


def test_serve_api_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('REVIEWS_DIR', str(tmp_path))
    import serve
    serve._store = None
    assert serve.api('health', {})['ok']
    serve.api('rate', {'subject': 'm', 'rater': 'a', 'score': 4, 'kind': 'agent', 'ref': 'r'})
    assert serve.api('score', {'subject': 'm'})['score'] == 4.0
    assert serve.api('aggregators', {})['default'] == 'mean'
    assert serve.api('aggregate', {'ratings': [{'rater': 'q', 'score': 2}]})['score'] == 2.0
    assert 'weights' in serve.api('weights', {'subject': 'm'})
    # aggregate + subject reads the published table and the subject's default
    serve.api('set_weights', {'subject': 'm', 'weights': {'by_kind': {'agent': 0}}})
    out = serve.api('aggregate', {'subject': 'm', 'aggregator': 'weighted',
                                  'ratings': [{'rater': 'a', 'kind': 'agent', 'score': 1},
                                              {'rater': 'b', 'kind': 'human', 'score': 5}]})
    assert out['score'] == 5.0 and out['weights_source'] == 'm'
    serve.api('set_aggregator', {'subject': 'm', 'aggregator': 'weighted'})
    assert serve.api('aggregate', {'subject': 'm', 'ratings': [{'rater': 'a', 'kind': 'agent', 'score': 1}]})['aggregator'] == 'weighted' 
