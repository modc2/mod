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
    def __init__(self, 
                mod='mod', 
                fn='forward',
                default_fn='go',
                ):
        self.argv = self.get_argv()
        self.fn = fn
        self.mod = mod
        self.time = m.time()
        self.default_fn = default_fn

    def get_args(self, argv=None):
        """
        Get the arguments passed to the script
        """
        argv = argv or sys.argv[1:] # remove the first argument (the script name)
        self.argv =  argv
        return self.argv

    def forward(self, argv=None, **kwargs):
        """
        Forward the function to the mod and function
        """
        self.args =self.get_args(argv)
        fn = self.get_fn()
        params = self.get_params()
        self.run_fn(fn, params)


    def run_fn(self, fn:Any, params:dict):
        """
        Run
        """

        result = fn(*params['args'], **params['kwargs']) if callable(fn) else fn
        self.duration = m.time() - self.time
        is_generator = self.is_generator(result)
        if is_generator:
            for item in result:
                if isinstance(item, dict):
                    print(item)
                else:
                    print(item, end='')
        else:
            print(result, color='green')
        self.result = result
        return result

    def get_fn(self) -> tuple:
        """
        Get the function object from the mod and function name
        """
        argv = self.argv
        fn = self.fn
        mod = m.mod(self.mod)()

        if len(argv) == 0:
            # scenario 1: no arguments, use the default function
            fn = self.default_fn
        elif len(argv) > 0 :
            if hasattr(mod, argv[0]):
                fn = argv.pop(0)
            elif argv[0].endswith('/'):
                # scenario 4: the fn name is of another mod so we will look it up in the fn2mod
                mod = argv.pop(0)[:-1]
            elif argv[0].startswith('/'):
                # scenario 5: the fn name is of another mod so we will look it up in the fn2mod
                fn = argv.pop(0)[1:]
            elif len(argv[0].split('/')) == 2:
                # scenario 6: first argument is a path to a function m mod/fn *args **kwargs
                # first mod/submodule/.../fn
                mod , fn = argv.pop(0).split('/')
                mod = m.mod(mod)()
            elif len(argv[0].split('/')) >= 2:
                print(f'First argument {argv[0]} looks like a mod/submodule.../fn path', color='green')
                # scenario 7: first argument is a path to a function m.mod.submodule...fn
                parts = argv.pop(0).split('/')
                fn = parts.pop(-1)
                mod = m.mod(parts.pop(0))()
                for part in parts:
                    mod = getattr(mod, part)

        else:
            # scenario 2: no arguments, use the default function
            fn = self.default_fn
        print(f'Function: {mod.__class__.__name__}.{fn}', color='cyan')
        fn_obj = getattr(mod, fn, None)
        return fn_obj

    def get_params(self) -> tuple:
        """
        Get the parameters passed to the function
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

    def get_argv(self, argv=None):
        """
        Get the arguments passed to the script
        """
        argv = argv or sys.argv[1:] # remove the first argument (the script name)
        return argv

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