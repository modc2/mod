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

    chain = 'modchain'
    def __init__(self, url: str = None, network: str = 'test', **kwargs):
        super().__init__(url=url, network=network, **kwargs)
    ## MODCHAIN STUFF
    def format_mod_info(self, mod_info):
        mod_info['collateral'] = self.format_amount(mod_info.get('collateral', 0), fmt='j')
        return mod_info

    cached_mods = None

    def mods(self, search=None, key=None, update=False):
        if not update and self.cached_mods is not None:
            mods = self.cached_mods
        else:
            mods =  list(self.storage(feature='Modules', module='Modules', update=update).values())
            mods = [self.format_mod_info(mod) for mod in mods]
            self.cached_mods = mods
        if key:
            key_address = self.key_address(key)
            mods = [mod for mod in mods if mod.get('owner') == key_address]
        if search:
            mods = [mod for mod in mods if search in mod['name']]
        return mods
        
    def claim(self, key=None):
        return self.call( module="ComClaim", fn="claim", params={}, key=key)

    def reg(self , name='api', take=0, key=None, update=False):
        modstruct = self.modstruct(name, key=key, update=update)
        return self.call( module="Modules", fn="register_module", params=modstruct, key=key)

    def modstruct(self, name='api', key=None, update=False, take=0):
        info = m.fn('api/reg')(name, update=update, key=key)
        print(info)
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
    
    _registry = None
    def registry(self, key=None, update=False, **kwargs) -> Dict[str, Dict[str, str]]:
        """
        Get the chain registry mapping keys and module names to chain IDs.
        Args:
            key: Optional; Key to filter modules by owner.
            update: If True, forces an update of the registry.
            **kwargs: Additional arguments for module search.
        Returns:
            A dictionary mapping keys to module names and their corresponding chain IDs.
        """

        net = self.net()
        result = {}
        if self._registry is None or update:
            mods = self.mods(update=update, **kwargs)
            for mod in mods:
                if '/' not in mod['name']:
                    continue
                key, name = mod['name'].split('/')
                if not self.valid_ss58_address(key):
                    name, key = mod['name'].split('/')
                if key not in result:
                    result[key] = {}
                result[key][name] = self.get_chainid(mod)
            self._registry = result
        result = self._registry
        if key is not None:
            result = result.get(key, {})
        return result

    def get_chainid(self, mod:dict) -> str:
        mod['chainid'] = f'{self.net()}/{mod["id"]}'
        return mod['chainid']
    def mymods(self, key=None, update=False):
        key = m.key(key)
        mods = self.mods(key=key, update=update)
        return list(filter(is_my_mod, mods))

    def mod(self, name='api', key=None, update=False):
        mod_id = self.modid(name=name, key=key, update=update)
        mods = self.mods(update=update)
        mod = mods[mod_id]
        info = m.fn('api/mod')(name, key=key)
        info['id'] = mod_id
        info['collateral'] = mod.get('collateral', 0)
        return info

    def key_address(self, key=None):
        if isinstance(key, str):
            if self.valid_ss58_address(key):
                return key
            else:
                return m.key(key).address
        else:
            key = m.key(key)
            return key.address
    
        
    # def modules()

