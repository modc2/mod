import requests
import os
import json
from typing import Optional, Dict, Any, List
from pathlib import Path
import time
import mod as m

class  Registry:

    
    def __init__(self, store = 'ipfs'):
        if not 'ipfs' in m.servers(): 
            m.serve('ipfs')
        self.store = m.mod(store)()

    def mod(self, mod: m.Mod='store', pin=True, schema=False, content=False) -> Dict[str, Any]:
        """Add a mod Mod to IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        cid = self.mod2cid().get(mod, mod)
        mod =  self.store.get_data(cid) if cid else None
        if schema: 
            mod['schema'] = self.store.get_data(mod['schema'])
        if content:
            mod['content'] = self.store.get_data(mod['content'])
        return mod

    
    mod2cid_path = '~/.modchain/mods.json'
    # Register or update a mod in IPFS

    def reg(self, mod = 'store', 
            key=None, 
            comment=None, 
            update=False, 
            forbidden_mods = ['mod'],
            branch='main') -> Dict[str, Any]:
        # het =wefeererwfwefhuwoefhiuhuihewds wfweferfgr frff frrefeh fff
        current_time = m.time()
        key = m.key(key)
        mod2cid = m.get(self.mod2cid_path, {}, update=update)
        prev = mod2cid.get(mod, None)
        cid = self.store.add_mod(mod)
        if prev is None:
           info = {
                   'content': cid,
                   'schema': self.store.add_data(m.schema(mod)),
                   'prev': None, # previous state
                   'name': mod,
                   'created':  current_time,  # created timestamp
                   'updated': current_time, 
                   'key': key.address, 
                   'nonce': 1 # noncf

                   }
        # fam
        else:
            info = self.store.get_data(prev)
            if info['content'] != cid:
                info['prev'] = prev # previous state
                info['content'] = cid
                info['nonce'] = info['nonce'] + 1
                info['updated'] = current_time
                info['schema'] = self.store.add_data(m.schema(mod))
        info['signature'] = key.sign(info, mode='str')
        info_cid = self.store.add_data(info)
        info['cid'] = info_cid # not included in signature
        mod2cid[mod] = info_cid
        m.put(self.mod2cid_path, mod2cid)
        return info # fam fdffffffjferfejrfjoijiojhwefefijh


    def mods(self) -> List[str]:
        """List all registered mods in IPFS.
        
        Returns:
            List of mod names
        """
        mod2cid = self.mod2cid()
        return list(mod2cid.keys())

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
        prev_content = self.store.get_data(self.mod(prev)['content'])
        print(prev_content)
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
        
    def mod2cid(self, update=False) -> Dict[str, str]:
        return m.get(self.mod2cid_path, {}, update=update)

    def regall(self, mods: List[m.Mod]=None, key=None, comment=None, update=False, branch='main') -> Dict[str, Any]:
        mods = mods or m.mods()
        mod2info = {}
        for mod in mods:
            print(f"Registering mod: {mod}")
            info = self.reg(mod, key=key, comment=comment, update=update, branch=branch)
            mod2info[mod] = info
        return mod2info
