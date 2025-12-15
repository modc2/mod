import sys
import time
import sys
from typing import Any
import inspect
import mod as m
from typing import List
from copy import deepcopy
import json
print = m.print
class Cli:
    def __init__(self,  mod='mod',  fn='forward' ):

        self.argv = sys.argv[1:] # remove the first argument (the script name)
        self.fn = fn
        self.mod = m.mod(mod)()

    def forward(self, argv=None, **kwargs):
        """
        Forward the function to the mod and function
        """
        t0 = m.time()
        fn = self.get_fn()
        params = self.get_params()
        result = fn(*params['args'], **params['kwargs']) if callable(fn) else fn
        duration = m.time() - t0
        self.print_result(result)
        # return result
        print(f'∆t = {duration:.4f}s ', color='cyan')



    def print_result(self, result):
        if  self.is_generator(result):
            for item in result:
                if isinstance(item, dict):
                    print(item)
                else:
                    print(item, end='')
        else:
            print(result, color='green')
    def get_fn(self) -> tuple:
        """
        Get the function object from the mod and function name
        the rule goes like this
        {mod}/{fn} *args **kwargs
        in this case the mod is dot seperated to represent the folders 
        e.g mod.submod.subsubmod/fn
        if you have more than one slash it means the first part is the mod and the last part is the fn
        e.g mod/submod/fn
        1. if no function name is provided, use default 'go'
        2. if the fn name is in the mod
        3. if the fn name is of another mod so we will look it up in the fn2mod
        4. if first argument is a path to a function m mod/fn *args
        5. if first argument is a path to a function m.mod.submodule...fn
        6. else raise exception
        
        """
        argv = self.argv
        mod = self.mod

        if len(argv) == 0:
            # scenario 1: no function name provided, use default 'go'
            fn = 'go'
        elif hasattr(mod, argv[0]):
            # scenario 2: the fn name is in the mod
            fn = argv.pop(0)
        elif argv[0].endswith('/'):
            # scenario 4: the fn name is of another mod so we will look it up in the fn2mod
            mod = m.mod(argv.pop(0)[:-1])()
            fn = self.fn
        elif argv[0].startswith('/'):
            # scenario 5: the fn name is of another mod so we will look it up in the fn2mod
            fn = argv.pop(0)[1:]
        elif len(argv[0].split('/')) == 2:
            # scenario 6: first argument is a path to a function m mod/fn *args **kwargs
            # first mod/submodule/.../fn
            mod , fn = argv.pop(0).split('/')
            mod = m.mod(mod)()
        elif len(argv[0].split('/')) >= 2:
            # scenario 7: first argument is a path to a function m.mod.submodule...fn
            parts = argv.pop(0).split('/')
            fn = parts.pop(-1)
            mod = m.mod(parts.pop(0))()
            for part in parts:
                mod = getattr(mod, part)
        else: 
            raise Exception(f'Function was not extracted from {argv} ')
        return getattr(mod, fn, None)

    def get_params(self) -> tuple:
        """
        Get the parameters passed to the function
        The parameters can be passed as positional arguments or keyword arguments
        e.g mod/fn arg1 arg2 key1=value1 key2=value2
        or mod/fn key1=value1 key2=value2
        1. if an argument contains '=' it is a keyword argument
        2. else it is a positional argument
        3. if mixing positional and keyword arguments raise exception
        4. if the value is a json object, parse it as a json object
        5. if the value is a list, parse it as a list
        6. if the value is a dict, parse it as a dict
        7. else parse it as a string, int, float, bool or None
        """

        argv = self.argv
        # ---- PARAMS ----
        params = {'args': [], 'kwargs': {}} 
        parsing_kwargs = False
        json_object_detected = False
        if len(argv) > 0:
            for arg in argv:
                if json_object_detected:
                    # we are in the middle of a json object, keep appending to the last key
                    last_key = list(params['kwargs'].keys())[-1]
                    params['kwargs'][last_key] += ' ' + arg
                    if arg.endswith('}'):
                        # end of json object
                        json_object_detected = False
                        # try to parse the json object
                        print(f'Parsing json object for key {last_key}: {params["kwargs"][last_key]}', argv)
                        params['kwargs'][last_key] = json.loads(params['kwargs'][last_key])
    
                    continue
                if '=' in arg:
                    parsing_kwargs = True
                    key, value = arg.split('=')
                    # is value a json object? 
                    if value.startswith('{'):
                        json_object_detected = True
                        json_str = value
                        if value.endswith('}'):
                            json_object_detected = False
                            try:
                                value = json.loads(value)
                            except:
                                pass
                    params['kwargs'][key] = self.str2python(value)
                else:
                    assert parsing_kwargs is False, f'Cannot mix positional and keyword arguments {argv}'
                    params['args'].append(self.str2python(arg))    
 
        return  params

    _object_cache = {}


    def shorten(self, x:str, n=12):
        if len(x) > n:
            return x[:n] +  '...' + x[-n:]
        return x

    def is_generator(self, obj):
        """
        Is this shiz a generator dawg?
        """
        if isinstance(obj, str):
            if not hasattr(self, obj):
                return False
            obj = getattr(self, obj)
        if not callable(obj):
            result = inspect.isgenerator(obj)
        else:
            result =  inspect.isgeneratorfunction(obj)
        return result

    def str2python(self, x):
        x = str(x)
        if isinstance(x, str) :
            if x.startswith('py(') and x.endswith(')'):
                try:
                    return eval(x[3:-1])
                except:
                    return x
        if x.lower() in ['null'] or x == 'None':  # convert 'null' or 'None' to None
            return None 
        elif x.lower() in ['true', 'false']: # convert 'true' or 'false' to bool
            return bool(x.lower() == 'true')
        elif x.startswith('[') and x.endswith(']'): # this is a list
            try:
                list_items = x[1:-1].split(',')
                # try to convert each item to its actual type
                x =  [self.str2python(item.strip()) for item in list_items]
                if len(x) == 1 and x[0] == '':
                    x = []
                return x
            except:
                # if conversion fails, return as string
                return x
        elif x.startswith('{') and x.endswith('}'):
            # this is a dictionary
            if len(x) == 2:
                return {}
            try:
                dict_items = x[1:-1].split(',')
                # try to convert each item to a key-value pair
                return {key.strip(): self.str2python(value.strip()) for key, value in [item.split(':', 1) for item in dict_items]}
            except:
                # if conversion fails, return as string
                return x
        else:
            # try to convert to int or float, otherwise return as string
            
            for type_fn in [int, float]:
                try:
                    return type_fn(x)
                except ValueError:
                    pass
        return x