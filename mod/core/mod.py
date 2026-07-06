"""
Mod — the unified core class.

    core/mod.py  ← this file (all core functionality)
"""

import os
import sys
import glob as globmod
import json
import time
import logging
import inspect
import subprocess
import shutil
from typing import Any, Dict, List, Optional, Union
from functools import partial

import yaml

logger = logging.getLogger(__name__)


class Mod:

    # ── Class Constants ──────────────────────────────────────────────────
    name = 'mod'

    _avoid_folders = frozenset([
        '__pycache__', '.git', '.ipynb_checkpoints', 'node_modules',
        'egg-info', 'private', '.venv', 'venv', '.env', '.mod'
    ])
    _default_file_types = ('py', 'json', 'sol')
    _default_anchor_names = ('agent', 'mod', 'block')
    lib_name = __file__.split('/')[-3]   # core/mod/__init__.py → 'core'

    # ── Caches (class-level) ─────────────────────────────────────────────
    _mod_cache = {}
    _config_cache = {}
    obj_cache = {}
    modscache = {}
    executor_cache = {}

    # ── Init & Config ────────────────────────────────────────────────────

    def __init__(self, config=None, **kwargs):
        if os.getcwd() in [os.path.expanduser('~'), '/']:
            raise ValueError(
                f'Cannot sync in home directory {os.getcwd()}, '
                'please cd into a project directory'
            )

        self.paths = {}
        # core/mod/__init__.py → dirname x3 = mod/core → dirname = mod → dirname = lib
        self.paths['mod'] = self.mod_path = os.path.dirname(os.path.dirname(__file__))
        self.paths['lib'] = self.lib_path = os.path.dirname(self.paths['mod'])
        self.paths['orbit'] = {
            'orbit': f'{self.mod_path}/orbit',
            'core': f'{self.mod_path}/core',
            'mods': f'{self.mod_path}/orbit/registry/mods',
            'local': os.getcwd(),
        }

        for path in self.paths['orbit'].values():
            os.makedirs(path, exist_ok=True)

        self.paths['home'] = self.homepath = os.path.expanduser('~')
        self.paths = self.munch(self.paths)
        self.avoid_folders = set(self._avoid_folders)

        config = self.config()
        self.name = config['name']
        self.storage_path = f'{self.homepath}/.{self.name}'
        self.port_range = config['port_range']
        self.expose = self.endpoints = config['expose']
        self.file_types = list(self._default_file_types)
        self.anchor_names = list(self._default_anchor_names) + [self.name]

        from .tree import Tree
        self._tree = Tree(self)
        self.set_routes(self.routes())

    # ── Config ───────────────────────────────────────────────────────────

    def config(self, mod=None):
        """Returns the config for a mod (or root config if mod is None)."""
        configs = self.config_paths(mod=mod)
        if not configs:
            return {}
        data = self.get_json(configs[0])
        if isinstance(data, dict) and 'encrypted' in data and 'data' in data:
            return data['data']
        return data

    def config_paths(self, mod=None, modes=['yaml', 'json'], search=None,
                     depth=3, filename_options=['config', 'cfg', 'mod', 'block', 'agent', 'bloc', 'server']):
        path =  self.dirpath(mod)

        def is_config(f):
            return any(f.endswith(f'/{name}.{m}') for name in filename_options for m in modes)

        configs = [f for f in self.files(path, depth=depth) if is_config(f)]
        if search is not None:
            configs = [f for f in configs if search in f]
        return sorted(configs, key=len)

    def config_path(self, mod=None, **kwargs):
        configs = self.config_paths(mod=mod, **kwargs)
        return configs[0] if configs else None

    def set_config(self, config=None):
        if config is None:
            configs = sorted(self.config_paths(config), key=len)
            if not configs:
                raise FileNotFoundError('No config file found')
            if configs[0].endswith('.json'):
                config = self.get_json(configs[0])
            elif configs[0].endswith(('.yaml', '.yml')):
                config = self.get_yaml(configs[0])
            else:
                raise ValueError(f'Unknown config format: {configs[0]}')
        return config

    def save_config(self, mod: str, config: dict):
        paths = self.config_paths(mod)
        path = paths[0] if paths else self.dp(mod) + '/config.json'
        assert path is not None, 'No config path found'
        if path.endswith('.json'):
            with open(path, 'w') as f:
                json.dump(config, f, indent=4)
        elif path.endswith(('.yaml', '.yml')):
            with open(path, 'w') as f:
                yaml.dump(config, f)
        return path

    @property
    def shortcuts(self):
        if not hasattr(self, '_shortcuts'):
            shortcuts = self.config()['shortcuts']
            shortcuts[self.name] = 'mod'
            self._shortcuts = {k: v for k, v in shortcuts.items() if isinstance(v, str)}
        return self._shortcuts

    def set_routes(self, routes: dict, verbose=True):
        for mod, fns in routes.items():
            mod = self.import_mod(mod)
            for fn in fns:
                if not hasattr(self, fn):
                    def partial_fn(mod, fn):
                        return partial(getattr(mod, fn))
                    setattr(self, fn, partial_fn(mod, fn))

    def routes(self, obj=None):
        routes = {}
        for util in self.get_utils():
            k = '.'.join(util.split('.')[:-1])
            v = util.split('.')[-1]
            routes.setdefault(k, []).append(v)
        return routes

    # ── Filesystem (path & file utilities) ────────────────────────────────

    def abspath(self, path: str = '') -> str:
        return os.path.abspath(os.path.expanduser(path))

    def relpath(self, path: str = '~') -> str:
        path = os.path.abspath(os.path.expanduser(path))
        return path.replace(self.homepath, '~')

    def pwd(self):
        return os.getcwd()

    def cwd(self, mod=None):
        return self.dirpath(mod) if mod else os.getcwd()

    def get_path(self, path: str = None, extension: Optional[str] = None) -> str:
        """Resolve path relative to storage dir (~/.mod) unless absolute."""
        storage_dir = self.storage_dir()
        if path is None:
            return storage_dir
        if path.startswith('/'):
            pass
        elif path.startswith('~/'):
            path = os.path.expanduser(path)
        elif path.startswith('.'):
            path = os.path.abspath(path)
        elif storage_dir not in path:
            path = os.path.join(storage_dir, path)
        if extension is not None and not path.endswith(extension):
            path = f'{path}.{extension}'
        return path

    def storage_dir(self, mod=None):
        mod = (mod or self.name).replace('/', '.')
        return self.abspath(f'~/.{self.name}/{mod}')

    def is_home(self, path: str = None) -> bool:
        if path is None:
            path = self.pwd()
        return os.path.abspath(path) == os.path.abspath(self.homepath)

    def filter_path(self, path, search=None, include_hidden=False):
        """Return True if path passes avoid-folder / hidden / search filters."""
        parts = path.replace('\\', '/').split('/')
        for part in parts:
            if part in self.avoid_folders:
                return False
            if not include_hidden and part.startswith('.') and part != '.':
                return False
        if search and search not in path:
            return False
        return True

    def _walk(self, path, depth=10, include_hidden=False):
        """Shared os.walk with depth-limiting and folder pruning."""
        avoid = self.avoid_folders
        for root, dirs, files in os.walk(path):
            rel = os.path.relpath(root, path)
            cur_depth = 0 if rel == '.' else rel.count(os.sep) + 1
            if cur_depth >= depth:
                dirs.clear()
                continue
            dirs[:] = sorted(
                d for d in dirs
                if d not in avoid and (include_hidden or not d.startswith('.'))
            )
            yield root, dirs, files

    def folders(self, path: str = './', depth: Optional[int] = 1,
                search=None, include_hidden=False, **kwargs) -> List[str]:
        path = self.abspath(path)
        result = []
        for root, dirs, _files in self._walk(path, depth, include_hidden):
            for d in dirs:
                full = os.path.join(root, d)
                if search is not None and search not in full:
                    continue
                result.append(full)
        return sorted(result)

    def files(self, path='./', search: str = None, include_hidden: bool = False,
              depth=10, **kwargs) -> List[str]:
        """List all files in path with depth-limiting and folder pruning."""
        path = self.abspath(path)
        if not os.path.exists(path) and self.mod_exists(path):
            path = self.dirpath(path)
        if depth <= 0:
            return []
        result = []
        for root, dirs, files in self._walk(path, depth, include_hidden):
            for f in files:
                if not include_hidden and f.startswith('.'):
                    continue
                full = os.path.join(root, f)
                if search is not None and search not in full:
                    continue
                result.append(full)
        return sorted(result)

    def glob(self, path: str = './', depth: Optional[int] = 4,
             files_only: bool = True, include_hidden=False, **kwargs):
        path = self.abspath(path)
        if depth <= 0:
            return []
        result = []
        for root, dirs, files in self._walk(path, depth, include_hidden):
            if not files_only:
                result.extend(os.path.join(root, d) for d in dirs)
            for f in files:
                if not include_hidden and f.startswith('.'):
                    continue
                result.append(os.path.join(root, f))
        return result

    def ls(self, path: str = './', search=None, include_hidden=False,
           depth=None, return_full_path: bool = True):
        """List directory entries (non-recursive)."""
        path = self.abspath(path)
        try:
            entries = os.scandir(path)
        except (OSError, PermissionError):
            return []
        ls_files = sorted(e.path if return_full_path else e.name for e in entries)
        if search is not None:
            ls_files = [f for f in ls_files if search in f]
        return ls_files

    # ── Resolve (module resolution, anchor discovery, object import) ───

    def mod(self, mod: str = 'mod', params: dict = None, cache=True,
            verbose=False, update=False, **kwargs):
        """Import and return a mod class."""
        t0 = self.time()
        mod = mod or self.name
        if mod == self.name:
            return Mod
        if not isinstance(mod, str):
            return mod
        name = self.get_name(mod)
        if name == self.name:
            return Mod
        if name in self._mod_cache:
            return self._mod_cache[name]
        obj = self.anchor_object(name)
        self._mod_cache[name] = obj
        self.print(f'mod({name} [{self.time() - t0:.2f}s])', verbose=verbose)
        return obj

    def fn(self, fn: Union[callable, str], params: str = None,
           default_fn='forward', default_mod='mod') -> 'Callable':
        """Resolve a function from 'mod/fn' string notation."""
        if callable(fn):
            return fn
        if hasattr(self, fn):
            fn_obj = getattr(self, fn)
        else:
            if fn.startswith('/'):
                mod_name, fn_name = default_mod, fn[1:]
            elif fn.endswith('/'):
                mod_name = fn[:-1].split('/')[0] if '/' in fn[:-1] else fn[:-1]
                fn_name = default_fn
            elif '/' in fn:
                mod_name, fn_name = fn.split('/')
            elif self.mod_exists(fn):
                mod_name, fn_name = fn, default_fn
            else:
                raise Exception(f'Function {fn} not found')

            if mod_name not in self.modscache:
                self.modscache[mod_name] = self.mod(mod_name)()
            fn_obj = getattr(self.modscache[mod_name], fn_name)

        return fn_obj(**params) if params else fn_obj

    def mod_exists(self, mod: str, **kwargs) -> bool:
        try:
            name = self.get_name(mod)
            if self.search(name):
                return True
        except Exception:
            pass
        mod_path = os.path.join(self.paths['orbit']['orbit'], mod)
        return os.path.exists(mod_path) and os.path.isdir(mod_path)

    def get_name(self, name: Optional[str] = None,
                 avoid_terms=['src', 'mods', '_mods', 'core', 'modules', '_exp', 'ext']) -> str:
        name = name or 'mod'
        if any(name.startswith(p) for p in ['.', '~', '/']):
            name = self.path2name(name)
        avoid_terms = avoid_terms + [
            self.paths['orbit']['orbit'].split('/')[-1],
        ]
        name = name.replace('/', '.')
        new_name = [c for c in name.split('.') if c not in avoid_terms]
        if not new_name:
            return self.name
        if new_name[0] == self.name:
            new_name = new_name[1:]
        name = '.'.join(new_name).strip('.').lower() or self.name
        return self.shortcuts.get(name, name)

    def dirpath(self, mod=None, update=False, trials=4, key=None, **kwargs) -> str:
        """Get the directory path of a mod."""
        if mod is None or mod == self.name:
            return self.paths['lib']
        tree_options = list(self.search(search=mod, update=update).values())
        if not tree_options:
            if trials > 0:
                return self.dirpath(mod=mod, update=True, trials=trials - 1, key=key)
            assert False, f'Mod {mod} not found in tree'
        dirpath = tree_options[0]
        assert os.path.exists(dirpath), f'Dirpath {dirpath} does not exist for mod {mod}'
        return dirpath

    def filepath(self, obj=None) -> str:
        return inspect.getfile(self.mod(obj))

    def anchor_file(self, path, depth=4):
        """Find the anchor file (mod.py, agent.py, etc.) in a mod directory."""
        path = path.replace('/', '.')
        path = self.dirpath(path)
        anchor_names = self.anchor_names.copy() + path[len(self.homepath + '/'):].split('/')
        files = sorted(self.files(path, depth=depth), key=len)
        files = [f for f in files if any(f.endswith('.' + ft) for ft in self.file_types)]

        # Single-file shortcut only if it's a Python file (not config.json, etc.)
        if len(files) == 1 and files[0].endswith('.py'):
            return files[0]

        result_options = []
        for ft in self.file_types:
            for anchor in anchor_names:
                for f in files:
                    if f.endswith(f'/{anchor}.{ft}'):
                        result_options.append(f)

        if result_options:
            return sorted(result_options, key=len)[0]

        # No anchor found — attempt wrap to generate one, or return None
        if not getattr(self, '_wrapping', False):
            try:
                self._wrapping = True
                self.fn('wrap/forward')(path)
            except Exception:
                pass
            finally:
                self._wrapping = False
            # Re-scan after wrap
            files = sorted(self.files(path, depth=depth), key=len)
            files = [f for f in files if any(f.endswith('.' + ft) for ft in self.file_types)]
            for ft in self.file_types:
                for anchor in anchor_names:
                    for f in files:
                        if f.endswith(f'/{anchor}.{ft}'):
                            return f
            if len(files) == 1 and files[0].endswith('.py'):
                return files[0]

            # Last resort: auto-generate a mod.py from README / project info
            generated = self._generate_anchor(path)
            if generated:
                return generated

        return None

    def _generate_anchor(self, path: str) -> Optional[str]:
        """Auto-generate a mod.py anchor file for a project that lacks one."""
        mod_name = os.path.basename(path.rstrip('/'))
        anchor_path = os.path.join(path, 'mod.py')
        if os.path.exists(anchor_path):
            return anchor_path

        # Read README for description
        description = mod_name
        readmes = [f for f in self.files(path, depth=2) if 'readme' in os.path.basename(f).lower()]
        if readmes:
            try:
                text = self.get_text(readmes[0])
                # Use first non-empty line or first 200 chars as description
                for line in text.splitlines():
                    line = line.strip().strip('#').strip()
                    if line and not line.startswith('[') and not line.startswith('|'):
                        description = line[:200]
                        break
            except Exception:
                pass

        # Detect project type from files present
        project_files = [os.path.basename(f) for f in self.files(path, depth=1)]
        has_package_json = 'package.json' in project_files
        has_requirements = 'requirements.txt' in project_files
        has_pyproject = 'pyproject.toml' in project_files

        # Build helper methods based on project type (first match wins for install)
        extra_methods = ''
        if has_package_json:
            extra_methods += f'''
    def install(self):
        """Install project dependencies."""
        import subprocess
        return subprocess.run(['npm', 'install'], cwd=r'{path}', capture_output=True, text=True).stdout

    def build(self):
        """Build the project."""
        import subprocess
        return subprocess.run(['npm', 'run', 'build'], cwd=r'{path}', capture_output=True, text=True).stdout
'''
        elif has_requirements:
            extra_methods += f'''
    def install(self):
        """Install Python dependencies."""
        import subprocess
        return subprocess.run(['pip', 'install', '-r', 'requirements.txt'], cwd=r'{path}', capture_output=True, text=True).stdout
'''
        elif has_pyproject:
            extra_methods += f'''
    def install(self):
        """Install Python project."""
        import subprocess
        return subprocess.run(['pip', 'install', '-e', '.'], cwd=r'{path}', capture_output=True, text=True).stdout
'''

        content = f'''import os
import mod as m

class Mod:
    description = """{description}"""
    path = r'{path}'

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        return {{
            'name': '{mod_name}',
            'description': self.description,
            'path': self.path,
            'files': os.listdir(self.path),
        }}

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
{extra_methods}'''

        try:
            self.put_text(anchor_path, content)
            self.print(f'Auto-generated anchor: {anchor_path}', color='green')
            return anchor_path
        except Exception as e:
            self.print(f'Failed to generate anchor for {path}: {e}', color='red')
            return None

    def anchor_object(self, path):
        path = self.get_name(path)
        anchor_file = self.anchor_file(path)
        if not anchor_file:
            raise Exception(f'No anchor file found in {path}')
        classes = self.classes(anchor_file)
        assert classes, f'No classes found in {anchor_file}'
        return self.obj(classes[-1])

    def has_anchor(self, path) -> bool:
        """Fast check if a path has a valid anchor file."""
        path = self.abspath(path)
        if not os.path.isdir(path):
            return False
        path_parts = path.rstrip('/').split('/')
        anchor_names = list(self._default_anchor_names) + [path_parts[-1]]
        return any(
            os.path.isfile(os.path.join(path, f'{name}.{ft}'))
            for ft in self.file_types for name in anchor_names
        )

    def ensure_syspath(self):
        if not getattr(self, '_syspath_done', False):
            for path in [self.pwd(), self.paths['lib']]:
                if path not in sys.path:
                    sys.path.append(path)
            self._syspath_done = True

    def obj(self, key: str) -> Any:
        self.ensure_syspath()
        if key in self.obj_cache:
            return self.obj_cache[key]
        from importlib import import_module as _imp
        try:
            parts = key.replace('/', '.').replace('::', '.').rsplit('.', 1)
            obj = getattr(_imp(parts[0]), parts[1])
        except (ValueError, AttributeError):
            obj = self.import_module(key)
        self.obj_cache[key] = obj
        return obj

    def obj_exists(self, path: str) -> bool:
        try:
            self.obj(path)
            return True
        except Exception:
            return False

    def import_mod(self, mod: str):
        from importlib import import_module
        mod = mod.replace(f'{self.lib_name}.{self.lib_name}', self.lib_name)
        return import_module(mod)

    # ── Introspect (code inspection, schema, content analysis) ────────

    def code(self, obj=None, **kwargs) -> Union[str, Dict[str, str]]:
        if '/' in str(obj):
            obj = self.fn(obj)
        elif self.mod_exists(obj):
            obj = self.mod(obj)
        elif hasattr(self, obj):
            obj = getattr(self, obj)
        elif self.obj_exists(obj):
            obj = self.obj(obj)
        return inspect.getsource(obj)

    def claude(self, *text, mod=None, **kwargs) -> str:
        return self.dev(*text, mod=mod, agent='claude', **kwargs)

    def _has_hidden(self, relpath: str) -> bool:
        """Check if any path component is a hidden file/folder (starts with '.')."""
        return any(part.startswith('.') for part in relpath.split('/') if part)

    def content(self, mod=None, ignore_folders=[], depth=10, **kwargs) -> Dict[str, str]:
        """Get mod content as {relative_path: file_text}."""
        mod = mod or 'mod'
        dirpath = self.abspath(self.dirpath(mod))
        files = self.files(dirpath, depth=depth)
        content = {}
        for k in files:
            try:
                content[k] = self.text(k)
            except Exception as e:
                content[k] = str(self.error(e))
        prefix = dirpath + '/'
        content = {k[len(prefix):]: v for k, v in content.items()}
        if ignore_folders:
            content = {k: v for k, v in content.items()
                       if not any(f'/{f}/' in k for f in ignore_folders)}
        # Filter hidden files/folders unless suffixed with .example
        content = {k: v for k, v in content.items()
                   if not self._has_hidden(k) or k.endswith('.example')}
        return dict(sorted(content.items()))

    def content_files(self, mod='store', **kwargs) -> List[str]:
        return list(self.content(mod=mod, **kwargs).keys())

    def size(self, mod) -> int:
        return len(str(self.content(mod)))

    def mod_files(self, mod='store', search=None, **kwargs) -> List[str]:
        return self.files(self.dirpath(mod), search=search, **kwargs)

    def nfiles(self, mod=None, depth=2):
        return len(self.files(self.dirpath(mod), depth=depth))

    def fns(self, obj: Any = None, search=None, include_hidden=False, **kwargs) -> List[str]:
        """Get list of public functions on a mod."""
        obj = self.mod(obj)
        fns = sorted(set(dir(obj)))
        if search is not None:
            fns = [f for f in fns if search in f]
        if not include_hidden:
            fns = [f for f in fns if not f.startswith('_')]
        return sorted(fns)

    def fnschema(self, fn: str = '__init__', public=True,
                 avoid_arguments=['self', 'cls'], **kwargs) -> dict:
        """Get function schema including input/output types."""
        fn_obj = self.fn(fn)
        if not callable(fn_obj):
            return {'fn_type': 'property', 'type': type(fn_obj).__name__}

        sig = inspect.signature(fn_obj)
        schema = {'input': [], 'output': {}, 'docs': '', 'cost': 0, 'name': '', 'content': None}
        if public:
            schema['content'] = inspect.getsource(fn_obj)

        for k, v in sig._parameters.items():
            if k in avoid_arguments:
                continue
            schema['input'].append({
                'name': k,
                'value': '_empty' if v.default == inspect._empty else v.default,
                'type': '_empty' if v.default == inspect._empty else type(v.default).__name__,
            })

        schema['output'] = {
            'value': None,
            'type': str(getattr(fn_obj, '__annotations__', {}).get('return', None)),
        }
        schema['docs'] = fn_obj.__doc__
        schema['cost'] = getattr(fn_obj, '__cost__', 0)
        schema['name'] = fn_obj.__name__
        return schema

    def args(self, fn: str = '__init__', **kwargs) -> list:
        return self.fnschema(fn, **kwargs)['input']

    def schema(self, obj=None, search=None, public=True,
               return_mode='dict', verbose=False, **kwargs) -> dict:
        """Get schema for all functions on a mod."""
        obj = obj or 'mod'
        public = bool(public)
        if callable(obj) or (isinstance(obj, str) and '/' in obj):
            return self.fnschema(obj, public=public, **kwargs)

        fns = self.fns(obj, search=search, **kwargs)
        mod_obj = self.mod(obj)
        schema = {}
        for fn in fns:
            try:
                schema[fn] = self.fnschema(getattr(mod_obj, fn), public=public, **kwargs)
            except Exception as e:
                if verbose:
                    print(self.error(e))
        if return_mode == 'list':
            return list(schema.values())
        elif return_mode == 'dict':
            return schema
        raise Exception('Invalid return mode', return_mode)

    def get_args(self, fn) -> List[str]:
        if not callable(fn):
            return []
        try:
            return inspect.getfullargspec(fn).args
        except Exception:
            return []

    def classes(self, path='./', **kwargs):
        path2classes = self.path2classes(path=path, **kwargs)
        classes = []
        for v in path2classes.values():
            classes.extend(v)
        return classes

    def path2classes(self, path='./', depth=4, tolist=False, **kwargs):
        """Get classes defined in Python files under path."""
        class_suffix = ':'
        class_prefix = 'class '
        path = self.abspath(path)
        path2classes = {}

        if os.path.isdir(path) and depth > 0:
            for p in self.ls(path):
                try:
                    for k, v in self.path2classes(p, depth=depth - 1).items():
                        if v:
                            path2classes[k] = v
                except Exception:
                    pass
        elif os.path.isfile(path) and path.endswith('.py'):
            classes = []
            code = self.get_text(path)
            objectpath = self.path2objectpath(path)
            for line in code.split('\n'):
                if line.startswith(class_prefix) and line.strip().endswith(class_suffix):
                    new_class = line.split(class_prefix)[-1].split('(')[0].strip()
                    if new_class.endswith(class_suffix):
                        new_class = new_class[:-1]
                    if ' ' not in new_class:
                        classes.append(new_class)
            if objectpath.startswith(path):
                objectpath = objectpath[len(path) + 1:]
            objectpath = objectpath.replace('/', '.')
            path2classes = {path: [objectpath + '.' + cl for cl in classes]}

        if tolist:
            return [cl for v in path2classes.values() for cl in v]
        return path2classes

    def path2fns(self, path='./', tolist=False, **kwargs):
        path2fns = {}
        path = os.path.abspath(path)
        if os.path.isdir(path):
            for p in globmod.glob(path + '/**/**.py', recursive=True):
                for k, v in self.path2fns(p, tolist=False).items():
                    if v:
                        path2fns[k] = v
        else:
            fns = []
            code = self.get_text(path) or ''
            path_prefix = self.path2objectpath(path)
            for line in code.split('\n'):
                if line.startswith('def ') or line.startswith('async def '):
                    fn = line.split('def ')[-1].split('(')[0].strip()
                    fns.append(f'{path_prefix}.{fn}')
            path2fns = {path: fns}
        if tolist:
            return [fn for v in path2fns.values() for fn in v]
        return path2fns

    def path2objectpath(self, path: str, **kwargs) -> str:
        """Convert filesystem path to Python object path (foo/bar.py -> foo.bar)."""
        path = os.path.abspath(path)
        for prefix in [self.paths['lib'], os.getcwd()]:
            if path.startswith(prefix):
                path = path[len(prefix) + 1:].replace('/', '.')
                break
        if path.endswith('.py'):
            path = path[:-3]
        return path.replace('__init__.', '.')

    def path2name(self, path: str,
                  ignore_folder_names=['mods', 'agents', 'src'],
                  possible_suffixes=['_', '']):
        ignore_set = {f + s for f in ignore_folder_names for s in possible_suffixes}
        name = self.path2objectpath(path)
        name_chunks = []
        for chunk in name.split('.'):
            if chunk not in ignore_set and chunk not in name_chunks:
                name_chunks.append(chunk)
        if name_chunks and name_chunks[0] == self.name:
            name_chunks = name_chunks[1:]
        return '.'.join(name_chunks) if name_chunks else self.name

    def is_in_file_types(self, f: str) -> bool:
        return any(f.endswith('.' + ft) for ft in self.file_types)

    def is_mod_file(self, mod=None, exts=['py', 'rs', 'ts'],
                    folder_filenames=['mod', 'agent', 'block', 'server']) -> bool:
        dirpath = self.dirpath(mod)
        try:
            fp = self.filepath(mod)
        except Exception as e:
            self.print(f'Error getting filepath for {mod}: {e}', color='red', verbose=False)
            return False
        folder_filenames.append(mod.split('.')[-1])
        for ext in exts:
            for fn in folder_filenames:
                if fp.endswith(f'/{fn}.{ext}'):
                    return False
                if fp.endswith(f'/{mod.split(".")[-1]}.{ext}'):
                    return True
        return dirpath.split('/')[-1] != fp.split('/')[-1].split('.')[0]

    # ── Tree & Search (delegated to core.tree) ───────────────────────────

    def tree(self, *args, **kwargs):
        return self._tree.tree(*args, **kwargs)

    def get_tree(self, *args, **kwargs):
        return self._tree.get_tree(*args, **kwargs)

    def core_tree(self, *args, **kwargs):
        return self._tree.core_tree(*args, **kwargs)

    def orbit(self, *args, **kwargs):
        return self._tree.orbit(*args, **kwargs)

    def operational_tree(self, *args, **kwargs):
        return self._tree.operational_tree(*args, **kwargs)

    @staticmethod
    def filter_fn(k, search):
        from .tree import Tree
        return Tree.filter_fn(k, search)

    def search(self, *args, **kwargs):
        return self._tree.search(*args, **kwargs)

    def process_path(self, x):
        return self._tree.process_path(x)

    # ── Key & Crypto ─────────────────────────────────────────────────────

    def get_key(self, key: str = None, **kwargs):
        return self.mod('key')().get_key(key, **kwargs)

    def key(self, key: str = None, **kwargs):
        return self.get_key(key, **kwargs)

    def keys(self, key: str = None, **kwargs):
        return self.get_key().keys(key, **kwargs)

    def key2address(self, key: str = None, **kwargs):
        return self.get_key().key2address(key, **kwargs)

    def key_address(self, key: str = None, **kwargs) -> str:
        return self.get_key(key).key2address(**kwargs).get(key, key)

    def address2key(self, *args, **kwargs):
        return self.fn('key/address2key')(*args, **kwargs)

    def addy(self, key=None):
        return self.fn('key/addy')(key)

    def envs(self, key: str = None, **kwargs):
        return self.get_key(key, **kwargs).envs()

    def encrypt(self, data: Union[str, bytes], key: str = None, password: str = None, **kwargs) -> bytes:
        return self.get_key(key).encrypt(data, password=password)

    def decrypt(self, data: Any, password: str = None, key: str = None, **kwargs) -> bytes:
        return self.get_key(key).decrypt(data, password=password)

    def sign(self, data: dict = None, key: str = None, crypto_type=None, mode='str', **kwargs) -> bool:
        key = self.get_key(key, crypto_type=crypto_type)
        crypto = crypto_type or key.crypto_type
        signature = key.sign(data, mode=mode, **kwargs)
        assert self.verify(data, signature=signature, address=key.address, crypto_type=crypto), 'Invalid signature'
        return signature

    def verify(self, data, signature=None, address=None, key=None, **kwargs) -> bool:
        return self.get_key(key).verify(data=data, signature=signature, address=address, **kwargs)

    def owner(self):
        return self.get_key().address

    def is_owner(self, address: str) -> bool:
        return address.lower() == self.owner().lower()

    def mnemonic(self, words=24):
        if words not in [12, 15, 18, 21, 24]:
            if words > 24:
                tiles = words // 24 + 1
                mnemonic = ' '.join(self.mnemonic(24) for _ in range(tiles))
            else:
                mnemonic = self.mnemonic(24)
            return ' '.join(mnemonic.split()[:words])
        return self.mod('key')().generate_mnemonic(words=words)

    def pwd2key(self, pwd, **kwargs) -> str:
        return self.mod('key')().str2key(pwd, **kwargs)

    # ── Storage (get/put) ────────────────────────────────────────────────

    def get(self, k: str, default: Any = None, max_age: str = None,
            fs = 'localfs',
            update: bool = False, password: str = None, verbose=False, **kwargs) -> Any:
        if self.iscid(k):
            if '/' in k:
                fs = k.split('/')[0]
            return self.fn(f'{fs}/get')(k)
        k = self.get_path(k)
        data = self.get_json(k, default=default, **kwargs)
        data = data or default
        if not isinstance(data, dict):
            return default
        if update:
            return default
        is_wrapped = 'encrypted' in data and 'data' in data
        if is_wrapped:
            if password is not None:
                assert data['encrypted'], f'{k} is not encrypted'
                data['data'] = self.decrypt(data['data'], password=password)
            if max_age is not None:
                timestamp = data.get('timestamp', data.get('time', 0))
                if (time.time() - float(timestamp)) > max_age:
                    return default
            return data['data']
        return data

    def put(self, k: str, v: Any, encrypt: bool = False, password: str = None, **kwargs) -> Any:
        k = self.get_path(k)
        encrypt = encrypt or password is not None
        if encrypt:
            v = self.encrypt(v, password=password)
            data = {'data': v, 'encrypted': True, 'timestamp': time.time()}
        else:
            data = v
        return self.put_json(k, data)

    def get_json(self, path: str, default: Any = None, **kwargs) -> Any:
        path = self.abspath(path)
        if not path.endswith('.json'):
            path += '.json'
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f'Error loading json from {path}: {e}')
            return default

    def put_json(self, path: str, data: Dict, **kwargs) -> str:
        path = self.abspath(path if path.endswith('.json') else path + '.json')
        if isinstance(data, dict):
            self.put_text(path, json.dumps(data, indent=4))
        return path

    def get_text(self, path: str, default=None, **kwargs) -> str:
        path = self.abspath(path)
        try:
            with open(path, 'r') as f:
                return f.read()
        except Exception:
            return default

    def put_text(self, path: str, text: str, key=None, password=None):
        path = self.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not isinstance(text, str):
            text = self.python2str(text)
        if key is not None:
            text = self.get_key(key).encrypt(text, password=password)
        with open(path, 'w') as f:
            f.write(text)
        return {'success': True, 'path': path, 'size': len(text) * 8}

    def text(self, path: str = './', **kwargs) -> str:
        path = self.abspath(path)
        assert self.homepath != path, f'Cannot read {path}'
        if os.path.isdir(path):
            return self.file2text(path)
        with open(path, 'r') as f:
            return f.read()

    fs = 'localfs'
    def iscid(self, text: str) -> bool:
        if not isinstance(text, str):
            return False
        return (text.startswith('Qm') and len(text) == 46) or (text.startswith(self.fs) and len(text) == 59) or text.startswith(self.fs)

    def env(self, key: str = None) -> Union[str, dict, None]:
        if key is not None:
            return os.environ.get(key)
        return dict(os.environ)

    def rm(self, path: str, possible_extensions=None, avoid_paths=None) -> dict:
        possible_extensions = possible_extensions or ['json']
        avoid_paths = avoid_paths or ['~', '/']
        path = self.abspath(path)
        if path in set(map(self.abspath, avoid_paths)):
            raise PermissionError(f'Cannot remove protected path: {path}')
        if not os.path.exists(path):
            for ext in possible_extensions:
                candidate = f'{path}.{ext}'
                if os.path.exists(candidate):
                    path = candidate
                    break
            else:
                return {'success': False, 'message': f'{path} does not exist'}
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
        if os.path.exists(path):
            raise OSError(f'Failed to remove {path}')
        return {'success': True, 'message': f'{path} removed'}

    # ── CLI & Misc ───────────────────────────────────────────────────────

    def main(self, *args, **kwargs):
        from .cli.cli import Cli
        return Cli().forward()

    def forward(self, fn: str = 'info', params: dict = None, auth=None) -> Any:
        params = params or {}
        fn_obj = getattr(self, fn) if hasattr(self, fn) else self.fn(fn)
        return fn_obj(**params)

    def call(self, _fn: str = 'mod/forward', params: Dict[str, Any] = {},
             timeout=30, wait=True, **_kwargs):
        if '/' not in _fn:
            _fn = _fn + '/forward'
        fn_obj = self.fn(_fn)
        return fn_obj(**{**params, **_kwargs})

    def ask(self, *args, **kwargs):
        return self.fn('agent/')(*args, **kwargs)

    def about(self, mod='store', query='what is this?', *extra_query):
        query = query + ' '.join(extra_query)
        return self.ask(f' {self.schema(mod)} {query}')

    def how(self, mod, query, *extra_query):
        code = self.code(mod)
        query = ' '.join([query, *extra_query])
        return self.fn('model.openrouter/')(f'query={query} code={code}')

    def help(self, mod='mod', query: str = 'what is this', *extra_query, **kwargs):
        query = ' '.join(map(str, [query, *extra_query]))
        context = self.code()
        return self.mod('agent')().ask(
            f'given the code {self.code(mod)} and CONTEXT OF mod {context} '
            f'answer the following question: {query}',
            preprocess=False,
        )

    def context(self, path=None):
        return self.code()

    def edit(self, *query, mod='app', agent=None, reg=True, **kwargs):
        """Edit a module with a dev agent (claude or equivalent), then re-register it."""
        query_str = ' '.join(map(str, query))
        self.print(f'Editing {mod} with query: {query_str}')
        result = {'mod': mod, 'result': self.dev(query_str, mod=mod, agent=agent, **kwargs)}
        if reg:
            try:
                result['reg'] = self.reg(mod, comment=query_str)
            except Exception as e:
                self.print(f'reg after edit failed: {e}', color='yellow')
        return result

    def desc(self, mod='mod', **kwargs):
        return self.fn('desc/')(mod, **kwargs)

    def test(self, mod=None, **kwargs) -> Dict[str, str]:
        return self.fn('tester/forward')(mod=mod, **kwargs)

    def pytest(self, mod='pypm'):
        return self.fn('tester/pytest')(mod)

    # ── Info & Registry (api surface lives on mod.py, no core/api) ───────

    def info(self, mod: str = 'mod', schema=False, **kwargs):
        """Module card built locally from the mod's files and config.json."""
        config = self.config(mod)
        if not isinstance(config, dict):
            config = {}
        fns = config.get('fns') or config.get('endpoints') or config.get('expose')
        if isinstance(fns, dict):
            fns = list(fns)
        if not fns:
            try:
                fns = self.fns(mod)
            except Exception:
                fns = []
        info = {
            'name': config.get('name', self.get_name(mod)),
            'key': self.owner(),
            'desc': config.get('desc') or config.get('description'),
            'path': self.relpath(self.dirpath(mod)),
            'url': config.get('urls') or config.get('url'),
            'port': config.get('port'),
            'app_port': config.get('app_port'),
            'fns': fns,
            'time': self.time(),
        }
        if schema:
            info['schema'] = self.schema(mod, public=False)
        try:
            info['signature'] = self.sign(info)
        except Exception:
            pass
        return info

    def verify_info(self, info: Union[str, dict] = None, **kwargs) -> bool:
        if isinstance(info, str):
            info = self.info(info, **kwargs)
        signature = info.pop('signature')
        assert self.verify(data=info, signature=signature, address=info['key']), 'Invalid signature'
        info['signature'] = signature
        return info

    def cid(self, mod=None, **kwargs) -> Union[str, Dict[str, str]]:
        return self.fn('registry/put')(self.content(mod, **kwargs))

    def reg(self, *args, **kwargs):
        return self.fn('registry/reg')(*args, **kwargs)

    def setback(self, *args, **kwargs):
        return self.fn('registry/setback')(*args, **kwargs)

    def txs(self, search=None, **kwargs):
        return self.fn('tx/list')(search=search, **kwargs)

    def token(self, *args, **kwargs):
        return self.fn('auth/token')(*args, **kwargs)

    # ── Module Listing ───────────────────────────────────────────────────

    def mods(self, search=None, startswith=None, endswith=None, all=False, **kwargs) -> List[str]:
        if all:
            tree = self.tree(search=search, **kwargs)
        else:
            tree = self.operational_tree(search=search, **kwargs)
        names = list(tree.keys())
        if startswith:
            names = [n for n in names if n.startswith(startswith)]
        if endswith:
            names = [n for n in names if n.endswith(endswith)]
        return names

    def core_mods(self, *args, **kwargs) -> List[str]:
        return list(self.core_tree(*args, orbit='core', **kwargs).keys())

    def local_mods(self) -> List[str]:
        return list(self.orbit('local').keys())

    # ── Utilities ────────────────────────────────────────────────────────

    def munch(self, d: dict) -> Any:
        from .config import Config
        return Config.from_dict(d)

    def print(self, *text: str, **kwargs):
        return self.obj('mod.core.utils.print_console')(*text, **kwargs)

    def time(self, mode='float') -> float:
        t = time.time()
        if mode == 'int':
            return int(t * 1000)
        elif mode == 'float':
            return float(t)
        elif mode == 'iso':
            return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))
        elif 'date' in mode:
            return time.ctime(t)

    def time2str(self, t: float = None, fmt: str = '%Y-%m-%d %H:%M:%S') -> str:
        t = t or time.time()
        return time.strftime(fmt, time.localtime(t))

    def sleep(self, period):
        time.sleep(period)

    def hash(self, obj, mode='sha256', **kwargs):
        return self.obj('mod.core.utils.hash')(obj, mode=mode, **kwargs)

    def get_utils(self, search=None):
        utils = self.path2fns(self.paths['orbit']['core'] + '/utils.py', tolist=True)
        if search is not None:
            utils = [u for u in utils if search in u]
        return sorted(utils)

    def utils(self, search=None):
        return self.get_utils(search=search)

    def kwargs2str(self, kwargs) -> str:
        kwargs.pop('self', None)
        kwargs.pop('cls', None)
        return '_'.join(f'{k}={v}' for k, v in kwargs.items())

    def loop(self):
        import asyncio
        return asyncio.get_event_loop()

    def confirm(self, message: str = 'Are you sure?', suffix=' (y/n): '):
        if input(message + suffix).lower() != 'y':
            raise KeyboardInterrupt('Operation cancelled by user')
        return True

    def mod_class(self, obj=None) -> str:
        return (obj or self.__class__).__name__

    def dir(self, obj=None, search=None, *args, **kwargs):
        obj = self.obj(obj)
        result = dir(obj)
        return [f for f in result if search in f] if search is not None else result

    def hasattr(self, mod, k):
        return hasattr(self.mod(mod)(), k)

    def readmes(self, mod=None, depth=2):
        return [f for f in self.files(self.dirpath(mod), depth=depth) if 'readme' in f.lower()]

    def readme(self, mod=None, depth=3):
        readmes = self.readmes(mod=mod, depth=depth)
        return self.get_text(readmes[0]) if readmes else None

    def client(self, *args, **kwargs):
        return self.fn('client/client')(*args, **kwargs)

    def host(self):
        return self.key().address

    def hosts(self):
        return self.fn('remote/hosts')()

    def repo2path(self, search=None):
        repo2path = {}
        for p in self.ls('~/'):
            if os.path.exists(p + '/.git'):
                r = p.split('/')[-1]
                if search is None or search in r:
                    repo2path[r] = p
        return dict(sorted(repo2path.items()))

    def repos(self, search=None):
        return list(self.repo2path(search=search).keys())

    def go(self, mod=None, **kwargs):
        path = self.dirpath(mod, relative=False)
        assert os.path.exists(self.abspath(path)), f'{path} does not exist'
        return self.cmd(f'code {path}', **kwargs)

    def vs(self, path=None):
        path = os.path.abspath(path or __file__)
        return self.cmd(f'code {path}')

    def push(self, comment, *extra_comment, mod=None, safety=False):
        path = self.dp(mod, relative=True)
        comment = ' '.join([comment, *extra_comment])
        if not os.path.exists(path):
            raise FileNotFoundError(f'Path {path} does not exist')
        if safety:
            self.confirm(f'Are you sure you want to push to {path} with comment: {comment}?')
        subprocess.run(['git', 'add', '.'], cwd=path, check=True)
        result = subprocess.run(['git', 'commit', '-m', comment], cwd=path)
        if result.returncode != 0:
            return {'msg': f'Nothing to commit in {path}'}
        subprocess.run(['git', 'push'], cwd=path, check=True)
        return {'msg': f'Pushed to {path} with comment: {comment}'}

    def pull(self, mod=None):
        path = self.dp(mod, relative=True)
        if not os.path.exists(path):
            raise FileNotFoundError(f'Path {path} does not exist')
        subprocess.run(['git', 'stash'], cwd=path, check=True)
        subprocess.run(['git', 'pull'], cwd=path, check=True)
        subprocess.run(['git', 'stash', 'pop'], cwd=path)
        return {'msg': f'Pulled {path}'}

    def tool(self, tool_name: str = 'cmd', *args, **kwargs) -> Any:
        return self.mod(tool_name)(*args, **kwargs).forward

    def initialize(self, globals_input: dict):
        for fn in dir(self):
            if not fn.startswith('_'):
                globals_input[fn] = getattr(self, fn)
        return globals_input

    def cache(self, path: str, max_age: int = 60, default=None, directory: str = '~/.mod/cache'):
        def decorator(func):
            def wrapper(*args, **kwargs):
                fn_id = f'{func.__module__}.{func.__name__}'
                cache_path = self.abspath(f'{directory}/{fn_id}/{args}_{kwargs}')
                result = self.get(cache_path, default, max_age=max_age)
                if result is not None:
                    return result
                result = func(*args, **kwargs)
                self.put(cache_path, result)
                return result
            return wrapper
        return decorator

    def executor(self, max_workers=8, mode='thread', cache=True):
        path = f'executor/{mode}/{max_workers}'
        if cache and path in self.executor_cache:
            return self.executor_cache[path]
        if mode == 'process':
            from concurrent.futures import ProcessPoolExecutor
            executor = ProcessPoolExecutor(max_workers=max_workers)
        elif mode == 'thread':
            executor = self.mod('executor')(max_workers=max_workers)
        else:
            raise ValueError(f"Unknown mode: {mode}. Use 'thread' or 'process'.")
        if cache:
            self.executor_cache[path] = executor
        return executor

    def submit(self, fn, params=None, timeout: int = 40, mod: str = None,
               mode: str = 'thread', max_workers: int = 100):
        executor = self.executor(mode=mode, max_workers=max_workers)
        return executor.submit(self.fn(fn), params=params, timeout=timeout)

    def epoch(self, *args, **kwargs):
        return self.fn('vali/epoch')(*args, **kwargs)

    # ── Background Jobs ─────────────────────────────────────────────────

    def jobs(self):
        """List all background jobs."""
        import json as _json
        from pathlib import Path as _Path
        job_dir = _Path('/tmp/mod_jobs')
        if not job_dir.exists():
            return []
        jobs = []
        for f in sorted(job_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                jobs.append(_json.loads(f.read_text()))
            except Exception:
                pass
        return jobs

    def job(self, job_id: str = None):
        """Get a background job by ID. With no args, lists all jobs."""
        import json as _json
        from pathlib import Path as _Path
        if not job_id:
            return self.jobs()
        job_dir = _Path('/tmp/mod_jobs')
        # Match by prefix
        matches = list(job_dir.glob(f'{job_id}*.json'))
        if not matches:
            return {'error': f'No job found matching {job_id}'}
        data = _json.loads(matches[0].read_text())
        # Also attach last 20 lines of log if available
        log_path = matches[0].with_suffix('.log')
        if log_path.exists():
            lines = log_path.read_text().splitlines()
            data['log_tail'] = lines[-20:] if len(lines) > 20 else lines
        return data

    # ── Server & Deploy ──────────────────────────────────────────────────

    def serve(self, mod: str = 'app', port: int = None, remote=True, **kwargs):
        fn = self.fn('server/serve')
        if isinstance(mod, str):
            return fn(mod, port=port, remote=remote, **kwargs)
        elif isinstance(mod, list):
            threads = []
            for m in mod:
                params = {'mod': m, 'port': port, 'remote': remote, **kwargs}
                t = self.thread(fn, params)
                threads.append(t)
            return self.wait(threads)

    def servers(self, *args, pm='pm2', **kwargs):
        return self.fn(f'{pm}/servers')(*args, **kwargs)

    def server_exists(self, server: str = 'mod', pm='pm2', *args, **kwargs):
        return self.fn(f'{pm}/exists')(server, *args, **kwargs)

    def ensure_server(self, server: str = 'mod', pm='pm2', *args, **kwargs):
        if not self.server_exists(server, pm=pm, *args, **kwargs):
            return self.serve(server, pm=pm, *args, **kwargs)
        return {'msg': f'Server {server} already running'}

    def kill(self, server: str = 'mod'):
        return self.fn('server/kill')(server)

    stop = kill
    start = serve

    def kill_all(self):
        return self.fn('server/killall')()

    def urls(self, *args, **kwargs):
        return self.fn('server/urls')(*args, **kwargs)

    def namespace(self, *args, **kwargs):
        return self.fn('server/namespace')(*args, **kwargs)

    def logs(self, mod, pm='pm2', **kwargs):
        return self.fn(f'{pm}/logs')(mod, **kwargs)

    def app(self):
        """Serve the core API (mod.py itself) and the core app."""
        self.serve('mod')
        return self.serve('app')

    def setup(self):
        self.serve('ipfs')
        self.serve('mod')
        self.up('app')

    def get_ports(self, n: int = 3) -> list:
        port_range = self.get_port_range()
        used_ports = set(self.used_ports())
        available = [p for p in range(port_range[0], port_range[1]) if p not in used_ports]
        if len(available) < n:
            raise RuntimeError(f'Not enough available ports in range {port_range}')
        return available[:n]

    def get_port_range(self, port_range: list = None) -> list:
        if port_range is None:
            port_range = self.get('port_range', [])
        if isinstance(port_range, str):
            port_range = list(map(int, port_range.split('-')))
        if not port_range:
            port_range = self.port_range
        port_range = list(port_range)
        if len(port_range) < 2:
            raise ValueError('Port range must be a list of at least 2 integers')
        if not isinstance(port_range[0], int) or not isinstance(port_range[1], int):
            raise TypeError('Port range values must be integers')
        return port_range

    # ── Module Management (deploy / configure via dev agents) ────────────

    agent_options = ['claude', 'codex', 'cursor', 'dev', 'agent']

    def agents(self, search=None) -> List[str]:
        """List available dev-agent modules (claude and equivalents)."""
        agents = [a for a in self.agent_options if self.mod_exists(a)]
        if search is not None:
            agents = [a for a in agents if search in a]
        return agents

    def get_agent(self, agent: str = None) -> str:
        """Resolve a dev-agent name, defaulting to the first available (claude)."""
        agents = self.agents()
        assert agents, f'No dev-agent modules available (options: {self.agent_options})'
        agent = agent or agents[0]
        assert agent in agents, f'Agent {agent} not available, options: {agents}'
        return agent

    def dev(self, *query, mod=None, agent=None, **kwargs):
        """Run a dev agent (claude or an equivalent) over a module."""
        agent = self.get_agent(agent)
        return self.fn(f'{agent}/forward')(' '.join(map(str, query)), mod=mod, **kwargs)

    def configure(self, mod: str, updates: dict = None, query: str = None,
                  agent: str = None, **kwargs):
        """Configure a module: merge updates into its config.json, and/or
        describe the change in natural language for a dev agent to apply."""
        result = {'mod': mod}
        if updates:
            config = self.config(mod)
            config = {**(config if isinstance(config, dict) else {}), **updates}
            result['config_path'] = self.save_config(mod, config)
        if query:
            result['agent'] = self.get_agent(agent)
            result['result'] = self.dev(
                f'configure this module (config.json and related wiring): {query}',
                mod=mod, agent=agent, **kwargs)
        result['config'] = self.config(mod)
        return result

    def is_repo_url(self, url) -> bool:
        """True for git URLs and user/repo GitHub shorthand."""
        if not isinstance(url, str):
            return False
        if url.startswith(('http://', 'https://', 'git@')):
            return True
        parts = url.split('/')
        return len(parts) == 2 and all(parts) and not os.path.exists(self.abspath(url))

    def add_repo(self, url: str, name=None, key=None, comment=None) -> Dict[str, Any]:
        """Copy a module from GitHub: clone the repo into the orbit and register it."""
        if self.is_repo_url(url) and not url.startswith(('http://', 'https://', 'git@')):
            url = f'https://github.com/{url}'
        return self.fn('registry/reg_git')(url, name=name, key=key, comment=comment)

    def modules(self, search=None, **kwargs) -> List[Dict[str, Any]]:
        """Rich module cards (config + urls + running state) for the app."""
        running = self._pm2_running()
        mods = []
        for name, path in self.operational_tree(search=search).items():
            config = self.get_json(os.path.join(path, 'config.json'), {})
            if not isinstance(config, dict):
                config = {}
            mods.append({
                'name': config.get('name', name),
                'path': self.relpath(path),
                'url': config.get('urls') or config.get('url'),
                'port': config.get('port'),
                'app_port': config.get('app_port'),
                'desc': config.get('desc') or config.get('description'),
                'icon': config.get('icon'),
                'color': config.get('color'),
                'owner': config.get('owner') or config.get('key'),
                'running': name in running,
            })
        return mods

    def app_namespace(self, search: str = None, **kwargs):
        """Registered app servers {name: {url, api_url, owner}}. Used by the gateway middleware to route /{mod}/* and /api/{mod}/*."""
        return self.fn('server.namespace/app_namespace')(search=search, **kwargs)

    def app_owner(self, name: str, **kwargs):
        """Owner address of a registered app."""
        return self.fn('server.namespace/app_owner')(name, **kwargs)

    def is_app_owner(self, name: str, address: str, **kwargs):
        """Whether an address owns a registered app."""
        return self.fn('server.namespace/is_app_owner')(name, address, **kwargs)

    def app_status(self, **kwargs):
        """All module apps with running/stopped status."""
        return self.fn('server.namespace/app_status')(**kwargs)

    def serve_app(self, name: str, port: int = None, key=None, **kwargs):
        """Start a module's servers. Owner only (when an owner is registered)."""
        ns = self.mod('server.namespace')()
        address = self.key_address(key) if key else self.owner()
        if ns.app_owner(name) and not ns.is_app_owner(name, address):
            return {'error': f'Not owner of {name}'}
        return self.serve(name, port=port, **kwargs)

    def app_logs(self, name: str, lines: int = 100, **kwargs):
        """Tail the api/app logs for a module."""
        logs = {}
        for svc in ['api', 'app']:
            for log_path in [f'/tmp/{name}/{svc}.log', f'/tmp/mod-{svc}-{name}.log']:
                if os.path.exists(log_path):
                    text = self.get_text(log_path, '') or ''
                    logs[svc] = '\n'.join(text.splitlines()[-lines:])
                    break
        if not logs:
            try:
                logs['pm2'] = self.logs(name)
            except Exception as e:
                return {'error': f'No logs found for {name}: {e}'}
        return logs

    # ── Docker ───────────────────────────────────────────────────────────

    def up(self, mod='mod'):
        return self.fn('pm.docker/up')(mod)

    def down(self, mod='mod'):
        return self.fn('pm.docker/down')(mod)

    def enter(self, image='mod'):
        return self.fn('pm.docker/enter')(image)

    def build(self, *args, **kwargs):
        return self.fn('pm.docker/build')(*args, **kwargs)

    def exec(self, mod: str = 'mod', *args, **kwargs):
        return self.fn('pm.docker/exec')(mod, *args, **kwargs)

    def dockerfiles(self, mod=None):
        dirpath = self.dirpath(mod)
        return [os.path.join(dirpath, f) for f in os.listdir(dirpath) if f.startswith('Dockerfile')]

    # ── Start & Deploy ───────────────────────────────────────────────────

    def run_script(self, mod=None):
        """Find and execute the closest start.sh script."""
        path = self.abspath(self.dirpath(mod) if mod else os.getcwd())
        start_script = None

        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', '.git')]
            if 'start.sh' in files:
                start_script = os.path.join(root, 'start.sh')
                break

        if start_script is None:
            current = os.path.dirname(path)
            while True:
                candidate = os.path.join(current, 'start.sh')
                if os.path.isfile(candidate):
                    start_script = candidate
                    break
                parent = os.path.dirname(current)
                if parent == current:
                    break
                current = parent

        if start_script is None:
            raise FileNotFoundError(f'No start.sh found in or above {path}')

        os.chmod(start_script, os.stat(start_script).st_mode | 0o755)
        mod_dir = self.abspath(self.dirpath(mod) if mod else self.dirpath())
        self.print(f'Running {start_script} in {mod_dir}')
        return self.cmd(f'bash {start_script}', cwd=mod_dir, verbose=True)

    def _pm2_running(self):
        """Get set of running PM2 process names."""
        try:
            result = subprocess.run(['pm2', 'jlist'], capture_output=True, text=True, timeout=10)
            procs = json.loads(result.stdout) if result.stdout.strip() else []
            return {p['name'] for p in procs if p.get('pm2_env', {}).get('status') == 'online'}
        except Exception:
            return set()

    def _find_start_script(self, mod):
        """Find start.sh for a module."""
        path = self.abspath(self.dirpath(mod))
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')
                       and d not in ('node_modules', '__pycache__', '.git', 'target', '.next', 'venv', '.venv')]
            if 'start.sh' in files:
                return os.path.join(root, 'start.sh')
        return None

    def _detect_mod_type(self, mod):
        """Detect module type based on its files."""
        path = self.abspath(self.dirpath(mod))
        info = {
            'path': path, 'name': mod,
            'has_python': False, 'has_node': False, 'has_rust': False,
            'has_docker': False, 'has_app': False, 'has_requirements': False,
            'has_mod_py': False, 'python_module': None,
        }
        skip_dirs = {'node_modules', '__pycache__', '.git', 'target', '.next', 'venv', '.venv'}
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in skip_dirs]
            rel = os.path.relpath(root, path)
            if (0 if rel == '.' else rel.count(os.sep) + 1) > 2:
                continue
            for f in files:
                if f == 'requirements.txt':
                    info['has_requirements'] = info['has_python'] = True
                elif f == 'package.json':
                    info['has_node'] = True
                    if os.path.basename(root) in ('app', '') or rel == '.':
                        info['has_app'] = True
                elif f == 'Cargo.toml':
                    info['has_rust'] = True
                elif f in ('docker-compose.yml', 'Dockerfile'):
                    info['has_docker'] = True
                elif f == 'mod.py':
                    info['has_mod_py'] = info['has_python'] = True
                    info['python_module'] = os.path.basename(root)
        return info

    def create_start_script(self, mod):
        """Auto-generate a start.sh script for a module."""
        info = self._detect_mod_type(mod)
        path, mod_name = info['path'], info['name'].split('.')[-1]
        lines = [
            '#!/bin/bash',
            f'# Auto-generated start script for {mod_name}',
            'DIR="$(cd "$(dirname "$0")" && pwd)"',
            'cd "$DIR"', '',
        ]

        if info['has_docker'] and os.path.isfile(os.path.join(path, 'docker-compose.yml')):
            lines += [f'echo "Starting {mod_name} via docker-compose..."', 'docker-compose up -d']
        elif info['has_rust']:
            lines += [
                f'if [ ! -f "target/release/{mod_name}" ]; then',
                f'    echo "Building {mod_name}..."', '    cargo build --release', 'fi', '',
                f'echo "Starting {mod_name}..."', f'exec ./target/release/{mod_name}',
            ]
        elif info['has_python'] and info['has_node'] and info['has_app']:
            lines += ['if [ -f "requirements.txt" ]; then', '    pip3 install -r requirements.txt -q', 'fi', '']
            if info['has_mod_py'] and info['python_module']:
                lines += [f'python3 -m {info["python_module"]}.mod &', 'SERVER_PID=$!', '']
            lines += [
                'cd "$DIR/app"',
                'if [ ! -d "node_modules" ]; then', '    npm install', 'fi',
                'npm run dev &', 'APP_PID=$!', '',
                f'echo "{mod_name} started"',
                'trap "kill $SERVER_PID $APP_PID 2>/dev/null" EXIT', 'wait',
            ]
        elif info['has_node']:
            lines += [
                'if [ ! -d "node_modules" ]; then', '    npm install', 'fi', '',
                f'echo "Starting {mod_name}..."', 'npm run dev',
            ]
        elif info['has_python']:
            if info['has_requirements']:
                lines += ['if [ -f "requirements.txt" ]; then', '    pip3 install -r requirements.txt -q', 'fi', '']
            if info['has_mod_py'] and info['python_module']:
                lines += [f'echo "Starting {mod_name}..."', f'python3 -m {info["python_module"]}.mod']
            else:
                lines += [f'echo "Starting {mod_name}..."', f'python3 -c "import mod; mod.Mod().serve(\'{mod_name}\')"']
        else:
            lines += [f'echo "Starting {mod_name}..."', f'python3 -c "import mod; mod.Mod().serve(\'{mod_name}\')"']

        scripts_dir = os.path.join(path, 'scripts')
        script_path = os.path.join(scripts_dir if os.path.isdir(scripts_dir) else path, 'start.sh')
        with open(script_path, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        os.chmod(script_path, os.stat(script_path).st_mode | 0o755)
        self.print(f'Created {script_path}')
        return script_path

    def off_mods(self, search=None, orbit='orbit'):
        """List modules not currently running in PM2."""
        running = self._pm2_running()
        return [m for m in self.mods(search=search, orbit=orbit) if m not in running]

    def deploy(self, mod=None, search=None, orbit='orbit', create=True):
        """Deploy modules by running their start scripts."""
        if mod:
            mods_to_deploy = [mod]
        else:
            mods_to_deploy = self.off_mods(search=search, orbit=orbit)
            if not search and not mod:
                self.print(f'{len(mods_to_deploy)} modules are off:')
                for m in sorted(mods_to_deploy):
                    has_script = 'Y' if self._find_start_script(m) else 'N'
                    self.print(f'  [{has_script}] {m}')
                return mods_to_deploy

        results = {}
        for mod_name in mods_to_deploy:
            script = self._find_start_script(mod_name)
            if script is None:
                if create:
                    self.print(f'No start.sh for {mod_name}, creating one...')
                    script = self.create_start_script(mod_name)
                else:
                    results[mod_name] = 'skipped'
                    continue
            try:
                self.print(f'Deploying {mod_name}...')
                mod_dir = self.abspath(self.dirpath(mod_name))
                os.chmod(script, os.stat(script).st_mode | 0o755)
                self.cmd(f'bash {script}', cwd=mod_dir, verbose=True)
                results[mod_name] = 'deployed'
            except Exception as e:
                self.print(f'Failed to deploy {mod_name}: {e}')
                results[mod_name] = f'error: {e}'
        return results

    # ── Branch Management ─────────────────────────────────────────────

    def init_mod_meta(self, dirpath, branch='main'):
        """Ensure {dirpath}/.mod/ exists with a default branch file."""
        mod_dir = os.path.join(dirpath, '.mod')
        os.makedirs(mod_dir, exist_ok=True)
        branch_file = os.path.join(mod_dir, 'branch')
        if not os.path.exists(branch_file):
            with open(branch_file, 'w') as f:
                f.write(branch)

    def branch(self, mod=None):
        """Read the current branch for a module. Defaults to 'main'."""
        dirpath = self.dirpath(mod) if mod else self.paths['lib']
        branch_file = os.path.join(dirpath, '.mod', 'branch')
        if os.path.exists(branch_file):
            with open(branch_file, 'r') as f:
                return f.read().strip()
        return 'main'

    def set_branch(self, mod=None, branch='main'):
        """Set the branch for a module."""
        dirpath = self.dirpath(mod) if mod else self.paths['lib']
        self.init_mod_meta(dirpath, branch=branch)
        branch_file = os.path.join(dirpath, '.mod', 'branch')
        with open(branch_file, 'w') as f:
            f.write(branch)
        return {'mod': mod or self.name, 'branch': branch}

    # ── Module Creation ───────────────────────────────────────────────

    def new(self, name='test_base', base='base', orbit='orbit'):
        """Create a new mod from a base template, or copy it from a GitHub URL."""
        if isinstance(name, str) and (
                name.startswith(('http://', 'https://', 'git@')) or 'github.com' in name):
            return self.add_repo(name)
        dirpath = self.paths['orbit'][orbit] + '/' + name.replace('.', '/')
        if os.path.exists(dirpath):
            shutil.rmtree(dirpath)
        for k, v in self.content(base).items():
            new_path = dirpath + '/' + k.replace(f'{base}/', f'/{name}/')
            self.put_text(new_path, v)
        self.init_mod_meta(dirpath)
        self.update()
        assert self.mod_exists(name), f'Mod {name} not found after creation'
        return {'name': name, 'path': dirpath, 'msg': 'Mod Created', 'base': base, 'cid': self.cid(name)}

    def addpath(self, path, name=None, update=True):
        assert os.path.exists(path), f'Path {path} does not exist'
        path = self.abspath(path)
        name = name or path.split('/')[-1]
        dirpath = self.paths['orbit']['orbit'] + '/' + name.replace('.', '/')
        self.cmd(f'cp -r {path} {dirpath}')
        self.init_mod_meta(dirpath)
        return {'name': name, 'path': dirpath, 'msg': 'Mod Created from path'}

    def addcid(self, name='churn', cid='QmXUjBQRFa8DbY2GhD1Aq6a44EBYzgejmtwwnYYTfvnFW4'):
        file2text = self.fn('registry/content')(cid, expand=True)
        path = self.paths['orbit']['orbit'] + '/' + name.replace('.', '/')
        for k, v in file2text.items():
            print(f'Creating {path}/{k} for mod {name}')
            self.put_text(f'{path}/{k}', v)
        self.init_mod_meta(path)
        self.tree(update=True)
        assert self.mod_exists(name), f'Mod {name} not found after creation from cid {cid}'
        return {'name': name, 'path': path, 'msg': 'Mod Created from cid', 'cid': cid}

    def update(self):
        tree = self.tree(update=1)
        return {
            'success': True, 'message': 'Mod tree updated',
            'mods': len(tree), 'orbits': self.paths['orbit'].to_dict(),
            'orbit2depth': self._tree.orbit2depth,
        }

    def cpmod(self, from_mod: str = 'dev', to_mod: str = 'dev2', force=True):
        return self.fn('factory/cpmod')(from_mod=from_mod, to_mod=to_mod, force=force)

    def rmmod(self, mod: str = 'test'):
        return self.fn('factory/rmmod')(mod=mod)

    def clone(self, mod, name):
        return self.fn('factory/clone')(mod=mod, name=name)

    def mergemods(self, from_mod: Any, to_mod: Any, fns: list):
        for fn in fns:
            setattr(to_mod, fn, getattr(from_mod, fn))
        return to_mod

    def add_fns(self, obj, add_fns=['fns', 'schema', 'code', 'cid', 'edit', 'config', 'info']):
        for fn in add_fns:
            if not hasattr(obj, fn):
                setattr(obj, fn, partial(getattr(self, fn), obj=obj.__name__))
        return obj

    def get_mods_path(self, exp=True):
        return self.paths['orbit']['orbit']

    def new_app(self, name='myapp', port=None, orbit='orbit', install=True, serve=True):
        """One-step: create a module app, configure, install, and serve.

        Usage:
            m.new_app('dashboard')
            # -> Creates orbit/dashboard/ with Next.js app
            # -> Sets owner, port, basePath
            # -> Installs npm deps
            # -> Starts the server via PM2
            # -> Registers in app_namespace
        """
        # 1. Copy the appbase template (full directory tree, not just mod content)
        base_root = self.dirpath('appbase')            # orbit/appbase/
        target = self.paths['orbit'][orbit] + '/' + name.replace('.', '/')
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(base_root, target, ignore=shutil.ignore_patterns(
            'node_modules', '.next', '__pycache__', '_serve.sh',
        ))

        # 2. Rename inner module dir: target/appbase/ -> target/{name}/
        old_inner = os.path.join(target, 'appbase')
        new_inner = os.path.join(target, name)
        if os.path.exists(old_inner):
            os.rename(old_inner, new_inner)

        # 3. Allocate port and fill mod.json with owner, name, port, basePath
        import mod
        owner = self.owner()
        port = port or mod.free_port()
        app_dir = os.path.join(target, 'app')
        mod_json_path = os.path.join(app_dir, 'app.json')
        config = {
            'name': name,
            'port': port,
            'basePath': f'/{name}',
            'owner': owner,
        }
        with open(mod_json_path, 'w') as f:
            json.dump(config, f, indent=2)

        # 4. Update next.config.js basePath
        next_config_path = os.path.join(app_dir, 'next.config.js')
        if os.path.exists(next_config_path):
            with open(next_config_path) as f:
                content = f.read()
            content = content.replace("'/appbase'", f"'/{name}'")
            with open(next_config_path, 'w') as f:
                f.write(content)

        # 5. Update package.json name
        pkg_path = os.path.join(app_dir, 'package.json')
        if os.path.exists(pkg_path):
            with open(pkg_path) as f:
                pkg = json.load(f)
            pkg['name'] = name
            with open(pkg_path, 'w') as f:
                json.dump(pkg, f, indent=2)

        # 6. Update template page and layout with module name
        for fpath in [
            os.path.join(app_dir, 'app', 'page.tsx'),
            os.path.join(app_dir, 'app', 'layout.tsx'),
        ]:
            if os.path.exists(fpath):
                with open(fpath) as f:
                    txt = f.read()
                txt = txt.replace('appbase', name)
                with open(fpath, 'w') as f:
                    f.write(txt)

        # 7. Init .mod/branch metadata
        self.init_mod_meta(target)

        # 8. Refresh the module tree so the new mod is discoverable
        self.update()

        # 8. Install npm deps
        if install:
            subprocess.run(['npm', 'install', '--prefer-offline'], cwd=app_dir,
                           capture_output=True, timeout=120)

        # 9. Register and serve
        if serve:
            ns = self.mod('server.namespace')()
            ns.reg_app(name, f'http://0.0.0.0:{port}', owner=owner, port=port, path=target)
            mod_obj = self.mod(name)()
            mod_obj.serve(port=port)

        return {
            'name': name,
            'port': port,
            'url': f'http://localhost:{port}/{name}',
            'owner': owner,
            'path': target,
            'app_dir': app_dir,
            'msg': 'App module created and serving',
        }

    def kill_app(self, name, key=None):
        """Kill a module app server. Owner only."""
        ns = self.mod('server.namespace')()
        address = self.key_address(key) if key else self.owner()
        if not ns.is_app_owner(name, address):
            return {'error': f'Not owner of {name}'}
        pm2 = self.mod('pm.pm2')()
        pm2.kill(name)
        ns.dereg_app(name)
        return {'status': 'killed', 'name': name}

    def edit_app(self, name, query='', key=None, **kwargs):
        """Edit a module app. Owner only."""
        ns = self.mod('server.namespace')()
        address = self.key_address(key) if key else self.owner()
        if not ns.is_app_owner(name, address):
            return {'error': f'Not owner of {name}'}
        return self.edit(name, query, **kwargs)

    # ── Aliases ──────────────────────────────────────────────────────────
    fp = filepath
    dp = dirpath
    af = anchor_file
    ao = anchor_object
    cfg = config
    cpath = cfgpath = config_path
    cont = codemap = content
    get_fn = fn
    am = ms = mods
    cm = cmods = core_mods
    lm = lmods = local_mods
    a = ask
    e = edit
    card = info
    killall = kill_all
    rm_mod = rmmod
    is_file_mod = is_mod_file
    future = fut = submit
    create = add = fork = new
    copy = add_repo


def main():
    return Mod().main()
