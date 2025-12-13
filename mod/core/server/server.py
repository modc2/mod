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
        executor = 'executor', # the executor to use,
        tx = 'tx', # the tx to use
        auth = 'auth', # the auth to use
        sudo_roles = [ 'owner'], # the roles that can access all functions
        run_mode = 'hypercorn',
        max_timeout = 300,
        **_kwargs):
        self.loop = asyncio.get_event_loop()
        self.store = m.mod('store')(path)
        self.verbose = verbose
        self.set_pm(pm)
        self.serializer = m.mod(serializer)() # sets the serializer
        self.executor = m.mod('executor')()
        self.tx = m.mod(tx)()
        self.auth = m.mod(auth)()
        self.sudo_roles = sudo_roles
        self.run_mode = run_mode
        self.max_timeout = max_timeout

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
                self.tx.forward(
                    mod=info["name"],
                    fn=request['fn'], # 
                    params=request['params'], # params of the inputes
                    client=request['client'],
                    cost=request['cost'],
                    result=server_auth['result'],
                    server= self.auth.generate(data=server_auth, cost=request['cost']), 
                    key=self.key)
            # if the result is a generator, return a stream
            return  EventSourceResponse(generator_wrapper(result))
        else:
            # save the transaction between the h and server for future auditing
            server_auth['result'] = result
        tx = self.tx.forward(
            mod=info["name"],
            fn=request['fn'], # 
            params=request['params'], # params of the inputes
            client=request['client'], # client auth
            result=result,
            server=self.auth.generate(data={**server_auth, "result": result }, cost=request['cost']), 
            key=self.key)
        return result

    def preprocess(self, fn:str, request) -> dict:
        """
        process the request
        """

        # step 1 : verify the ehaders
        if fn == '':
            fn = 'info'
        h = dict(request.headers) 
        info = self.mod.info()   
        assert self.is_user(info['name'], h['key']), f"User {h['key']} is not a user"
        is_owner = self.is_owner(h['key'])
        if not is_owner:
            assert fn in info['fns'], f"Function {fn} not in fns={info['fns']}"
        cost = float(info['schema'].get(fn, {}).get('cost', 0))
        cost_client = float(h.get('cost', 0))
        assert cost_client >= cost, f'Insufficient cost {cost_client} for fn {fn} with cost {cost}'

        # step 2 : get the params which is the serialized json body
        # self.auth.verify(h)
        params = self.loop.run_until_complete(request.json())
        if isinstance(params, str):
            params = json.loads(params)
        assert self.auth.verify(h, data={'fn': fn, 'params': params}) # verify the h
        return  {
                    'fn': fn, 
                    'params': params, 
                    'client': {k:v for k,v in h.items() if k in self.auth.auth_features}, 
                    'cost': cost
                    }

    def txs(self, *args, **kwargs) -> Union[pd.DataFrame, List[Dict]]:
        return self.tx.txs( *args, **kwargs)
        
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
            """Filter function to preprocess if a mod contains the required features."""
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
        """preprocess if the server exists"""
        return bool(name in self.servers(**kwargs))

    def call(self, fn , params=None, **kwargs): 
        return self.fn('client/forward')(fn, params, **kwargs)

    def add_user_max(self, mod:str, max_users:int):
        """
        preprocess if the address is usersed
        """
        return self.store.put('user_max/' + mod , max_users)

    def user_max(self, mod:str, default:bool = 10) -> int:
        """
        preprocess if the address is usersed
        """
        return self.store.get('user_max/' + mod , default)


    def add_user(self, mod:str , user:str, update:bool = False, ):
        """
        preprocess if the address is usersed
        """
        path = self.users_path(mod)
        user_max = self.user_max(mod)
        users = self.store.get(path, [], update=update)
        assert len(users) < user_max, f'User limit reached for mod {mod}: {len(users)}/{user_max}'
        users.append(user)
        users = list(set(users))
        self.store.put(path, users)
        return {'users': users, 'user': user }

    def owner_key(self) -> str:
        if not hasattr(self, 'owner_address'):
            self.owner_address = m.key().ss58_address
        return self.owner_address

    def users(self, mod:str, update:bool = False):
        """
        preprocess if the address is usersed
        """
        path = self.users_path(mod)
        users =  self.store.get(path, [], update=update)
        owner_key = self.owner_key()
        if owner_key not in users:
            users.append(owner_key)
            self.store.put(path, users)
        return users

    def users_path(self, mod:str) -> str:
        return f'users/{mod}'

    def rm_user(self, mod:str,  user:str, update:bool = False):
        """
        preprocess if the address is usersed
        """
        path = self.users_path(mod)
        users =self.store.get(path, [], update=update)
        users.remove(user)
        self.store.put(path , users)
        return {'users': users, 'user': user }

    def is_user(self, mod:str,  user:str) -> bool:
        """
        preprocess if the address is usersed
        """
        return user in self.users(mod)
    

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
              docker_in_docker = False,
              env = None,
              server_mode = 'http',
              daemon = True, 
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
            return m.fn('pm/forward')(mod=mod, params=params, port=port, key=key, cwd=cwd, daemon=daemon, volumes=volumes, env=env, docker_in_docker=docker_in_docker)
        self.serve_api(mod=mod, params=params, key=key, public=public, fns=fns, port=port)

    def get_fns(self, 
                        fns  = None, 
                        helper_fns = ['info', 'forward'],
                        fn_attributes = ['endpoints',  'fns', 'expose',  'exposed', 'functions', 'fns', 'expose_fns']
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

    def serve_api(self, 
                mod: Union[str, 'Module', Any], 
                port:Optional[int]=None,
                params:Optional[dict] = None, 
                key:Optional[str]=None,
                public:bool=False, 
                fns:Optional[List[str]]=None ):
        self.mod = m.mod(mod)(**(params or {}))
        self.key = m.key(key)
        self.url =  '0.0.0.0:' + str(port)
        fns = self.get_fns(fns)
        info = m.info(mod, key=self.key, public=public, schema=True, url=self.url, fns=fns)
        def get_info( schema:bool = True, fns=True):
            info_ = m.copy(info)
            return info_ 
        self.mod.info = get_info
        self.app = FastAPI()
        @self.app.options("/{fn}")
        async def options_handler(fn: str):
            return Response(status_code=204)
        self.app.add_middleware(CORSMiddleware,allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        def server_fn(fn: str, request: Request):
            try:
                request = self.preprocess(fn=fn, request=request) # get the request
                future = self.executor.submit(self.forward, request, timeout=self.max_timeout)
                return future.result()
            except Exception as e:
                return {'success': False, 'msg': str(e), 'details': m.detailed_error(e)}
        self.app.post("/{fn}")(server_fn)
        self.show_info()
        self.run_api(self.app, port=port)

    def run_api(self, app:FastAPI, port:int):
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
            raise Exception(f'Unknown mode {mode} for run_api')

    def show_info(self):
        print('--- Server Info ---', color='green', verbose=self.verbose)
        shorten_v = lambda fn: fn[:6] + '...' + fn[-4:] if len(fn) > 12 else fn
        shorten_keys = ['key', 'cid', 'signature']
        show_info = self.mod.info().copy()
        print(show_info, color='green', verbose=self.verbose)
        print('-------------------', color='green', verbose=self.verbose)
        return show_info
