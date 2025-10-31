import requests
import os
import json
from typing import Optional, Dict, Any, List
from pathlib import Path
import time
import mod as m

class  Api:
    endpoints = ['mods', 'names', 'reg', 'mod', 'users', 'user_info', 'n']


    def __init__(self, store = 'ipfs'):
        self.store = m.mod(store)()

    def mod(self, mod: m.Mod='store', schema=False, content=False, **kwargs) -> Dict[str, Any]:
        """Add a mod Mod to IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        cid = self.registry().get(mod, mod)
        mod =  self.store.get_data(cid) if cid else None
        if schema: 
            mod['schema'] = self.store.get_data(mod['schema'])
        if content:
            mod['content'] = self.store.get_data(mod['content'])
            for file, cid in mod['content'].items():
                mod['content'][file] = self.store.get_data(cid)
        return mod

    
    registry_path = '~/.mod/api/registry.json'
    # Register or update a mod in IPFS

    def get_mod_cid(self, mod, update=False):
        registry = m.get(self.registry_path, {}, update=False)
        return  registry.get(mod, None)
    
    def put_mod_cid(self, mod:str, cid:str):
        registry = m.get(self.registry_path, {}, update=False)
        registry[mod] = cid
        m.put(self.registry_path, registry)
        return cid



    def reg(self, 
            mod = 'store', 
            key=None, 
            comment=None, 
            update=False, 
            forbidden_mods = ['mod']) -> Dict[str, Any]:
        # het =wefeererwfwefhuwoefhiuhuihewds wfweferfgr frff frrefeh fff
        current_time = m.time()
        key = m.key(key)
        prev_cid = self.get_mod_cid(mod, update=update)

        if prev_cid is None:
           info = {
                   'content': self.store.add_mod(mod),
                   'schema': self.store.add_data(m.schema(mod)),
                   'prev': prev_cid, # previous state
                   'name': mod,
                   'created':  current_time,  # created timestamp
                   'updated': current_time, 
                   'key': key.address, 
                   'nonce': 1, # noncf

                   }
        # fam
        else:
            content_cid = self.store.add_mod(mod)
            info = self.store.get_data(prev_cid)
            prev_content_cid = info['content']
            assert info['key'] == key.address, 'Key mismatch'
            if info['content'] != content_cid:
                info.update(
                    {
                    'prev': prev_cid, 
                    'content': content_cid,
                    'nonce':  info['nonce'] + 1,
                    'updated': current_time,
                    'schema': self.store.add_data(m.schema(mod)),

                    }
                )
        info['url'] = m.namespace().get(mod, None)
        info.pop('signature', None)
        info['signature'] = key.sign(info, mode='str')
        info_cid = self.store.add_data(info)
        self.put_mod_cid(mod, info_cid)
        assert self.verify(info)
        
        return info 


    def mods(self, search=None, **kwargs) -> List[str]:
        """List all registered mods in IPFS.
        
        Returns:
            List of mod names
        """
        registry = self.registry(search=search)
        return [self.mod(k) for k in registry.keys()]

    def names(self, search=None):
        mods = list(self.registry(search=search).keys())
        return mods

    def history(self, mod='store', features=['content', 'updated']):

        history = []
        info = self.mod(mod)
        while True:
            prev = info['prev']
            nonce = info['nonce']
            history.append(info)
            if prev == None:
                break
            info = self.mod(info['prev'])
        df =  m.df(history)[features]
        return df

    def diff(self, mod = 'store', update=False) -> Dict[str, Any]:
        mod = self.mod(mod)
        prev = mod.get('prev', None)
        print(f"Getting diff for mod: {mod}, prev: {prev}")
        content_cid = self.mod(prev)['content']
        prev_content = self.store.get_data(content_cid)
        current_content = self.store.get_data(mod['content'])
        diffs = {}
        for file in set(list(prev_content.keys()) + list(current_content.keys())):
            prev_file_content = prev_content.get(file, None)
            current_file_content = current_content.get(file, None)
            if prev_file_content != current_file_content:
                diffs[file] = {
                    'previous': prev_file_content,
                    'current': current_file_content
                }
            
        return diffs
        
    def registry(self, search=None, update=False) -> Dict[str, str]:
        registry =  m.get(self.registry_path, {}, update=update)
        if search: 
            registry = {k:v for k,v in registry.items() if search in k }
        return registry

    def clear_registry(self) -> bool:
        m.put(self.registry_path, {})
        self.store._rm_all_pins()
        return True


    def dereg(self, mod = 'store') -> bool:
        registry = self.registry()
        cid = registry.get(mod)
        mod_data = self.store.get_data(cid)
        content_cid = mod_data['content']
        schema_cid = mod_data['schema']
        content_map = self.store.get_data(content_cid)
        for file, file_cid in content_map.items():
            self.store.rm(file_cid)
        self.store.rm(content_cid)
        self.store.rm(schema_cid)
        self.store.rm(cid)
        del registry[mod]
        m.put(self.registry_path, registry)
        return False

    def regall(self, mods: List[m.Mod]=None, key=None, comment=None, update=False) -> Dict[str, Any]:
        mods = mods or m.mods()
        mod2info = {}
        for mod in mods:
            print(f"Registering mod: {mod}")
            info = self.reg(mod, key=key, comment=comment, update=updat)
            mod2info[mod] = info
        return mod2info


    def schema(self, mod: m.Mod='store') -> Dict[str, Any]:
        """Get the schema of a mod Mod from IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Schema dictionary
        """
        mod_info = self.mod(mod)
        schema = self.store.get_data( mod_info['schema'])
        return schema

    def content(self, mod: m.Mod='store', expand=False) -> Dict[str, Any]:
        """Get the content of a mod Mod from IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Content dictionary
        """
        mod_info = self.mod(mod)
        content = self.store.get_data( mod_info['content'])
        if expand: 
            for file, cid in content.items():
                content[file] = self.store.get_data(cid)
        return content


    def verify(self, mod_info: m.Mod='store') -> bool:
        """Verify the signature of a mod Mod in IPFS.
        
        Args:
            mod: Commune Mod object
        Returns:
            True if signature is valid, False otherwise
        """
        key_address = mod_info['key']
        signature = mod_info['signature']
        mod_info = {k:v for k,v in mod_info.items() if k != 'signature'}
        valid = m.verify(mod_info, signature=signature, address=key_address, mode='str')
        return valid

    def user_keys(self) -> List[str]:
        """List all unique users who have registered mods in IPFS.
        
        Returns:
            List of user addresses
        """
        registry = self.registry()
        users = set()
        for mod_name, cid in registry.items():
            mod_info = self.mod(mod_name)
            users.add(mod_info['key'])
        return list(users)
    def users(self, search=None, **kwargs) -> List[Dict[str, Any]]:
        """List all users who have registered mods in IPFS.
        
        Args:
            search: Optional search term to filter users
        Returns:
            List of user information dictionaries
        """
        user_keys = self.user_keys()
        users_info = []
        for user_key in user_keys:
            if search and search not in user_key:
                continue
            info = self.user_info(user_key)
            users_info.append(info)
        return users_info

    def user_mods(self, user_address: str = None) -> List[str]:
        """List all mods registered by a specific user in IPFS.
        
        Args:
            user_address: Address of the user
        Returns:
            List of mod names   
        """
        if user_address is None:
            user_address = m.key().address
        registry = self.registry()
        user_mods = []
        for mod_name, cid in registry.items():
            mod_info = self.mod(mod_name)
            user_mods.append(mod_info)
        return user_mods

    def user_info(self, user_address: str = None) -> Dict[str, Any]:
        """Get information about a specific user in IPFS.
        
        Args:
            user_address: Address of the user
        Returns:
            Dictionary with user information
        """
        if user_address is None:
            user_address = m.key().address
        mods = self.user_mods(user_address)
        info = {
            'key': user_address,
            'mods': mods,
            'balance': self.balance(user_address)
        }
        return info
        
    def balance(self, user_address: str = None) -> float:
        """Get the balance of a specific user in IPFS.
        
        Args:
            user_address: Address of the user
        Returns:
            Balance as a float
        """
        return 0
