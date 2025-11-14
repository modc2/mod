from typing import *
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
import uvicorn
import os
import hashlib
import os
import pandas as pd
import json
import inspect
import asyncio
import time
import mod as m

print = m.print

class Server:

    def __init__(
        self, 
        path = '~/.mod/server', # the path to store the server data
        verbose:bool = True, # whether to print the output
        pm = 'pm', # the process manager to use
        serializer = 'serializer', # the serializer to use
        tx = 'tx', # the tx to use
        auth = 'auth', # the auth to use
        sudo_roles = [ 'owner'], # the roles that can access all functions
        run_mode = 'hypercorn',
        **_kwargs):

        self.store = m.mod('store')(path)
        self.verbose = verbose
        self.set_pm(pm)
        self.serializer = m.mod(serializer)() # sets the serializer
        self.tx = m.mod(tx)()
        self.auth = m.mod(auth)()
        self.sudo_roles = sudo_roles
        self.run_mode = run_mode


    def set_pm(self, pm: Union[str, 'Module', Any], sync_fns = ['logs', 'namespace', 'kill', 'kill_all']):
        self.pm = m.mod(pm)()
        for fn in sync_fns:
            if hasattr(self.pm, fn) and hasattr(pm, fn):
                setattr(self.pm, fn, getattr(pm, fn))
    
    def is_owner(self, key:str) -> bool:
        if not hasattr(self, 'owner_address'):
            self.owner_address = m.key().address
        return self.owner_address == key

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

    def forward(self, fn:str, request: Request):
        """
        runt the function
        """
        print('Server: Forwarding request for function', fn)
        request = self.gate(fn=fn, request=request) # get the request
        fn = request['fn']
        params = request['params']
        cost = request['cost']
        info = self.mod.info()
        cost = float(info['schema'].get(fn, {}).get('cost', 0))
        if '/' in fn:

            temp_mod = fn.split('/')[0]
            fn = '/'.join(fn.split('/')[1:])
            fn_obj = getattr(m.mod(temp_mod)(), fn)
        else:
            fn_obj = getattr(self.mod, fn) # get the function object from the mod
        if callable(fn_obj):
            if len(params) == 2 and 'args' in params and 'kwargs' in params :
                kwargs = dict(params.get('kwargs')) 
                args = list(params.get('args'))
            else:
                args = []
                kwargs = dict(params)
            result = fn_obj(*args, **kwargs) 
        else:
            # if the fn is not callable, return it
            result = fn_obj


        if self.is_generator(result):
            def generator_wrapper(generator):
                gen_result =  {'data': [], 'start_time': time.time(), 'end_time': None, 'cost': 0}
                for item in generator:
                    print(item, end='')
                    gen_result['data'].append(item)
                    yield item
                # save the transaction between the headers and server for future auditing
                self.tx.forward(
                    mod=info["name"],
                    fn=fn, # 
                    params=params, # params of the inputes
                    client=request['client'],
                    cost=cost,
                    result=gen_result,
                    server= self.auth.generate(data={"fn": fn, "params": params, "result": gen_result, 'client': request['client']}, cost=cost), 
                    key=self.key)
            # if the result is a generator, return a stream
            return  EventSourceResponse(generator_wrapper(result))
        else:

            result = self.serializer.forward(result) # serialize the result
            tx = self.tx.forward(
                mod=info["name"],
                fn=fn, # 
                params=params, # params of the inputes
                client=request['client'], # client auth
                result=result,
                server=self.auth.generate(data={"fn": fn, "params": params, "result": result, 'client': request['client']}, cost=cost), 
                key=self.key)
            return result
        
        raise Exception('Should not reach here, something went wrong in forward')


    def gate(self, fn:str, request) -> float:
        """
        process the request
        """
        if fn == '':
            fn = 'info'
        headers = dict(request.headers) 
        info = self.mod.info()   
        cost = float(info['schema'].get(fn, {}).get('cost', 0))
        client_cost = float(headers.get('cost', 0))
        assert client_cost >= cost, f'Insufficient cost {client_cost} for fn {fn} with cost {cost}'
        loop = asyncio.get_event_loop()
        params = loop.run_until_complete(request.json())
        params = json.loads(params) if isinstance(params, str) else params
        assert self.auth.verify(headers, data={"fn": fn, "params": params}) # verify the headers
        if not self.is_owner(headers['key']):
            assert fn in info['public_fns'], f"Function {fn} not in fns={info['fns']}"
        request =  {
                    'fn': fn, 
                    'params': params, 
                    'client': {k:v for k,v in headers.items() if k in self.auth.auth_features}, 
                    'cost': cost
                    }
        self.print_request(request)
        return request

    def txs(self, *args, **kwargs) -> Union[pd.DataFrame, List[Dict]]:
        return  self.tx.txs( *args, **kwargs)
        
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
            """Filter function to check if a mod contains the required features."""
            return isinstance(m, dict) and all(feature in m for feature in features )    
        mods = self.store.get(path, None, max_age=max_age, update=update)
        if mods == None :
            urls = self.urls(search=search, **kwargs)
            print(f'Updating mods from {path}', color='yellow')
            futures  = [m.submit(m.call, {"fn":url + '/info'}, timeout=timeout, mode='thread') for url in urls]
            mods =  m.wait(futures, timeout=timeout)
            print(f'Found {len(mods)} mods', color='green')
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
        """check if the server exists"""
        return bool(name in self.servers(**kwargs))

    def call(self, fn , params=None, **kwargs): 
        return self.fn('client/forward')(fn, params, **kwargs)

    def whitelist_user(self, user:str, update:bool = False):
        """
        check if the address is whitelisted
        """
        whitelist = self.store.get(f'whitelist', [], max_age=max_age, update=update)
        whitelist.append(user)
        whitelist = list(set(whitelist))
        self.store.put(f'whitelist', whitelist)
        return {'whitelist': whitelist, 'user': user }

    def unwhitelist_user(self, user:str, update:bool = False):
        """
        check if the address is whitelisted
        """
        whitelist = self.store.get(f'whitelist', [], update=update)
        whitelist.remove(user)
        whitelist = list(set(whitelist))
        self.store.put(f'whitelist', whitelist)
        return {'whitelist': whitelist, 'user': user }
    
    def whitelist(self,  update:bool = False):
        """
        check if the address is whitelisted
        """
        return self.store.get(f'whitelist', [], update=update)

    def blacklist_user(self, user:str, update:bool = False):
        """
        check if the address is blacklisted
        """
        blacklist = self.store.get(f'blacklist', [],  update=update)
        blacklist.append(user)
        blacklist = list(set(blacklist))
        self.store.put(f'blacklist', blacklist)
        return {'blacklist': blacklist, 'user': user }

    def unblacklist_user(self, user:str,  update:bool = False):
        """
        check if the address is blacklisted
        """
        blacklist = self.store.get(f'blacklist', [],  update=update)
        blacklist.remove(user)
        blacklist = list(set(blacklist))
        self.store.put(f'blacklist', blacklist)
        return {'blacklist': blacklist, 'user': user }

    def blacklist(self,  max_age:int = 60, update:bool = False):
        """
        check if the address is blacklisted
        """
        return self.store.get(f'blacklist', [], update=update)

    def is_blacklisted(self, user:str,  update:bool = False):
        """
        check if the address is blacklisted
        """
        blacklist = self.blacklist( update=update)
        return user in blacklist

    def wait_for_server(self, name:str, max_time:int=10, trial_backoff:int=0.5, network:str='local', verbose=True, max_age:int=20):
        # wait for the server to start
        t0 = m.time()
        while m.time() - t0 < max_time:
            namespace = self.namespace(network=network)
            if name in namespace:
                try:
                    return  m.call(namespace[name]+'/info')
                except Exception as e:
                    if verbose:
                        print(f'Error getting info for {name} --> {m.detailed_error(e)}', color='red')
                        if trial > 1:
                            print(f'Error getting info for {name} --> {m.detailed_error(e)}', color='red')
                        # print(m.logs(name, tail=10))
            m.sleep(trial_backoff)
        raise Exception(f'Failed to start {name} after {trials} trials')

    def kill(self, name, *extra_mods):
        self.pm.kill(name, *extra_mods)

    def kill_all(self):
        return self.pm.kill_all()

    killall = kill_all

    def logs(self, name, **kwargs):
        return self.pm.logs(name, **kwargs)

    def namespace(self,  search=None,**kwargs) -> dict:
        return self.pm.namespace(search=search, **kwargs)

    def prepare_server(self, mod, fn_options = ['ensure_env']):
        mod_obj = m.mod(mod)()
        for fn in fn_options:
            if hasattr(mod_obj, fn):
                print(f'Preparing server: running {fn}()', color='green', verbose=self.verbose)
                getattr(mod_obj, fn)()
                break
        return True

    def serve(self, 
              mod: Union[str, 'Module', Any] = None, # the mod in either a string
              params:Optional[dict] = None,  # kwargs for the mod
              port :Optional[int] = None, # name of the server if None, it will be the mod name
              fns = None, # list of fns to serve, if none, it will be the endpoints of the mod
              key = None, # the key for the server
              cwd = None, # the cwd to run the server in
              remote = False, # whether to run the server remotely
              host = '0.0.0.0',
              volumes = None, 
              public=False,
              env = None,
              server_mode = 'http',
              daemon = True, 
              **extra_params 
              ):
        mod = mod or 'mod'
        self.prepare_server(mod)
        if mod not in ['mod']:
            _mod = m.mod(mod)
            if hasattr(_mod, 'serve'):
                return _mod().serve(**extra_params)
        port = self.get_port(port, mod=mod)
        print(f'Serving {mod} on port {port}', color='green', verbose=self.verbose)
        params = {**(params or {}), **extra_params}
        if remote:
            return m.fn('pm/forward')(mod=mod, params=params, port=port, key=key, cwd=cwd, daemon=daemon, volumes=volumes, env=env)
        self.serve_mod(mod=mod, params=params, key=key, public=public, fns=fns, port=port)

    def get_public_fns(self, 
                        fns  = None, 
                        helper_fns = ['info', 'forward'],
                        fn_attributes = ['endpoints',  'fns', 'expose',  'exposed', 'functions', 'public_fns', 'expose_fns']
                        ) -> List[str]: 

        """
        get the public functions
        """

        fns =  fns or []
        # if no fns are provided, get them from the mod attributes
        if len(fns) == 0:
            for fa in fn_attributes:
                if hasattr(self.mod, fa) and isinstance(getattr(self.mod, fa), list):
                    fns = getattr(self.mod, fa) 
                    break
        return list(set(fns + helper_fns))

    def serve_mod(self, 
                mod: Union[str, 'Module', Any], 
                port:Optional[int]=None,
                params:Optional[dict] = None, 
                key:Optional[str]=None,
                public:bool=False, 
                fns:Optional[List[str]]=None ):

        self.mod = m.mod(mod)(**(params or {}))
        self.key = m.key(key)
        self.url =  '0.0.0.0:' + str(port)
        info = m.info(mod, key=self.key, public=public, schema=True, url=self.url)
        info['public_fns'] = self.get_public_fns(fns)
        self.info = info
        def get_info( schema:bool = True, fns=True):
            info_ = m.copy(self.info)
            if not schema:
                info_.pop('schema', None)
            if not fns:
                info_.pop('fns', None)
            return info_ 
        self.mod.info = get_info
        self.app = FastAPI()
        self.app.add_middleware(CORSMiddleware,allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        def server_fn(fn: str, request: Request):
            try:
                result =  self.forward(fn, request)
            except Exception as e:
                result = m.detailed_error(e)
            return result
        self.app.post("/{fn}")(server_fn)
        self.show_info()
        self.run_app(self.app, port=port)

    def run_app(self, app:FastAPI, port:int):
        if self.run_mode == 'uvicorn':
            uvicorn.run(self.app, host='0.0.0.0', port=port)
        elif self.run_mode == 'hypercorn':
            from hypercorn.config import Config
            from hypercorn.asyncio import serve
            config = Config()
            config.bind = [f"0.0.0.0:{port}"]
            print(f'Starting server with hypercorn on port {port}', color='green', verbose=self.verbose)
            asyncio.run(serve(self.app, config))
        else:
            raise Exception(f'Unknown mode {mode} for run_app')

    def show_info(self):
        print('--- Server Info ---', color='green', verbose=self.verbose)
        shorten_v = lambda fn: fn[:6] + '...' + fn[-4:] if len(fn) > 12 else fn
        shorten_keys = ['key', 'cid', 'signature']
        show_info = self.mod.info().copy()
        show_info.pop('schema', None)
        show_info['fns'] = show_info['fns']
        print(show_info, color='green', verbose=self.verbose)
        print('-------------------', color='green', verbose=self.verbose)
        return show_info
