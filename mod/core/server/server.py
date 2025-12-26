from typing import *
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
import os
import hashlib
import os
import pandas as pd
import json
import inspect
from functools import partial
import asyncio
import time
import mod as m

print = m.print

class Server:
    
    # possible attributes in the mod that list the functions to expose
    fn_attributes = ['endpoints',  'fns', 'expose',  'exposed', 'functions', 'fns', 'expose_fns']

    # helper functions that are always exposed are always exposed [WARNING: HELPER FNS SHOULD BE CAREFULLY CHOSEN TO AVOID SECURITY RISKS]
    HELPER_FNS = ['info', 'forward']
    
    def __init__(
        self, 
        path = '~/.mod/server', # the path to store the server data
        pm = 'pm', # the process manager to use
        executor = 'executor', # the executor to use,
        gate='gate',
        timeout = 300,
        **_kwargs):
        self.loop = asyncio.get_event_loop()
        self.store = m.mod('store')(path)
        self.set_pm(pm)
        self.executor = m.mod(executor)()
        self.timeout = timeout
        
    def set_pm(self,  pm: Union[str, 'Module', Any],  fns = ['logs', 'namespace', 'kill', 'kill_all','namespace', 'killall']):
        self.pm = m.mod(pm)()
        m.mergemods(from_mod=self.pm, to_mod=self, fns=fns)

    def forward(self, **request: dict):
        """
        runt the function
        """
        return self.gate.forwrd(**request)

    def get_port(self, port:Optional[int]=None, mod:Union[str, 'Module', Any]=None) -> int:
        if port == None: 
            config = m.config(mod)
            if config != None and 'port' in config:
                port = config['port']
            else:
                port  = mod.port if hasattr(mod, 'port') else None
        port = port or m.free_port()
        return port

    def servers(self, search=None,  **kwargs) -> List[str]:
        return list(self.namespace(search=search, **kwargs).keys())

    def urls(self, search=None,  **kwargs) -> List[str]:
        return list(self.namespace(search=search, **kwargs).values())   

    def mods(self, 
                search=None, 
                max_age=None, 
                update=False, 
                features=['name', 'url', 'key'], 
                timeout=24, 
                path = 'mods.json',
                **kwargs):

        def module_filter(m: dict) -> bool:
            """Filter function to gate if a mod contains the required features."""
            return isinstance(m, dict) and all(feature in m for feature in features )    
        mods = self.store.get(path, None, max_age=max_age, update=update)
        if mods == None :
            urls = self.urls(search=search, **kwargs)
            futures  = [m.submit(m.call, {"fn":url + '/info'}, timeout=timeout, mode='thread') for url in urls]
            mods =  m.wait(futures, timeout=timeout)
            mods = list(filter(module_filter, mods))
            self.store.put(path, mods)
        else:
            mods = list(filter(module_filter, mods))

        if search != None:
            mods = [m for m in mods if search in m['name']]
       
        return mods

    def n(self, search=None, **kwargs):
        return len(self.mods(search=search, **kwargs))

    def exists(self, name:str, **kwargs) -> bool:
        """gate if the server exists"""
        return bool(name in self.servers(**kwargs))

    def call(self, fn , params=None, **kwargs): 
        return self.fn('client/forward')(fn, params, **kwargs)

    def wait_for_server(self, name:str, max_time:int=10, trial_backoff:int=0.5, network:str='local',  max_age:int=20):
        # wait for the server to start
        t0 = m.time()
        while m.time() - t0 < max_time:
            namespace = self.namespace(network=network)
            if name in namespace:
                try:
                    return  m.fn('client/call')(namespace[name]+'/info')
                except Exception as e:
                    print(f'Error calling server {name} at {namespace[name]}: {m.detailed_error(e)}', color='red')
            m.sleep(trial_backoff)
        raise Exception(f'Failed to start {name} after {trials} trials')

    def prepare_server(self, mod, fn_options = ['ensure_env']):
        mod_obj = m.mod(mod)()
        for fn in fn_options:
            if hasattr(mod_obj, fn):
                print(f'Preparing server: running {fn}()', color='green')
                getattr(mod_obj, fn)()
                break
        return True

    def serve(self, 
              mod: Union[str, 'Module', Any] = None, # the mod in either a string
              params:Optional[dict] = None,  # kwargs for the mod
              port :Optional[int] = None, # name of the server if None, it will be the mod name
              fns = None, # list of fns to serve, if none, it will be the endpoints of the mod
              key = None, # the key for the server
              remote = False, # whether to run the server remotely
              d = True, 
              run_mode = 'hypercorn', # the mode to run the api server
              pm = 'pm',
              **extra_params 

              ):

        
        mod = mod or m.name
        if mod not in [m.name]:
            try:
                _mod = m.mod(mod)
            except Exception as e:
                print(f'Error loading mod {mod}: {m.detailed_error(e)}', color='red')
                return m.fn('pm/up')(mod)
            if hasattr(_mod, 'serve'):
                return _mod().serve(**extra_params)
        self.prepare_server(mod)
        port = self.get_port(port, mod=mod)
        params = {**(params or {}), **extra_params}
        if remote:
            return m.fn(f'{pm}/forward')(mod=mod, params=params, port=port, key=key,  daemon=d)
        self.set_mod(mod=mod, key=key, params=params ,fns = fns)
        self.gate = m.mod('gate')(mod=self.mod)
        # setup the api server
        self.app = FastAPI()
        @self.app.options("/{fn}")
        async def options_handler(fn: str):
            return Response(status_code=204)
        cors_params = {
            "allow_origins": ["*"],
            "allow_credentials": True,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }
        self.app.add_middleware(CORSMiddleware, **cors_params)
        def server_fn(fn: str, request: Request):
            try:
                result = self.gate.forward(fn=fn, request=request, mod=self.mod) # get the request
            except Exception as e:
                result =  m.detailed_error(e)
            return result
        self.app.post("/{fn}")(server_fn)

        # run the api server
        if run_mode == 'uvicorn':
            import uvicorn
            uvicorn.run(self.app, host='0.0.0.0', port=port)
        elif run_mode == 'hypercorn':
            from hypercorn.config import Config
            from hypercorn.asyncio import serve
            config = Config()
            config.bind = [f"0.0.0.0:{port}"]
            asyncio.run(serve(self.app, config))
        else:
            raise Exception(f'Unknown mode {run_mode} for run_api')

    def set_mod(self, mod, key=None, params=None ,fns = None) -> List[str]: 
        """
        get the public functions
        """

        self.config = m.config(mod) or {}
        self.mod = m.mod(mod)(**(params or {}))
        self.key = m.key(key)
        fns =  fns or []
        # if no fns are provided, get them from the mod attributes
        if len(fns) == 0:
            for fa in self.fn_attributes:
                if fa in self.config: 
                    fns = self.config[fa]
                if hasattr(self.mod, fa) and isinstance(getattr(self.mod, fa), list):
                    fns = getattr(self.mod, fa) 
                if len(fns) > 0:
                    break
        fns =  list(set(fns + self.HELPER_FNS))
        print(f'Exposing functions: {fns}', color='green')

        def get_info(mod, **kwargs):
                info =  m.info(mod, **kwargs)
                info['fns'] = fns
                return info
        self.mod.info = partial(get_info, mod=mod, key=self.key)

        print('--- Server Ixnfo ---', color='green')
        shorten_v = lambda fn: fn[:6] + '...' + fn[-4:] if len(fn) > 12 else fn
        print(self.mod.info().copy(), color='green')
        print('-------------------', color='green')

        return fns
