
import os
import pandas as pd
from typing import *
import inspect
from copy import deepcopy
import mod as c
print = c.print

class Vali:
    endpoints = ['score', 'scoreboard']
    def __init__(self,
                    network= 'local', # for local chain:test or test # for testnet chain:main or main # for mainnet
                    search : Optional[str] =  None, # (OPTIONAL) the search string for the network 
                    batch_size : int = 12, # the batch size of the most parallel tasks
                    task : str= None,
                    params = None, # the parameters for the task
                    key : str = None, # the key for the module
                    tempo : int = 60, # the time between epochs
                    timeout : int = 32, # timeout per evaluation of the module
                    update : bool =False, # update during the first epoch
                    loop : bool = False, # This is the key that we need to change to false
                    verbose: bool = True, # print verbose output
                    path : str= None, # the storage path for the module eval, if not null then the module eval is stored in this directory
                    subnet = None, # the subnet to use for the network
                 **kwargs): 

        self.epoch_time = 0
        self.vote_time = 0 # the time of the last vote (for voting networks)
        self.epochs = 0 # the number of epochs
        self.timeout = timeout
        self.batch_size = batch_size
        self.verbose = verbose
        self.search = search
        self.set_key(key)
        self.task = task or self.task
        self.auth = c.mod('auth')()
        self.set_network(network=network, subnet=subnet, tempo=tempo,  search=search,  path=path, update=update)
        if loop:
            c.thread(self.run_loop) 
            
    def set_key(self, key):
        key = c.get_key(key)
        assert hasattr(key, 'key_address'), f'Key {key} does not have a key_address'
        self.key = key
        c.print(f'VALI KEY --> {self.key}', color='yellow')
        return self.key

    def set_network(self, 
                    network:Optional[str] = None, 
                    tempo:int= 10, 
                    search:str=None, 
                    path:str=None, 
                    subnet=None,
                    update = True) -> str:
        if not hasattr(self, 'network'):
            self.network = 'local'
        self.network = network or self.network
        if '/' in self.network:
            self.subnet = self.network.split('/')[0]
            self.network = self.network.split('/')[1]
        else:
            self.subnet = subnet or 0

        self.tempo = tempo
        self.storage_path = self.get_path(self.network + '/' + self.network)
        self.search = search or self.search
        self.net = c.mod(self.network)() 
        self.mods = [m for m in c.wait([ c.submit(c.call, {'fn': url}, timeout=10) for url in self.net.urls()]) if 'error' not in m]
        self.key2module = {m['key']: m for m in self.mods if 'key' in m}
        self.name2module = {m['name']: m for m in self.mods if 'name' in m}
        self.url2module = {m['url']: m for m in self.mods if 'url' in m}
        return self.network

    def get_path(self, path):
        return os.path.expanduser(f'~/.commune/vali/{path}')

    def next_epoch_time(self):
        return self.epoch_time + self.tempo

    def seconds_until_epoch(self):
        return int(self.next_epoch_time() - c.time())
    
    def run_loop(self, step_time=2):
        while True:
            # wait until the next epoch)
            seconds_until_epoch = self.seconds_until_epoch()
            if seconds_until_epoch > 0:
                progress = c.tqdm(total=seconds_until_epoch, desc='Time Until Next Progress')
                for i in range(seconds_until_epoch):
                    progress.update(step_time)
                    c.sleep(step_time)
            try:
                c.df(self.epoch())
            except Exception as e:
                c.print('XXXXXXXXXX EPOCH ERROR ----> XXXXXXXXXX ',c.detailed_error(e), color='red')

    def forward(self, module:Union[str, dict], **params):
        t0 = c.time()
        module['params'] = params
        module['score'] = self.task(c.client(url=module['url'], key=self.key), **params)
        module['time'] = c.time()
        module['duration'] = c.time() - t0
        proof_data = c.copy(module)
        module['proof'] = self.auth.headers(proof_data, key=self.key)
        proof = module.get('proof', None)
        assert self.auth.verify(proof), f'Invalid Proof {proof}'
        path = self.get_module_path(module['key'])
        c.put_json(path, module)
        return module

    def get_module_path(self, module:str):
        return self.storage_path + '/' + module + '.json'

    def epoch(self, search=None, result_features=['score',  'name', 'cid'], key=None, debug=False, df=True, **kwargs):
        self.set_network(search=search, **kwargs)
        n = len(self.mods)
        if key:
            self.set_key(key)
        batches = [self.mods[i:i+self.batch_size] for i in range(0, n, self.batch_size)]
        num_batches = len(batches)
        results = []
        for i, batch in enumerate(batches):
            futures = []
            print(f'Starting batch {i+1}/{num_batches} with {len(batch)} modules...')
            names = []
            for m in batch:
                future = c.submit(self.forward, {"module": m} , timeout=self.timeout)
                futures.append(future)
            results.extend(c.wait(futures, timeout=self.timeout))
        self.epochs += 1
        self.epoch_time = c.time()
        self.vote(results)
        if debug: 
            results =  [r for r in results if 'error' in r]
        else: 
            results =  [r for r in results if 'error' not in r]
        if df:
            return c.df(results)[result_features]
        return results

    @property
    def vote_staleness(self):
        return c.time() - self.vote_time

    def vote(self, results):
        if not bool(hasattr(self.net, 'vote')) :
            return {'success': False, 'msg': f'NOT VOTING NETWORK({self.network})'}
        if self.vote_staleness < self.tempo:
            return {'success': False, 'msg': f'Vote is too soon {self.vote_staleness}'}
        if len(results) == 0:
            return {'success': False, 'msg': 'No results to vote on'}
        # get the top modules
        assert all('score' in r for r in results), f'No score in results {results}'
        assert all('key' in r for r in results), f'No key in results {results}'
        return self.net.vote(
                    modules=[m['key'] for m in modules], 
                    weights=[m['score'] for m in modules],  
                    key=self.key, 
                    subnet=self.subnet
                    )
    
    def results(self,
                    keys = ['name', 'score', 'duration',  'url', 'key', 'time', 'age'],
                    ascending = True,
                    by = 'score',
                    to_dict = False,
                    page = None,
                    max_age = 10000,
                    update= False,
                    **kwargs
                    ) -> Union[pd.DataFrame, List[dict]]:
        page_size = 1000
        df = []
        # chunk the jobs into batches
        for path in c.ls(self.storage_path):
            r = c.get(path, {},  max_age=max_age, update=update)
            if isinstance(r, dict) and 'key' and  r.get('score', 0) > 0  :
                df += [{k: r.get(k, None) for k in keys}]
            else :
                c.print(f'REMOVING({path})', color='red')
                c.rm(path)
        df = c.df(df) 
        if len(df) > 0:
            if isinstance(by, str):
                by = [by]
            df = df.sort_values(by=by, ascending=ascending)
        if len(df) > page_size:
            pages = len(df)//page_size
            page = page or 0
            df = df[page*page_size:(page+1)*page_size]
        df['age'] = c.time() - df['time']
        if to_dict:
            return df.to_dict(orient='records')
        return df

    @classmethod
    def run_epoch(cls, network='local', **kwargs):
        kwargs['run_loop'] = False
        return  cls(network=network,**kwargs).epoch()
    
    def refresh_results(self):
        path = self.storage_path
        c.rm(path)
        return {'success': True, 'msg': 'Leaderboard removed', 'path': path}


    def task(self,  mod,  fn='info', params={}) -> float:
        mod = c.connect(mod) if isinstance( mod, str) else mod
        result =  getattr(mod, fn)(**params)
        if 'cid' in result and isinstance(result, dict) :
            score =  1
        else:
            score =  0
        return float(score)