"""
Tests for Venice AI module.

Run:
    python -m pytest tests/test_venice.py -v
    python tests/test_venice.py          # standalone
"""

import pytest
import os
import sys

# Ensure venice package is importable from the module directory
_venice_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _venice_root not in sys.path:
    sys.path.insert(0, _venice_root)


@pytest.fixture(scope="module")
def client():
    from venice.mod import Mod
    return Mod()


# ── Model listing ──────────────────────────────────────────────────────

class TestModels:

    def test_models_returns_list(self, client):
        models = client.models()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_models_search_filters(self, client):
        models = client.models(search='llama')
        assert all('llama' in m for m in models)

    def test_model2info_returns_dict(self, client):
        info = client.model2info()
        assert isinstance(info, dict)
        assert len(info) > 0
        first = next(iter(info.values()))
        assert 'id' in first

    def test_model_info_single(self, client):
        info = client.model_info(client.model)
        assert 'id' in info

    def test_model_infos_returns_list(self, client):
        infos = client.model_infos()
        assert isinstance(infos, list)
        assert len(infos) > 0

    def test_resolve_model_exact(self, client):
        resolved = client.resolve_model(client.model)
        assert resolved == client.model

    def test_resolve_model_partial(self, client):
        resolved = client.resolve_model('deepseek')
        assert 'deepseek' in resolved


# ── Filter models ──────────────────────────────────────────────────────

class TestFilterModels:

    def test_filter_none_returns_all(self):
        from venice.mod import Mod
        models = [{'id': 'a'}, {'id': 'b'}]
        result = Mod.filter_models(models, search=None)
        assert len(result) == 2

    def test_filter_single(self):
        from venice.mod import Mod
        models = [{'id': 'deepseek-r1-671b'}, {'id': 'llama-3.3-70b'}]
        result = Mod.filter_models(models, search='deepseek')
        assert len(result) == 1
        assert result[0]['id'] == 'deepseek-r1-671b'

    def test_filter_comma_separated(self):
        from venice.mod import Mod
        models = [
            {'id': 'deepseek-r1-671b'},
            {'id': 'llama-3.3-70b'},
            {'id': 'qwen-2.5-coder'},
        ]
        result = Mod.filter_models(models, search='deepseek,llama')
        assert len(result) == 2

    def test_filter_string_list(self):
        from venice.mod import Mod
        models = ['deepseek-r1-671b', 'llama-3.3-70b']
        result = Mod.filter_models(models, search='deepseek')
        assert len(result) == 1


# ── Key management ─────────────────────────────────────────────────────

class TestKeys:

    def test_keys_returns_list(self, client):
        keys = client.keys()
        assert isinstance(keys, list)

    def test_api_key_returns_string(self, client):
        key = client.api_key()
        assert isinstance(key, str)
        assert len(key) > 0


# ── Generation (live API calls) ────────────────────────────────────────

class TestGeneration:

    def test_forward_basic(self, client):
        response = client.forward('Say "hello" and nothing else.', temperature=0)
        assert isinstance(response, str)
        assert len(response) > 0

    def test_forward_stream(self, client):
        gen = client.forward('Say "hi"', stream=True, temperature=0)
        tokens = []
        for token in gen:
            if token:
                tokens.append(token)
        assert len(tokens) > 0

    def test_forward_with_system_prompt(self, client):
        response = client.forward(
            'What are you?',
            system_prompt='You are a pirate. Respond in one sentence.',
            temperature=0,
        )
        assert isinstance(response, str)
        assert len(response) > 0

    def test_forward_with_history(self, client):
        history = [
            {"role": "user", "content": "My name is TestBot."},
            {"role": "assistant", "content": "Hello TestBot!"},
        ]
        response = client.forward(
            'What is my name? Reply with just the name.',
            history=history,
            temperature=0,
        )
        assert isinstance(response, str)

    def test_forward_extra_text(self, client):
        response = client.forward('Say', ' hello', temperature=0)
        assert isinstance(response, str)


# ── History ────────────────────────────────────────────────────────────

class TestHistory:

    def test_history_returns_list(self, client):
        try:
            history = client.history()
            assert isinstance(history, (list, dict))
        except TypeError:
            history = client.store.items('history')
            assert isinstance(history, (list, dict))


# ── Misc ───────────────────────────────────────────────────────────────

class TestMisc:

    def test_env(self, client):
        env = client.env()
        assert 'VENICE_API_KEY' in env
        assert isinstance(env['VENICE_API_KEY'], str)


# ── Standalone runner ──────────────────────────────────────────────────

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
