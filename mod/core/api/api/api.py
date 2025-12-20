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

    port = 8000
    sync_interval = 0.1
    sync_delay = 3
    protocal = 'mod'
    folder_path = m.abspath('~/.mod/api')
    endpoints = [
                'mods',
                'names', 
                'reg', 
                'mod',
                'versions',
                'history',
                'call',
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
                'reg_payload'
                 ]

    def __init__(self, store = 'ipfs', chain='chain', key=None, auth='auth.jwt'):
        self.set_store(store)
        self.key = m.key(key)
        self.model = m.mod('model.openrouter')()
        self.registry_path = self.path('registry.json')
        self.executor = m.mod('executor')()
        self.calls_path = self.path('calls')
        self._sync_loop_thread = None
        self.auth = m.mod(auth)()

    @property
    def store(self):
        if not hasattr(self, '_store'):
            self._store = m.mod(self._store_path)()
        return self._store

    def set_store(self, store):
        # set the store mod
        self._store_path = store
        return {'store': self._store_path}
            
    def is_valid_cid(self, cid: str) -> bool:
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
        if content:
            mod['content'] = self.content(cid, expand=expand)
        mod['cid'] = cid
        mod['protocal'] = mod.get('protocal', self.protocal)
        if 'version' not in mod:
            # get the history and set the version to the length of the history
            history = self.history(mod=mod['name'], key=mod['key'], df=0)
            mod['version'] = len(history) if history is not None else 0
        self.check_modchain(mod)
        return mod


    devmode = True


    def task_data(self , fn: str = 'model.openrouter/forward',  params: Dict[str, Any] = {}, timeout=1000) -> Dict[str, Any]:
        return  {
            'fn': fn,
            'params': self.put(params),       
            'timeout': timeout,  
            'status': 'pending',
            'time': m.time()
        }

    def future_paths(self):
        return list(self.path2future.keys())

    path2future = {}

    def resolve_fn(self, fn):
        if not '/' in fn:
            fn = 'api/' + fn
        return fn
    def call(self , 
                fn: str = 'api/edit',  
                params: Dict[str, Any] = {}, 
                key='api', 
                signature=None, 
                api=None,
                sync=False,
                wait_for_running =False,
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
        fn = self.resolve_fn(fn)
        params = {**params, **extra_params}
        if api != None:
            remote_params = {'fn': fn, 'params': params, 'key': key, 'signature': signature, 'api': None}
            return m.fn('client/call')('api/call', params=remote_params, timeout=timeout)
        if self._sync_loop_thread is None :
            self._sync_loop_thread = m.thread(self.sync_loop)
        task = self.task_data( fn=fn, params=params, timeout=timeout)
        if self.devmode:
            key = m.key(key)
            key_address =  key.address
            signature = key.sign(task, mode='str')
        else:
            key_address = key
            assert signature is not None, "Signature must be provided in non-devmode"
        assert self.key.verify(task, signature=signature, address=key_address), "Signature verification failed"
        task['fn'] = fn
        task['key'] = key_address
        task['signature'] = signature
        task['path'] = self.task_path(task)
        m.put(task['path'], task)
        future =  m.submit(self.run_task, task ,  timeout=timeout)
        self.path2future[task['path']] = future
        if sync:
            return future.result()
        return task

    def run_task(self, **task:dict) -> Any:
        """
        Send the function call request to the appropriate mod Mod and function.
        """
        task['status'] = 'running'
        assert '/' in task['fn'], "Function name must be in the format 'mod/fn'"
        mod, fn =  task['fn'].split('/', 1)
        path = task['path']
        params = params = self.get(task['params']) if isinstance(task['params'], str) else task['params']
        m.put(path, task)
        server_exists = bool('api' != mod and self.server_exists(mod))
        try:
            result = m.call(task['fn'], params=params, timeout=task['timeout']) if server_exists else m.fn(task['fn'])(**params)
        except Exception as e:
            result = m.detailed_error(e)
        task['status'] = 'error' if isinstance(result, dict) and 'error' in result else 'success'
        if self.is_generator(result):
            task['result'] = []
            for item in result:
                task['result'].append(item)
                print(item, end='')
                m.put(path, task)
        else:
            task['result'] = result
        task['delta'] = m.time() - task['time']
        task['server'] = self.auth.headers(task, key=self.key)
        task['result'] = self.put(task['result'])
        task['cid'] = self.put(task)
        m.put(path, task)
        return task['result']
        
    def task_path(self, data): 

        path = f'{self.calls_path}/{data["key"]}/{data["fn"]}/{data["time"]}.json'
        return m.relpath(path)

    def call_paths(self):
        return glob.glob(self.calls_path+'/**/*.json', recursive=True)

    def history(self, key=None, mod=None, df=1, features=['fn', 'status', 'cid' ], n=10) -> List[Dict[str, Any]]:
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
        if len(calls) == 0:
            return calls
        else:
            calls.sort_values('time', ascending=False, inplace=True)
            calls['time'] = calls['time'].apply(lambda x: datetime.datetime.fromtimestamp(x).strftime('%Y-%m-%d %H:%M:%S'))
            calls = calls[:n]
        if df:
            calls = calls[features]
            return calls
        else:
            return calls.to_dict(orient='records')
        return calls
    
    h = history

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

    def call_data(self, fn: str = 'models', params: Dict[str, Any] = {}, time = None, cost = 0, **kwargs) -> Dict[str, Any]:

        payload = {
            'fn': fn,
            'params': params,
            'time': time,
            'cost': cost, 
            'key': self.key.address,
        }

        return payload

    def verify_call_data(self, payload: Dict[str, Any], signature: str, address=None) -> bool:
        return m.verify(payload, signature, address,  mode='str')

    def test_call(self, mod: m.Mod='openrouter', fn: str='models', params: Dict[str, Any]={}, key=None, **kwargs) -> Any:
        key = m.key(key)
        time = m.time()
        cost = 0
        payload = self.call_data(mod=mod, fn=fn, params=params, time=time, cost=cost, **kwargs)
        signature = key.sign(payload, mode='str')
        assert self.verify_call_data(payload, signature, key.address), "Payload verification failed"
        return self.call(fn= mod + '/' + fn, params=params, time=time, cost=cost, signature=signature, **kwargs)

    def content(self, mod, key=None, expand=False,  depth=None, h=False) -> Dict[str, Any]:
        """Get the content of a mod Mod from IPFS.
        
        Args:
            mod: Commune Mod object
            
        Returns:
            Content dictionary
        """

        if self.is_valid_cid(mod):
            content_cid = mod
        elif not isinstance(mod, dict):
            content_cid = self.mod(mod, key=key)['content']
        else: 
            assert 'content' in mod, "Mod dictionary must contain 'content' key"
        content = self.get(self.get(content_cid)['data'])
        if expand: 
            for file, cid in content.items():
                content[file] = self.get(cid)
        if h: 
            return self.hc(content)
        return content


    def hc(self, content:Dict[str, Any], flatten=False) -> Dict[str, Any]:
        """Get a human-readable version of the content dictionary.
        
        Args:
            content: Content dictionary
        Returns:
            Human-readable content dictionary
        """

        new_dict = {}
        for file, cid in content.items():
            subfiles = file.split('/')
            self.dict_put(subfiles, cid, new_dict)
        return self.get_folder_cid(new_dict)

    def sort_recursive_dict(self, d:Dict[str, Any]) -> Dict[str, Any]:
        """Recursively sort a nested dictionary by keys.
        
        Args:
            d: Dictionary to sort
        Returns:
            Sorted dictionary
        """
        sorted_dict = {}
        for key in sorted(d.keys()):
            if isinstance(d[key], dict):
                sorted_dict[key] = self.sort_recursive_dict(d[key])
            else:
                sorted_dict[key] = d[key]
        return sorted_dict


    def get_folder_cid(self, folder_content: dict) -> str:
        """Get the CID of a folder in IPFS.
        
        Args:
            folder_path: Path to the folder
        Returns:
            CID of the folder
        """
        is_single_depth_dict = lambda v: all(isinstance(v, str) for v in folder_content.values())
        new_folder_content = {}
        for file, content in folder_content.items():
            if isinstance(content, dict)  and len(content) > 0:
                if is_single_depth_dict(content):
                    new_folder_content[file+'/'] = self.put(content)
                else:
                    new_folder_content[file+'/'] = self.put(self.get_folder_cid(content))
            else:
                new_folder_content[file] = content
        return new_folder_content

    def dict_put(self,  k_list, v, d:Dict[str, Any]):
        """Put a value into a nested dictionary using a list of keys."""
        if len(k_list) == 0:
            return v
        key = k_list[0]
        if key not in d:
            d[key] = {}
        d[key] = self.dict_put(k_list[1:], v, d[key])
        return d

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
    
    def update_registgry(self, info:dict):
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

    def put(self, data):
        return self.store.add(data)
    add = put
    
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
        self.update_registgry(info)
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
        info['cid'] = self.update_registgry(info) 
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
 

    def sync_loop(self):
        while True:
            time.sleep(self.sync_interval)
            self.sync()

    def sync(self):
        n_tasks = len(list(self.path2future.values()))
        if n_tasks == 0:
            print("No tasks to sync.")
            return
        else:
            print(f"Syncing {n_tasks} tasks...")
            future2path = {future: path for path, future in self.path2future.items()}
        # check completed futures
        for future in m.as_completed(future2path.keys(), timeout=10):
            path = future2path.pop(future)
            try:
                print(f"Result({path})")
                result = future.result()
            except Exception as e:
                print(f"Error in future for path {path}: {e}")
                pass
            # remove from path2future
            self.path2future.pop(path, None)

    def time_since_last_sync(self) -> int:
        return m.time() - self.last_sync

    def path(self, path:str) -> str:
        """Get content from a specific path in IPFS.
        
        Args:
        """
        return self.folder_path + '/' + path

    def mods(self, network=None, search=None, key=None, update=False,  **kwargs) -> List[str]:
        """
        List all registered mods in IPFS.
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

    @property
    def chain(self):
        if not hasattr(self, '_chain'):
            self._chain = m.mod('chain')()
            self._chain.name = 'chain'
            sync_fns = ['balances', 'balance']
            for fn_name in sync_fns:
                setattr(self, fn_name, getattr(self._chain, fn_name))
        return self._chain

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

    def versions(self, mod='store' , key=None, df=False, n=None, update=False, max_age=100) -> List[Dict[str, Any]]:

        key_address = self.key_address(key)
        cache_path = self.path(f'versions/{key_address}/{mod}.json')
        result = m.get(cache_path, None, update=update, max_age=max_age)
        if result is None:
            cid = self.cid(key=key, mod=mod)
            result = []   

            if cid != None:
                while True:
                    info = self.mod(cid, key=key)
                    content =  self.get(info['content'])
                    prev_cid = info.get('prev', None)
                    result.append({'cid': info['cid'], 'comment':  content.get('comment', ''), 'updated': self.timestamp2utc(info['updated']) })
                    if prev_cid == None:
                        break
                    else:
                        cid = prev_cid
            if len(result) > 0:
                result =  m.df(result)
                result.sort_values('updated', ascending=False, inplace=True)
                result = result[:n]
                if not df:
                    result = result.to_dict(orient='records')
            m.put(cache_path, result)
        return result

    v = versions
    def txs(self, mod='store', limit=10,  update=False) -> List[Dict[str, Any]]:
        return m.txs(mod=mod, limit=limit, update=update)
        
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
        m.rm(mod)
        for file, file_content in content.items():
            filepath = os.path.join(dirpath, file)
            write_files.append(filepath)
            m.put_text(filepath, self.get(file_content))     
        modinfo = self.update_registgry(modinfo)
        versions = self.versions(mod, key=key)
        assert cid == versions[0]['cid'], "Setback failed: content CID mismatch"
        return {
            'mod': mod,
            'write':write_files,
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

    def edit(self, mod,  query, *extra_query,  key=None,  api=None, **kwargs) -> Dict[str, Any]:
        query = ' '.join(list(map(str,  [query] + list(extra_query))))
        if api != None:
            return self.call('api/edit', params={'mod': mod, 'query': query}, api=api, key=key)
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
        if not self.server_exists('ipfs'):
            m.serve('ipfs')
            m.print("IPFS node is running", color="green")

    def servers(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return m.servers(*args, **kwargs)

    def server_exists(self, name: str) -> bool:
        return name in self.servers()


        

