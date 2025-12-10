import sys
import time
import sys
from typing import Any
import inspect
import mod as m
import json
from datetime import datetime
from copy import deepcopy
import time

class Tx:

    def __init__(self, 
                tx_path = '~/.mod/server/tx' , 
                serializer='serializer',
                auth = 'auth',
                roles = ['client', 'server'], # the roles that need to sign the transaction
                tx_schema = {
                    'mod': str,
                    'fn': str,
                    'params': dict,
                    'result': dict,
                    'client': dict,  # client auth
                    'server': dict,  # server auth
                }, # the schema of the transaction
                key:str = None, 
                 version='v0'):

        self.key = m.key(key)
        self.version = version
        self.store = m.mod('store')(f'{tx_path}/{self.version}')
        self.tx_schema = tx_schema
        self.tx_features = list(self.tx_schema.keys())
        self.serializer = m.mod(serializer)()
        self.auth = m.mod(auth)()
        self.roles = roles

    def forward(self, 
                 mod:str = 'mod', 
                 fn:str = 'forward', 
                 cost = 0,
                 params:dict = {}, 
                 result:Any = {}, 
                 auths = {},
                 key = None,
                 client= None,
                 server= None
                 ):

        """ 
        create a transaction
        """

        if client is None or server is None: 
            auths = self.get_auths(mod, fn, params, result, key=key)
        else: 
            if client is not None:
                auths['client'] = client
            
            if server is not None:
                auths['server'] = server
            
        tx = {
            'mod': mod, # the mod name (str)
            'fn': fn, # the function name (str)
            'params': params, # the params is the input to the function  (dict)
            'cost': cost, # the cost of the function (float)
            'result': result, # the result of the function (dict)
            'client': auths['client'], # the client auth (dict)
            'server': auths['server'], # the server auth (dict)
        }
        tx_path = f'{mod}/{tx["client"]["key"]}/{fn}_{auths["client"]["time"]}'
        self.store.put(tx_path, tx)
        return tx

    create_tx = create = tx = forward

    def paths(self, path=None):
        return self.store.paths(path=path)

    def clear(self):
        """
        DANGER: This will permanently remove all transactions from the store.
        remove the transactions
        """
        paths = self.store.paths()
        for p in self.store.paths():
            self.store.rm(p)
        new_paths = self.store.paths()
        assert len(new_paths) == 0, f'Failed to remove all transactions. Remaining paths: {new_paths}'
        return {'status': 'success', 'message': 'All transactions removed successfully', 'removed_paths': paths}

    def is_tx(self, tx):
        """
        Check if the transaction is valid
        """
        if isinstance(tx, str):
            tx = self.store.get(tx)
        if not isinstance(tx, dict):
            return False
        if not all([key in tx for key in self.tx_features]):
            return False
        return True

    def shorten(self, name:str = '12334555', start_len:int = 4, end_len:int = 4, filler:str = '...'):
        if len(name) <= start_len + end_len + len(filler):
            return name
        return name[:start_len] + filler + name[-end_len:]

    def txs(self, 
            server= None,
            client= None,
            n = None,
            max_age:float = 3600, 
            features:list = ['mod', 'fn', 'params', 'cost', 'client', 'server' ],
            records = False
            ):
        path = None
        if client is not None:
            path = f'*/{client}'
        if server is not None:
            path = f'{server}/*' 
        txs = [x for x in self.store.values(path, max_age=max_age) if self.is_tx(x)]  

        result = txs[:n]
        if not records:
            result = m.df(txs)
            if len(result) == 0:
                return result
            result = result[features]
            # configure the time columns
            result['time'] = result['client'].apply(lambda x: float(x['time']))
            result['time_end'] = result['server'].apply(lambda x: float(x['time']))
            result['delta'] = result['time_end'] - result['time'] ; 
            result['age'] = time.time() - result['time']
            del result['time_end']
            # now shorten the client and server keys
            result['server'] = result['server'].apply(lambda x: self.shorten(x['key']))
            result['client'] = result['client'].apply(lambda x: self.shorten(x['key']))
            result = result.sort_values(by='time', ascending=False)
        return result

    def n(self):
        """
        Get the number of transactions
        """
        return len(self.store.items())
