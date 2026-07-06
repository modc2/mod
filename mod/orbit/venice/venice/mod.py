import json
import time
import os
import random
import requests
import openai
import mod as m


class Mod:
    description = """
    Venice AI — OpenAI-compatible LLM interface with uncensored models.
    Supports chat completions, streaming, model listing, and key management.
    """

    fns = [
        'forward', 'history', 'resolve_model',
        'api_key', 'keys', 'add_key', 'set_keys', 'set_key', 'rm_key',
        'model2info', 'models', 'model_infos', 'model_info', 'filter_models',
        'pricing', 'test', 'env',
    ]

    api_path = 'apikeys'
    env_varname = 'VENICE_API_KEY'

    @classmethod
    def create(cls, copy_from=None, **kwargs):
        if copy_from is not None:
            if hasattr(copy_from, '__self__'):
                copy_from = copy_from.__self__
            base_settings = {
                'api_key': getattr(copy_from, 'api_key', lambda: None)() if callable(getattr(copy_from, 'api_key', None)) else None,
                'url': getattr(copy_from, 'url', None),
                'model': getattr(copy_from, 'model', None),
                'prompt': getattr(copy_from, 'prompt', None),
            }
            base_settings = {k: v for k, v in base_settings.items() if v is not None}
            base_settings.update(kwargs)
            return cls(**base_settings)
        return cls(**kwargs)

    def __init__(
        self,
        api_key=None,
        url='https://api.venice.ai/api/v1',
        timeout=None,
        prompt=None,
        model='zai-org-glm-5',
        max_retries=10,
        path='~/.mod/model/venice',
        key=None,
        **kwargs
    ):
        # load API key from env var or store
        self.store = m.mod('store')(path)
        self.url = url
        self.model = model
        self.prompt = prompt
        self._api_key = api_key
        self._client_kwargs = dict(timeout=timeout, max_retries=max_retries)
        self._client = None


    @property
    def client(self):
        if self._client is None:
            key = self.api_key(self._api_key)
            if key is None:
                raise ValueError(f"No Venice API key found. Set {self.env_varname}, pass api_key=, or call add_key() first.")
            self._client = openai.OpenAI(
                base_url=self.url,
                api_key=key,
                **self._client_kwargs,
            )
        return self._client

    def forward(
        self,
        message: str,
        *extra_text,
        history=None,
        prompt: str = None,
        system_prompt: str = None,
        stream: bool = False,
        model: str = None,
        max_tokens: int = 10000000,
        text_only=True,
        temperature: float = 1.0,
        **kwargs
    ) -> str:
        model = model or self.model
        prompt = prompt or system_prompt
        if len(extra_text) > 0:
            message = message + ' '.join(extra_text)
        history = history or []
        prompt = prompt or self.prompt
        message = message + prompt if prompt else message
        model = self.resolve_model(model)
        model_info = self.model_info(model)
        num_tokens = len(message) // 4
        print(f'Sending ~{num_tokens} tokens -> {model}')
        max_tokens = min(max_tokens, max(1024, model_info.get('context_length', 128000) - num_tokens))
        messages = history.copy()
        messages.append({"role": "user", "content": message})
        result = self.client.chat.completions.create(
            model=model,
            messages=messages,
            stream=bool(stream),
            max_tokens=max_tokens,
            temperature=temperature,
        )

        item = {
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
        else:
            item['result'] = result.choices[0].message.content
            self.store.put(path, item)
            return item['result']

    generate = forward

    def history(self, path=None, update=False):
        return self.store.items('history', update=update)

    def resolve_model(self, model=None):
        model = model or self.model
        models = self.models()
        model = str(model)
        if model not in models:
            if ',' in model:
                matches = [m for m in models if any(s in m for s in model.split(','))]
            else:
                matches = [m for m in models if model in m]
            if len(matches) == 0:
                # retry with fresh model list from API
                models = self.models(update=True)
                if model in models:
                    return model
                if ',' in model:
                    matches = [m for m in models if any(s in m for s in model.split(','))]
                else:
                    matches = [m for m in models if model in m]
                if len(matches) == 0:
                    raise ValueError(f"No model matching '{model}' found on Venice. Available: {', '.join(models[:10])}...")
            print(f"Model {model} not found. Using {matches[0]} instead.")
            model = matches[0]
        return model

    def api_key(self, api_key=None):
        if api_key is None:
            if self.env_varname in os.environ:
                return os.environ[self.env_varname]
            keys = self.store.get(self.api_path, [])
            if keys:
                return random.choice(keys)
            # raise ValueError(f"No Venice API key found. Set {self.env_varname} or call add_key().")
        else:
            if api_key in os.environ or api_key.startswith('$'):
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

    def model2info(self, search=None, path='models', update=False):
        models = self.store.get(path, default={}, update=update)
        if len(models) == 0:
            print('Updating models from Venice...')
            response = requests.get(self.url + '/models')
            models = json.loads(response.text)['data']
            self.store.put(path, models)
        models = self.filter_models(models, search=search)
        return {m['id']: m for m in models}

    def models(self, search=None, path='models', update=False):
        return list(self.model2info(search=search, path=path, update=update).keys())

    def model_infos(self, search=None, path='models', update=False):
        return list(self.model2info(search=search, path=path, update=update).values())

    def model_info(self, model):
        model = self.resolve_model(model)
        model2info = self.model2info()
        return model2info[model]

    @classmethod
    def filter_models(cls, models, search=None):
        if search is None:
            return models
        if isinstance(models[0], str):
            models = [{'id': m} for m in models]
        if ',' in search:
            search = [s.strip() for s in search.split(',')]
        else:
            search = [search]
        return [m for m in models if any(s in m['id'] for s in search)]

    def pricing(self, search=None, ascending=False, sortby='completion', df=True, **kwargs):
        pricing = [{'name': k, **v.get('pricing', {})} for k, v in self.model2info(search=search, **kwargs).items()]
        if df:
            return m.df(pricing).sort_values(sortby, ascending=ascending)
        return pricing

    def test(self):
        response = self.forward('Say hello in one word.', stream=False)
        print(response)
        assert isinstance(response, str)
        print('Test passed')
        stream_response = self.forward('Say hi in one word.', stream=True)
        print(next(stream_response))
        return {'status': 'success'}

    def env(self):
        return {self.env_varname: self.api_key()}
