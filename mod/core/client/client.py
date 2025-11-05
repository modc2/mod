
from typing import *
import asyncio
import json
import requests
import os
import mod as m

class Client:
    def __init__( self,  
                 url: Optional[str] = None,  # the url of the mod server
                 key : Optional[str]= None,  
                 timeout = 10,
                 auth = 'auth',
                 mode = 'http',
                 storage_path = '~/.mod/client',
                 fn = 'info',
                 **kwargs):
        self.url = url
        self.mode = mode
        self.auth = m.mod(auth)()
        self.key  = m.get_key(key)
        self.store = m.mod('store')(storage_path)
        self.timeout = timeout
        self.fn = fn
        self.namespace = m.namespace()
        # ensure info from the server is fetched

    def forward(self, 
                fn  = 'info', 
                params: Optional[Union[list, dict]] = {}, # if you want to pass params as a list or dict
                timeout:int=10,  # the timeout for the request
                key : str = None,  # the key to use for the request
                cost=0,
                stream: bool = True,
                **extra_kwargs 
    ):
        url = self.get_url(fn)
        key = self.get_key(key)
        fn = url.split('/')[-2]
        # step 3: get the params
        params = params or {}
        params.update(extra_kwargs)   
        headers = self.auth.forward({'fn': fn, 'params': params}, key=key, cost=cost)
        # step 4: make the request
        with requests.Session() as conn:
            response = conn.post( url, json=params,  headers=headers, timeout=timeout, stream=stream)
        return self.process_response(response)

    
    def process_response(self, response):
        # step 5: handle the response
        if response.status_code != 200:
            raise Exception(response.text)
        if 'text/event-stream' in response.headers.get('Content-Type', ''):
            print('Streaming response...')
            result = self.stream_generator(response)
        else:
            if 'application/json' in response.headers.get('Content-Type', ''):
                result = response.json()
            elif 'text/plain' in response.headers.get('Content-Type', ''):
                result = response.text
            else:
                result = response.content
                if response.status_code != 200:
                    raise Exception(result)
        return result
    def get_key(self,key=None):
        key = key or  self.key
        if isinstance(key, str):
            key = m.get_key(key)
        return key

    def get_url(self, fn='info'):
        """
        gets the url and makes sure its legit
        """
        if '/' in str(fn):
            url, fn = '/'.join(fn.split('/')[:-1]), fn.split('/')[-1]
            if len(fn) == 0:
                fn = self.fn
        else :
            if self.url is None:
                url = fn
                fn= self.fn
            else:
                url = self.url
        url = self.namespace.get(url, url)
        if not url.startswith(self.mode):
            url = f'{self.mode}://{url}'
        return url + '/' + fn + '/'

    call = forward

    def process_stream_line(self, line , stream_prefix = 'data: '):
        event_data = line.decode('utf-8')
        if event_data.startswith(stream_prefix):
            event_data = event_data[len(stream_prefix):] 
        if event_data == "": # skip empty lines if the event data is empty
            return ''
        if isinstance(event_data, str):
            if event_data.startswith('{') and event_data.endswith('}') and 'data' in event_data:
                event_data = json.loads(event_data)['data']
        return event_data

        
    def stream_generator(self, response):
        try:
            for chunk in response.iter_lines():
                yield self.process_stream_line(chunk)
        except Exception as e:
            yield m.detailed_error(e)

    def is_url(self,  url:str) -> bool:
        if not isinstance(url, str):
            return False
        if '://' in url:
            return True
        conds = []
        conds.append(isinstance(url, str))
        conds.append(':' in url)
        conds.append(m.is_int(url.split(':')[-1]))
        return all(conds)

    def client(self,  url:str = 'mod', key:str = None, virtual:bool = True,  **client_kwargs):
        """
        Create a client instance.
        """
        client =  Client(url, key=key, **client_kwargs)
        return self.virtual_client(client) if virtual else client

    def virtual_client(self, client = None):
        client = client or self
        class ClientVirtual:
            def __init__(self, client):
                self._client = client
                for key in dir(client):
                    if key.startswith('_') or key in ['_client', '_remote_call']:
                        continue
                    if callable(getattr(client, key)):
                        setattr(self, key, getattr(client, key))
            def _remote_call(self, *args, remote_fn, timeout:int=10, key=None, **kwargs):
                return self._client.forward(fn=remote_fn, args=args, kwargs=kwargs, key=key, timeout=timeout)
            def __getattr__(self, key):
                if key in [ '_client', '_remote_call'] :
                    return getattr(self, key)
                else:
                    return lambda *args, **kwargs : self._remote_call(*args, remote_fn=key, **kwargs)
        return ClientVirtual(client)

    conn = connect = client # alias for client method
