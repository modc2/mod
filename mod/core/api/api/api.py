import requests
import os
import json
from typing import Optional, Dict, Any, List, Union
from pathlib import Path
import time
import mod as m

class  Api:

    sync_interval = 20
    sync_delay = 3
    protocal = 'mod'
    folder_path = m.abspath('~/.mod/api')
    endpoints = ['mods',
                 'names', 
                 'reg', 
                 'mod',
                 'get',
                 'users', 
                 'user', 
                 'n', 
                 'balance',
                 'reg_url',
                 'reg_from_info',
                 'hardware',
                 'reg_payload']

    def __init__(self, store = 'ipfs', chain='chain', key=None):
        self.store = m.mod(store)()
        self.key = m.key(key)
        self.set_chain(chain)
        self.model = m.mod('model.openrouter')()
        self.registry_path = self.path('registry.json')
        self.balances()
        self._sync_loop_thread = m.thread(self.sync_loop)

    def set_chain(self, chain='chain', key=None, sync_fns = ['call_with_signature', 'get_signature_payload', 'balances', 'balance']):
        self.chain = m.mod(chain)()
        self.chain.name = chain
        for fn_name in sync_fns:
            setattr(self, fn_name, getattr(self.chain, fn_name))
            
    def is_valid_ipfs_cid(self, cid: str) -> bool:
        """Check if a given string is a valid IPFS CID.
        
        Args:
            cid: IPFS CID string
        Returns:
            True if valid, False otherwise
        """
        if isinstance(cid, str) and len(cid) > 0:
            try:
                self.get(cid)
                return True
            except:
                return False
        return False

    def exists(self, mod: m.Mod='store', key=None) -> bool:
        """
        Check if a mod Mod exists in IPFS.
        """
        return bool(self.modcid(mod=mod, key=key))

    def mod(self, mod: m.Mod='store', key=None, schema=False, content=False,  expand = False, fns=None,**kwargs) -> Dict[str, Any]:
        """
        get the mod Mod from IPFS.
        """
        # if not self.exists(mod=mod, key=key):
        #     self.reg(mod=mod, key=key)
        cid = self.modcid(mod=mod, key=key, default=mod)
        mod =  self.get(cid) if cid else None
        if mod == None:
            raise Exception(f'Mod {mod} not found for key {key}')
        if schema:
            mod['schema'] = self.get(mod['schema'])
        if fns is not None:
            mod['fns'] =fns
        mod['name'] = mod['name'].split('/')[0]
        
        mod['content'] = self.content(mod, expand=expand) if content else mod['content']
        mod['cid'] = cid
        mod['protocal'] = mod.get('protocal', self.protocal)
        self.check_modchain(mod)
        return mod

    def call(self , mod: m.Mod, fn: str, params: Dict[str, Any], time:int, cost:int, signature, **kwargs) -> Any:
        """
        Call a function from a mod Mod in IPFS.
        Args:
            mod: Commune Mod object
            fn: Function name to call
            params: Parameters for the function call
            key: Key object or address string
        Returns:
            Result of the function call
        """
        modinfo = self.mod(mod, schema=True, **kwargs)
        assert fn in modinfo['schema'], f"Function {fn} not found in mod {modinfo['name']}"
        return m.fn(f"{modinfo['name']}/{fn}")(**params)

    def call_payload(self, mod: m.Mod = 'openrouter', fn: str = 'models', params: Dict[str, Any] = {}, time = None, cost = 0, **kwargs) -> Dict[str, Any]:

        payload = {
            'mod': mod,
            'fn': fn,
            'params': params,
            'time': time,
            'cost': cost
        }

        return payload

    def verify_call_payload(self, payload: Dict[str, Any], signature: str, key=None) -> bool:
        key = m.key(key)
        return key.verify(payload, signature, mode='str')

    def test_call(self, mod: m.Mod='openrouter', fn: str='models', params: Dict[str, Any]={}, key=None, **kwargs) -> Any:
        key = m.key(key)
        time = m.time()
        cost = 0
        payload = self.call_payload(mod=mod, fn=fn, params=params, time=time, cost=cost, **kwargs)
        signature = key.sign(payload, mode='str')
        assert self.verify_call_payload(payload, signature, key=key), "Payload verification failed"
        return self.call(mod=mod, fn=fn, params=params, time=time, cost=cost, signature=signature, **kwargs)



    def content(self, mod, key=None, expand=False) -> Dict[str, Any]:
        """Get the content of a mod Mod from IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Content dictionary
        """
        if not isinstance(mod, dict):
            mod = self.mod(mod, key=key)
        else: 
            assert 'content' in mod, "Mod dictionary must contain 'content' key"
        content = self.get(self.get(mod['content'])['data'])
        if expand: 
            for file, cid in content.items():
                content[file] = self.get(cid)
        return content


    def content_commit(self, mod='app', key=None) -> Dict[str, str]:
        return self.get(self.mod(mod, key=key)['content'])

    def verify_mod(self, mod: str, key=None) -> bool:
        return self.mod(mod=mod, key=key)

    
    # Register or update a mod in IPFS
    def key_address(self, key=None):
        key = key or 'mod'
        if isinstance(key, str):
            if self.key.valid_ss58_address(key):
                return key
            else:
                return m.key(key).address
        else:
            return (key or m.key()).address

    def modcid(self, mod, key=None, default=None) -> str:
        key = self.key_address(key)
        return  self.registry().get(key, {}).get(mod, default)
    
    def update_registry(self, info:dict):
        if 'cid' in info:
            cid = info['cid']
        else:
            cid = self.add(info)
        registry = m.get(self.registry_path, {})
        mod = info['name']
        key = info['key']
        if key not in registry:
            registry[key] = {}
        registry[key][mod] = cid
        print(f"Updated registry for mod: {mod}, cid: {cid}")
        m.put(self.registry_path, registry)
        path = self.path('mods')
        mods = m.get(path, [])
        mods.append(mod)
        m.put(path, mods)
        return cid

    def add(self, data):
        return self.store.add(data)
    put = add

    def get(self, cid: str) -> Any:
        return self.store.get(cid)

    def add_content(self, mod: str='store', comment=None) -> Dict[str, str]:        
        file2cid = {}
        mod = mod.lower()
        content = m.content(mod)
        for file,content in content.items():
            cid = self.add(content)
            file2cid[file] = cid
        return self.add({'data': self.add(file2cid), 'comment': comment})
    
    def add_schema(self, mod: str='store') -> str:
        try:
            return self.add(m.schema(mod))
        except Exception as e:
            return self.add({})

    def get_url(self, url: str) -> str:
        url = m.namespace().get(url, None)
        return url

    def reg_url(self, 
                    url: str, 
                    mod=None, 
                    signature = None, 
                    key=None, 
                    collateral=0.0,
                    comment=None, 
                    payload = False,
                    external = False) -> Dict[str, Any]:

        """
        Register a mod Mod from a URL in IPFS.
        Args:
            url: URL to fetch mod data from
            mod:  Mod str
            signature: Optional signature for verification
            key: Key object or address string
            comment: Optional comment about the registration
        Returns:
            Dictionary with registration info
        """
        if 'github.com' in url or 'gitlab.com' in url:
            mod = url.split('/')[-1].split('.git')[0] 
            # assert not m.mod_exists(mod), f'Mod {mod} already exists. Please choose a different mod name or deregister the existing mod first.'
            mod = mod.lower()
            dirpath = m.ext_path
            modpath = os.path.join(dirpath, mod)
            if not os.path.exists(modpath):
                git_cmd = f'git clone --single-branch {url} {modpath}'
                os.makedirs(dirpath, exist_ok=True)
                os.system(git_cmd)
                m.print(f"[✓] Cloned repository from {url} to {modpath}", color="green")
        else:
            raise ValueError(f'Unsupported URL for reg_from_url: {url}')
        m.ext_tree(update=1)
        info = self.get_info(mod=mod, key=key, comment=comment, collateral=collateral)
        if payload:
            return info
        if signature == None:
            key = m.key(key)
            info['key'] = key.address
            info['signature'] = key.sign(info, mode='str')
        return self.reg_from_info(info)

    def reg_from_info(self, info: Dict[str, Any]) -> Dict[str, Any]:
        # assert self.verify_mod(info)
        self.update_registry(info)
        return info


    def get_info(self, mod='store', key=None, comment=None, collateral=0.0, protocal='mod') -> Dict[str, Any]:
        """
        Register mod Mod data in IPFS.
        """
        current_time = m.time()
        key = self.key_address(key)
        prev_cid = self.modcid(mod=mod, key=key)
        prev_info = self.mod(prev_cid, key=key) if prev_cid else {}
        content_cid = self.add_content(mod=mod, comment=comment)
        prev_content_cid = prev_info.get('content', None)
        if content_cid == str(prev_content_cid):
            prev_info.pop('cid', None)
            prev_info['collateral'] = collateral
            prev_info['protocal'] =  prev_info.get('protocal', protocal)
            return prev_info  # No changes, return existing info
        else:
            info = {
                'content': content_cid,
                'schema': self.add_schema(mod),
                'prev': prev_cid, # previous state
                'created':  prev_info.get('created', current_time),  # created timestamp
                'updated': current_time, 
                'name': prev_info.get('name', mod),  # mod name
                'collateral': collateral,
                'key': prev_info.get('key', key),
                'url': self.get_url(mod),
                'protocal': protocal,
            }
        return info

    def reg(self, 
                mod : Union[str, dict] = 'store', 
                key=None,  
                comment=None, 
                signature=None, 
                update=False, 
                protocal='mod'
                ) -> Dict[str, Any]:
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
        if isinstance(mod, dict):
            return self.reg_from_info(mod)
        # het =wefeererwfwefhuwoefhiuhuihewds wfweferfgr frff frrefeh fff
        prev_cid = self.modcid(mod=mod, key=key)
        current_time = m.time()
        key = m.key(key)
        if prev_cid == None:
            info = self.get_info(mod=mod, key=key, protocal=protocal, comment=comment)
        else:
            prev_info = self.mod(prev_cid, key=key)
            info = self.get_info(mod=mod, key=key, comment=comment, protocal=protocal)
            info['prev'] = prev_cid
        info['cid'] = self.update_registry(info) 
        return info 
 
    sync_info = {}
    def sync(self, timeout=300,  fns = ['mods', 'balances']) -> Dict[str, Any]:
        t0 = m.time()
        results =  m.wait([ m.future(getattr(self.chain, fn), dict(update=True), timeout=timeout) for fn in fns ], timeout=timeout)
        self.users(update=1)
        t1 = m.time()
        self.sync_info = {'time': t1, 'delta': t1 - t0}
        return self.sync_info

    def time_since_last_sync(self) -> int:
        return m.time() - self.last_sync

    def sync_loop(self):
        if self.sync_delay > 0:
            m.print(f"Initial delay of {self.sync_delay} seconds before starting sync loop...", color="yellow")
            time.sleep(self.sync_delay)
        while True:
            try:
                self.sync()
                m.print("Sync completed", color="green")
            except Exception as e:
                m.print(f"Error during sync: {e}", color="red")
            m.print(f"Waiting for {self.sync_interval} seconds before next sync...", color="yellow")
            time.sleep(self.sync_interval)

    def path(self, path:str) -> str:
        """Get content from a specific path in IPFS.
        
        Args:
        """
        return self.folder_path + '/' + path


    def mods(self, network=None, search=None, key=None, update=False,  **kwargs) -> List[str]:
        """List all registered mods in IPFS.
        
        Returns:
            List of mod names
        """
        path = self.path('mods')
        mods = m.get(path, None, update=update)
  
        if mods == None: 
            self.chain.mods(update=update, **kwargs)
            registry = self.registry()
            key = self.key_address(key)
            if key != 'all':
                mods =  [self.mod(k, key=key) for k in registry.get(key, {}).keys()]
            else:
                mods = []
                for user_key, user_mods in registry.items():
                    for mod in user_mods.keys():
                        mods.append(self.mod(mod, key=user_key))  
            mods =  list(map(self.check_modchain, mods))
            m.put(path, mods)
            
        if search != None:
            mods = [m for m in mods if search in m['name']]
        if network != None:
            mods = [m for m in mods if m.get('network', 'local') == network]

        mods = [m for m in mods if isinstance(m, dict)]
        return mods

    def check_modchain(self, mod:dict):
        """
        Check and update mod Mod chain information.
        """
        chain_registry = self.chain.registry(key=mod['key'])
        if mod['name'] in chain_registry:
            mod['network'] = self.chain.name
            mod['id'] = int(chain_registry[mod['name']])
        else:
            mod['network'] = 'local'
        return mod

    def history(self, mod='store' , key=None, df=False) -> List[Dict[str, Any]]:
        modid = self.modcid(key=key, mod=mod)
        history = []       
        if modid != None:
            info = self.mod(modid)
            while True:
                history.append(info)
                if info['prev'] == None:
                    break
                info = self.mod(info['prev'])
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
        
    def registry(self,  key='all', update=False) -> Dict[str, str]:
        """
        Get the mod registry from IPFS.
        """
        registry =  m.get(self.registry_path, {}, update=update)
        if key != 'all':
            registry = registry.get(self.key_address(key), {})
        return registry
            
    def clear(self) -> bool:
        m.put(self.registry_path, {})
        self.store._rm_all_pins()
        return {'status': 'registry cleared'}

    def regall(self, mods: List[m.Mod]=None, key=None, comment=None, update=False) -> List[str]:
        mods = mods or m.mods()
        mod2info = {}
        future2mod = {}
        for mod in mods:
            print(f"Registering mod: {mod}")
            params = dict(comment=comment, key=key, mod=mod)
            future = m.future(self.reg, params)
            future2mod[future] = mod
        try:
            for future in m.as_completed(future2mod):
                mod = future2mod[future]
                info = future.result()
                mod2info[mod] = info
                print(f"Registered mod: {mod}, cid: {info['cid']}")
        except TimeoutError as e:
            print(f"Failed to register mod: {mod}, error: {e}")

        return mod2info

    def schema(self, mod: m.Mod='store', key=None) -> Dict[str, Any]:
        """Get the schema of a mod Mod from IPFS.
        
        Args:
            mod: Commune Mod object
        Returns:
            Schema dictionary
        """
        if not isinstance(mod, dict):
            mod  = self.mod(mod, key=key, schema=False)
        else:
            assert 'schema' in mod, "Mod dictionary must contain 'schema' key"
        return self.get(mod['schema'])

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
        content = self.content(modinfo)
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
        modinfo = self.update_registry(modinfo)
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
        registry = self.registry()
        key = self.key_address(key)
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
        del registry[key][mod]
        m.put(self.registry_path, registry)
        return True      

    def user_keys(self, key=None) -> List[str]:
        """
        List all unique users who have registered mods in IPFS.
        """
        return list(self.registry().keys())

    def users(self, search=None, update=False,**kwargs) -> List[Dict[str, Any]]:
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
            info = self.user(user_key, update=update)
            users_info.append(info)
        return users_info

    def user_mods(self, key: str = None) -> List[str]:
        """List all mods registered by a specific user in IPFS.
        
        Args:
            user_address: Address of the user
        Returns:
            List of mod names   
        """
        key = self.key_address(key)
        registry = self.registry(key)
        mods = []
        for mod in list(registry.keys()):
            info = self.mod(mod, key=key)
            if info != None:
                mods.append(info)
        return mods


    def path(self, path:str) -> str:
        """Get content from a specific path in IPFS.
        
        Args:
        """
        return self.folder_path + '/' + path

    def user(self, address: str = None, update=False) -> Dict[str, Any]:
        """Get information about a specific user in IPFS.
        
        Args:
            user_address: Address of the user
        Returns:
            Dictionary with user information
        """
        address = self.key_address(address)
        path = self.path('users/' + address)
        mods = self.user_mods(address)
        user = {
            'key': address,
            'mods': mods,
            'balance': self.balance(address, update=update)
        }
        return user
        
    user = user

    def edit(self, *query,  mod: str='app',  key=None, **kwargs) -> Dict[str, Any]:
        text = ' '.join(list(map(str, query)))
        m.fn('dev/')(mod=mod, text=text, safety=False, **kwargs)
        return self.reg(mod=mod, key=key, comment=text)

    def chat(self, text, *extra_texts, mod: str='model.openrouter',**kwargs) -> Dict[str, Any]:
        return self.model.forward(' '.join([text] + list(extra_texts)), stream=stream, **kwargs)
    
    def models(self, search=None, mod: str='model.openrouter', **kwargs) -> List[Dict[str, Any]]:
        return self.model.models(search=search, **kwargs)

    def hardware(self) -> Dict[str, Any]:
        hardware =  m.hardware() 
        return hardware

    def __delete__(self):
        self._sync_loop_thread.kill()
        del self._sync_loop_thread

    def stats(self):
        return m.df(self.mods())[['name', 'key', 'created', 'updated', 'collateral', 'network', 'cid']]

    def ensure_env(self):
        m.serve('ipfs.node') if not m.server_exists('ipfs.node') else None
        m.print("IPFS node is running", color="green")