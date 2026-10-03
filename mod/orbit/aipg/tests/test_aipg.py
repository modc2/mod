"""Tests that pass with no API key and no network.

That is the point rather than a compromise: the joins are where the bugs live,
and a join is pure. The live checks are marked and skipped by default —
`-m live` opts in, and even those never need a key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import importlib.util

spec = importlib.util.spec_from_file_location('aipg_anchor', HERE / 'mod.py')
aipg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aipg)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the operator's real ~/.mod/aipg or real key."""
    monkeypatch.delenv('AIPG_API_KEY', raising=False)
    monkeypatch.setattr(aipg, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(aipg, 'KEY_FILE', tmp_path / 'state' / 'key.json')
    monkeypatch.setattr(aipg, 'OUT', tmp_path / 'state' / 'out')
    yield


# the real payload shapes, trimmed — including the casing mismatch
ROSTER = [
    {'name': 'gpt-oss-120b', 'count': 1, 'type': 'text',
     'max_context_length': 60000, 'tokens_per_s': None, 'avg_latency_s': None},
    {'name': 'FLUX.2 Klein 4B FP8', 'count': 1, 'type': 'image',
     'max_context_length': 2048},
    {'name': 'LTX-2.3', 'count': 0, 'type': 'video', 'max_context_length': 2048},
    {'name': 'ace-step-v1.5-xl-turbo', 'count': 1, 'type': 'audio',
     'max_context_length': 2048},
]
BOOK = {
    'currency': 'USD', 'ledger_unit': 'micro_usd',
    'price_book': {'version': 'test', 'models': [
        {'model': 'gpt-oss-120b', 'rates': {'input_per_mtok_usd': 0.05,
                                            'output_per_mtok_usd': 0.2}},
        {'model': 'flux.2 klein 4b fp8', 'rates': {'per_image_usd': 0.01}},
        {'model': 'ltx-2.3', 'rates': {'per_video_second_usd': 0.05}},
    ]},
}
OPENAI = {'object': 'list', 'data': [{'id': 'auto'}, {'id': 'gpt-oss-120b'}]}
WORKERS = {'count': 2, 'workers': [
    {'id': 'a', 'name': 'w1', 'models': ['gpt-oss-120b'],
     'job_types': ['text'], 'online': True},
    {'id': 'b', 'name': 'w2', 'models': ['LTX-2.3'], 'job_types': ['video'],
     'online': False},
]}


@pytest.fixture
def mod(monkeypatch):
    """A Mod whose wire is a dict lookup."""
    m = aipg.Mod()
    routes = {'/status/models': ROSTER, '/pricing': BOOK, '/models': OPENAI,
              '/workers': WORKERS}

    def fake(path, body=None, keyed=False, raw=False, stream=False):
        assert not keyed, 'a read asked for the key'
        return json.loads(json.dumps(routes[path]))

    monkeypatch.setattr(m, '_req', fake)
    return m


def test_price_book_joins_across_case(mod):
    """The whole reason the join is case-insensitive."""
    flux = next(r for r in mod.models() if r['model'] == 'FLUX.2 Klein 4B FP8')
    assert flux['price'] == {'per_image_usd': 0.01}
    assert flux['priced'] is True


def test_only_the_rate_for_that_job_type_survives(mod):
    """A text model must not report per_image_usd, even if the book has one."""
    text = next(r for r in mod.models() if r['model'] == 'gpt-oss-120b')
    assert set(text['price']) == {'input_per_mtok_usd', 'output_per_mtok_usd'}


def test_online_is_worker_count_not_a_price(mod):
    """LTX-2.3 is priced and has zero workers. That is not callable."""
    ltx = next(r for r in mod.models() if r['model'] == 'LTX-2.3')
    assert ltx['priced'] is True and ltx['online'] is False
    assert 'LTX-2.3' not in [r['model'] for r in mod.models(online=True)]


def test_openai_visibility_is_reported_not_assumed(mod):
    rows = {r['model']: r['openai_visible'] for r in mod.models()}
    assert rows['gpt-oss-120b'] is True
    assert rows['FLUX.2 Klein 4B FP8'] is False
    assert rows['ace-step-v1.5-xl-turbo'] is False


def test_type_filter(mod):
    assert [r['model'] for r in mod.models(type='image')] == \
           ['FLUX.2 Klein 4B FP8']
    assert mod.models(type='nonsense') == []


def test_models_survive_a_dead_price_book(mod, monkeypatch):
    """The roster is the load-bearing read; pricing is enrichment."""
    def fake(path, **kw):
        if path == '/pricing':
            raise aipg.Refused(500, 'boom', path)
        return json.loads(json.dumps({'/status/models': ROSTER,
                                      '/models': OPENAI,
                                      '/workers': WORKERS}[path]))
    monkeypatch.setattr(mod, '_req', fake)
    rows = mod.models()
    assert len(rows) == len(ROSTER)
    assert all(r['price'] == {} for r in rows)


def test_grid_summary(mod):
    g = mod.grid()
    assert g['models'] == 4 and g['online'] == 3
    assert g['by_type']['video'] == {'models': 1, 'online': 0, 'names': []}
    assert g['cheapest_text'] == {'model': 'gpt-oss-120b',
                                  'usd_per_mtok_out': 0.2}
    assert sorted(g['hidden_from_openai_clients']) == \
           ['FLUX.2 Klein 4B FP8', 'ace-step-v1.5-xl-turbo']


def test_workers_filters_offline(mod):
    assert mod.workers()['count'] == 1
    assert mod.workers(online=False)['count'] == 2


def test_pricing_lookup_is_case_insensitive_and_suggests(mod):
    assert mod.pricing('GPT-OSS-120B')['rates']['input_per_mtok_usd'] == 0.05
    with pytest.raises(KeyError, match='klein'):
        mod.pricing('flux')
    with pytest.raises(KeyError):
        mod.pricing('no-such-model')


def test_estimate(mod):
    got = mod.estimate('gpt-oss-120b', input_tokens=1_000_000,
                       output_tokens=500_000)
    assert got['usd'] == pytest.approx(0.05 + 0.1)
    with pytest.raises(KeyError):
        mod.estimate('flux.2 klein 4b fp8', 10, 10)


def test_media_default_model_is_the_first_online_one(mod, monkeypatch):
    seen = {}

    def fake(path, body=None, keyed=False, **kw):
        if path == '/images/generations':
            seen.update(body=body, keyed=keyed)
            return {'data': []}
        return json.loads(json.dumps({'/status/models': ROSTER,
                                      '/pricing': BOOK, '/models': OPENAI,
                                      '/workers': WORKERS}[path]))
    monkeypatch.setattr(mod, '_req', fake)
    got = mod.image('a tin robot')
    assert got['model'] == 'FLUX.2 Klein 4B FP8'
    assert seen['keyed'] is True
    assert seen['body']['prompt'] == 'a tin robot'


def test_media_refuses_when_no_worker_holds_that_type(mod):
    with pytest.raises(aipg.Refused, match='no video worker'):
        mod.video('a tin robot waving')


def test_key_is_written_0600_and_never_echoed(mod):
    got = mod.set_key('sk-abcdef0123456789')
    assert oct(Path(got['stored']).stat().st_mode)[-3:] == '600'
    assert 'abcdef0123456789' not in json.dumps(got)
    assert mod.key() == 'sk-abcdef0123456789'
    assert mod.key_status()['present'] is True
    assert 'abcdef0123456789' not in json.dumps(mod.key_status())


def test_env_key_wins(mod, monkeypatch):
    mod.set_key('sk-stored-key-value')
    monkeypatch.setenv('AIPG_API_KEY', 'sk-env-key-value')
    assert mod.key() == 'sk-env-key-value'
    assert mod.key_status()['source'] == 'AIPG_API_KEY'


def test_no_key_is_an_actionable_error(mod):
    with pytest.raises(aipg.NoKey, match='console.aipowergrid.io'):
        mod.key()
    assert mod.key_status()['present'] is False


def test_forget_key(mod):
    mod.set_key('sk-abcdef0123456789')
    assert mod.forget_key()['removed'] is True
    assert mod.forget_key()['removed'] is False


def test_chat_needs_something_to_say(mod):
    with pytest.raises(ValueError):
        mod.chat()


def test_chat_builds_an_openai_body_and_returns_the_text(mod, monkeypatch):
    seen = {}

    def fake(path, body=None, keyed=False, **kw):
        seen.update(path=path, body=body, keyed=keyed)
        return {'model': 'gpt-oss-120b',
                'choices': [{'message': {'role': 'assistant', 'content': 'hi'},
                             'finish_reason': 'stop'}],
                'usage': {'total_tokens': 3}}
    monkeypatch.setattr(mod, '_req', fake)
    got = mod.chat('hello', system='be brief')
    assert seen['path'] == '/chat/completions' and seen['keyed'] is True
    assert [m['role'] for m in seen['body']['messages']] == ['system', 'user']
    assert seen['body']['model'] == 'auto' and seen['body']['stream'] is False
    assert got['text'] == 'hi' and got['usage']['total_tokens'] == 3
    assert mod.ask('hello') == 'hi'


def test_save_handles_b64_and_reports_paths(mod):
    import base64
    blob = base64.b64encode(b'x' * 4096).decode()
    saved = aipg.Mod._save({'data': [{'b64_json': blob}]}, 'image')
    assert len(saved) == 1
    p = Path(saved[0])
    assert p.suffix == '.png' and p.read_bytes() == b'x' * 4096


def test_fns_in_config_all_exist():
    """config.json must not promise a fn the anchor does not have."""
    cfg = json.loads((HERE / 'config.json').read_text())
    m = aipg.Mod()
    missing = [f for f in cfg['fns'] if not callable(getattr(m, f, None))]
    assert missing == []
    assert set(cfg['public_fns']) <= set(cfg['fns'])


# ── live, opt-in with -m live, still no key needed ───────────────────

@pytest.mark.live
def test_live_roster_is_bigger_than_the_openai_list():
    """The claim the whole module rests on. If this fails, say so in the README."""
    m = aipg.Mod()
    roster, openai = m.roster(), m.openai_models()
    assert len(roster) > 0
    assert len(roster) >= len(openai)
    assert {r['type'] for r in roster} - {'text'}, \
        'no media models in the roster — upstream may have changed'


@pytest.mark.live
def test_live_reads_agree():
    got = aipg.Mod().test()
    assert got['ok'], got['checks']
