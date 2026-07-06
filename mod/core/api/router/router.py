import requests
import os
import re
import json
from typing import Optional, Dict, Any, List, Union
from pathlib import Path
import time
import glob
import datetime
import inspect
import shutil
import mod as m
from mod.core.server.executor.worker import SandboxWorker, WorkerPool

# ── Security constants ──
MAX_TASK_TIMEOUT = 300  # 5 minute cap on user-supplied timeouts
_SAFE_PATH_RE = re.compile(r'^[a-zA-Z0-9_/.-]+$')  # no traversal characters

class Router:
    threads = {}
    cid2future = {}
    cid2worker = {}  # cid -> True for sandbox-executed tasks
    folder_path = m.abspath('~/.mod/api/router')
    intervals = {
        'sync_tasks': 10,
        # 'sync_ious': 60,
    }

    def __init__(self, store=None, key=None, auth='auth', chain='chain',
                 min_workers: int = 1, max_workers: int = 10):
        self.store = store or m.config('api').get('store', 'localfs')
        self.key = m.key(key )
        self.tasks_path = self.path('tasks')
        self.chain = m.mod(chain)()
        self.auth = m.mod(auth)()
        self.pool = WorkerPool(min_workers=min_workers, max_workers=max_workers)
        self.pool.start()
        # Legacy alias
        self.sandbox = self.pool
        self.threads['sync'] = m.thread(self.sync_loop)

    def get(self, cid: str) -> Dict[str, Any]:
        return self.store.get(cid)
    
    def put(self, data: Dict[str, Any]) -> str:
        return self.store.put(data)


    def path(self, path:str) -> str:
        """Get content from a specific path in IPFS.
        
        Args:
        """
        return self.folder_path + '/' + path
    

    def call(self , 
                fn: str = 'api/edit',  
                params: Dict[str, Any] = {}, 
                token = None, 
                wait=True,
                owner = None,
                timeout=1000, 
                **extra_params) -> Any:
        """
        Call a function from a mod Mod in IPFS.
        Args:
            mod: mod Mod object
            fn: Function name to task
            params: Parameters for the function task
            key: Key object or address string
        Returns:
            Result of the function task
        """
        print(f'Calling function: {fn} with params: {params} and extra_params: {extra_params}')
        timeout = min(int(timeout), MAX_TASK_TIMEOUT)  # cap user-supplied timeout
        params = {**params, **extra_params}
        task = self.task_data(fn=fn, params=params, timeout=timeout)
        task['key'] =  self.auth.verify(token)['key']
        task['token'] = token
        mod_name = '/'.join(fn.split('/')[:-1]) if '/' in fn else fn
        task['owner'] = owner or m.info(mod_name)['key'] 
        task['cid'] = self.store.put(task)
        m.put(self.task_path(task), task)
        future =  m.submit(self.run_task, params=task ,  timeout=timeout)
        self.cid2future[task['cid']] = future
        if wait:
            result =  future.result()
            return self.store.get(self.store.get(result).get('result', None))
        return task

    def task_path(self, data):
        # Sanitize path components to prevent directory traversal
        key = re.sub(r'[^a-zA-Z0-9_-]', '_', str(data.get("key", "unknown")))
        fn = re.sub(r'[^a-zA-Z0-9_/.-]', '_', str(data.get("fn", "unknown")))
        fn = fn.replace('..', '_')  # extra guard against traversal
        cid = re.sub(r'[^a-zA-Z0-9_-]', '_', str(data.get("cid", "unknown")))
        path = m.relpath(f'{self.tasks_path}/{key}/{fn}/{cid}.json')
        # Final check: ensure path stays within tasks_path
        real_tasks = os.path.realpath(self.tasks_path)
        real_path = os.path.realpath(path)
        assert real_path.startswith(real_tasks), f"Path traversal blocked: {path}"
        return path

    def _clear_tasks(self):
        shutil.rmtree(self.tasks_path) if os.path.exists(self.tasks_path) else None
        assert len(self.task_paths()) == 0, "Failed to clear task paths"
        return True

    def txs(self, key=None, mod=None, cid=None, df=1, features=['fn', 'status', 'cid' ], n=10, page=0, expand=1, max_age=None) -> List[Dict[str, Any]]:
        paths = self.task_paths(key=key, mod=mod, max_age=max_age)
        tasks = []

        filter_fn = lambda p : (True if mod is None else mod in p) and (True if key is None else key in p)
        paths = list(filter(filter_fn, paths))
        for path in paths:
            task = m.get(path)
            if task is not None and cid is not None and task.get('cid') != cid:
                continue
            if expand and task is not None:
                if 'result' in task:
                    try:
                        task['result'] = self.store.get(task['result'])
                    except:
                        pass
                if 'params' in task:
                    try:
                        task['params'] = self.store.get(task['params'])
                    except:
                        pass
            if task != None:
                tasks.append(task)
    
        tasks = sorted(tasks, key=lambda x: x['time'], reverse=True)
        
        if page > 0:
            start = page * n
            end = start + n
            tasks = tasks[start:end]
        else: 
            tasks = tasks[:n]

        
        if len(tasks) == 0:
            return tasks
        else:
            if df:
                tasks = m.df(tasks)
                tasks.sort_values('time', ascending=False, inplace=True)
                tasks['time'] = tasks['time'].apply(lambda x: datetime.datetime.fromtimestamp(x).strftime('%Y-%m-%d %H:%M:%S'))
                tasks = tasks[:n]
                return tasks[features]
            else:

                # remove all of the json non-serializable items
                txs = []
                for task in tasks:
                    try:
                        json.loads(json.dumps(task))
                        txs.append(task)
                    except:
                        continue    
                return txs
    
    h = txs

    def task_paths(self, key=None, mod=None, max_age= 3600):

        if key is not None:
            paths = glob.glob(self.tasks_path+f'/**/{key}/**/*.json', recursive=True)
        elif mod is not None:
            paths = glob.glob(self.tasks_path+f'/**/{mod}/**/*.json', recursive=True)
        else:
            paths = glob.glob(self.tasks_path+f'/**/*.json', recursive=True)
        
        if max_age is not None:
            current_time = time.time()
            filtered_paths = []
            for path in paths:
                file_mod_time = os.path.getmtime(path)
                if (current_time - file_mod_time) <= max_age:
                    filtered_paths.append(path)
            paths = filtered_paths
        return paths

    def reset_tasks(self):
        for path in self.task_paths():
            print(f'Removing task path: {path}')
            m.rm(path)
        for future in self.cid2future.values():
            print(f'Cancelling future -> {future}')
            future.cancel()
        self.cid2future = {}
        assert len(self.task_paths()) == 0, "Failed to reset all task paths"
        return True
    


    def _clear_task_paths(self):
        for cid in self.cid2future.keys():
            print(f'Removing task path: {cid}')
            future = self.cid2future[cid]
            future.cancel()
        self.cid2future = {}


    
    def is_generator(self, obj):
        """
        Is this shiz a generator dawg?
        """
        if not callable(obj):
            result = inspect.isgenerator(obj)
        else:
            result =  inspect.isgeneratorfunction(obj)
        return result

    def verify_task_data(self, payload: Dict[str, Any], signature: str, address=None) -> bool:
        return m.verify(payload, signature, address,  mode='str')

    def test_call(self, mod: m.Mod='openrouter', fn: str='models', params: Dict[str, Any]={}, key=None, **kwargs) -> Any:
        key = m.key(key)
        time = m.time()
        cost = 0
        payload = self.task_data(mod=mod, fn=fn, params=params, time=time, cost=cost, **kwargs)
        signature = key.sign(payload, mode='str')
        assert self.verify_task_data(payload, signature, key.address), "Payload verification failed"
        return self.call(fn= mod + '/' + fn, params=params, time=time, cost=cost, signature=signature, **kwargs)


    last_time_sync = 0

    def sync_info(self):
        fns = list(self.intervals.keys())
        sync_info = {}
        for fn in fns:
            last_time = m.get(self.path('last_time_' + fn)) or 0
            sync_info[fn] = {
                'last_time': last_time,
                'since_last': int(m.time() - last_time),
                'interval': self.intervals.get(fn, None),
                'count': self.sync_counts.get(fn, 0),
            }
        return sync_info
    

    def n_tasks(self):
        return len(list(self.cid2future.values()))
    
            
    def sync_tasks(self,timeout=1000):
        self.last_time_sync = m.time()
        n_tasks = self.n_tasks()
        if n_tasks == 0:
            return True
        else:
            future2path = {future: path for path, future in self.cid2future.items()}

        for future in m.as_completed(future2path.keys(), timeout=timeout):
            path = future2path.pop(future)
            try:
                print(f"Result({path})")
            except Exception as e:
                print(f"Error in future for path {path}: {e}")
                pass
            # remove from cid2future
            self.cid2future.pop(path, None)

    sync_counts = {}
    def sync_loop(self):

        def cansync(name: str, interval:int=10) -> bool:
            current_time = m.time()
            interval = self.intervals.get(name, interval)
            path = self.path('last_time_' + name)
            last_time = float(m.get(path, 0))
            time_lapsed = current_time - last_time
            result = bool(time_lapsed > interval)
            if result:
                m.put(path, current_time)
            return result
    
        while True:
            time.sleep(2)
            fns = list(self.intervals.keys())
            for fn in fns:
                if cansync(fn):
                    try:
                        print(f'running {fn}')
                        getattr(self, fn)()
                        self.sync_counts[fn] = self.sync_counts.get(fn, 0) + 1
                    except Exception as e:
                        print(f'Error in sync_loop for {fn}: {e}')
                    
    def wait_for_task(self, task, wait_frequency=0.2):
        task_path =  self.task_path(task)
        print(f'Waiting for task {task_path} status={task.get("status","pending")}')
        while task.get('status', '') != 'success':
            time.sleep(wait_frequency)
            task = self.store.get(task_path, default={})
            if task.get('status', '') == 'error':
                raise Exception(f'Task {task_path} failed with error: {task.get("result","unknown error")}   ')
        return task

    def get_fns(self, 
                mod:str, 
                helper_fns:List[str]=['info', 'forward'],
                fn_attributes = ['endpoints',  'fns', 'expose',  'exposed', 'functions', 'fns', 'expose_fns']):
        fns = []
        config = m.config(mod) or {}

        mod_obj = m.mod(mod)
        for fa in fn_attributes:
            if fa in config: 
                fns = config[fa]
            if hasattr(mod_obj, fa) and isinstance(getattr(mod_obj, fa), list):
                fns = getattr(mod_obj, fa) 
            if len(fns) > 0:
                break
        fns =  list(set(fns + helper_fns))
        return fns

    def kill_task(self, cid: str) -> bool:
        """
        Kill a running task by its CID.
        Kills both the future and any worker pool subprocess.
        """
        killed = False
        # Kill worker pool subprocess if running
        if self.pool.kill(cid):
            print(f'KillWorker({cid})', )
            killed = True
        # Cancel the future
        future = self.cid2future.get(cid, None)
        if future is not None:
            print(f'KillFuture({cid})', )
            future.cancel()
            del self.cid2future[cid]
            killed = True
        return killed

    def tasks(self) -> bool:
        return list(self.cid2future.keys())
    

    @property
    def namespace(self):
        namespace =  m.namespace()
        namespace.pop('api', None)
        return namespace

    def run_task(self, **task:dict) -> Any:
        """
        Send the function task request to the appropriate mod Mod and function.
        Uses SandboxWorker for local execution to isolate module code.
        Supports remote job execution via namespace registry lookup.
        """
        task['status'] = 'running'
        assert '/' in task['fn'], "Function name must be in the format 'mod/fn'"
        mod, fn =  task['fn'].split('/', 1)
        path = self.task_path(task)
        if isinstance(task['params'], str):
            params =  self.store.get(task['params'])
        assert isinstance(params, dict)
        m.put(path, task)
        client = m.mod('client')()
        try:
            # Check namespace for remote execution
            url = self._get_remote_url(mod)
            if url != None:
                # Remote call - no sandbox needed, goes over HTTP
                print(f'RemoteJob: {task["fn"]} -> {url}', )
                task['remote'] = True
                task['remote_url'] = url
                result = client.call(url + '/' + fn, params=params, timeout=task['timeout'])
            else:
                # Local execution - use worker pool
                cid = task.get('cid')
                timeout = min(task.get('timeout', 120), 300)  # Cap at 5 min
                print(f'WorkerPool: executing {task["fn"]} (cid={cid})', )
                task['remote'] = False
                result = self.pool.run(
                    fn_path=task['fn'],
                    params=params,
                    timeout=timeout,
                    cid=cid,
                )
            task['status'] = 'success'
        except Exception as e:
            result = m.detailed_error(e)
            task['status'] = 'error'
        if self.is_generator(result):
            task['result'] = []
            for item in result:
                task['result'].append(item)
                print(item, end='')
                m.put(path, task)
        else:
            task['result'] = result
        task['result'] = self.store.put(result)
        task['delta'] = m.time() - task['time']
        task['owner'] = self.key.address
        task['request_cid'] = m.copy(task['cid'])
        task['owner_token'] = self.auth.token(data=task, key=self.key)
        task['cid'] = self.store.put(task)
        task['cost'] = self.get_cost(task)
        m.put(path, task)
        return task['cid']

    def _get_remote_url(self, mod: str) -> str:
        """Get remote URL for a module from namespace registry.

        Supports both API namespace and app namespace lookups.
        Uses caching to avoid repeated lookups.

        Args:
            mod: Module name to lookup

        Returns:
            Remote URL if found, None otherwise
        """
        # Check API namespace first
        url = self.namespace.get(mod, None)
        if url:
            return url

        # Check app namespace via server.namespace
        try:
            ns = m.mod('server.namespace')()
            app_ns = ns.app_namespace()
            if mod in app_ns:
                # App namespace stores full info dict
                app_info = app_ns.get(mod, {})
                if isinstance(app_info, dict):
                    return app_info.get('url') or app_info.get('api_url')
                return app_info
        except Exception:
            pass

        return None

    @property
    def store(self):
        if not hasattr(self, '_store'):
            self._store = m.mod(self._store_path)()
        return self._store
    
    @store.setter
    def store(self, store):
        # set the store mod
        if isinstance(store, str):
            self._store_path = store
        else: 
            self._store = store
        return {'store': self._store_path}

    def get_cost(self, task, default_cost=0.01):
        mod_name = task['fn'].split('/')[0]
        calculate_cost = m.config(mod_name).get('cost', {}).get(task['fn'], default_cost)
        return calculate_cost
    
    def task_data(self , 
                fn: str = 'store/ls',  
                params: Dict[str, Any] = {}, 
                cost = 0.01,
                timeout=1000,
                ) -> Dict[str, Any]:

        if self.store.valid_cid(fn):
            task = self.store.get(fn)
            fn = task['fn']
            params = task['params']
        else:
            fn = fn + '/info' if '/' not in fn else fn
        if isinstance(params, dict):
            params = self.store.put(params)

        task =  {
            'fn': fn,
            'params': params,       
            'timeout': timeout,  
            'status': 'pending',
            'time': m.time(), 
            'cost': cost, # bid cost
        }
        return task

    
    def is_tx_settled(self, tx):
        return 'payment_hash' in tx
    
    def ious(self):
        ious  = {}
        for tx in self.txs(df=0):
            if tx['status'] != 'success':
                continue
            if not 'owner' in tx or tx['owner'] is None:
                continue
            if self.is_tx_settled(tx):
                continue
            if 'cid' not in tx:
                tx['cid'] = self.store.put(tx)
            cid = tx['cid']
            tx = self.get(cid)

            # from and to 
            
            if not tx['key'] in ious:
                ious[tx['key']] = {}
            if not tx['owner'] in ious[tx['key']]:
                ious[tx['key']][tx['owner']] = []
            tx['cid'] = cid
            print(f'Adding IOU tx: client={tx["key"]} provider={tx["owner"]} cost={tx["cost"]} cid={cid}')
            ious[tx['key']][tx['owner']] += [tx]

        return ious
    
    def update_tx(self, tx, new_data: dict):
        tx_data = self.get(tx['cid'])
        tx_data.update(new_data)
        if not 'path' in tx:
            tx['path'] = self.task_path(tx)
        tx_data['cid'] = self.store.put(tx_data)
        m.put(tx['path'], tx_data)
        return tx_data

    def sync_ious(self):
        for client, prov2txs in  self.ious().items():
            for prov, txs in prov2txs.items():
                amount = sum([tx['cost'] for tx in txs])
                print(f'Syncing IOU: {len(txs)} txs from {client} to {prov} for total amount {amount}')
                payment_hash = self.chain.debit(client, prov, amount)
                print(f'Syncing IOU: Debiting {amount} from {client} to {prov} for {len(txs)} txs --> tx: {payment_hash}')
                for tx in txs:
                    self.update_tx(tx, {'payment_hash': payment_hash})
                    print(f'Synced tx: {tx["cid"]}')

        return True
    



