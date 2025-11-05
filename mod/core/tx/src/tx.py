import sys
import time
import sys
from typing import Any
import inspect
import mod as m
import json
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
        result = self.serializer.forward(result)

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
        tx_path = f'{tx["client"]["key"]}/{tx["server"]["key"]}/{fn}_{auths["client"]["time"]}'
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

    def txs(self, 
            search=None,
            client= None,
            server= None,
            n = None,
            max_age:float = 3600, 
            features:list = ['mod', 'fn', 'params', 'cost', 'client'],
            shorten_features = ['client', 'server'],
            to_json = False
            ):
        path = None
        if client is not None:
            path = f'{client}/'
        if server is not None:
            path = f'*/{server}' if path is not None else f'/{server}/'
        txs = [x for x in self.store.values(path) if self.is_tx(x)]   
        if to_json:
            return txs[:n]
        else:
            # filter by features
            
            df = m.df(txs)[features]
            df['time'] = df['client'].apply(lambda x: float(x['time']))
            df = df.sort_values(by='time', ascending=False)
        return df

    def n(self):
        """
        Get the number of transactions
        """
        return len(self.store.items())
