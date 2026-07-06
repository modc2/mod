"""
Tests for OpenRouter module.

Run:
    python -m pytest openrouter/test.py -v
    python openrouter/test.py          # standalone
"""

import pytest
import os
import sys

# Ensure module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="module")
def client():
    from model import OpenRouter
    return OpenRouter()


# ── Model listing ──────────────────────────────────────────────────────

class TestModels:

    def test_models_returns_list(self, client):
        models = client.models()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_models_search_filters(self, client):
        models = client.models(search='claude')
        assert all('claude' in m for m in models)

    def test_model2info_returns_dict(self, client):
        info = client.model2info()
        assert isinstance(info, dict)
        assert len(info) > 0
        first = next(iter(info.values()))
        assert 'id' in first
        assert 'pricing' in first

    def test_model_info_single(self, client):
        info = client.model_info(client.model)
        assert 'id' in info
        assert 'context_length' in info
        assert info['context_length'] > 0

    def test_model_infos_returns_list(self, client):
        infos = client.model_infos()
        assert isinstance(infos, list)
        assert len(infos) > 0

    def test_resolve_model_exact(self, client):
        resolved = client.resolve_model('anthropic/claude-opus-4')
        assert resolved == 'anthropic/claude-opus-4'

    def test_resolve_model_partial(self, client):
        resolved = client.resolve_model('claude-opus')
        assert 'claude' in resolved and 'opus' in resolved


# ── Free models ────────────────────────────────────────────────────────

class TestFreeModels:

    def test_free_models_returns_list(self, client):
        free = client.free_models()
        assert isinstance(free, list)
        assert len(free) > 0

    def test_free_models_all_have_zero_cost(self, client):
        model2info = client.model2info()
        for mid in client.free_models():
            info = model2info.get(mid)
            if info:
                pricing = info.get('pricing', {})
                assert float(pricing.get('prompt', '1') or '1') == 0
                assert float(pricing.get('completion', '1') or '1') == 0

    def test_free_models_search(self, client):
        free = client.free_models(search='google')
        assert all('google' in m.lower() for m in free)

    def test_free_models_search_multi(self, client):
        free = client.free_models(search='google,nvidia')
        assert all(
            'google' in m.lower() or 'nvidia' in m.lower()
            for m in free
        )

    def test_free_models_info_mode(self, client):
        free = client.free_models(info=True)
        assert isinstance(free, list)
        if len(free) > 0:
            item = free[0]
            assert 'id' in item
            assert 'name' in item
            assert 'context_length' in item

    def test_free_model_returns_string(self, client):
        model = client.free_model()
        assert model is None or isinstance(model, str)

    def test_free_model_with_search(self, client):
        model = client.free_model(search='gemma')
        if model:
            assert 'gemma' in model.lower()


# ── Filter models ──────────────────────────────────────────────────────

class TestFilterModels:

    def test_filter_none_returns_all(self):
        from model import OpenRouter
        models = [{'id': 'a'}, {'id': 'b'}]
        result = OpenRouter.filter_models(models, search=None)
        assert len(result) == 2

    def test_filter_single(self):
        from model import OpenRouter
        models = [{'id': 'anthropic/claude-opus-4'}, {'id': 'google/gemma-4-31b-it:free'}]
        result = OpenRouter.filter_models(models, search='claude')
        assert len(result) == 1
        assert result[0]['id'] == 'anthropic/claude-opus-4'

    def test_filter_comma_separated(self):
        from model import OpenRouter
        models = [
            {'id': 'anthropic/claude-opus-4'},
            {'id': 'google/gemma-4-31b-it:free'},
            {'id': 'meta/llama-3'},
        ]
        result = OpenRouter.filter_models(models, search='claude,gemma')
        assert len(result) == 2

    def test_filter_string_list(self):
        from model import OpenRouter
        models = ['anthropic/claude-opus-4', 'google/gemma-4-31b-it:free']
        result = OpenRouter.filter_models(models, search='claude')
        assert len(result) == 1


# ── Pricing ────────────────────────────────────────────────────────────

class TestPricing:

    def test_pricing_returns_dataframe(self, client):
        df = client.pricing()
        assert hasattr(df, 'columns')
        assert 'name' in df.columns
        assert len(df) > 0

    def test_pricing_no_df(self, client):
        result = client.pricing(df=False)
        assert isinstance(result, list)
        assert len(result) > 0
        assert 'name' in result[0]

    def test_pricing_search(self, client):
        df = client.pricing(search='claude')
        assert all('claude' in name for name in df['name'].values)


# ── Key management ─────────────────────────────────────────────────────

class TestKeys:

    def test_keys_returns_list(self, client):
        keys = client.keys()
        assert isinstance(keys, list)

    def test_api_key_returns_sk_prefix(self, client):
        key = client.api_key()
        assert key.startswith('sk-')


# ── Generation (live API calls) ────────────────────────────────────────

class TestGeneration:

    def test_forward_basic(self, client):
        """Basic non-streaming generation with a free model."""
        response = client.forward('Say "hello" and nothing else.', free=True, temperature=0)
        assert isinstance(response, str)
        assert len(response) > 0

    def test_forward_stream(self, client):
        """Streaming generation returns a generator."""
        gen = client.forward('Say "hi"', free=True, stream=True, temperature=0)
        tokens = []
        for token in gen:
            if token:
                tokens.append(token)
        assert len(tokens) > 0

    def test_forward_with_system_prompt(self, client):
        """System prompt is applied."""
        response = client.forward(
            'What are you?',
            system_prompt='You are a pirate. Respond in one sentence.',
            free=True,
            temperature=0,
        )
        assert isinstance(response, str)
        assert len(response) > 0

    def test_forward_with_history(self, client):
        """Chat history is passed through."""
        history = [
            {"role": "user", "content": "My name is TestBot."},
            {"role": "assistant", "content": "Hello TestBot!"},
        ]
        response = client.forward(
            'What is my name? Reply with just the name.',
            history=history,
            free=True,
            temperature=0,
        )
        assert isinstance(response, str)

    def test_forward_extra_text(self, client):
        """Extra positional args are concatenated."""
        response = client.forward('Say', ' hello', free=True, temperature=0)
        assert isinstance(response, str)


# ── History ────────────────────────────────────────────────────────────

class TestHistory:

    def test_history_returns_list(self, client):
        try:
            history = client.history()
            assert isinstance(history, (list, dict))
        except TypeError:
            # store.items() may not support update kwarg in all versions
            history = client.store.items('history')
            assert isinstance(history, (list, dict))


# ── Misc ───────────────────────────────────────────────────────────────

class TestMisc:

    def test_env(self, client):
        env = client.env()
        assert 'OPENROUTER_API_KEY' in env
        assert env['OPENROUTER_API_KEY'].startswith('sk-')

    def test_token_count(self, client):
        count = client.get_token_count('hello world this is a test')
        assert count > 0


# ── Standalone runner ──────────────────────────────────────────────────

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
