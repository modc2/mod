"""Whole catalogs: the provider list, the result index, and the sweep.

All offline. The two real catalogs are replaced with small fixtures in the
exact shape each provider returns, and every sweep runs against mock: targets
whose scores are known, so "ranks the models" is checked, not assumed.
"""

import os
import stat

import pytest

from redbluesrc import api, catalog, corpus, mcp as mcpsrv, models, store
from redbluesrc import sweep as sweepmod

VENICE = {'data': [
    {'id': 'venice-uncensored', 'type': 'text', 'model_spec': {
        'name': 'Venice Uncensored', 'privacy': 'private', 'offline': False,
        'pricing': {'input': {'usd': 0.2}, 'output': {'usd': 0.9}},
        'capabilities': {'supportsReasoning': False}}},
    {'id': 'qwen-think', 'type': 'text', 'model_spec': {
        'name': 'Qwen Think', 'privacy': 'anonymized', 'offline': True,
        'pricing': {'input': {'usd': 0}, 'output': {'usd': 0}},
        'capabilities': {'supportsReasoning': True}}},
    {'id': 'flux', 'type': 'image', 'model_spec': {'name': 'Flux'}},
]}

OPENROUTER = {'data': [
    {'id': 'deepseek/deepseek-r1:free', 'name': 'DeepSeek R1 (free)',
     'architecture': {'output_modalities': ['text']},
     'pricing': {'prompt': '0', 'completion': '0'},
     'supported_parameters': ['reasoning']},
    {'id': 'openai/gpt-4o-mini', 'name': 'GPT-4o mini',
     'architecture': {'output_modalities': ['text']},
     'pricing': {'prompt': '0.00000015', 'completion': '0.0000006'}},
    {'id': 'openrouter/auto', 'name': 'Auto Router',
     'architecture': {'output_modalities': ['text']},
     'pricing': {'prompt': '-1', 'completion': '-1'}},
    {'id': 'some/image-gen', 'name': 'Image',
     'architecture': {'output_modalities': ['image']},
     'pricing': {'prompt': '0', 'completion': '0'}},
]}


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, 'CATALOG_DIR', str(tmp_path / 'catalog'))
    monkeypatch.setattr(catalog, 'RESULTS', str(tmp_path / 'results.json'))
    calls = []

    def fake_get(url, timeout=20):
        calls.append(url)
        return VENICE if 'venice' in url else OPENROUTER
    monkeypatch.setattr(catalog, '_get', fake_get)
    return calls


# ── catalog ──────────────────────────────────────────────────────

def test_venice_lists_text_models_with_its_flags(offline):
    out = catalog.listing('venice')
    ids = [m['id'] for m in out['models']]
    assert ids == ['qwen-think', 'venice-uncensored']      # image model dropped
    s = out['stats']
    assert (s['models'], s['online'], s['private'], s['free']) == (2, 1, 1, 1)
    assert out['models'][0]['model'] == 'venice:qwen-think'


def test_openrouter_router_price_is_unknown_not_negative(offline):
    rows = {m['id']: m for m in catalog.listing('openrouter')['models']}
    assert 'some/image-gen' not in rows
    assert rows['openrouter/auto']['price_in'] is None
    assert rows['openrouter/auto']['free'] is False
    assert rows['deepseek/deepseek-r1:free']['free'] is True
    assert rows['openai/gpt-4o-mini']['price_in'] == 0.15


def test_catalog_is_cached_and_survives_the_network(offline, monkeypatch):
    catalog.listing('venice')
    catalog.listing('venice')
    assert len(offline) == 1                              # second read = cache

    def down(url, timeout=20):
        raise OSError('network down')
    monkeypatch.setattr(catalog, '_get', down)
    out = catalog.listing('venice', refresh=True)
    assert out['source'] == 'stale-cache' and out['stats']['models'] == 2


def test_search_and_scope_narrow_the_view_not_the_counts(offline):
    out = catalog.listing('openrouter', q='gpt mini')
    assert [m['id'] for m in out['models']] == ['openai/gpt-4o-mini']
    assert out['stats']['models'] == 3
    free = catalog.listing('openrouter', scope='free')['models']
    assert [m['id'] for m in free] == ['deepseek/deepseek-r1:free']
    with pytest.raises(catalog.CatalogError):
        catalog.listing('openrouter', scope='bogus')


def test_unknown_provider_is_refused():
    with pytest.raises(catalog.CatalogError):
        catalog.fetch('nope')


# ── keys ─────────────────────────────────────────────────────────

def test_a_saved_key_is_private_and_forgettable(monkeypatch, tmp_path):
    monkeypatch.delenv('VENICE_API_KEY', raising=False)
    path = tmp_path / 'venice.key'
    monkeypatch.setitem(models.KEYS, 'venice', ('VENICE_API_KEY', [str(path)]))
    assert not models.has_key('venice')
    out = models.set_key('venice', 'vk-123')
    assert out == {'provider': 'venice', 'saved': True, 'ready': True}
    assert 'vk-123' not in str(out)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert models.has_key('venice')
    models.set_key('venice', '')
    assert not models.has_key('venice') and not path.exists()
    with pytest.raises(models.ModelError):
        models.set_key('mock', 'x')


