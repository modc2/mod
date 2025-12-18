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

class TxType:
    mod: str
    fn: str
    params: dict
    result: dict
    client: dict  # client auth
    server: dict  # server auth


class Tx:


    tx_schema = {
                    'mod': str,
                    'fn': str,
                    'params': dict,
                    'result': dict,
                    'client': dict,  # client auth
                    'server': dict,  # server auth
                }

    def __init__(self,  path = '~/.mod/server/tx'):
        self.store = m.mod('store')(path)

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
        path =  f'{tx["mod"]}/{tx["client"]["key"]}/{tx["fn"]}_{tx["client"]["time"]}'
        return self.store.put(path, tx)
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
        if not all([key in tx for key in self.tx_schema]):
            return False
        return True

    def txs(self, 
            server= None,
            client= None,
            n = None,
            max_age:float = 3600, 
            features:list = ['mod', 'fn',  'cost', 'client', 'age', 'delta', 'params'],
            records = False
            ):

        def shorten(name:str = '12334555', start_len:int = 4, end_len:int = 4, filler:str = '...'):
            if len(name) <= start_len + end_len + len(filler):
                return name
            return name[:start_len] + filler + name[-end_len:]
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
            # configure the time columns
            result['time'] = result['client'].apply(lambda x: float(x['time']))
            result['time_end'] = result['server'].apply(lambda x: float(x['time']))
            result['delta'] = (result['time_end'] - result['time']).apply(lambda x: round(x, 2))
            del result['time_end']
            result['age'] = (time.time() - result['time']).apply(lambda x: str(int(x)) + 's')
            result['server'] = result['server'].apply(lambda x: shorten(x['key']))
            result['client'] = result['client'].apply(lambda x: shorten(x['key']))
            result = result.sort_values(by='time', ascending=False)
            result = result[features]
        return result

    def n(self):
        """
        Get the number of transactions
        """
        return len(self.store.items())
