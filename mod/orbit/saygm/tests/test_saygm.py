"""Offline tests — a recorded catalog fixture, no network, no key."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(HERE)
if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import gm  # noqa: E402

with open(os.path.join(HERE, 'models_fixture.json')) as f:
    RAW = json.load(f)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, 'STATE_DIR', str(tmp_path))
    monkeypatch.setattr(gm, 'KEY_FILE', str(tmp_path / 'key.json'))
    monkeypatch.setattr(gm, 'CACHE_FILE', str(tmp_path / 'models.json'))
    monkeypatch.delenv('SAYGM_API_KEY', raising=False)

    def no_net(*a, **kw):
        raise AssertionError('network call in an offline test')
    monkeypatch.setattr(gm, '_http', no_net)


def test_config_parses_and_matches():
    with open(os.path.join(MODULE_DIR, 'config.json')) as f:
        cfg = json.load(f)
    assert cfg['name'] == 'saygm' and cfg['anchor'] == 'mod.py'
    assert cfg['port'] == 51270 and cfg['base_path'] == '/saygm'
    import mod
    for fn in cfg['fns']:
        assert callable(getattr(mod.Mod, fn)), fn


def test_normalize_converts_nanodollars():
    r = gm.model('claude-sonnet-5', raw=RAW)
    assert r['tier'] == 'frontier' and r['shapes'] == ['messages']
    assert 0 < r['input'] < r['list_input']          # discounted vs list
    assert r['input'] < 100                          # USD/Mtok, not nano
    assert 0 < r['discount'] < 1


def test_filters_and_sort():
    tee = gm.models(tier='confidential', raw=RAW)
    assert tee and all(r['confidential'] for r in tee)
    ids = [r['id'] for r in gm.models(sort='input', raw=RAW)]
    prices = [gm.model(i, RAW)['input'] for i in ids if gm.model(i, RAW)['input'] is not None]
    assert prices == sorted(prices)
    assert 'claude-fable-5-2' not in [r['id'] for r in gm.models(raw=RAW)]  # unavailable hidden
    assert 'claude-fable-5-2' in [r['id'] for r in gm.models(available=False, raw=RAW)]


def test_quote_vs_list():
    q = gm.quote('gpt-5.5', 1_000_000, 0, raw=RAW)
    r = gm.model('gpt-5.5', RAW)
    assert q['usd'] == pytest.approx(r['input'])
    assert q['saved_usd'] > 0 and q['list_usd'] == pytest.approx(r['list_input'])


def test_quote_rejects_image_model():
    with pytest.raises(gm.GMError):
        gm.quote('flux.2-klein-4b', raw=RAW)


@pytest.mark.parametrize('mid,shape,path', [
    ('claude-sonnet-5', 'messages', '/v1/messages'),
    ('gpt-5.5', 'chat.completions', '/v1/chat/completions'),
    ('gpt-5.5-pro', 'responses', '/v1/responses'),
    ('gemini-3.5-flash', 'generateContent', '/v1beta/models/gemini-3.5-flash:generateContent'),
    ('glm-5.3-flash-tee', 'chat.completions', '/v1/chat/completions'),
])
def test_shape_routing(mid, shape, path):
    row = gm.model(mid, RAW)
    msgs, system = gm._messages('', [{'role': 'system', 'content': 'be brief'},
                                     {'role': 'user', 'content': 'gm'}], '')
    s, p, body = gm.build_request(row, msgs, system, 64, None)
    assert (s, p) == (shape, path)
    dumped = json.dumps(body)
    assert 'be brief' in dumped and 'gm' in dumped
    if shape == 'messages':
        assert body['system'] == 'be brief'
        assert all(m['role'] != 'system' for m in body['messages'])


def test_image_model_is_not_chat():
    with pytest.raises(gm.GMError):
        gm.pick_shape(gm.model('flux.2-klein-4b', RAW))


def test_extract_every_shape():
    assert gm.extract('chat.completions', {'choices': [{'message': {'content': 'gm'}}],
                      'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}) == ('gm', 3, 1)
    assert gm.extract('messages', {'content': [{'type': 'text', 'text': 'gm'}],
                      'usage': {'input_tokens': 3, 'output_tokens': 1}}) == ('gm', 3, 1)
    assert gm.extract('responses', {'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': 'gm'}]}], 'usage': {'input_tokens': 3, 'output_tokens': 1}}) == ('gm', 3, 1)
    assert gm.extract('generateContent', {'candidates': [{'content': {'parts': [{'text': 'gm'}]}}],
                      'usageMetadata': {'promptTokenCount': 3, 'candidatesTokenCount': 1}}) == ('gm', 3, 1)


def test_spend_guard_before_key():
    r = gm.chat('gpt-5.5-pro', prompt='gm', max_tokens=100_000, raw=RAW)
    assert r['needs_confirm'] and r['worst_case_usd'] > gm.SPEND_USD


def test_chat_without_key_is_401():
    with pytest.raises(gm.GMError) as e:
        gm.chat('glm-5.3-flash-tee', prompt='gm', max_tokens=16, raw=RAW)
    assert e.value.status == 401


def test_chat_sends_right_auth(monkeypatch):
    seen = {}

    def fake(method, url, body=None, headers=None, timeout=120):
        seen.update(url=url, headers=headers, body=body)
        return {'content': [{'type': 'text', 'text': 'gm'}], 'usage': {'input_tokens': 2, 'output_tokens': 1}}
    monkeypatch.setattr(gm, '_http', fake)
    r = gm.chat('claude-sonnet-5', prompt='gm', max_tokens=16, key='gm-test-key', raw=RAW)
    assert r['text'] == 'gm' and r['usd'] > 0
    assert seen['url'].endswith('/v1/messages') and seen['headers']['x-api-key'] == 'gm-test-key'


def test_key_file_is_private():
    gm.save_key('gm-abcdefghijklmnop')
    assert oct(os.stat(gm.KEY_FILE).st_mode & 0o777) == '0o600'
    assert gm.load_key() == 'gm-abcdefghijklmnop'
    assert 'abcdefghijkl' not in gm.mask(gm.load_key())
    gm.save_key('')
    assert gm.load_key() is None
