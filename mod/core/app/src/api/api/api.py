import requests
import os
import json
from typing import Optional, Dict, Any, List, Union
from pathlib import Path
import time
import glob
import datetime
import inspect
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
                 'versions',
                 'reg_url',
                 'reg_from_info',
                 'hardware',
                 'servers', 
                 'server_exists',
                 'reg_payload']

    public = True

    def __init__(self, store = 'ipfs', chain='chain', key=None):
        self.store = m.mod(store)()
        self.key = m.key(key)
        self.set_chain(chain)
        self.model = m.mod('model.openrouter')()
        self.registry_path = self.path('registry.json')
        self.executor = m.mod('executor')()
        self.calls_path = self.path('calls')
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
        return bool(self.cid(mod=mod, key=key))

    def mod(self, mod: m.Mod='store', key=None, schema=False, content=False,  expand = False, fns=None,**kwargs) -> Dict[str, Any]:
        """
        get the mod Mod from IPFS.
        """
        cid = self.cid(mod=mod, key=key, default=mod)
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


    devmode = True


    def call_data(self , fn: str = 'model.openrouter/forward',  params: Dict[str, Any] = {}, timeout=1000) -> Dict[str, Any]:
        mod , fn = fn.split('/')
        data = {
            'mod': mod,
            'fn': fn,
            'params': params,       
            'timeout': timeout,  
            'status': 'pending',
            'time': m.time()
        }
        return data

    def future_paths(self):
        return list(self.path2future.keys())

    path2future = {}
    sync_call_loop_thread = None
    def call(self , 
                fn: str = 'api/edit',  
                params: Dict[str, Any] = {}, 
                key='mod', 
                signature=None, 
                url=None,
                sync=False,
                timeout=1000, **extra_params) -> Any:
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
        params = {**params, **extra_params}
        if url != None:
            print(f'Calling {fn} at {url} with params {params}')
            return m.call('api/call', params={'fn': fn, 'params': params, 'key': key, 'signature': signature}, timeout=timeout)
        if self.sync_call_loop_thread is None:
            self.sync_call_loop_thread = m.thread(self.sync_call_loop)
    
        # step 1 get the call data
        data = self.call_data( fn=fn, params=params, timeout=timeout)

        if self.devmode:
            key = m.key(key)
            key_address =  key.address
            signature = key.sign(data, mode='str')
        else:
            key_address = key

        assert self.key.verify(data, signature=signature, address=key_address), "Signature verification failed"
        data['key'] = key_address
        data['signature'] = signature
        data['path'] = path = self.call_path(data)
        m.put(path, data)
        future =  m.submit(self.send_requeest, {'data': data},  timeout=timeout)
        self.path2future[data['path']] = future
        if sync:
            result = future.result()
        return data

    def call_path(self, data): 
        
        path = f'{self.calls_path}/{data["key"]}/{data["mod"]}/{data["time"]}.json'
        return m.relpath(path)

    def call_paths(self):
        return glob.glob(self.calls_path+'/**/*.json', recursive=True)

    def h(self, key=None, mod=None, df=1, features=['mod', 'fn', 'params', 'result', 'status', 'time', 'delta'], n=10) -> List[Dict[str, Any]]:
        paths = self.call_paths()
        calls = []
        for path in paths:
            if key is not None and key not in path:
                continue
            if mod is not None and mod not in path:
                continue
            call = m.get(path)
            if call != None:
                calls.append(call)
    
        calls = sorted(calls, key=lambda x: x['time'], reverse=True)
        calls = m.df(calls)
        calls.sort_values('time', ascending=False, inplace=True)
        calls['time'] = calls['time'].apply(lambda x: datetime.datetime.fromtimestamp(x).strftime('%Y-%m-%d %H:%M:%S'))
        calls = calls[features][:n]
        if df:
            return calls
        else:
            return calls.to_dict(orient='records')

        return calls

    def reset_calls(self):
        for path in self.call_paths():
            print(f'Removing call path: {path}')
            m.rm(path)
        for future in self.path2future.values():
            print(f'Cancelling future -> {future}')
            future.cancel()
        self.path2future = {}
        assert len(self.call_paths()) == 0, "Failed to reset all call paths"
        return True

    def clear_call_paths(self):
        for path in self.path2future.keys():
            print(f'Removing call path: {path}')
            m.rm(path)
            future = self.path2future[path]
            future.cancel()
        self.path2future = {}

    def is_generator(self, obj):
        """
        Is this shiz a generator dawg?
        """
        if not callable(obj):
            result = inspect.isgenerator(obj)
        else:
            result =  inspect.isgeneratorfunction(obj)
        return result

    def send_requeest(self, data:dict) -> Any:
        """
        Send the function call request to the appropriate mod Mod and function.
        """
        path = self.call_path(data)
        data['status'] = 'running'
        m.put(path, data)
        mod = data['mod']
        fn = data['fn']
        params = data['params']
        server_exists = self.server_exists(mod)
        print(f"Sending request to mod: {mod}, fn: {fn}, server_exists: {server_exists}")
        try:
            if server_exists:
                result = m.call(f'{mod}/{fn}', params=params, timeout=data['timeout'])
            else:
                result = getattr(m.mod(mod)(), fn)(**params)
            data['status'] = 'success'
        except Exception as e:
            result = m.detailed_error(e)
            data['status'] = 'error'
        # is generator
        if self.is_generator(result):
            data['result'] = []
            for item in result:
                data['result'].append(item)
                print(item, end='')
                m.put(path, data)
        else:
            data['result'] = result
        data['delta'] = m.time() - data['time']
        data['owner'] = self.key.address
        data['owner_signature'] = self.key.sign(data, mode='str')
        m.put(path, data)
        return data['result']
        
        

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

    def cid(self, mod, key=None, default=None) -> str:
        return  self.registry().get(self.key_address(key), {}).get(mod, default)
    
    def update_local_registry(self, info:dict):
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
        self.update_local_registry(info)
        return info


    def get_info(self, mod='store', key=None, comment=None, collateral=0.0, protocal='mod') -> Dict[str, Any]:
        """
        Register mod Mod data in IPFS.
        """
        current_time = m.time()
        key = self.key_address(key)
        prev_cid = self.cid(mod=mod, key=key)
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
        prev_cid = self.cid(mod=mod, key=key)
        current_time = m.time()
        key = m.key(key)
        if prev_cid == None:
            info = self.get_info(mod=mod, key=key, protocal=protocal, comment=comment)
        else:
            prev_info = self.mod(prev_cid, key=key)
            info = self.get_info(mod=mod, key=key, comment=comment, protocal=protocal)
            info['prev'] = prev_cid
        info['cid'] = self.update_local_registry(info) 
        return info
    def reg_payload(self, mod: str = 'store', key=None, comment=None, collateral=0.0, protocal='mod') -> Dict[str, Any]:
        """
        Generate registration payload without executing registration.
        
        Args:
            mod: Mod str
            key: Key object or address string
            comment: Optional comment about the registration
            collateral: Collateral amount
            protocal: Protocol type
            
        Returns:
            Dictionary with registration payload ready to be signed
        """
        info = self.get_info(mod=mod, key=key, comment=comment, collateral=collateral, protocal=protocal)
        return info
 
    sync_info = {}
    def sync(self, timeout=300,  fns = ['mods', 'balances']) -> Dict[str, Any]:
        t0 = m.time()
        results =  m.wait([ m.future(getattr(self.chain, fn), dict(update=True), timeout=timeout) for fn in fns ], timeout=timeout)
        self.users(update=1)
        t1 = m.time()
        self.sync_info = {'time': t1, 'delta': t1 - t0}
        return self.sync_info

    def sync_call_loop(self, sync_interval=0.2):
        while True:
            time.sleep(sync_interval)
            n_tasks = len(list(self.path2future.values()))
            print("Call sync loop checking futures, n_tasks:", n_tasks)
            if n_tasks == 0:
                continue
            future2path = {future: path for path, future in self.path2future.items()}
            # check completed futures
            for future in m.as_completed(future2path.keys(), timeout=10):
                path = future2path[future]
                try:
                    result = future.result()
                    print(f"Call completed for path: {path}, result: {result}")
                except Exception as e:
                    print(f"Call failed for path: {path}, error: {e}")
                # remove from path2future
                self.path2future.pop(path, None)

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
            self.chain.mods( **kwargs)
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

    def timestamp2utc(self, timestamp:int) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

    def versions(self, mod='store' , key=None, df=False) -> List[Dict[str, Any]]:
        cid = self.cid(key=key, mod=mod)
        result = []       
        if cid != None:
            
            while True:
                info = self.mod(cid, key=key)
                content =  self.get(info['content'])
                comment = content.get('comment', '')
                cid = info['cid']
                prev_cid = info.get('prev', None)
                info = {'cid': cid, 'comment':  comment, 'updated': info['updated']}
                # convert timestamp to readable date
                info['updated'] = self.timestamp2utc(info['updated'])
                result.append(info)
                if prev_cid == None:
                    break
                else:
                    cid = prev_cid
        if df and len(result) > 0:
            return m.df(result)
        return result

    v = versions
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
        versions = self.versions(mod, key=key)
        assert cid in [h['cid'] for h in versions], "Specified CID not found in mod versions"
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
        modinfo = self.update_local_registry(modinfo)
        versions = self.versions(mod, key=key)
        assert cid == versions[0]['cid'], "Setback failed: content CID mismatch"
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
        versions = self.versions(mod, key=key)
        for info in versions:
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
            'balance': self.balance(address)
        }
        return user
        
    user = user

    def edit(self, mod,  query, *extra_query,  key=None,  url=None, **kwargs) -> Dict[str, Any]:
        query = ' '.join(list(map(str,  [query] + list(extra_query))))
        if url != None:
            return self.call('api/edit', params={'mod': mod, 'query': query}, url=url, key=key)
        m.fn('dev/forward')(mod=mod, text=query, safety=False, **kwargs)
        return self.reg(mod=mod, key=key, comment=query)

    def chat(self, text, *extra_texts, mod: str='model.openrouter', stream=False, **kwargs) -> Dict[str, Any]:
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


    def servers(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return m.servers(*args, **kwargs)

    def server_exists(self, name: str) -> bool:
        return name in self.servers()


        

