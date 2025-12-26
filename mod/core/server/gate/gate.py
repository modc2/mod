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
import time
import mod as m

print = m.print

class Gate:

    def __init__(self, path = '~/.mod/server', auth='auth.v0',mod='api', **_kwargs):
        """
        Initialize the Gate class
        params:
            path: the path to store the gate data
            auth: the auth module to use
        """
        self.loop = m.loop()
        self.store = m.mod('store')(path)
        self.auth = m.mod(auth)()
        self.set_mod(mod=mod)

    def is_generator(self, obj):
        """
        Is this shiz a generator dawg?
        """
        if not callable(obj):
            result = inspect.isgenerator(obj)
        else:
            result =  inspect.isgeneratorfunction(obj)
        return result

    def set_mod(self, mod:Any=None):
        if isinstance(mod, str):
            mod = m.mod(mod)()
        elif mod is None:
            mod = m.mod('mod')()
        elif not hasattr(mod, 'info'):
            mod = m.mod('mod')(mod)
        self.mod = mod 
        return self.mod

    def forward(self, fn:str, request, mod:Any=None) -> dict:
        """
        process the request
        """
        mod = mod or self.mod
        assert not isinstance(fn, str) or fn != '', "Function name cannot be empty"
        info = mod.info()
        headers = dict(request.headers)
        headers = self.auth.verify(headers)
        assert self.is_user(info['name'], headers['key']), f"User {headers['key']} for Mod {info['name']} is not a user"
        assert fn in info['fns'], f"Function {fn} not in fns={info['fns']}"
        params = self.loop.run_until_complete(request.json())
        params = json.loads(params) if isinstance(params, str) else params
        self.print_request(request)
        fn_obj = self.get_fn_obj(fn, mod=mod)
        result = fn_obj(**params) if callable(fn_obj) else fn_obj
        if self.is_generator(result):
            def generator_wrapper(generator):
                for item in generator:
                    yield item
            return  EventSourceResponse(generator_wrapper(result))
        else:
            return result


    def print_request(self, request: dict):
        """
        print the request nicely
        """
        fn = request.get('fn', '')
        params = request['params'] if 'params' in request else {}
        client = request['client']['key'] if 'client' in request and 'key' in request['client'] else ''
        right_buffer = '>'*64
        left_buffer = '<'*64
        print(right_buffer, color='blue')
        print(f"""Request\t""" , color='blue')
        print(left_buffer)
        print_params = {'fn': fn, 'params': params, 'client': client,'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}
        # side ways dataframe where each param is a row
        df = pd.DataFrame(print_params.items(), columns=['param', 'value'])
        print(df.to_string(index=False), color='blue')
        print(right_buffer, color='blue')

    def get_fn_obj(self, fn:str, mod:Any) -> Any:
        if not hasattr(self, '_obj_cache'):
            self._obj_cache = {}
        if '/' in fn:
            if fn in self._obj_cache:
                fn_obj = self._obj_cache[fn]
                print(f'Using cached function object for {fn}', color='green')
            else:
                temp_mod = fn.split('/')[0]
                fn = '/'.join(fn.split('/')[1:])
                if hasattr(self.mod, temp_mod):
                    mod_obj = getattr(mod, temp_mod)
                    fn_obj = getattr(mod_obj, fn)
                else: 
                    if m.mod_exists(temp_mod):
                        mod_obj = m.mod(temp_mod)()
                        fn_obj = getattr(mod_obj, fn)
                self._obj_cache[fn] = fn_obj
        else:
            fn_obj = getattr(self.mod, fn) # get the function object from the mod
        return fn_obj


    def add_user_max(self, mod:str, max_users:int):
        """
        gate if the address is usersed
        """
        return self.store.put('user_max/' + mod , max_users)

    def user_max(self, mod:str, default:bool = 10) -> int:
        """
        gate if the address is usersed
        """
        return self.store.get('user_max/' + mod , default)

    def add_user(self, mod:str , user:str, update:bool = False, ):
        """
        gate if the address is usersed
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
        """
        preprocess if the address is usersed
        """
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

    role2data_path = 'role2data'

    def role2data(self):
        """
        get the role to data mapping
        """
        return self.store.get(self.role2data_path, {})

    def add_role(self, role:str = 'owner', data:dict = {'fns': ['*']}):
        """
        add a role
        """
        role2data = self.role2data()
        role2data[role] = data
        self.store.put(self.role2data_path, role2data)
        return role2data
    
    def reset_roles(self):
        """
        reset the roles
        """
        self.store.put(self.role2data_path, {})

    role_registry_path = 'role_registry'

    def set_user_role(self, role:str, user:str):
        """
        set the user role
        """
        registry = self.store.get(self.role_registry_path, {})

    def role_registry(self):
        """
        get the role registry
        """
        path = 'role_registry'
        return  self.store.get(path, {})

    def ensure_role_map(self):
        """
        ensure that the owner role exists
        """
        role2data = self.role2data()
        if 'owner' not in role2data:
            self.add_role('owner', {'fns': ['*']})
        