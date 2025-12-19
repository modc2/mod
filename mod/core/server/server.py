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


    helper_fns = ['info', 'forward']
    fn_attributes = ['endpoints',  'fns', 'expose',  'exposed', 'functions', 'fns', 'expose_fns']
    
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
        self.set_gate(gate)

    def set_gate(self, gate,  fns = ['add_user', 'rm_user', 'users', 'is_user', 'txs']):
        self.gate = m.mod(gate)()
        m.mergemods(from_mod=self.gate, to_mod=self, fns=fns)

    def set_pm(self,  pm: Union[str, 'Module', Any],  fns = ['logs', 'namespace', 'kill', 'kill_all','namespace', 'killall']):
        self.pm = m.mod(pm)()
        m.mergemods(from_mod=self.pm, to_mod=self, fns=fns)

    def is_generator(self, obj):
        """
        Is this shiz a generator dawg?
        """
        if not callable(obj):
            result = inspect.isgenerator(obj)
        else:
            result =  inspect.isgeneratorfunction(obj)
        return result

    def print_request(self, request: dict):
        """
        print the request nicely
        """
        fn = request.get('fn', '')
        params = request['params'] if 'params' in request else {}
        client = request['client']['key'] if 'client' in request and 'key' in request['client'] else ''
        cost = request['cost'] if 'cost' in request else 0
        right_buffer = '>'*64
        left_buffer = '<'*64
        print(right_buffer, color='blue')
        print(f"""Request\t""" , color='blue')
        print(left_buffer)
        print_params = {'fn': fn, 'params': params, 'client': client, 'cost': cost, 'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}
        # side ways dataframe where each param is a row
        df = pd.DataFrame(print_params.items(), columns=['param', 'value'])
        print(df.to_string(index=False), color='blue')
        print(right_buffer, color='blue')

    _obj_cache = {}
    def get_fn_obj(self, fn:str) -> Any:
        if '/' in fn:
            if fn in self._obj_cache:
                fn_obj = self._obj_cache[fn]
                print(f'Using cached function object for {fn}', color='green')
            else:
                temp_mod = fn.split('/')[0]
                fn = '/'.join(fn.split('/')[1:])
                if hasattr(self.mod, temp_mod):
                    mod_obj = getattr(self.mod, temp_mod)
                    fn_obj = getattr(mod_obj, fn)
                else: 
                    if m.mod_exists(temp_mod):
                        mod_obj = m.mod(temp_mod)()
                        fn_obj = getattr(mod_obj, fn)
                self._obj_cache[fn] = fn_obj
        else:
            fn_obj = getattr(self.mod, fn) # get the function object from the mod
        return fn_obj

    def forward(self, **request: dict):
        """
        runt the function
        """
        fn = request['fn']
        params = request['params']
        info = self.mod.info()
        request['cost'] = float(info['schema'].get(fn, {}).get('cost', 0))
        self.print_request(request)
        fn_obj = self.get_fn_obj(fn)
        result = fn_obj(**params) if callable(fn_obj) else fn_obj
        server_auth = {k:v for k,v in request.items() if k in ['fn', 'params', 'client']}
        if self.is_generator(result):
            def generator_wrapper(generator):
                server_auth['result'] =  []
                for item in generator:
                    print(item, end='')
                    server_auth['result'].append(item)
                    yield item
                # save the transaction between the h and server for future auditing
                self.gate.save_tx(
                    mod=info["name"],
                    fn=request['fn'], # 
                    params=request['params'], # params of the inputes
                    client=request['client'],
                    cost=request['cost'],
                    result=server_auth['result'],
                    server= self.gate.generate(data=server_auth, cost=request['cost']), 
                    key=self.key)
            # if the result is a generator, return a stream
            return  EventSourceResponse(generator_wrapper(result))
        else:
            # save the transaction between the h and server for future auditing
            server_auth['result'] = result
        tx = self.gate.tx.forward(
            mod=info["name"],
            fn=request['fn'], # 
            params=request['params'], # params of the inputes
            client=request['client'], # client auth
            result=result,
            server=self.gate.auth.generate(data={**server_auth, "result": result }, cost=request['cost']), 
            key=self.key)
        return result

    def get_port(self, port:Optional[int]=None, mod:Union[str, 'Module', Any]=None) -> int:
        if port == None: 
            config = m.config(mod)
            if config != None and 'port' in config:
                port = config['port']
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
                    return  m.call(namespace[name]+'/info')
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
              daemon = True, 
              run_mode = 'hypercorn', # the mode to run the api server
              dind = False, # whether to run the server in docker in docker
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
            return m.fn('pm/forward')(mod=mod, params=params, port=port, key=key,  daemon=daemon, docker_in_docker=dind)

        self.mod = m.mod(mod)(**(params or {}))
        self.key = m.key(key)
        self.url =  '0.0.0.0:' + str(port)
        fns = self.get_fns(fns)
        def get_info(mod, **kwargs):
            info =  m.info(mod, **kwargs)
            info['fns'] = fns
            return info
        self.mod.info = partial(get_info, mod=mod, key=self.key)
        self.app = FastAPI()
        @self.app.options("/{fn}")
        async def options_handler(fn: str):
            return Response(status_code=204)
        self.app.add_middleware(CORSMiddleware,allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        def server_fn(fn: str, request: Request):
            fn = fn or 'info'
            try:
                request = self.gate.forward(fn=fn, request=request, info=self.mod.info()) # get the request
                future = self.executor.submit(self.forward, request, timeout=self.timeout)
                result =  future.result()
            except Exception as e:
                result =  m.detailed_error(e)
            return result
        self.app.post("/{fn}")(server_fn)
        self.show_info()
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

    def show_info(self):
        print('--- Server Info ---', color='green')
        shorten_v = lambda fn: fn[:6] + '...' + fn[-4:] if len(fn) > 12 else fn
        shorten_keys = ['key', 'cid', 'signature']
        show_info = self.mod.info().copy()
        print(show_info, color='green')
        print('-------------------', color='green')
        return show_info

    def get_fns(self, fns  = None) -> List[str]: 

        """
        get the public functions
        """
        fns =  fns or []
        # if no fns are provided, get them from the mod attributes
        if len(fns) == 0:
            for fa in self.fn_attributes:
                if hasattr(self.mod, fa) and isinstance(getattr(self.mod, fa), list):
                    fns = getattr(self.mod, fa) 
                    break
        return list(set(fns + self.helper_fns))
