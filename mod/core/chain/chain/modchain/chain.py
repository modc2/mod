import requests
import json
import os
import queue
import re
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Mapping, TypeVar, cast, List, Dict, Optional
from collections import defaultdict
from typing import Any, Callable, Optional, Union, Mapping
import pandas as pd
import mod as m

Substrate = m.mod('chain.substrate')
class ModChain(Substrate):

    def __init__(self, url: str = None, **kwargs):
        super().__init__(url=url, **kwargs)
    ## MODCHAIN STUFF
    def format_mod_info(self, mod_info):
        mod_info['collateral'] = self.format_amount(mod_info.get('collateral', 0), fmt='j')
        return mod_info

    def mods(self, update=False):
        mods =  list(self.storage(feature='Modules', module='Modules', update=update).values())
        mods = [self.format_mod_info(mod) for mod in mods]
        return mods
        
    def claim(self, key=None):
        return self.call( module="ComClaim", fn="claim", params={}, key=key)

    def reg(self , name='api', take=0, key=None, update=False):
        modstruct = self.modstruct(name, key=key, update=update)
        return self.call( module="Modules", fn="register_module", params=modstruct, key=key)

    def modstruct(self, name='api', key=None, update=False, take=0):
        info = m.fn('api/reg')(name, update=update, key=key)
        params = {
                'name': info['name'] + '/' + info['key']  , 
                'data': info['cid'], 
                'url': info['url'] ,
                'take': take
                }
        return params

    def modid(self, name='api', key=None, update=False):
        key = m.key(key)
        mods = self.mymods(key=key, update=update)
        module_id = None
        for mod in mods:
            if mod['name'] == f'{key.address}/{name}' or mod['name'] == f'{name}/{key.address}':
                module_id = mod['id']
                break
        assert module_id is not None, f"Module {name} not found for key {key}"
        return module_id

    def update(self, name='api', take=0, key=None, update=False):
        modstruct = self.modstruct(name=name, key=key, update=update)
        mods = self.mymods(key=key, update=update)
        module_id = self.modid(name=name, key=key, update=update)
        return self.call( module="Modules", fn="update_module", params=modstruct, key=key)

    def key2mods(self, key=None, update=False):
        key = m.key(key)
        mods = self.mods(update=update)
        key2mods = {}
        for mod in mods:
            mod_name = mod['name'].replace(f'{key.address}/', '').replace(f'/{key.address}', '')
            key_address = mod['owner']
            key2mods[key_address] = key2mods.get(key_address, []) + [mod]
        return key2mods
        

    def exists(self, name='api', key=None, update=False):
        """
        whether the module exists
        """
        key = m.key(key)
        mods = self.mymods(key=key, update=update)
        for mod in mods:
            if mod['name'] in [f'{key.address}/{name}' , f'{name}/{key.address}' ] :
                return True
        return False

    def key2address(self):
        return  m.key2address()
    
    def my_addresses(self):
        return list(self.key2address().keys())
    
    def mybalances(self, fmt='j', update=False):
        balances = self.balances(fmt=fmt, update=update)
        my_balances = {}
        for key, addr in self.key2address().items():
            if addr in balances:
                my_balances[key] = balances[addr]
        return my_balances

    def mymods(self, key=None, update=False):
        key = m.key(key)
        mods = self.mods(update=update)
        is_my_mod = lambda mod_info: mod_info['name'].startswith(key.address) or mod_info.get('owner') == key.address
        return list(filter(is_my_mod, mods))

    def mykey2mods(self, update=False):
        key2mods = self.key2mods(update=update)
        my_key2mods = {}
        key2address = m.key2address()
        for key_str, address in key2address.items():
            if address in key2mods:
                my_key2mods[address] = key2mods.get(address, [])
        return my_key2mods

    def mod(self, name='api', key=None, update=False):
        mod_id = self.modid(name=name, key=key, update=update)
        mods = self.mods(update=update)
        mod = mods[mod_id]
        info = m.fn('api/mod')(name, key=key)
        info['id'] = mod_id
        info['collateral'] = mod.get('collateral', 0)
        return info
    
        
    myk2m = mykey2mods
    # def modules()

