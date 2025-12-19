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

    def __init__(
        self, 
        path = '~/.mod/server', # the path to store the server data
        **_kwargs):
        self.loop = m.loop()
        self.store = m.mod('store')(path)
        self.auth = m.mod('auth')()
        self.tx = m.mod('tx')()
        self.auth = m.mod('auth')()
        self.generate = self.auth.forward
        self.verify = self.auth.verify
        self.save_tx = self.tx.forward

    def forward(self, fn:str, request, info:dict) -> dict:
        """
        process the request
        """
        # step 1 : verify the headers
        headers = self.verify(dict(request.headers) )
        assert self.is_user(info['name'], headers['key']), f"User {headers['key']} for Mod {info['name']} is not a user"
        # step 2 : check function access
        assert fn in info['fns'], f"Function {fn} not in fns={info['fns']}"
        # step 2 : check cost
        cost = float(info['schema'].get(fn, {}).get('cost', 0))
        cost_client = float(headers.get('cost', 0))
        assert cost_client >= cost, f'Insufficient cost'
        params = self.loop.run_until_complete(request.json())
        if isinstance(params, str):
            params = json.loads(params)
        return  {
                    'fn': fn, 
                    'params': params, 
                    'client': {k:v for k,v in headers.items() if k in self.auth.features}, 
                    'cost': cost
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
        