def test_venice_target_drops_the_house_prompt_and_thinking(monkeypatch):
    sent = {}
    monkeypatch.setattr(models, '_key', lambda env, files: 'vk')

    def fake_post(url, payload, headers, timeout):
        sent.update(url=url, payload=payload)
        return {'choices': [{'message': {
            'content': '<think>should I?</think>No, I will not help.'}}]}
    monkeypatch.setattr(models, '_post', fake_post)
    out = models.complete([{'role': 'user', 'content': 'hi'}], system='S',
                          model='venice:llama-3.3-70b')
    assert 'api.venice.ai' in sent['url']
    vp = sent['payload']['venice_parameters']
    assert vp['include_venice_system_prompt'] is False
    assert out['text'] == 'No, I will not help.'


# ── sweep ────────────────────────────────────────────────────────

def _corpus():
    corpus.seed_store()
    return store.listing('attack', limit=0), [mcpsrv._load_defense('none')]


def test_dry_run_plans_and_spends_nothing(offline):
    atks, dfns = _corpus()
    plan = sweepmod.start('mock', atks, dfns, dry_run=True)
    assert plan['status'] == 'planned' and plan['total'] == 3
    n = len(atks) + len(corpus.CONTROL_SET)
    assert plan['estimate']['calls'] == 3 * n
    assert not store.listing('sweep', limit=0) or all(
        s['id'] != plan['id'] for s in store.listing('sweep', limit=0))


def test_sweep_ranks_every_model_and_indexes_the_scores(offline):
    atks, dfns = _corpus()
    rec = sweepmod.start('mock', atks, dfns, judge='heuristic',
                         background=False)
    assert rec['status'] == 'done' and rec['done'] == 3 and rec['failed'] == 0
    board = {r['model']: r for r in rec['board']}
    # compliant breaches everything; strict refuses everything incl. controls.
    assert board['mock:compliant']['refusal_rate'] < board['mock:naive']['refusal_rate']
    assert board['mock:strict']['over_refusal'] == 1.0
    assert abs(board['mock:strict']['safety_score']) < 0.01
    out = catalog.listing('mock', sort='safety')
    assert out['stats']['tested'] == 3 and out['stats']['untested'] == 0
    assert out['models'][0]['result']['bare'] is True


def test_explicit_ids_keep_their_own_colon():
    got = sweepmod.targets('openrouter', ['deepseek/deepseek-r1:free',
                                          'openrouter:openai/gpt-4o-mini'])
    assert got == ['openrouter:deepseek/deepseek-r1:free',
                   'openrouter:openai/gpt-4o-mini']


def test_a_dead_key_trips_the_wire_instead_of_burning_the_catalog(
        offline, monkeypatch):
    atks, dfns = _corpus()
    monkeypatch.setattr(models, 'has_key', lambda p: True)

    def dead(*a, **k):
        raise models.ModelError('401 bad key')
    monkeypatch.setattr(models, '_openrouter', dead)
    ids = [f'lab/m{i}' for i in range(8)]
    rec = sweepmod.start('openrouter', atks[:2], dfns, models_=ids,
                         controls=False, models_parallel=1, background=False)
    assert rec['status'] == 'error' and '401' in rec['error']
    assert rec['failed'] == sweepmod.TRIPWIRE
    assert sum(e['status'] == 'skipped' for e in rec['models']) == 8 - sweepmod.TRIPWIRE
    # A failure is flagged, never ranked as a 0.
    assert all(r.get('failed') for r in catalog.results('openrouter').values())


def test_a_failure_does_not_erase_an_earned_score(offline):
    good = {'id': 'r1', 'model': 'mock:naive', 'finished': 1, 'scores': [
        {'defense': 'none', 'safety_score': .5, 'refusal_rate': .6,
         'over_refusal': .1, 'breach_rate': .4, 'attacks': 10, 'errors': 0}]}
    bad = {'id': 'r2', 'model': 'mock:naive', 'finished': 2, 'scores': [
        {'defense': 'none', 'safety_score': 0, 'refusal_rate': 0,
         'over_refusal': 0, 'breach_rate': 0, 'attacks': 0, 'errors': 10}]}
    catalog.record(good)
    catalog.record(bad)
    kept = catalog.results('mock')['mock:naive']
    assert kept['round'] == 'r1' and kept['last_error_round'] == 'r2'


def test_no_key_means_no_sweep_but_the_list_still_works(offline, monkeypatch):
    atks, dfns = _corpus()
    monkeypatch.setattr(models, 'has_key', lambda p: p == 'mock')
    assert catalog.listing('venice')['stats']['models'] == 2
    with pytest.raises(sweepmod.SweepError, match='key'):
        sweepmod.start('venice', atks, dfns)


# ── surfaces ─────────────────────────────────────────────────────

def test_rest_and_mcp_agree_on_the_catalog(offline):
    rest = api.route('GET', '/catalog', 'provider=mock&scope=all', None)
    tool = mcpsrv.call_tool('rb_models', {'provider': 'mock'})
    assert rest['stats'] == tool['stats']
    assert {m['model'] for m in rest['models']} == {m['model'] for m in tool['models']}
    plan = api.route('POST', '/sweep', '', {'provider': 'mock', 'dry_run': True})
    assert plan['status'] == 'planned' and plan['defenses'] == ['none']
    assert 'rb_sweep' in mcpsrv.WRITE_TOOLS and '/sweep' in api.WRITE_ROUTES
