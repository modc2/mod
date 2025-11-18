
import os
import inspect
import json
import yaml
import shutil
import time
import glob
import sys
import argparse
from functools import partial
import os
from copy import deepcopy
from typing import *
import nest_asyncio
nest_asyncio.apply()

class Mod: 

    file_types = ['py'] # default file types
    anchor_names = ['agent', 'mod', 'block'] # default anchor names
    endpoints = ['ask'] # default endpoints
    lib_name = __file__.split('/')[-2] # mod/core/mod.py -> mod 

    def __init__(self, 
                  config = None,
                  **kwargs):
        """
        Initialize the mod by sycing with the config
        """
        self.sync(config=config)

    def get_ports(self, n=3) -> list:
        port_range = self.get_port_range()
        used_ports = self.used_ports()
        available_ports = [p for p in range(port_range[0], port_range[1]) if p not in used_ports]
        if len(available_ports) < n:
            raise Exception(f'Not enough available ports in range {port_range}, only {len(available_ports)} available')
        return available_ports[:n]

    def get_port_range(port_range: list = None) -> list:
        import mod as m
        port_range = m.get('port_range', [])
        if isinstance(port_range, str):
            port_range = list(map(int, port_range.split('-')))
        if len(port_range) == 0:
            port_range = m.port_range
        port_range = list(port_range)
        assert isinstance(port_range, list), 'Port range must be a list'
        assert isinstance(port_range[0], int), 'Port range must be a list of integers'
        assert isinstance(port_range[1], int), 'Port range must be a list of integers'
        return port_range

    def sync(self, mod=None, verbose=False, config = None):
        if os.getcwd() in [os.path.expanduser('~'), '/']:
            raise ValueError(f'For your safety we do not allow syncing in the home directory {os.getcwd()}, please cd into a project directory like cd mod or cd mymod')
        
        self.mod_path =os.path.dirname(os.path.dirname(__file__))
        self.lib_path  = self.libpath = self.repo_path  = self.repopath = os.path.dirname(self.mod_path) # the path to the repo
        self.core_path = '/'.join(__file__.split('/')[:-1])
        module_path_options = ['mods', 'modules', '_mods', '_modules', 'locals']
        self.mods_path = list(filter(lambda x: os.path.exists(x), [f'{self.mod_path}/{option}' for option in module_path_options]))[0] # the path to the mods
        self.ext_path = f'{self.mod_path}/_ext' # the path to the external mods
        self.home_path  = os.path.expanduser('~')
        config =self.config()
        self.name  = config['name']
        self.storage_path = f'{self.home_path}/.{self.name}'
        self.port_range = config['port_range']
        self.expose = self.endpoints = config['expose']
        self.anchor_names.append(self.name)
        if mod is not None:
            print(f'Syncing mod {mod}')
            return self.fn(f'{mod}/sync')()
        self.set_routes(self.routes())

        return {'success': True, 'msg': 'synced mods and utils'}


    def set_routes(self, routes:dict, verbose=True):
        for mod, fns in routes.items():
            mod = self.import_mod(mod)
            for fn in fns: 
                if not hasattr(self, fn):
                    def partial_fn(mod, fn):
                        return partial(getattr(mod, fn))
                    setattr(self, fn, partial_fn(mod, fn))

    @property
    def shortcuts(self):
        shortcuts = self.config()['shortcuts']
        shortcuts[self.name] = 'mod'
        shortcuts = {k: v for k, v in shortcuts.items() if isinstance(v, str)}
        return shortcuts

    def mod(self, 
                mod: str = 'mod', 
                params: dict = None,  
                cache=True, 
                verbose=False, 
                update=True,
                **kwargs) -> str:

        """
        imports the mod core
        """
        # Load the mod
        mod = mod or 'mod'
        if mod in [self.name, 'mod', 'mod']:
            return Mod
        if not isinstance(mod, str):
            return mod
        mod = self.get_name(mod)

        if not hasattr(self, '_mod_cache'):
            self._mod_cache = {}

        if mod in self._mod_cache:
            return self._mod_cache[mod]
        
        obj =  self.anchor_object(mod)
        self._mod_cache[mod] = obj
        return obj


    mod = mod

    def text2color(self, text:str='info') -> str:
        # i want to convert he text into a color through maping the string to a real value and mapping that to a color

        real_value = sum([ord(c) for c in text])

        color_value = real_value % 256
        return f'\033[38;5;{color_value}m{text}\033[0m'

    def forward(self, fn:str='info', params:dict=None, auth=None) -> Any:
        params = params or {}
        # assert fn in self.endpoints, f'{fn} not in {self.endpoints}'
        if hasattr(self, fn):
            fn_obj = getattr(self, fn)
        else:
            fn_obj = self.fn(fn)
        return fn_obj(**params)

    def go(self, mod=None, **kwargs):
        path = self.dirpath(mod, relative=False)
        assert os.path.exists(self.abspath(path)), f'{path} does not exist'
        return self.cmd(f'code {path}', **kwargs)

    def getfile(self, obj=None) -> str:
        return inspect.getfile(self.mod(obj))

    def path(self, obj=None) -> str:
        return inspect.getfile(self.mod(obj))

    def about(self, mod, query='what is this?', *extra_query):
        """
        Ask a question about the mod
        """
        query = query + ' '.join(extra_query)
        return self.ask(f' {self.schema(mod)} {query}')

    def abspath(self,path:str=''):
        return os.path.abspath(os.path.expanduser(path))


    def filepath(self, obj=None) -> str:
        """
        get the file path of the mod
        """
        return inspect.getfile(self.mod(obj)) 

    fp = filepath

    def dockerfiles(self, mod=None):
        """
        get the dockerfiles of the mod
        """
        dirpath = self.dirpath(mod)
        dockerfiles = [f for f in os.listdir(dirpath) if f.startswith('Dockerfile')]
        return [os.path.join(dirpath, f) for f in dockerfiles]

    def vs(self, path = None):
        path = path or __file__
        path = os.path.abspath(path)
        return self.cmd(f'code {path}')

    def mod_class(self, obj= None) -> str:
        if obj == None: 
            objx = self 
        return obj.__name__

    def storage_dir(self, mod=None):
        mod = (mod or self.name).replace('/', '.')
        return os.path.abspath(os.path.expanduser(f'~/.{self.name}/{mod}'))
    
    def is_home(self, path:str = None) -> bool:
        """
        Check if the path is the home path
        """
        if path == None:
            path = self.pwd()
        return os.path.abspath(path) == os.path.abspath(self.home_path)

    def print(self,  *text:str,  **kwargs):
        return self.obj('mod.core.utils.print_console')(*text, **kwargs)

    def time(self, t=None) -> float:
        import time
        return time.time()
        
    def pwd(self):
        return os.getcwd()

    def token(self, data, key=None, mod='auth.jwt',  **kwargs) -> str:
        token = self.mod(mod)().get_token(data=data, key=key, **kwargs)
        assert self.verify_token(token), f'Token {token} is not valid'
        return token
    def verify_token(self, token:str = None,  mod='auth.jwt',  *args, **kwargs) -> str:
        return self.mod(mod)().verify_token(token=token, *args, **kwargs)

    def run(self, fn:str='info', params: Union[str, dict]="{}", auth=None,**_kwargs) -> Any: 
        if isinstance(signature, str):
            signature = self.verify(auth)
        mod = 'mod'
        if '/' in fn:
            mod, fn = fn.split('/')
        parser = argparse.ArgumentParser(description='Argparse for the mod')
        parser.add_argument('--mod', dest='mod', help='The function', type=str, default=mod)
        parser.add_argument('--fn', dest='fn', help='The function', type=str, default=fn)
        parser.add_argument('--params', dest='params', help='key word arguments to the function', type=str, default=params) 
        argv = parser.parse_args()
        params = argv.params
        mod = self.get_name(argv.mod)
        fn = argv.fn
        args, kwargs = [], {}
        if isinstance(params, str):
            params = json.loads(params.replace("'",'"')) 
        print(f'Running {mod}.{fn} with params {params}')
        if isinstance(params, dict):
            if 'args' in params and 'kwargs' in params:
                args = params['args']
                kwargs = params['kwargs']
            else:
                kwargs = params
        elif isinstance(params, list):
            args = params
        else:
            raise Exception('Invalid params', params)
        mod = self.mod(mod)()
        return getattr(mod, fn)(*args, **kwargs)     
        
    def commit_hash(self, lib_path:str = None):
        if lib_path == None:
            lib_path = self.lib_path
        return self.cmd('git rev-parse HEAD', cwd=lib_path, verbose=False).split('\n')[0].strip()

    def build(self, *args,   **kwargs):
        return self.fn(f'pm/build')(*args, **kwargs)
    
    def run_fn(self,fn:str, params:Optional[dict]=None, args=None, kwargs=None, mod='mod') -> Any:
        """
        get a fucntion from a strings
        """

        if '/' in fn:
            mod, fn = fn.split('/')
        mod = self.mod(mod)()
        fn_obj =  getattr(mod, fn)
        params = params or {}
        if isinstance(params, str):
            params = json.loads(params.replace("'",'"'))
        if isinstance(params, list):
            args = params
        elif isinstance(params, dict):
            kwargs = params
        args = args or []
        kwargs = kwargs or {}
        return fn_obj(*args, **kwargs)

    def is_mod_file(self, mod = None, exts=['py', 'rs', 'ts'], folder_filenames=['mod', 'agent', 'block',  'server']) -> bool:
        dirpath = self.dirpath(mod)
        try:
            filepath = self.filepath(mod)
        except Exception as e:
            self.print(f'Error getting filepath for {mod}: {e}', color='red', verbose=False)
            return False
        folder_filenames.append(mod.split('.')[-1]) # add the last part of the mod name to the folder filenames
        for ext in exts:
            for fn in folder_filenames:
                if filepath.endswith(f'/{fn}.{ext}'):
                    return False
                non_folder_name = mod.split('.')[-1]
                if filepath.endswith(f'/{non_folder_name}.{ext}'):
                    return True
        return bool(dirpath.split('/')[-1] != filepath.split('/')[-1].split('.')[0])
    
    is_file_mod = is_mod_file

    def is_mod_folder(self,  mod = None) -> bool:
        return not self.is_mod_file(mod)
    
    is_folder_mod = is_mod_folder 

    def get_key(self,key:str = None , **kwargs) -> None:
        return self.mod('key')().get_key(key, **kwargs)
        
    def key(self,key:str = None , **kwargs) -> None:
        return self.get_key(key, **kwargs)

    def keys(self,key:str = None , **kwargs) -> None:
        return self.get_key().keys(key, **kwargs)

    def key2address(self,key:str = None , **kwargs) -> None:
        return self.get_key().key2address(key, **kwargs)
    
    def folders(self, 
            path:str = './', 
            depth:Optional[int]=4, 
            recursive:bool=True, 
            avoid_folders = ['__pycache__', '.git', '.ipynb_checkpoints', 'node_modules', '/artifacts', 'egg-info',  '/private/', 'node_modules', '/.venv/', '/venv/', '/.env/'], 
            include_hidden=False):
        dirpath = self.abspath(path)
        listdir_paths = self.ls(dirpath, include_hidden=include_hidden, depth=depth)
        dirs = list(filter(lambda f: os.path.isdir(f), listdir_paths))
        if not include_hidden:
            dirs = [ d for d in dirs if '/.' not in d ]
        if len(avoid_folders) > 0:
            dirs = [d for d in dirs if not any([at in d for at in avoid_folders])]
        if depth > 1:
            sub_dirs = []
            for d in dirs:
                sub_dirs += self.dirs(d, depth=depth-1, recursive=recursive, include_hidden=include_hidden, avoid_folders=avoid_folders)
            dirs += sub_dirs
            dirs = sorted(list(set(dirs)))
            return dirs
        else:
            return sorted(list(set(dirs)))
            
    dirs = folders

    def files(self, 
              path='./', 
              search:str = None, 
              avoid_folders = ['__pycache__', '.git', '.ipynb_checkpoints', 'node_modules', '/artifacts', 'egg-info',  '/private/', 'node_modules', '/.venv/', '/venv/', '/.env/'], 
              endswith:str = None,
              include_hidden:bool = False, 
              always_include_terms = ['.gitignore', '.dockerignore'],
              relative = False, # relative to the current working directory
              startswith:str = None,
              depth=3,
              **kwargs) -> List[str]:
        """
        Lists all files in the path
        """
        # if self.mod_exists(path):
        #     path = self.dirpath(path)
        files =self.glob(path, depth=depth,**kwargs)
        if not include_hidden:
            files = [f for f in files if not '/.' in f ]
        files = list(filter(lambda f: not any([at in f for at in avoid_folders]), files))
        if relative: 
            cwd = os.getcwd()
            files = [f.replace(path + '/', '') if f.startswith(cwd) else f for f in files]
        if search != None:
            files = [f for f in files if search in f]
        if len(files) == 0 and self.mod_exists(path):
            return self.files(self.dirpath(path), search=search, avoid_folders=avoid_folders, endswith=endswith, include_hidden=include_hidden, relative=relative, startswith=startswith, depth=depth, **kwargs)
        return files

    def files_size(self):
        return len(str(self.files()))

    def envs(self, key:str = None, **kwargs) -> None:
        return self.get_key(key, **kwargs).envs()

    def encrypt(self,data: Union[str, bytes], key: str = None, password: str = None, **kwargs ) -> bytes:
        return self.get_key(key).encrypt(data, password=password)
    def decrypt(self, data: Any,  password : str = None, key: str = None, **kwargs) -> bytes:
        return self.get_key(key).decrypt(data, password=password)
        
    def sign(self, data:dict  = None, key: str = None,  crypto_type='sr25519', mode='str', **kwargs) -> bool:
        return self.get_key(key, crypto_type=crypto_type).sign(data, mode=mode, **kwargs)

    def size(self, mod) -> int:
        return len(str(self.content(mod)))

    def verify(self, data, signature=None, address=None, key=None, **kwargs ) -> bool:  
        key = self.get_key(key)
        return key.verify(data=data, signature=signature, address=address, **kwargs)

    def get_utils(self, search=None):
        utils = self.path2fns(self.core_path + '/utils.py', tolist=True)
        if search != None:
            utils = [u for u in utils if search in u]
        return sorted(utils)
        
    def utils(self, search=None):
        return self.get_utils(search=search)

    def relpath(self, path:str = '~') -> str:
        path = os.path.abspath(os.path.expanduser(path))
        return path.replace(self.home_path, '~')

    def routes(self, obj=None):
        obj = obj or self
        routes = {}
        config = self.config()
        if hasattr(config, 'routes'):
            routes.update(conifg.routes)
        for util in self.get_utils():
            k = '.'.join(util.split('.')[:-1])
            v = util.split('.')[-1]
            routes[k] = routes.get(k , [])
            routes[k].append(v)
        return routes
    def set_config(self, config=None):

        if config is None:
            configs = self.config_paths(config)
            # sort configs by shortest then by json first
            configs = sorted(configs, key=lambda x: len(x))
            assert len(configs) > 0, 'No config found'
            # is this json or yaml
            if configs[0].endswith('.json'):
                config = self.get_json(configs[0])
            elif configs[0].endswith('.yaml') or configs[0].endswith('.yml'):
                config = self.get_yaml(configs[0])
            else:
                raise Exception('Unknown config format', configs[0])
        return config

    def fn2mod(self, search=None, update=False, core = True, local = True, verbose=False):
        fn2mod = {}
        path = self.get_path('fn2mod')
        fn2mod = self.get(path, {}, update=update)
        if len(fn2mod) == 0:
            mods = []
            if core:
                mods = self.core_mods() 
            if local:
                mods += self.local_mods()
            for m in mods:
                try:
                    for fn in self.fns(m):
                        fn2mod[fn] = m
                except Exception as e:
                    if verbose:
                        print(f'Error getting fns for {m}: {e}', )
            self.put(path, fn2mod)    
        if search != None:
            fn2mod = {k:v for k,v in fn2mod.items() if search in k or search in v}    
        return fn2mod

    def secret(self, key:str = None, seed=None, update=False, tempo=None, **kwargs) -> str:
        secret = self.get('secret', {}, update=update, max_age=tempo)
        if len(secret) > 0 :
            return secret
        time = self.time()
        seed = seed or self.random_int(0, 1000000) * self.time() / (self.random_int(1, 1000) + 1)
        nonce = str(int(secret.get('nonce', 0)) + 1)
        secret = self.sign({'time': time, 'nonce': nonce}, key=key,**kwargs)
        self.put('secret', secret)
        return secret

    def tempo_secret(self, key:str = None,  tempo=1, seed=None, **kwargs) -> str:
        """
        Get a secret that is valid for a certain time
        """
        return self.secret(key=key, seed=seed, update=True, tempo=tempo, **kwargs)

    def put_json(self, 
                 path:str, 
                 data:Dict, 
                 meta = None,
                 verbose: bool = False,
                 **kwargs) -> str:
        if not path.endswith('.json'):
            path = path + '.json'
        path = self.abspath(path)
        if isinstance(data, dict):
            data = json.dumps(data)
        self.put_text(path, data)
        return path

    def env(self, key=None):
        """
        Get the environment variables
        """
        import os
        env = {}
        for k,v in os.environ.items():
            env[k] = v
        if key != None:
            return env.get(key, None)
        return env

    def rm(self, path:str, possible_extensions = ['json'], avoid_paths = ['~', '/']):
        avoid_paths = list(set((avoid_paths)))
        path = self.abspath(path)
        avoid_paths = [self.abspath(p) for p in avoid_paths] 
        assert path not in avoid_paths, f'Cannot remove {path}'
        path_exists = lambda p: os.path.exists(p)
        if not path_exists(path): 
            for pe in possible_extensions:
                if os.path.exists(path + f'.{pe}'):
                    path = path + f'.{pe}'
                    break
            if not path_exists(path):
                return {'success':False, 'message':f'{path} does not exist'}
        if os.path.isdir(path):
            shutil.rmtree(path)
        if os.path.isfile(path):
            os.remove(path)
        assert not os.path.exists(path), f'{path} was not removed'
        return {'success':True, 'message':f'{path} removed'}
    
    def glob(self, path:str='./', depth:Optional[int]=4, recursive:bool=True, files_only:bool = True, include_hidden=False):
        path = self.abspath(path)
        if depth > 0:
            paths = []
            for path in self.ls(path):
                if os.path.isdir(path):
                    paths += self.glob(path, depth=depth-1)
                else:
                    paths.append(path)
        else:
            return []
        if files_only:
            paths =  list(filter(lambda f:os.path.isfile(f), paths))
        if not include_hidden: 
            paths = [ p for p in paths if '/.' not in p]
        return paths
    
    def get_json(self, path:str,default:Any=None, **kwargs):
        path = self.abspath(path)

        # return self.util('get_json')(path, default=default, **kwargs)
        if not path.endswith('.json'):
            path = path + '.json'
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r') as file:
                data = json.load(file)
        except Exception as e:
            return default
        return data
    
    def get_path(self, 
                     path:str = None, 
                     extension:Optional[str]=None) -> str:
        '''
        Abspath except for when the path does not have a

        if you specify "abc" it will be resolved to the storage dir
        {storage_dir}/abc, in this case its ~/.mod
        leading / or ~ or . in which case it is appended to the storage dir
        '''
        storage_dir = self.storage_dir()
        if path == None :
            return storage_dir
        if path.startswith('/'):
            path = path
        elif path.startswith('~/') :
            path = os.path.expanduser(path)
        elif path.startswith('.'):
            path = os.path.abspath(path)
        else:
            if storage_dir not in path:
                path = os.path.join(storage_dir, path)
        if extension != None and not path.endswith(extension):
            path = path + '.' + extension
        return path

    def put_text(self, path:str, text:str, key=None) -> None:
        # Get the absolute path of the file
        path = self.abspath(path)
        dirpath = os.path.dirname(path)
        if not os.path.exists(dirpath):
            os.makedirs(dirpath, exist_ok=True)
        if not isinstance(text, str):
            text = self.python2str(text)
        if key != None:
            text = self.get_key(key).encrypt(text)
        # Write the text to the file
        with open(path, 'w') as file:
            file.write(text)
        # get size
        return {'success': True, 'path': f'{path}', 'size': len(text)*8}

    path = write =  get_path
    
    def ls(self, path:str = './', 
           search = None,
           include_hidden = False, 
           depth=None,
           return_full_path:bool = True):
        """
        provides a list of files in the path 
        this path is relative to the mod path if you dont specifcy ./ or ~/ or /
        which means its based on the mod path
        """
        path = self.abspath(path)
        try:
            ls_files = os.listdir(path)
        except Exception as e:
            return []
        if return_full_path:
            ls_files = [os.path.abspath(os.path.join(path,f)) for f in ls_files]
        ls_files = sorted(ls_files)
        
        if search != None:
            ls_files = list(filter(lambda x: search in x, ls_files))

        return ls_files

    def put(self, 
            k: str, 
            v: Any,  
            encrypt: bool = False, 
            password: str = None, **kwargs) -> Any:
        '''
        Puts a value in the config
        '''
        k = self.get_path(k)
        encrypt = encrypt or password != None
        if encrypt or password != None:
            v = self.encrypt(v, password=password)
        data = {'data': v, 'encrypted': encrypt, 'timestamp': time.time()}    
        return self.put_json(k, data)
    
    def get(self,
            k:str, 
            default: Any=None, 
            max_age:str = None,
            update :bool = False,
            password : str = None,
            verbose = False,
            **kwargs) -> Any:
        
        '''
        Puts a value in sthe config, with the option to encrypt it
        Return the value
        '''
        k = self.get_path(k)
        data = self.get_json(k, default=default, **kwargs)
        if password != None:
            assert data['encrypted'] , f'{k} is not encrypted'
            data['data'] = self.decrypt(data['data'], password=password)
        data = data or default
        if not isinstance(data, dict):
            return default
        if update:
            return default
        if max_age != None:
            # check if the data is expired
            timestamp = 0
            for k in ['timestamp', 'time']:
                if k in data:
                    timestamp = data[k]
                    break
            expired =  (time.time() - float(timestamp)) > max_age
            if expired:
                return default
        if isinstance(data, dict) and 'data' in data:
            return data['data']
        else: 
            return data

    def get_text(self, path: str, **kwargs ) -> str:
        # Get the absolute path of the file
        path = self.abspath(path)
        from .utils import get_text
        return get_text(path, **kwargs)

    def text(self, path: str = './', **kwargs ) -> str:
        # Get the absolute path of the file
        path = self.abspath(path)
        assert not self.home_path == path, f'Cannot read {path}'
        if os.path.isdir(path):
            return self.file2text(path)
        with open(path, 'r') as file:
            content = file.read()
        return content

    def sleep(self, period):
        time.sleep(period) 


    def fnschema(self, fn:str = '__init__', public=True, avoid_arguments = ['self', 'cls'],**kwargs)->dict:
        '''
        Get function schema of function in self
        ''' 
        public = bool(public)
        fn_obj = self.fn(fn)
        if not callable(fn_obj):
            return {'fn_type': 'property', 'type': type(fn_obj).__name__}
        
        fn_signature = inspect.signature(fn_obj)

        schema = {'input': {}, 'output': {}, 'docs': '', 'cost': 0, 'name': '', 'content': '' , 'public': public}

        for k,v in dict(fn_signature._parameters).items():
            if k  in avoid_arguments:
                continue
            else:
                schema['input'][k] = {
                        'value': "_empty"  if v.default == inspect._empty else v.default, 
                        'type': '_empty' if v.default == inspect._empty else str(type(v.default)).split("'")[1] 
                }

        # OUTPUT SCHEMA
        schema['output'] = {
            'value': None,
            'type': str(fn_obj.__annotations__.get('return', None) if hasattr(fn_obj, '__annotations__') else None)
        }
        schema['docs'] = fn_obj.__doc__
        schema['cost'] = 0 if not hasattr(fn_obj, '__cost__') else fn_obj.__cost__ # attribute the cost to the function
        schema['name'] = fn_obj.__name__
        if public:
            schema['content'] = inspect.getsource(fn_obj)
        return schema

    fnschema = fnschema

    def schema(self, obj = None , fns=None, public=False,  verbose=False, **kwargs)->dict:
        '''
        Get function schema of function in self
        '''   
        schema = {}
        obj = obj or 'mod'
        public = bool(public)
        if callable(obj):
            return self.fnschema(obj, public=public, **kwargs)
        elif isinstance(obj, str):
            if '/' in obj :
                return self.fnschema(obj, public=public,  **kwargs)
            else:
                obj = self.mod(obj)
        elif hasattr(obj, '__class__'):
            obj = obj.__class__
        fns = fns or self.fns(obj)
        for fn in self.fns(obj):
            try:
                schema[fn] = self.fnschema(getattr(obj, fn), public=public,  **kwargs)
            except Exception as e:
                self.print(self.detailed_error(e), color='red', verbose=verbose)
        return schema

    def code(self, obj = None, search=None, *args, **kwargs) -> Union[str, Dict[str, str]]:
        if '/' in str(obj):
            obj = self.fn(obj)
        elif hasattr(self, obj):
            obj = getattr(self, obj)
        else:
            obj = self.mod(obj)
        return  inspect.getsource(obj)
        
    def call(self, fn , params=None, **kwargs): 
        return self.fn('client/forward')(fn, params, **kwargs)
    
    def content(self, mod = None , search=None, ignore_folders = ['mods', 'mods', 'private', 'data'], relative=False,  **kwargs) ->  Dict[str, str]:
        """
        get the content of the mod as a dict of file path to file content
        return a dict of file path to file content
        """
        mod = mod or 'mod'
        dirpath = self.dirpath(mod)
        content = self.file2text(dirpath)
        content = {k[len(dirpath+'/'): ]:v for k,v in content.items()}
        # ignore if .mods . is in the path
        content = {k:v for k,v in content.items() if not any(['/'+f+'/' in k for f in ignore_folders])}
        return dict(sorted(content.items()))

    cont = codemap =  cm =  content

    def cid(self, mod , **kwargs) -> Union[str, Dict[str, str]]:
        """
        get the cid of the mod
        """
        return self.hash(self.content(mod, **kwargs))

    def dir(self, obj=None, search=None, *args, **kwargs):
        obj = self.obj(obj)
        if search != None:
            return [f for f in dir(obj) if search in f]
        return dir(obj)
    
    def fns(self, obj: Any = None,
                      search = None,
                      include_hidden = False,
                      include_children = False,
                      **kwargs) -> List[str]:
        '''
        Get a list of functions in a class (in text parsing)
        Args;
            obj: the class to get the functions from
            include_parents: whether to include the parent functions
            include_hidden:  whether to include hidden functions (starts and begins with "__")
        '''
        obj = self.mod(obj)
        fns = dir(obj)
        fns = sorted(list(set(fns)))
        if search != None:
            fns = [f for f in fns if search in f]
        if not include_hidden: 
            fns = [f for f in fns if not f.startswith('__') and not f.startswith('_')]
        return fns

    def mods(self, search=None,  startswith=None, endswith=None, **kwargs)-> List[str]:  
        return list(self.tree(search=search, endswith=endswith, startswith=startswith , **kwargs).keys())
    am = ms = mods

    mods = mods
    def get_mods (self, search=None, **kwargs):
        return self.mods (search=search, **kwargs)

    def core_mods(self) -> List[str]:
        return list(self.core_tree().keys())
    cm = cmods = core_mods = core_mods 

    def local_mods(self) -> List[str]:
        return list(self.local_tree().keys())
    lm = lmods = local_mods = local_mods

    def mod2schema(self, mod=None, max_age=30, update=False, core=True, verbose=False) -> List[str]:
        mod2schema = self.get('mod2schema', default=None, max_age=max_age, update=update)
        if mod2schema == None:
            mods = self.core_mods () if core else self.mods()
            mod2schema = {}
            for mod in mods:
                try:
                    mod2schema[mod] = self.schema(mod)
                except Exception as e:
                    self.print(f'mod2schemaError({e})', color='red', verbose=verbose)
            # self.put('mod2schema', mod2schema)
        return mod2schema 
    def mod2fns(self, mod=None, max_age=30, update=False, core=True, verbose=False) -> List[str]:
        mod2fns = self.get('mod2fns', default=None, max_age=max_age, update=update)
        if mod2fns == None:
            mods = self.core_mods() if core else self.mods()
            mod2fns = {}
            for mod in mods:
                try:
                    mod2fns[mod] = self.fns(mod)
                except Exception as e:
                    self.print(f'mod2fnsError({e})', color='red', verbose=verbose)
            # self.put('mod2fns', mod2fns)
        return mod2fns

    def info(self, 
            mod:str='mod',  # the mod to get the info of
            schema = True, # whether to include the schema of the mod
            fns  = True, # which functions to include in the schema
            url = None, # whether to include the url of the mod
            desc = False, # whether to include the description of the mod
            key = None, # the key to sign the info with
            public =  False, # whether to include the source code of the mod
            **kwargs):
        """
        Get the info of a mod, including its schema, key, cid, and code if specified.
        """
        mod  =  self.get_name(mod or 'mod')
        cid = self.cid(mod)
        key = self.get_key(key or mod)
        info =  {
                'name': mod, 
                'key': key.address,  
                'cid': cid,
                'time': time.time(),
                'public': bool(public),
                'dirpath': self.dirpath(mod, relative=True),
                }
        if public:
            info['content'] = self.content(mod)
        if schema:
            info['schema'] = self.schema(mod , public=public)
        if fns:
            info['fns'] = self.fns(mod)
        if url: 
            info['url'] = self.namespace().get(mod, None)
        if desc:
            info['desc'] = self.desc(mod)
        info['signature'] = self.sign(info, key=key)
        assert self.verify_info(info), f'Invalid signature {info["signature"]}'
        return  info

    card = info 

    def desc(self, mod='mod', **kwargs):
        return self.fn('desc/')(mod, **kwargs)

    def verify_info(self, info:Union[str, dict]=None, **kwargs) -> bool:
        """
        verify the info of the mod
        """
        if isinstance(info, str):
            info = self.info(info, **kwargs)
        signature = info.pop('signature')
        verify = self.verify(data=info, signature=signature, address=info['key'])  
        assert verify, f'Invalid signature {signature}'
        info['signature'] = signature
        return info

    def epoch(self, *args, **kwargs):
        return self.run_epoch(*args, **kwargs)

    def pwd2key(self, pwd, **kwargs) -> str:
        return self.mod('key')().str2key(pwd, **kwargs)

    _executors = {}
    
    def submit(self, 
                fn, 
                params = None,
                timeout:int = 40, 
                mod: str = None,
                mode:str='thread',
                max_workers : int = 100,
                ):
        executor = self.executor(mode=mode, max_workers=max_workers)
        if mode == 'thread':
            future = executor.submit(self.fn(fn), params=params, timeout=timeout)
        else:
            future =  executor.submit(self.fn(fn), *args, **kwargs)
        return future 

    def fn(self, fn:Union[callable, str], params:str=None, splitter='/', default_fn='forward', default_mod = 'mod') -> 'Callable':
        """
        Gets the function from a string or if its an attribute 
        """
        if callable(fn):
            return fn
        elif hasattr(self, fn):
            fn_obj = getattr(self, fn)
        elif fn.startswith('/'):
            fn_obj = getattr(self.mod(default_mod)(), fn[1:])
        elif fn.endswith('/'):
            fn_obj = getattr(self.mod(fn[:-1])(), default_fn)
        elif '/' in fn:
            mod, fn = fn.split('/')
            mod = self.mod(mod)()
            fn_obj = getattr(mod, fn)
        else:
            raise Exception(f'Function {fn} not found')
        if params:
            return fn_obj(**params)
        return fn_obj
    
    get_fn = fn

    def get_args(self, fn) -> List[str]:
        """
        get the arguments of a function
        params:
            fn: the function
        """        
        if not callable(fn):
            return []
        try:
            args = inspect.getfullargspec(fn).args
        except Exception as e:
            args = []
        return args

    def hosts(self):
        return self.fn('remote/hosts')()


    def host(self):
        return self.key().address
    def how(self, mod, query, *extra_query) : 
        code = self.code(mod)
        query = ' '.join([query, *extra_query])
        return self.fn('model.openrouter/')(f'query={query} code={code}')

    def client(self, *args, **kwargs) -> 'Client':
        """
        Get the client for the mod
        """
        return self.fn('client/client')( *args, **kwargs)
    
    def classes(self, path='./',  **kwargs):
        """
        Get the classes for each path inside the path variable
        """
        path2classes = self.path2classes(path=path,**kwargs)
        classes = []
        for k,v in path2classes.items():
            classes.extend(v)
        return classes  


    def mnemonic(self, words=24):
        """
        Generates a mnemonic phrase of the given length.
        """

        if words not in [12, 15, 18, 21, 24]:
            if words > 24 : 
                # tile to over 24
                tiles = words // 24 + 1
                mnemonic_tiles = [self.mnemonic(24) for _ in range(tiles)]
                mnemonic = ' '.join(mnemonic_tiles)
            if words < 24:
                # tile to under 12
                mnemonic = self.mnemonic(24)
            return ' '.join(mnemonic.split()[:words])
        return self.mod('key')().generate_mnemonic(words=words)

    
    def path2objectpath(self, path:str, **kwargs) -> str:
        """
        Converts a path to an object path (for instance ./foo/bar.py to foo.bar)
        """
        path = os.path.abspath(path)
        dir_prefixes  = [os.getcwd(), self.lib_path, self.home_path]
        for dir_prefix in dir_prefixes:
            if path.startswith(dir_prefix):
                path =   path[len(dir_prefix) + 1:].replace('/', '.')
                break
        if any([path.endswith(s) for s in ['.py']]):
            path = path[:-3]
        return path.replace('__init__.', '.')

    def path2name(self,
                    path:str , 
                    ignore_folder_names = ['mods', 'agents', 'src', 'mods'], 
                    possible_suffixes = ['_', '']):
    
        ignore_folder_names = [ f + s for f in ignore_folder_names for s in possible_suffixes]
        name = self.path2objectpath(path)
        name_chunks = []
        for chunk in name.split('.'):
            if chunk in ignore_folder_names:
                continue
            if chunk not in name_chunks:
                name_chunks += [chunk]
        if len(name_chunks) > 0:
            if name_chunks[0] == self.name:
                name_chunks = name_chunks[1:]
        else:
            return self.name
        return '.'.join(name_chunks)
    
    def path2classes(self, path='./', depth=4, tolist = False, **kwargs) :

        """
        Get the classes for each path inside the path variable
        params:
        - path: The path to search for classes
        - depth: The maximum depth to search
        - tolist: Whether to return a list of classes or a dict

        returns:
        - if tolist is True, returns a list of classes
        """
        class_suffix = ':', 
        class_prefix = 'class '
        path = self.abspath(path)
        path2classes = {}
        if os.path.isdir(path) and depth > 0:
            for p in self.ls(path):
                try:
                    for k,v in self.path2classes(p, depth=depth-1).items():
                        if len(v) > 0:
                            path2classes[k] = v
                except Exception as e:
                    pass
        elif os.path.isfile(path) and any([path.endswith(s) for s in ['.py']]):
            classes = []
            code = self.get_text(path)
            objectpath = self.path2objectpath(path)
            for line in code.split('\n'):
                if line.startswith(class_prefix) and line.strip().endswith(class_suffix):
                    new_class = line.split(class_prefix)[-1].split('(')[0].strip()
                    if new_class.endswith(class_suffix):
                        new_class = new_class[:-1]
                    if ' ' in new_class:
                        continue
                    classes += [new_class]
            if objectpath.startswith(path):
                objectpath = objectpath[len(path)+1:]
            objectpath = objectpath.replace('/', '.')
            path2classes =  {path:  [objectpath + '.' + cl for cl in classes]}
        if tolist:
            classes = []
            for k,v in path2classes.items():
                classes += v
            return classes
        return path2classes

    def path2fns(self, path = './', tolist=False,**kwargs):
        path2fns = {}
        fns = []
        path = os.path.abspath(path)
        if os.path.isdir(path):
            for p in glob.glob(path+'/**/**.py', recursive=True):
                for k,v in self.path2fns(p, tolist=False).items():
                    if len(v) > 0:
                        path2fns[k] = v
        else:
            code = self.get_text(path)
            path_prefix = self.path2objectpath(path)
            for line in code.split('\n'):
                if line.startswith('def ') or line.startswith('async def '):
                    fn = line.split('def ')[-1].split('(')[0].strip()
                    fns += [path_prefix + '.'+ fn]
            path2fns =  {path: fns}
        if tolist:
            fns = []
            for k,v in path2fns.items():
                fns += v
            return fns
        return path2fns

    ensure_syspath_flag = False

    def ensure_syspath(self):
        """
        Ensures that the path is in the sys.path
        """
        if not self.ensure_syspath_flag:
            import sys
            paths = [self.pwd(), self.repo_path]
            for path in paths:
                if path not in sys.path:
                    sys.path.append(path)
        return {'paths': sys.path, 'success': True}      
    obj_cache = {}
    def obj(self, key:str, **kwargs)-> Any:
        # add pwd to the sys.path if it is not already there
        self.ensure_syspath()
        if key in self.obj_cache:
            return self.obj_cache[key]
        else:
            from .utils import import_object
            obj = self.obj_cache[key] = import_object(key, **kwargs)
        return obj

    def obj_exists(self, path:str, verbose=False)-> Any:
        # better way to check if an object exists?
        try:
            self.obj(path, verbose=verbose)
            return True
        except Exception as e:
            return False

    def object_exists(self, path:str, verbose=False)-> Any:
        return self.obj_exists(path, verbose=verbose)

    def mod_exists(self, mod:str, **kwargs) -> bool:
        '''
        Returns true if the mod exists
        '''
        try:
            mod = self.get_name(mod)
            mod_exists = mod in self.tree()
        except Exception as e:
            mod_exists =  False
        if not mod_exists:
            mod_path = os.path.join(self.mods_path, mod)
            mod_exists = os.path.exists(mod_path) and os.path.isdir(mod_path)
        return mod_exists

    

    def logs(self, *args, **kwargs):
        return self.fn('pm/logs')(*args, **kwargs)
    
    def locals(self, **kwargs):
        return list(self.get_tree(self.pwd(), **kwargs).keys())

    def cwd(self, mod=None):
        if mod:
            return self.dirpath(mod)
        return os.getcwd()

    def anchor_file(self, path, file_depth=4):

        """
        desc:
            get the tree of the mod
            get the path from the tree
            search for the anchor file in the path
            assume the potential anchor files can be in the last two folders 
            names like model/openrouter/model.py or model/openrouter/openrouter.py
            return the first anchor file found
            return None if no anchor file is found

        parms: 
            path : the path to search for the anchor file
            file_types : the file types to search for
        returns:
            the anchor file path if found
        """
        path = path.replace('/', '.')
        # IF FOR SOME REASON WE ARE SPECIFYING A PATH THAT IS A FILE (NOT IN THE TREE AS THE TREE ONLY HAS FOLDERS)
        path = self.dirpath(path)

        # this is rather nuanced, but it basically says that the anchor names are the anchor names plus the path chunks
        # removing the home_path prefix
        anchor_names =  self.anchor_names.copy() + path[len(self.home_path+'/'):].split('/')
        files = list(sorted( self.files(path, depth=file_depth), key=lambda x: len(x)))
        # filter files that are in the file types
        files = [f for f in files if any([f.endswith('.' + ft) for ft in self.file_types])]
        if len(files) == 1:
            return files[0]
        for f in files:
            if any([f.endswith('/' + an + '.' + ft) for an in anchor_names for ft in self.file_types]):
                return f
        
        return None


    def anchor_object(self, path):
        path = self.get_name(path)
        anchor_file = self.anchor_file(path)
        if anchor_file:
            classes =  self.classes(anchor_file)
            assert len(classes) > 0, f'No classes found in {anchor_file}'
            class_obj_path = classes[-1]
            return self.obj(class_obj_path)
        else: 
            print(self.tree(search=path), anchor_file)
            raise Exception(f'No anchor file found in {path}, ')

    af = anchor_file
    ao = anchor_object

    def get_name(self, 
                name:Optional[str]=None, 
                avoid_terms = ['src', 'mods', '_mods', 'core', 'modules', '_ext', 'ext']) -> str:
        name = name or 'mod'
        if any([name.startswith(p) for p in ['.', '~', '/']]):
            name = self.path2name(name)
        name = name.replace('/', '.')
        new_name = []
        for name_chunk in name.split('.'):
            if name_chunk not in avoid_terms:
                new_name.append(name_chunk)
        name = '.'.join(new_name)
        if len(name) == 0:
            return self.name
        return name.strip('.').lower()

    def get_tree(self, 
                path:Optional[str]=None, 
                search:Optional[str]=None, 
                depth=2, 
                max_depth=7,
                avoid_terms = ['src', 'mods', '_mods', 'core', 'core'],
                avoid_prefixes = ['__',],
                avoid_suffixes = ['__', '/utils'],
                ignore_suffixes = ['/src', '/core'],
                folders:bool = True, 
                update=False,  
                **kwargs): 
                
        path = path or self.core_path
        cache_key = self.abspath(f'~/.mod/tree/path={path.replace("/", "_")}')
        tree = self.get(cache_key, None, update=update)
        if tree == None:
            path = path or self.core_path
            if folders:
                paths = list(self.folders(path, depth=max_depth))
            else:
                paths = list(self.files(path, depth=max_depth))
            def process_path(x):
                for k in ignore_suffixes: 
                    if x.endswith(k):
                        x = x[:-len(k)]
                x_list =  x.split('/')
                if len(x_list) >=2 :
                    if x_list[-1] == x_list[-2]: 
                        x = '/'.join(x_list[:-1])
                # remove avoid terms
                return x    
            new_tree = {}
            for p in paths:
                name = self.get_name(p)
                p = process_path(p)
                if name in new_tree:
                    # if the path is shorter, replace it
                    if len(p) < len(new_tree[name]):
                        new_tree[name] = p
                        continue
                else:
                    new_tree[name] = process_path(p)
            tree = new_tree
            tree = dict(sorted(tree.items(), key=lambda x: len(x[0])))
            for k,v in self.shortcuts.items():
                if v in tree:
                    tree[k] = tree[v]
            self.put(cache_key, tree)

        tree = dict(sorted(tree.items()))
        if depth < max_depth: 
            tree = {k:v for k,v in tree.items() if k.count('.') < depth}
        if search:
            tree = {k:v for k,v in tree.items() if search in k}
        return tree

    def core_tree(self, search=None, depth=1,  **kwargs): 
        return self.get_tree(self.core_path, search=search, depth=depth, **kwargs) 

    def mods_tree(self, search=None,  depth=1,**kwargs): 
        return self.get_tree(self.mods_path, search=search, depth=depth, **kwargs)

    def ext_tree(self, search=None, depth=1, **kwargs):
        return self.get_tree(self.ext_path, depth=depth,  search=search, **kwargs )

    def ext_mods(self, search=None, **kwargs):
        return list(self.ext_tree(search=search, **kwargs).keys())

    def local_tree(self, search=None, depth=1, **kwargs):
        return self.get_tree(os.getcwd(), depth=depth,  search=search, **kwargs )

    def tree(self, search=None, depth=8, **kwargs):
        """
        get the full tree of the mods, local and core
        ORDER OF PRIORITY:
        1. core
        2. local
        3. mods

        This means that if a mod exists in core and local, the core version will be used
        if a mod exists in local and mods, the local version will be used
        if a mod exists in core and mods, the core version will be used
        
        """
        local_tree = self.local_tree(search=search, depth=depth, **kwargs) if os.getcwd() != self.lib_path else {}
        ext_tree = self.ext_tree(search=search, depth=depth, **kwargs)
        return {
            **ext_tree,
            **self.mods_tree(search=search, depth=depth,  **kwargs),
            **local_tree,
            **self.core_tree(search=search, depth=depth, **kwargs),
                }

    def dirpath(self, mod=None, depth=6, relative=False) -> str:
        """
        get the directory path of the mod
        """
        if mod == None or mod == 'mod':
            return self.lib_path

        mod = self.shortcuts.get(mod, mod)
        mod = mod.lower()
        def get_dirpath_from_tree( update=False):
            tree = self.tree(folders=True, depth=depth, update=update)
            tree_options = [k for k in tree.keys() if all([part in k for part in mod.split('.')])]
            tree_options = sorted(tree_options, key=lambda x: len(x))
            if len(tree_options) > 0:
                dirpath = tree[tree_options[0]]  
            else:
                dirpath = None
            return dirpath

        dirpath = get_dirpath_from_tree(update=False)
        if dirpath == None:
            dirpath = get_dirpath_from_tree(update=True)
            if dirpath == None:
                raise Exception(f'Module {mod} not found')
        # remove any trailing repeats of the mod name in the dirpath
        if relative:
            dirpath = os.path.relpath(dirpath, self.lib_path)
        return  dirpath

    dp = dirpath


    def addpath(self, path, name=None, update=True):
        assert os.path.exists(path), f'Path {path} does not exist'
        path = self.abspath(path)
        name = name or path.split('/')[-1]
        dirpath = self.mods_path + '/' + name.replace('.', '/')
        self.cmd(f'cp -r {path} {dirpath}')
        files = self.files(dirpath, relative=True)
        return {'name': name, 'path': dirpath, 'msg': 'Mod Created from path', 'files': files}

    def addmod(self,  path  , name=None, base='base', update=True):
        """
        make a new mod
        """
        name = name or path.split('/')[-1]
        mods_path = self.mods_path
        dirpath = mods_path + '/' + name.replace('.', '/')
        mod_name = dirpath.split('/')[-1]
        for k,v in self.content(base).items():
            new_path = dirpath + '/' +  k.replace(base, mod_name)
            print(f'Creating {new_path} for mod {name}')
            self.put_text( new_path, v)
        files = self.files(dirpath)
        self.tree(update=True)
        return {'name': name, 'path': dirpath, 'msg': 'Mod Created', 'base': base, 'cid': self.cid(name)}

    add_mod = addmod

    def test_fork(self, base = 'base', name= 'base2', path=None, update=True, ):
        """
        test fork
        """
        fork_info = self.fork(base=base, name=name, path=path, update=update)
        fork_cid = fork_info['cid']
        base_cid = self.cid(base)
        assert fork_cid != base_cid, f'Fork failed: {fork_cid} == {base_cid}'
        self.rmmod(name)
        assert not self.mod_exists(name), f'Fork removal failed: {name} still exists'
        return fork_info
    
    create = new = add = fork = add_mod 

    def urls(self, *args, **kwargs):
        return self.fn('pm/urls')(*args, **kwargs)

    def servers(self, *args, **kwargs):
        return list(self.namespace().keys())

    executor_cache = {}
    def executor(self,  max_workers=8, mode='thread', cache=True):
        path = "executor/" + mode + '/' + str(max_workers)
        if cache and path in self.executor_cache:
            return self.executor_cache[path]
        if mode == 'process':
            from concurrent.futures import ProcessPoolExecutor
            executor =  ProcessPoolExecutor(max_workers=max_workers)
        elif mode == 'thread':
            executor =  self.mod('executor')(max_workers=max_workers)
        else:
            raise ValueError(f"Unknown mode: {mode}. Use 'thread', 'process' or 'async'.")
        if cache:
            self.executor_cache[path] = executor
        return executor

    def server_exists(self, server:str = 'mod', *args, **kwargs):
        return  self.fn('pm/server_exists')(server, *args, **kwargs)

    def namespace(self, *args, **kwargs):
        return self.fn('pm/namespace')(*args, **kwargs)

    def epoch(self, *args, **kwargs):
        return self.fn('vali/epoch')(*args, **kwargs)

    def up(self, mod = 'mod'):
        return self.fn('pm/up')(mod)
    def down(self, mod = 'mod'):
        return self.fn('pm/down')(mod)

    def enter(self, image = 'mod'):
        return self.fn('pm/enter')(image)

    def owner(self):
        return self.get_key().address

    own = owner

    def repo2path(self, search=None):
        repo2path = {}
        for p in self.ls('~/'): 
            if os.path.exists(p+'/.git'):
                r = p.split('/')[-1]
                if search == None or search in r:
                    repo2path[r] = p
        return dict(sorted(repo2path.items(), key=lambda x: x[0]))

    def repos(self, search=None):
        return list(self.repo2path(search=search).keys())

    def help(self, mod='mod', query:str = 'what is this', *extra_query, **kwargs):
        query = ' '.join(list(map(str, [query, *extra_query])))
        mod =  mod or mod
        context = self.context(path=self.core_path)
        return self.mod('agent')().ask(f'given the code {self.code(mod)} and CONTEXT OF COMMUNE {context} anster wht following question: {query}', preprocess=False)
    
    def ask(self, *args, mod=None, path='./' , stream=1, context=False, **kwargs):
        # commune_readmes = self.readmes(path=path)
        if mod != None:
            args = [self.code(mod)] + list(args)
        if context:
            context = self.context(path=path)
            args = [f'CONTEXT OF COMMUNE {context}'] + list(args)
        msg = ' '.join(list(map(str, args)))
        return self.mod("openrouter")().forward(msg, stream=stream, **kwargs) 

    def readmes(self,  path='./', search=None, avoid_terms=['mods']):
        files =  self.files(path)
        files = [f for f in files if f.endswith('.md')]
        files = [f for f in files if all([avoid not in f for avoid in avoid_terms])]
        if search != None:
            files = [f for f in files if search in f]
        return files

    def context(self, path=None):
        path = path or self.core_path
        return self.code()

    def import_mod(self, mod:str):
        from importlib import import_module
        mod = mod.replace(f'{self.lib_name}.{self.lib_name}', self.lib_name)
        return import_module(mod)

    def kill(self, server:str = 'mod'):
        return self.fn('pm/kill')(server)

    def kill_all(self):
        return self.fn('pm/kill_all')()

    killall = kill_all

    _config_cache = {}
    def config(self, mod=None):
        """
        Returns the config file in the path
        """
        if str(mod) in self._config_cache:
            return self._config_cache[str(mod)]
        configs = self.config_paths(mod=mod)
        if len(configs) == 0:
            return {}
        config =  self.get_json(configs[0])
        self._config_cache[str(mod)] = config
        return config

    def config_paths(self, mod=None, 
                modes=['yaml', 'json'], 
                search=None, 
                config_name_options = ['config', 'cfg', 'mod', 'block',  'agent', 'mod', 'bloc', 'server'],
                names=['config', 'cfg', 'mod', 'block',  'agent', 'mod', 'bloc']):
        """
        Returns a list of config files in the path
        """
        if mod == None:
            path = '/'.join(__file__.split('/')[:-3])
        else:
            path = self.dirpath(mod)
        def is_config(f):
            return any(f.endswith(f'/{name}.{m}') for name in config_name_options for m in modes)
        configs =  [f for f in  self.files(path) if is_config(f)]
        if search != None:
            configs = [f for f in configs if search in f]
        return list(sorted(configs, key=lambda x: len(x)))

    def config_path(self, mod=None, **kwargs):
        configs = self.config_paths(mod=mod, **kwargs)
        if len(configs) == 0:
            return None
        return configs[0]

    def serve(self, mod:str = 'mod', port:int=None, remote=True, **kwargs):
        return self.fn('server/serve')(mod, port=port, remote=remote, **kwargs)

    def exec(self, mod:str = 'mod', *args, **kwargs):
        return self.fn('pm/exec')(mod, *args, **kwargs)

    def app(self, mod=None, **kwargs):
        if mod:
            return self.fn(mod + '/app' )()
        return self.fn('app/serve')(**kwargs)

    def confirm(self, message:str = 'Are you sure?', suffix = ' (y/n): '):
        confirm = input(message + suffix)
        if confirm.lower() != 'y':
            raise KeyboardInterrupt('Operation cancelled by user')
        return True

    def push(self,  comment, *extra_comment, mod = None, safety=False):
        path = self.dp(mod, relative=True)
        comment = ' '.join([comment, *extra_comment])
        assert os.path.exists(path), f'Path {path} does not exist'
        cmd = f'cd {path} && git add . && git commit -m "{comment}" && git push'
        if safety:
            self.confirm(f'Are you sure you want to push to {path} with comment: {comment}?')
        os.system(cmd)
        return {'msg': f'Pushed to {path} with comment: {comment}'}
        
    def git_info(self, path:str = None, name:str = None, n=10):
        return self.fn('git/get_info', {'path': path, 'name': name, 'n': n})
    
    def isrepo(self, mod:str = None):
        path = self.dirpath(mod)
        return os.path.exists(path + '/.git')

    def cpmod(self, from_mod:str = 'dev', to_mod:str = 'dev2', force=True):
        """
        Copy the mod to the git repository
        """
        from_path = self.dirpath(from_mod)
        if not os.path.exists(from_path):
            raise Exception(f'Mod {from_path} does not exist')

        to_mod=to_mod.replace('.', '/')
        to_path = to_mod
        if to_path.startswith('./') or to_path.startswith('~/') or to_path.startswith('/'):
            to_path = self.abspath(to_mod)
        else:
            to_path = self.mods_path + '/' + to_mod.replace('.', '/')
        if os.path.exists(to_path):
            if force or input(f'Path {to_path} already exists. Do you want to remove it? (y/n)') == 'y':
                for f in self.files(to_path):
                    print(f'Removing {f}')
                    self.rm(f)
        from_files = self.files(from_path)
        for f in from_files:
            self.cp(f, to_path + '/' + f[len(from_path)+1:])
        # assert os.path.exists(to_path), f'Failed to copy {from_path} to {to_path}'
        self.tree(update=1)
        return { 
                'from': {'mod': from_mod, 'path': from_path, 'cid': self.cid(from_mod)}, 
                'to': {'path': to_path, 'mod': to_mod, 'cid': self.cid(to_mod)},
                } 

    def mvmod(self, from_mod:str = 'dev', to_mod:str = 'dev2'):
        """
        Move the mod to the git repository
        """
        from_path = self.dirpath(from_mod)
        to_path = self.mods_path + '/' + to_mod.replace('.', '/')
        for f in self.files(from_path):
            print(f'Moving {f} to {to_path + "/" + f[len(from_path)+1:]}')

        
        self.mv(from_path, to_path)
        self.tree(update=1)
        return {'from': {'mod': from_mod, 'path': from_path}, 'to': {'mod': to_mod, 'path': to_path}}

    mv_mod = mvmod

    def rmmod(self, mod:str = 'dev'):
        """
        Remove the mod from the git repository
        """
        path = self.dirpath(mod)
        assert os.path.exists(path), f'Mod {mod} does not exist'
        self.rm(path)
        self.tree(update=1)
        return {'success': True, 'msg': 'removed mod'}

    def address2key(self, *args, **kwargs):
        return self.fn('key/address2key')(*args, **kwargs)

    def clone(self, mod:str = 'dev', name:str = None):
        repo2path = self.repo2path()
        if os.path.exists(mod):
            to_path =  self.mods_path + '/' + mod.split('/')[-1]
            from_path = mod
            self.rm(to_path)
            self.cp(from_path, to_path)
            assert os.path.exists(to_path), f'Failed to copy {from_path} to {to_path}'
        elif 'github.com' in mod:
            code_link = mod
            mod = name or mod.split('/')[-1].replace('.git', '')
            # clone ionto the mods path
            to_path = self.mods_path + '/' + mod
            cmd = f'git clone {code_link} {self.mods_path}/{mod}'
            self.cmd(cmd, cwd=self.mods_path)
        else:
            raise Exception(f'Mod {mod} does not exist')
        git_path = to_path + '/.git'
        if os.path.exists(git_path):
            self.rm(git_path)
        self.tree(update=1)
        return {'success': True, 'msg': 'added mod',  'to': to_path}

    rm_mod = rmmod

    def initialize(self, globals_input:dict):
        for fn in dir(self):
            if fn.startswith('_'):
                continue
            globals_input[fn] = getattr(self, fn)
        return globals_input

    def main(self, *args, **kwargs):
        """
        Main function to run the mod
        """
        self.mod('cli')().forward()


    def hasattr(self, mod, k):
        """
        Check if the mod has the attribute
        """
        return hasattr(self.mod(mod)(), k)

    def hash(self, obj, mode='sha256', **kwargs):
        from mod.core.utils import hash
        return self.obj('mod.core.utils.hash')(obj, mode=mode, **kwargs)

    def test(self, mod = None,  **kwargs) ->  Dict[str, str]:
        return self.fn('tester/forward')( mod=mod,  **kwargs )

    def txs(self, *args, **kwargs) -> 'Callable':
        return self.fn('server/txs')( *args, **kwargs)

    # imported from different modules

    def edit(self, *args, **kwargs):
        return self.fn('api/edit')( *args, **kwargs)
    e = edit

    def reg(self, *args, **kwargs):
        return self.fn('api/reg')( *args, **kwargs)

    def children(self, mod:str='mod', depth:int=5, **kwargs) -> List[str]:
        tree = self.get_tree(self.dirpath(mod), **kwargs)
        return list(tree.keys())
    leaves = childs = children

    def hh(self, *args, **kwargs):
        return self.fn('api/hh')( *args, **kwargs)

    def setback(self, *args, **kwargs):
        return self.fn('api/setback')( *args, **kwargs)

    def time2str(self, t:float=None, fmt:str='%Y-%m-%d %H:%M:%S') -> str:
        """
        Convert a timestamp to a string
        """
        t = t or time.time()
        return time.strftime(fmt, time.localtime(t))