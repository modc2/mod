from typing import Generator
import requests
import json
import openai
import time
import os
import mod as c
import random

class OpenRouter:

    api_path = 'apikeys' # path to store api keys (relative to storage_path)
    env_varname = 'PRIMEINT_API_KEY' # environment variable name for api key

    def __init__(
        self,
        api_key = None,
        path = '~/.mod/.primeint/openrouter',
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
        self.store = c.mod('store')(path)

        self.add_key(api_key)


    def api_key(self, api_key: str = None):
        """
        get the api keys
        """
        api_key = api_key or self.env_varname
        # first check environment variable
        if self.env_varname in os.environ:
            return os.environ[self.env_varname]
        keys = self.store.get(self.api_path, [])
        if len(keys) > 0:
            return random.choice(keys)
        else:
            return ''
            print(f"No API key found in store.")

    def keys(self):
        """
        Get the list of API keys
        """
        keys =  self.store.get(self.api_path, [])
        keys = filter(lambda k: k is not None, keys)
        return list(keys)


    def add_key(self, key):
        if key is None:
            return self.keys()
        keys = self.store.get(self.api_path, [])
        keys.append(key)
        keys = list(set(keys))
        self.store.put(self.api_path, keys)
        return keys

    def rm_key(self, key):
        keys = self.store.get(self.api_path, [])
        keys = [k for k in keys if k != key]
        self.store.put(self.api_path, keys)
        return keys

    def rm_keys(self):
        self.store.put(self.api_path, [])
        return []
    def env(self): 
        return {
            'OPENROUTER_API_KEY': self.api_key()
        }


