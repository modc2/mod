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

    fn_attributes : List[str] =['endpoints',  'fns', 'expose',  'exposed', 'functions']  # the attributes that can contain the fns
    helper_fns : List[str] = ['info', 'forward'] # the helper fns
    hide_private_fns: bool = True  # whether to include private fns
    def __init__(
        self, 
        path = '~/.commune/server', # the path to store the server data
        verbose:bool = True, # whether to print the output
        pm = 'server.pm', # the process manager to use
        middleware = 'server.middleware', # the middleware to use
        serializer = 'server.serializer', # the serializer to use
        tracker = 'server.tracker', # the tracker to use
        auth = 'server.auth', # the auth to use
        sudo_roles = ['admin'], # the roles that can access all functions
        **_kwargs):
        
        self.store = m.mod('store')(path)
        self.verbose = verbose
        self.pm = m.mod(pm)() # sets the mod to the pm
        self.middleware = m.mod(middleware)
        self.serializer = m.mod(serializer)() # sets the serializer
        self.tracker = m.mod(tracker)()
        self.auth = m.mod(auth)()
        self.sudo_roles = sudo_roles
    
    def is_generator(self, obj):
        """
        Is this shiz a generator dawg?
        """
        if not callable(obj):
            result = inspect.isgenerator(obj)
        else:
            result =  inspect.isgeneratorfunction(obj)
        return result

    def forward(self, fn:str, request: Request):
        """
        runt the function
        """
        request = self.get_request(fn=fn, request=request) # get the request
        fn = request['fn']
        params = request['params']
        cost = float(self.info['schema'].get(fn, {}).get('cost', 0))
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
                print(f'Starting generator for fn {fn} with params {params}', color='green', verbose=self.verbose)
                for item in generator:
                    print(item, end='')
                    gen_result['data'].append(item)
                    yield item
                print(f'Ending generator for fn {fn} with params {params}', color='green', verbose=self.verbose)
                # save the transaction between the headers and server for future auditing
                server_auth = self.auth.headers(data={'fn': fn, 'params': params, 'result': gen_result, 'cost': cost})

                self.tracker.forward(
                    mod=self.info["name"],
                    schema=self.info['schema'].get(fn, {}),
                    fn=fn, # 
                    params=params, # params of the inputes
                    result=gen_result,
                    client=request['client'],
                    server=server_auth, 
                    key=self.key)
            # if the result is a generator, return a stream
            return  EventSourceResponse(generator_wrapper(result))
        else:

            # save the transaction between the headers and server for future auditing
            result = self.serializer.forward(result) # serialize the result
            fn = request['fn']
            params = request['params']
            server_auth = self.auth.generate(data={"fn": fn, "params": params, "result": result})
            tx = self.tracker.forward(
                mod=self.info["name"],
                fn=fn, # 
                params=params, # params of the inputes
                result=result,
                schema=self.info['schema'].get(fn, {}),
                client=request['client'], # client auth
                server= server_auth , 
                key=self.key)
            return result
        
        raise Exception('Should not reach here, something went wrong in forward')



    @property
    def roles(self):
        if not hasattr(self, '_roles'):
            self._roles = self.get_roles()
        return self._roles

    def get_request(self, fn:str, request) -> float:
        if fn == '':
            fn = 'info'
        headers = dict(request.headers)    
        cost = float(self.info['schema'].get(fn, {}).get('cost', 0))
        client_cost = float(headers.get('cost', 0))
        assert client_cost >= cost, f'Insufficient cost {client_cost} for fn {fn} with cost {cost}'
        self.auth.verify(headers) # verify the headers
        loop = asyncio.get_event_loop()
        params = loop.run_until_complete(request.json())
        params = json.loads(params) if isinstance(params, str) else params
        assert self.auth.hash({"fn": fn, "params": params}) == headers['data'], f'Invalid data hash for {params}'
        role = self.role(headers['key']) # get the role of the user
        if role not in self.sudo_roles:
            assert fn in self.info['fns'], f"Function {fn} not in fns={self.info['fns']}"
        return {'fn': fn, 'params': params, 'client': headers, 'role': role, 'cost': cost}

    def txs(self, *args, **kwargs) -> Union[pd.DataFrame, List[Dict]]:
        return  self.tracker.txs( *args, **kwargs)

        
    def get_port(self, port:Optional[int]=None):
        port = port or m.free_port()
        return port

    def servers(self, search=None,  **kwargs) -> List[str]:
        return list(self.namespace(search=search, **kwargs).keys())

    def urls(self, search=None,  **kwargs) -> List[str]:
        return list(self.namespace(search=search, **kwargs).values())   

    mods_path = 'mods.json'

    def mods(self, 
                search=None, 
                max_age=None, 
                update=False, 
                features=['name', 'url', 'key'], 
                timeout=24, 
                **kwargs):

        def module_filter(m: dict) -> bool:
            """Filter function to check if a mod contains the required features."""
            return isinstance(m, dict) and all(feature in m for feature in features )    
        mods = self.store.get(self.mods_path, None, max_age=max_age, update=update)
        if mods == None :
            urls = self.urls(search=search, **kwargs)
            print(f'Updating mods from {self.mods_path}', color='yellow')
            futures  = [m.submit(m.call, {"fn":url + '/info'}, timeout=timeout, mode='thread') for url in urls]
            mods =  m.wait(futures, timeout=timeout)
            print(f'Found {len(mods)} mods', color='green')
            mods = list(filter(module_filter, mods))
            self.store.put(self.mods_path, mods)
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

    def call_count(self, user:str, # the key to rate
            fn = 'info', # the function to rate
             max_age:int = 60, # the maximum age of the rate
             update:bool = False, # whether to update the rate
             mod = None, # the mod to rate on
             ) -> float:
        if mod == None:
            mod = self.info["name"]
        if '/' in user:
            mod, user = user.split('/')
        path = f'results/{mod}/{fn}/{user}'
        return len( self.store.paths(path)) 

    def role(self, user) -> str:
        """
        get the role of the address ( owner, local, public)
        """
        assert not self.is_blacklisted(user), f"Address {user} is blacklisted"
        role = 'public'
        if m.is_owner(user):
            # can call any fn
            role =  'owner'
        else:
            # non admin roles (cant call every fn)            
            # check if the user has a role
            if user in self.roles:
                role = roles[user]
            if not hasattr(self, 'address2key'):
                self.address2key = m.address2key()
            if user in self.address2key:
                role =  'local'
            else:
                role = 'public' # default role is public
        return role

    def get_roles(self, max_age:int = 60, update:bool = False):
        """
        get the roles of the addresses
        """
        roles = self.store.get(f'roles', {}, max_age=max_age, update=update)
        return roles

    def add_role(self, address:str, role:str, max_age:int = 60, update:bool = False):
        """
        add a role to the address
        """
        roles = self.store.get(f'roles', {}, max_age=max_age, update=update)
        roles[address] = role
        self.store.put(f'roles', roles)
        return {'roles': roles, 'address': address }

    def remove_role(self, address:str, role:str, max_age:int = 60, update:bool = False):
        """
        remove a role from the address
        """
        roles = self.store.get(f'roles', {}, max_age=max_age, update=update)
        if address in roles:
            del roles[address]
        self.store.put(f'roles', roles)
        return {'roles': roles, 'address': address }

    def get_role(self, address:str, max_age:int = 60, update:bool = False):
        """
        get the role of the address
        """
        roles = self.store.get(f'roles', {}, max_age=max_age, update=update)
        if address in roles:
            return roles[address]
        else:
            return 'public'

    def has_role(self, address:str, role:str, max_age:int = 60, update:bool = False):
        """
        check if the address has the role
        """
        roles = self.store.get(f'roles', {}, max_age=max_age, update=update)
        if address in roles:
            return roles[address] == role
        else:
            return False

    def blacklist_user(self, user:str, max_age:int = 60, update:bool = False):
        """
        check if the address is blacklisted
        """
        blacklist = self.store.get(f'blacklist', [], max_age=max_age, update=update)
        blacklist.append(user)
        blacklist = list(set(blacklist))
        self.store.put(f'blacklist', blacklist)
        return {'blacklist': blacklist, 'user': user }

    def unblacklist_user(self, user:str, max_age:int = 60, update:bool = False):
        """
        check if the address is blacklisted
        """
        blacklist = self.store.get(f'blacklist', [], max_age=max_age, update=update)
        blacklist.remove(user)
        blacklist = list(set(blacklist))
        self.store.put(f'blacklist', blacklist)
        return {'blacklist': blacklist, 'user': user }

    def blacklist(self,  max_age:int = 60, update:bool = False):
        """
        check if the address is blacklisted
        """
        return self.store.get(f'blacklist', [], max_age=max_age, update=update)

    def is_blacklisted(self, user:str, max_age:int = 60, update:bool = False):
        """
        check if the address is blacklisted
        """
        blacklist = self.blacklist(max_age=max_age, update=update)
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

    def kill(self, name):
        self.pm.kill(name)

    def kill_all(self):
        return self.pm.kill_all()

    killall = kill_all

    def logs(self, name, **kwargs):
        return self.pm.logs(name, **kwargs)

    def namespace(self,  search=None,**kwargs) -> dict:
        return self.pm.namespace(search=search, **kwargs)

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
        port = self.get_port(port)
        mod = mod or 'mod'
        key = key or mod
        params = {**(params or {}), **extra_params}
        if remote:
            return m.fn('pm/')(mod, params=params, port=port, key=key, cwd=cwd, daemon=daemon, volumes=volumes, env=env)
        self.set_mod(mod=mod, params=params, key=key, public=public, fns=fns, port=port)

    def set_mod(self, 
                mod: Union[str, 'Module', Any], 
                port:Optional[int]=None,
                params:Optional[dict] = None, 
                key:Optional[str]=None,
                public:bool=False, 
                fns:Optional[List[str]]=None, ):
        self.mod = m.mod(mod)(**(params or {}))
        self.key = m.key(key)
        self.url =  '0.0.0.0:' + str(port)
        self.info = m.info(mod, key=self.key, public=public, schema=True, url=self.url)
        fns =  fns or []
        # if no fns are provided, get them from the mod attributes
        if len(fns) == 0:
            for fa in self.fn_attributes:
                if hasattr(self.mod, fa) and isinstance(getattr(self.mod, fa), list):
                    fns = getattr(self.mod, fa) 
                    break
        # does not start with _ and is not a private fn
        if self.hide_private_fns:
            fns = [fn for fn in fns if not fn.startswith('_') ]
        fns = list(set(fns + self.helper_fns))
        self.info['schema'] = {fn:  self.info['schema'].get(fn, {}) for fn in fns if fn in  self.info['schema']}
        self.info['fns'] = sorted(list(set(list(self.info['schema'].keys()) + self.helper_fns)))
        def get_info( schema:bool = True):
            info_ = m.copy(self.info)
            if not schema:
                info_.pop('schema', None)
            return info_   
        self.mod.info = get_info
        # start the server
        def server_fn(fn: str, request: Request):
            try:
                result =  self.forward(fn, request)
            except Exception as e:
                result =  m.detailed_error(e)
            return result
        # add CORS middleware
        self.app = FastAPI()
        self.app.add_middleware(CORSMiddleware,allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        self.app.post("/{fn}")(server_fn)
        self.show_info()
        uvicorn.run(self.app,  host='0.0.0.0', port=port, loop='asyncio')

    def show_info(self):
        print('--- Server Info ---', color='green', verbose=self.verbose)
        shorten_v = lambda fn: fn[:6] + '...' + fn[-4:] if len(fn) > 12 else fn
        shorten_keys = ['key', 'cid', 'signature']
        show_info = self.mod.info().copy()
        show_info.pop('schema', None)
        show_info['fns'] = self.info['fns']
        print(show_info, color='green', verbose=self.verbose)
        print('-------------------', color='green', verbose=self.verbose)
        return show_info
