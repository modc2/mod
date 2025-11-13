import requests
import os
import json
from typing import Optional, Dict, Any, List
from pathlib import Path
import time
import mod as m

class  Api:
    endpoints = ['mods', 'names', 'reg', 'mod', 'users', 'user_info', 'n']

    def __init__(self, store = 'ipfs', chain='chain', key=None):
        self.store = m.mod(store)()
        self.key = m.key(key)
        self.chain = m.mod(chain)()

    def exists(self, mod: m.Mod='store', key=None) -> bool:
        """Check if a mod Mod exists in IPFS.
        
        Args:
            mod: Commune Mod object
            key: Key object or address string
        Returns:
            True if mod exists, False otherwise
        """
        cid = self.registry(key=key).get(mod, None)
        return bool(cid)

    def mod(self, mod: m.Mod='store', key=None, schema=False, content=False, **kwargs) -> Dict[str, Any]:
        """Add a mod Mod to IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        
        cid = self.registry(key=key).get(mod, mod)
        mod =  self.get(cid) if cid else None
        if schema: 
            mod['schema'] = self.get(mod['schema'])
        if content:
            mod['content'] = self.get(self.get(mod['content'])['data'])
            for file, cid in mod['content'].items():
                mod['content'][file] = self.get(cid)
        mod['cid'] = cid
        return mod

    
    registry_path = '~/.mod/api/registry.json'
    # Register or update a mod in IPFS

    def get_address(self, key=None):
        if isinstance(key, str):
            if self.key.valid_ss58_address(key):
                return key
            else:
                key = m.key(key)
        else:
            return (key or m.key()).address

    def modcid(self, mod, key=None, update=False):
        key = self.get_address(key)
        registry = m.get(self.registry_path, {}, update=False)
        return  registry.get(key, {}).get(mod, None)
    
    def update_registry(self, mod:str, info:dict):
        # assert self.verify(info)
        if 'cid' in info:
            cid = info['cid']
        else:
            cid = self.add(info)
        registry = m.get(self.registry_path, {})
        key = info['key']
        if key not in registry:
            registry[key] = {}
        registry[key][mod] = cid
        print(f"Updated registry for mod: {mod}, cid: {cid}")
        m.put(self.registry_path, registry)
        return cid

    def add_mod(self, mod: m.Mod) -> Dict[str, Any]:
        """Add a mod Mod to IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Dictionary with IPFS hash and other metadata
        """
        file2cid = {}
        content = m.content(mod)
        for file,content in content.items():
            cid = self.add(content)
            file2cid[file] = cid
        return self.add(file2cid)
    def add(self, data):
        return self.store.add(data)

    def get(self, cid: str) -> Any:
        return self.store.get(cid)

    def add_content(self, mod: str='store', comment=None) -> Dict[str, str]:
        return self.add({'data': self.add_mod(mod), 'comment': comment, 'time': m.time()})
    
    def add_schema(self, mod: str='store') -> str:
        return self.add(m.schema(mod))

    def get_url(self, url: str) -> str:
        url = m.namespace().get(url, None)
        return url


    def reg(self, mod = 'store',  key=None,  comment=None,  update=False) -> Dict[str, Any]:
        """
        Register or update a mod Mod in IPFS.
        Args:
            mod:  Mod str
            key: Key object or address string
            comment: Optional comment about the registration
            update: Whether to force update from IPFS
        Returns:
            Dictionary with registration info

        """
        # het =wefeererwfwefhuwoefhiuhuihewds wfweferfgr frff frrefeh fff
        prev_cid = self.registry(key=key).get(mod, None)
        current_time = m.time()
        key = m.key(key)
        if prev_cid == None:
            info = {
                'content': self.add_content(mod, comment=comment),
                'schema': self.add_schema(mod),
                'prev': prev_cid, # previous state
                'name': mod,
                'created':  current_time,  # created timestamp
                'updated': current_time, 
                'key': key.address,
                'url': self.get_url(mod),
            }
            info['signature'] = key.sign(info, mode='str')
            info['cid'] = self.update_registry(mod, info)
        else:
            info = self.mod(prev_cid, key=key)
            info.pop('cid', None)
            assert key.address == info['key'], f'Key mismatch {key.address} != {info["key"]}'
            content_cid = self.add_content(mod, comment=comment)
            old_content_cid = info['content']
            if content_cid == old_content_cid:
                return info  # No changes, return existing info
            else:
                new_info = {
                    'content': content_cid,
                    'schema': self.add_schema(mod),
                    'prev': prev_cid, # previous state
                    'updated': current_time, 
                    'url': self.get_url(mod),
                }
                info = {**info, **new_info}
                info['signature'] = key.sign(info, mode='str')
                info['cid'] = self.update_registry(mod, info)
        
        return info 

    def mods(self, search=None, key='all', **kwargs) -> List[str]:
        """List all registered mods in IPFS.
        
        Returns:
            List of mod names
        """
        registry = self.registry(key=key, search=search)
        if key != 'all':
            return [self.mod(k, key=key) for k in registry.keys()]
        else:
            mods = []
            for user_key, user_mods in registry.items():
                for mod in user_mods.keys():
                    mods.append(self.mod(mod, key=user_key))
            return mods


    def names(self, search=None, key=None, **kwargs) -> List[str]:
        mods = list(self.registry(search=search, key=key, **kwargs).keys())
        return mods

    def is_valid_cid(self, cid: str) -> bool:
        """Check if a given string is a valid IPFS CID.
        
        Args:
            cid: IPFS CID string
        Returns:
            True if valid, False otherwise
        """
        try:
            self.get(cid)
            return True
        except:
            return False

    def history(self, mod='store', features=['time', 'comment'] , key=None, df=False) -> List[Dict[str, Any]]:

        history = []        
        if mod not in registry:
            return history
        info = self.mod(mod, key=key)
        while True:
            history.append(info)
            if info['prev'] == None:
                break
            info = self.mod(info['prev'])
        if df:
            return m.df(history)[features]
        return history
    h = history
    def txs(self, mod='store', limit=10,  update=False) -> List[Dict[str, Any]]:
        return m.txs(mod=mod, limit=limit, update=update)

    def diff(self, mod = 'store', update=False) -> Dict[str, Any]:
        mod = self.mod(mod)
        prev = mod.get('prev', None)
        print(f"Getting diff for mod: {mod}, prev: {prev}")
        content_cid = self.mod(prev)['content']['data']
        prev_content = self.get(content_cid)
        current_content = self.get(mod['content'])
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
        
    def registry(self,  key=None, search=None, update=False) -> Dict[str, str]:
        user2registry =  m.get(self.registry_path, {}, update=update)
        filter_registry = lambda r: {k:v for k,v in r.items() if (search == None or search in k)}
        if key == 'all':
            registry = {}
            for user, user_registry in user2registry.items():
                registry[user] = filter_registry(user_registry)
            return registry
        else:
            registry = user2registry.get(self.get_address(key), {})
            registry = filter_registry(registry)
            return registry
            
    def clear(self) -> bool:
        m.put(self.registry_path, {})
        self.store._rm_all_pins()
        return {'status': 'registry cleared'}


    def dereg(self, mod = 'store', key=None) -> bool:
        registry = self.registry(key=key)
        cid = registry.get(mod)
        mod_data = self.get(cid)
        content_cid = mod_data['content']
        schema_cid = mod_data['schema']
        content_map = self.get(content_cid)
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
            try:
                info = self.reg(mod, key=key, comment=comment)
                mod2info[mod] = info
            except:
                print(f'Failed {mod}')
        return mod2info


    def schema(self, mod: m.Mod='store') -> Dict[str, Any]:
        """Get the schema of a mod Mod from IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Schema dictionary
        """
        mod_info = self.mod(mod)
        schema = self.get( mod_info['schema'])
        return schema

    def get_content(self, cid: str, expand=True) -> Dict[str, Any]:
        """Get the content of a mod Mod from IPFS using its CID.
        
        Args:
            cid: Content CID
            expand: Whether to expand file contents
        """
        content = self.get(cid)
        if expand:
            for file, file_cid in content.items():
                content[file] = self.get(file_cid)
        return content

    def verify_mod(self, mod: m.Mod='store', key=None) -> bool:
        """Verify the signature of a mod Mod in IPFS.
        
        Args:
            mod: Commune Mod object
            key: Key object or address string
        Returns:
            True if signature is valid, False otherwise
        """

        mod_info = self.mod(mod, key=key)
        key_address = mod_info['key']
        signature = mod_info['signature']
        mod_info = {k:v for k,v in mod_info.items() if k not in ['signature', 'cid']}
        valid = m.verify(mod_info, signature=signature, address=key_address, mode='str')
        return valid

    def verify_history(self, mod:str='store', key=None) -> bool:
        """Verify the entire history of a mod Mod in IPFS.
        
        Args:
            mod: Commune Mod object
            key: Key object or address string
        Returns:
            True if entire history is valid, False otherwise
        """
        history = self.history(mod, key=key)
        for info in history:
            key_address = info['key']
            signature = info['signature']
            mod_info = {k:v for k,v in info.items() if k not in ['signature', 'cid']}
            valid = m.verify(mod_info, signature=signature, address=key_address, mode='str')
            if not valid:
                return False
        return True

    def setback(self, mod:str, cid:str , key=None ) -> Dict[str, Any]:
        """
        Setback a mod Mod to a previous CID in IPFS.
        Args:
            mod: Commune Mod object
            cid: Target CID to setback to
            key: Key object or address string
        """
        modinfo = self.mod(cid, key=key)
        history = self.history(mod, key=key)
        assert cid in [h['cid'] for h in history], "Specified CID not found in mod history"
        content = self.get_content(self.get( modinfo['content'])['data'])
        dirpath = m.dp(mod)
        write_files= []
        for file, file_content in content.items():
            filepath = os.path.join(dirpath, file)
            write_files.append(filepath)
            m.put_text(filepath, file_content)     
        delete_files = []
        for file in m.files(dirpath):
            filepath = os.path.join(dirpath, file)
            if filepath not in write_files:
                delete_files.append(filepath)
                os.remove(filepath)
                filepath_dir = os.path.dirname(filepath)
                if len(os.listdir(filepath_dir)) == 0:
                    os.rmdir(filepath_dir)
                m.print(f"[✓] Deleted file: {filepath}", color="yellow")
        modinfo = self.update_registry(mod, modinfo)
        history = self.history(mod, key=key)
        assert cid == history[0]['cid'], "Setback failed: content CID mismatch"
        return {
            'mod': mod,
            'write':write_files,
            'delete': delete_files
        }  

    def rm_mod(self, mod: m.Mod='store', key=None) -> bool:
        """Remove a mod Mod from IPFS.
        
        Args:
            mod: Commune Mod object
        Returns:
            True if removal was successful, False otherwise
        """
        registry = self.registry(key=key)
        history = self.history(mod, key=key)
        for info in history:
            cid = info['cid']
            content_info_cid = info['content']
            content_cid = self.get(content_info_cid)['data']
            schema_cid = info['schema']
            content_map = self.get(content_cid)
            for file, file_cid in content_map.items():
                self.store.rm(file_cid)
            self.store.rm(content_info_cid)
            self.store.rm(schema_cid)
            self.store.rm(cid)

        del registry[mod]
        m.put(self.registry_path, registry)
        return True      

    def content(self, mod: m.Mod='store', expand=False) -> Dict[str, Any]:
        """Get the content of a mod Mod from IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Content dictionary
        """
        mod_info = self.mod(mod)
        content = self.get( mod_info['content'])
        if expand: 
            for file, cid in content.items():
                content[file] = self.get(cid)
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
        mod_info = {k:v for k,v in mod_info.items() if k not in ['signature', 'cid']}
        valid = m.verify(mod_info, signature=signature, address=key_address, mode='str')
        return valid

    def user_keys(self, key=None) -> List[str]:
        """List all unique users who have registered mods in IPFS.
        
        Returns:
            List of user addresses
        """
        return list(self.registry('all').keys())

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

    def user_mods(self, key: str = None) -> List[str]:
        """List all mods registered by a specific user in IPFS.
        
        Args:
            user_address: Address of the user
        Returns:
            List of mod names   
        """
        key = self.get_address(key)
        registry = self.registry(key)
        mods = []
        for mod in list(registry.keys()):
            mods.append(self.mod(mod, key=key))
        return mods

    def user_info(self, key: str = None) -> Dict[str, Any]:
        """Get information about a specific user in IPFS.
        
        Args:
            user_address: Address of the user
        Returns:
            Dictionary with user information
        """
        key = self.get_address(key)
        mods = self.user_mods(key)
        info = {
            'key': key,
            'mods': mods,
            'balance': self.balance(key)
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

    def edit(self, *query,  mod: str='app',  key=None) -> Dict[str, Any]:
        dev = m.mod('dev')()
        text = ' '.join(list(map(str, query)))
        dev.forward(mod=mod, text=text, safety=False)
        return self.reg(mod=mod, key=key, comment=text)

    def chat(self, text, *extra_texts, key=None, mod: str='model.openrouter', stream=False) -> Dict[str, Any]:
        return m.mod(mod)().forward(' '.join([text] + list(extra_texts)), stream=stream)
