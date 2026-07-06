from typing import Generator
import requests
import json
import openai
import time
import os
import mod as m
import random

class OpenRouter:

    api_path = 'apikeys' # path to store api keys (relative to storage_path)
    env_varname = 'OPENROUTER_API_KEY' # environment variable name for api key
    provider = 'openrouter'

    @classmethod
    def create(cls, copy_from=None, **kwargs):
        """
        Create a new OpenRouter instance, optionally copying settings from another mod/instance.

        Args:
            copy_from: Another OpenRouter instance or mod to copy settings from
            **kwargs: Override any settings from the copied instance

        Returns:
            OpenRouter: A new OpenRouter instance
        """
        if copy_from is not None:
            # If copy_from is a mod, try to access its instance
            if hasattr(copy_from, '__self__'):
                copy_from = copy_from.__self__

            # Extract settings from the source instance
            base_settings = {
                'api_key': getattr(copy_from, 'api_key', lambda: None)() if callable(getattr(copy_from, 'api_key', None)) else None,
                'url': getattr(copy_from, 'url', None),
                'timeout': getattr(copy_from, 'client', None) and getattr(copy_from.client, 'timeout', None),
                'prompt': getattr(copy_from, 'prompt', None),
                'model': getattr(copy_from, 'model', None),
                'max_retries': getattr(copy_from, 'client', None) and getattr(copy_from.client, 'max_retries', None),
                'path': getattr(copy_from, 'store', None) and getattr(copy_from.store, 'path', None),
            }
            # Filter out None values
            base_settings = {k: v for k, v in base_settings.items() if v is not None}
            # Merge with kwargs, giving priority to kwargs
            base_settings.update(kwargs)
            return cls(**base_settings)
        else:
            return cls(**kwargs)

    def __init__(
        self,
        api_key = None,
        url: str = 'https://openrouter.ai/api/v1',
        timeout: float = None,
        prompt:str=None,
        model = 'anthropic/claude-opus-4',
        max_retries: int = 10,
        path = '~/.mod/model/openrouter',
        key = None,
        **kwargs
    ):
        """
        Initialize the OpenAI with the specified model, API key, timeout, and max retries. fam

        Args:
            model (OPENAI_MODES): The OpenAI model to use.
            api_key (API_KEY): The API key for authentication.
            url (str, optional): can be used for openrouter api calls
            timeout (float, optional): The timeout value for the client. Defaults to None.
            model (str, optional): The model to use. Defaults to 'gpt-3.5-turbo'.
            prompt (str, optional): The system prompt to use. Defaults to None.
            path (str, optional): The path to store the API keys and history. Defaults to
            max_retries (int, optional): The maximum number of retries for the client. Defaults to None.
        """
        self.store = m.mod('store')(path)
        self.url = url
        self.client = openai.OpenAI(
            base_url=self.url,
            api_key=self.api_key(api_key),
            timeout=timeout,
            max_retries=max_retries,
        )
        self.model = model
        self.prompt = prompt

    def forward(
        self,
        message: str,
        *extra_text , 
        history = None,
        prompt: str =  None,
        system_prompt: str = None,
        stream: bool = False,
        model:str = None,
        free = False,
        max_tokens: int = 10000000,
        text_only = True ,
        temperature: float = 1.0,
        **kwargs
    ) -> str :
        """
        Generates a response using the OpenAI language model.

        Args:
            message (str): The message to send to the language model.
            history (ChatHistory): The conversation history.
            stream (bool): Whether to stream the response or not.
            max_tokens (int): The maximum number of tokens to generate.
            temperature (float): The sampling temperature to use.

        Returns:
        Generator[str] | str: A generator for streaming responses or the full streamed response.
        """
        if free:
            free_list = self.free_models()
            if not free_list:
                raise ValueError("No free models available on OpenRouter. Try update=True or check your connection.")
            model = free_list[0]
        model = model or self.model
        prompt = prompt or system_prompt
        if len(extra_text) > 0:
            message = message + ' '.join(extra_text)
        history = history or []
        prompt = prompt or self.prompt
        message = message + prompt if prompt else message
        model = self.resolve_model(model)
        model_info = self.model_info(model)
        num_tokens = len(message) // 4  # rough char-to-token estimate
        print(f'Sending ~{num_tokens} tokens -> {model}')
        max_tokens = min(max_tokens, max(1024, model_info['context_length'] - num_tokens))
        messages = history.copy()
        messages.append({"role": "user", "content": message})
        result = self.client.chat.completions.create(model=model, messages=messages, stream= bool(stream), max_tokens = max_tokens, temperature= temperature  )

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
            item['cost'] = result.usage.cost 
            self.store.put(path, item)
            return item['result']
        
    generate = forward

    def history(self, path:str = None, update:bool = False):
        """
        Get the history of the last requests
        """
        history = self.store.items('history', update=update)
        return history

    def resolve_model(self, model=None):
        model = model or self.model
        models =  self.models()
        model = str(model)
        if str(model) not in models:
            if ',' in model:
                models = [m for m in models if any([s in m for s in model.split(',')])]
            else:
                models = [m for m in models if str(model) in m]
            print(f"Model {model} not found. Using {models} instead.")
            assert len(models) > 0
            model = models[0]

        return model

    def api_key(self, api_key: str = None):
        """
        get the api keys
        """
        if api_key is  None:
            api_key =  random.choice(self.store.get(self.api_path, []))
        else:
            if api_key in os.environ or api_key.startswith('$'):
                api_key =  os.environ[self.env_varname]
        assert api_key.startswith('sk-'), 'Invalid API key'
        return api_key

    def keys(self):
        """
        Get the list of API keys
        """
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
    
    # def set_env_var(self, key = None):
    #     os.environ[self.env_varname] = key or self.api_key()
    #     cmd = f"echo 'export {self.env_varname}="{os.environ[self.env_varname]}"' >> ~/.zshrc"
    #     return os.system(cmd)
        
    #     import subprocess
    #     subprocess.run(cmd, shell=True)
    #     subprocess.run("source ~/.zshrc", shell=True)
    #     subprocess.run("echo 'Environment variable set. Please restart your terminal.'", shell=True)
    #     return os.environ[self.env_varname]

    def rm_key(self, key):
        keys = self.store.get(self.api_path, [])
        keys = [k for k in keys if k != key]
        self.store.put(self.api_path, keys)
        return keys

    def model2info(self, search: str = None, path='models',  update=False):
        models = self.store.get(path, default={},  update=update)
        if len(models) == 0:
            print('Updating models...')
            response = requests.get(self.url + '/models')
            models = json.loads(response.text)['data']
            self.store.put(path, models)
        models = self.filter_models(models, search=search)
        return {m['id']:m for m in models}
    
    def models(self, search: str = None, path='models', update=False):
        return list(self.model2info(search=search, path=path, update=update).keys())

    def free_models(self, search: str = None, update=False, info=False):
        """
        Get all free models (prompt + completion cost = 0).

        Args:
            search: Filter by substring in model id (e.g. 'google', 'claude')
            update: Force refresh from OpenRouter API
            info: Return full model info dicts instead of just ids

        Returns:
            list[str] | list[dict]: Model ids or full info dicts
        """
        model2info = self.model2info(path='models', update=update)
        free = []
        for mid, minfo in model2info.items():
            pricing = minfo.get('pricing', {})
            prompt_cost = float(pricing.get('prompt', '1') or '1')
            completion_cost = float(pricing.get('completion', '1') or '1')
            if prompt_cost == 0 and completion_cost == 0:
                free.append(minfo)
        if search:
            terms = [s.strip().lower() for s in search.split(',')]
            free = [f for f in free if any(t in f['id'].lower() for t in terms)]
        if info:
            return [{
                'id': f['id'],
                'name': f.get('name', f['id']),
                'context_length': f.get('context_length', 0),
                'architecture': f.get('architecture', {}).get('modality', ''),
            } for f in free]
        return [f['id'] for f in free]

    def free_model(self, search: str = None):
        """Pick a random free model, optionally filtered by search."""
        models = self.free_models(search=search)
        return m.choice(models) if models else None
    
    def model_infos(self, search: str = None, path='models',  update=False):
        return list(self.model2info(search=search, path=path, update=update).values())
    
    def model_info(self, model):
        model = self.resolve_model(model)
        model2info = self.model2info()
        return model2info[model]
    
    @classmethod
    def filter_models(cls, models, search:str = None):
        if search == None:
            return models
        if isinstance(models[0], str):
            models = [{'id': m} for m in models]
        if ',' in search:
            search = [s.strip() for s in search.split(',')]
        else:
            search = [search]
        models = [m for m in models if any([s in m['id'] for s in search])]
        return [m for m in models]
    
    def pricing(self, search: str = None , ascending=False, sortby='completion', df=True,   **kwargs):
        pricing =  [{'name': k , **v['pricing']} for k,v in self.model2info(search=search, **kwargs).items()]
        if df:
            return m.df(pricing).sort_values(sortby, ascending=ascending)
        return pricing

    def get_token_count(self, text: str):
        # rough estimate of token count
        return len(text.split()) // 0.75

        
    def test(self):
        response  =  self.forward('Hello, how are you?', stream=False)
        print(response)
        assert isinstance(response, str)
        print('Test passed')
        stream_response = self.forward('Hello, how are you?', stream=True)
        print(next(stream_response))
        return {'status': 'success'}

    def env(self): 
        return {
            'OPENROUTER_API_KEY': self.api_key()
        }


