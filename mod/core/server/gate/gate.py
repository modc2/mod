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

    def __init__(self, path = '~/.mod/server',  **_kwargs):
        self.loop = m.loop()
        self.store = m.mod('store')(path)
        self.auth = m.mod('auth')()
        self.tx = m.mod('tx')()
        self.auth = m.mod('auth')()
        self.generate = self.auth.forward
        self.verify = self.auth.verify
        self.save_tx = self.tx.forward
        self.ensure_role_map()


    def ensure_role_map(self):
        role2data = self.role2data()
        if 'owner' not in role2data:
            self.add_role('owner', {'fns': ['*']})

    def verify(self, headers):
        headers = self.auth.verify(headers)


    def forward(self, fn:str, request, info:dict) -> dict:
        """
        process the request
        """
        headers = dict(request.headers)
        print('Gate.forward headers=', headers)
        headers = self.verify(headers)
        
        assert self.is_user(info['name'], headers['key']), f"User {headers['key']} for Mod {info['name']} is not a user"
        assert fn in info['fns'], f"Function {fn} not in fns={info['fns']}"
        params = self.loop.run_until_complete(request.json())
        params = json.loads(params) if isinstance(params, str) else params
        return  {
                    'fn': fn, 
                    'params': params, 
                    'client': headers, 
                    }

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
    

    def txs(self, *args, **kwargs) -> Union[pd.DataFrame, List[Dict]]:
        return self.tx.txs( *args, **kwargs)


    role2data_path = 'role2data'

    def role2data(self):
        return self.store.get(self.role2data_path, {})

    def add_role(self, role:str = 'owner', data:dict = {'fns': ['*']}):
        role2data = self.role2data()
        role2data[role] = data
        self.store.put(self.role2data_path, role2data)
        return role2data
    
    def reset_roles(self):
        self.store.put(self.role2data_path, {})

    role_registry_path = 'role_registry'

    def set_user_role(self, role:str, user:str):
        registry = self.store.get(self.role_registry_path, {})

    def role_registry(self):
        path = 'role_registry'
        return  self.store.get(path, {})

        