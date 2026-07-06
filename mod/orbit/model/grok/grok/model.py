from typing import Generator
import requests
import json
import openai
import time
import os
import mod as m
import random


class Grok:
    """
    Grok model client patterned after OpenRouter, but routed through one of
    three OpenAI-compatible inference providers: chutes, targon, or venice.

    The provider is selected at construction time (or per-call via `provider=`)
    and determines the base URL, API key store, and env var that supplies a key.

    Usage:
        Grok()                          # default provider = chutes
        Grok(provider='targon')
        Grok(provider='venice', model='grok-3')
        m.fn('model/grok/forward')('hello', provider='venice')
    """

    provider = 'grok'

    # Per-sub-provider config. Add more by extending this dict.
    PROVIDERS = {
        'chutes': {
            'url': 'https://llm.chutes.ai/v1',
            'env_varname': 'CHUTES_API_KEY',
            'api_path': 'apikeys/chutes',
            'default_model': 'xai/grok-2-1212',
            'key_prefix': 'cpk_',
        },
        'targon': {
            'url': 'https://api.targon.com/v1',
            'env_varname': 'TARGON_API_KEY',
            'api_path': 'apikeys/targon',
            'default_model': 'x-ai/grok-2',
            'key_prefix': 'sn4_',
        },
        'venice': {
            'url': 'https://api.venice.ai/api/v1',
            'env_varname': 'VENICE_API_KEY',
            'api_path': 'apikeys/venice',
            'default_model': 'grok-3',
            'key_prefix': '',  # venice keys are opaque
        },
    }

    @classmethod
    def providers(cls):
        return list(cls.PROVIDERS.keys())

    def __init__(
        self,
        provider: str = 'chutes',
        api_key=None,
        url: str = None,
        timeout: float = None,
        prompt: str = None,
        model: str = None,
        max_retries: int = 10,
        path: str = '~/.mod/model/grok',
        **kwargs,
    ):
        if provider not in self.PROVIDERS:
            raise ValueError(
                f"Unknown grok provider '{provider}'. Choose from {self.providers()}."
            )
        self.provider_name = provider
        cfg = self.PROVIDERS[provider]
        self.env_varname = cfg['env_varname']
        self.api_path = cfg['api_path']
        self.key_prefix = cfg['key_prefix']

        self.store = m.mod('store')(f"{path}/{provider}")
        self.url = url or cfg['url']
        # Lazy key resolution: construction succeeds even without a key so the
        # caller can list providers / models / add_key without authenticating.
        try:
            key = self.api_key(api_key)
        except ValueError:
            key = 'placeholder-no-key'
        self.client = openai.OpenAI(
            base_url=self.url,
            api_key=key,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.model = model or cfg['default_model']
        self.prompt = prompt

    # ── Provider switching ────────────────────────────────────────────

    def use(self, provider: str, **kwargs):
        """Return a new Grok instance bound to a different sub-provider."""
        return Grok(provider=provider, **kwargs)

    # ── Core generation ───────────────────────────────────────────────

    def forward(
        self,
        message: str,
        *extra_text,
        history=None,
        prompt: str = None,
        system_prompt: str = None,
        stream: bool = False,
        model: str = None,
        provider: str = None,
        max_tokens: int = 10000000,
        temperature: float = 1.0,
        **kwargs,
    ):
        # Late-bound provider override
        if provider is not None and provider != self.provider_name:
            return self.use(provider).forward(
                message, *extra_text, history=history, prompt=prompt,
                system_prompt=system_prompt, stream=stream, model=model,
                max_tokens=max_tokens, temperature=temperature, **kwargs,
            )

        model = model or self.model
        prompt = prompt or system_prompt or self.prompt
        if extra_text:
            message = message + ' '.join(extra_text)
        if prompt:
            message = message + prompt
        history = history or []
        model = self.resolve_model(model)

        # Per-model context length; fall back to a conservative default if the
        # provider doesn't expose it.
        try:
            info = self.model_info(model)
            ctx = int(info.get('context_length') or info.get('max_model_len') or 32768)
        except Exception:
            ctx = 32768

        num_tokens = len(message) // 4
        print(f"[grok:{self.provider_name}] sending ~{num_tokens} tokens -> {model}")
        max_tokens = min(max_tokens, max(1024, ctx - num_tokens))

        messages = history.copy()
        messages.append({"role": "user", "content": message})
        result = self.client.chat.completions.create(
            model=model, messages=messages, stream=bool(stream),
            max_tokens=max_tokens, temperature=temperature,
        )

        item = {
            'provider': self.provider_name,
            'model': model,
            'params': {
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': temperature,
            },
            'time': time.time(),
        }
        item['hash'] = m.hash(item)
        item['result'] = ''
        path = f"history/{item['hash']}"

        if stream:
            def stream_generator(result):
                for token in result:
                    if len(token.choices) > 0:
                        content = token.choices[0].delta.content
                        if content is not None:
                            item['result'] += content
                            yield content
                self.store.put(path, item)
            return stream_generator(result)

        item['result'] = result.choices[0].message.content
        try:
            item['cost'] = getattr(result.usage, 'cost', None)
        except Exception:
            item['cost'] = None
        self.store.put(path, item)
        return item['result']

    generate = forward

    # ── History ───────────────────────────────────────────────────────

    def history(self, path: str = None, update: bool = False):
        return self.store.items('history', update=update)

    # ── Model discovery ───────────────────────────────────────────────

    def resolve_model(self, model=None):
        model = str(model or self.model)
        models = self.models()
        if model in models:
            return model
        # substring or comma-list fallback
        if ',' in model:
            terms = [s.strip() for s in model.split(',')]
            cand = [x for x in models if any(t in x for t in terms)]
        else:
            cand = [x for x in models if model in x]
        if not cand:
            # If the provider didn't list it, send it through anyway — they may
            # accept arbitrary IDs (Targon does this for unhosted Bittensor models).
            print(f"[grok:{self.provider_name}] {model} not in /models; passing through")
            return model
        print(f"[grok:{self.provider_name}] {model} not exact; using {cand[0]}")
        return cand[0]

    def model2info(self, search: str = None, path='models', update=False):
        models = self.store.get(path, default={}, update=update)
        if not models:
            print(f"[grok:{self.provider_name}] fetching /models")
            headers = {"Authorization": f"Bearer {self.api_key()}"}
            try:
                resp = requests.get(self.url + '/models', headers=headers, timeout=15)
                models = resp.json().get('data', [])
            except Exception as e:
                print(f"[grok:{self.provider_name}] /models fetch failed: {e}")
                models = []
            self.store.put(path, models)
        models = self.filter_models(models, search=search)
        return {x['id']: x for x in models if isinstance(x, dict) and 'id' in x}

    def models(self, search: str = None, path='models', update=False):
        return list(self.model2info(search=search, path=path, update=update).keys())

    def model_infos(self, search: str = None, path='models', update=False):
        return list(self.model2info(search=search, path=path, update=update).values())

    def model_info(self, model):
        model = self.resolve_model(model)
        return self.model2info().get(model, {'id': model, 'context_length': 32768})

    @classmethod
    def filter_models(cls, models, search: str = None):
        if search is None:
            return models
        if models and isinstance(models[0], str):
            models = [{'id': x} for x in models]
        terms = [s.strip() for s in search.split(',')] if ',' in search else [search]
        return [x for x in models if any(t in x.get('id', '') for t in terms)]

    def grok_models(self, update: bool = False):
        """All models matching 'grok' / 'xai' across the current provider."""
        all_models = self.models(update=update)
        return [x for x in all_models if 'grok' in x.lower() or 'xai' in x.lower() or 'x-ai' in x.lower()]

    # ── API key management ───────────────────────────────────────────

    def api_key(self, api_key: str = None):
        if api_key is None:
            keys = self.store.get(self.api_path, [])
            if not keys:
                env = os.environ.get(self.env_varname)
                if env:
                    return env
                raise ValueError(
                    f"No API key for grok/{self.provider_name}. "
                    f"Set {self.env_varname} or call add_key()."
                )
            return random.choice(keys)
        if api_key.startswith('$') or api_key in os.environ:
            return os.environ[self.env_varname]
        return api_key

    def keys(self):
        return self.store.get(self.api_path, [])

    def add_key(self, key):
        keys = self.store.get(self.api_path, [])
        keys.append(key)
        keys = list(set(keys))
        self.store.put(self.api_path, keys)
        return keys

    def set_keys(self, *keys):
        keys = list(set(keys))
        self.store.put(self.api_path, keys)
        return keys

    def set_key(self, key):
        return self.set_keys(key)

    def rm_key(self, key):
        keys = self.store.get(self.api_path, [])
        keys = [k for k in keys if k != key]
        self.store.put(self.api_path, keys)
        return keys

    # ── Misc ─────────────────────────────────────────────────────────

    def pricing(self, search: str = None, **kwargs):
        rows = []
        for k, v in self.model2info(search=search, **kwargs).items():
            row = {'name': k}
            row.update(v.get('pricing', {}) or {})
            rows.append(row)
        return rows

    def get_token_count(self, text: str):
        return len(text.split()) // 0.75

    def env(self):
        return {self.env_varname: self.api_key()}

    def test(self, provider: str = None):
        target = self if provider is None else self.use(provider)
        msg = f"Reply in one word: hello from {target.provider_name}"
        out = target.forward(msg, stream=False, max_tokens=64)
        assert isinstance(out, str) and len(out) > 0
        return {'provider': target.provider_name, 'model': target.model, 'response': out}

    def test_all(self):
        """Smoke-test every configured provider in order."""
        results = {}
        for p in self.providers():
            try:
                results[p] = self.test(provider=p)
            except Exception as e:
                results[p] = {'error': str(e)}
        return results
