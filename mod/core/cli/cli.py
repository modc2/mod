import sys
import time
import json
import inspect
import os
import subprocess
import signal
from typing import Any, List
from copy import deepcopy
from pathlib import Path

import mod as m

print = m.print

JOB_DIR = Path('/tmp/mod_jobs')


class Cli:
    def __init__(self, mod='mod', fn='forward'):
        self.argv = sys.argv[1:]
        self.fn = fn
        self.mod = m.mod(mod)()
        self._fn_name = None

    def forward(self, argv=None, **kwargs):
        """Forward the function to the mod and function, with token auth and tx saving."""
        # Check for bg=True — fork into background before parsing fn
        bg = self._pop_bg_flag()
        if bg:
            return self._run_bg()

        # Check for __mod_bg_child — we ARE the background child
        if os.environ.get('__MOD_BG_CHILD'):
            return self._run_bg_child()

        t0 = m.time()

        fn = self.get_fn()
        params = self.get_params()

        # Generate identity token (1s replay window)
        token = None
        client = None
        try:
            from mod.core.server.auth.auth.auth import Auth
            auth = Auth(max_age=1)
            token_data = {'fn': self._fn_name or 'unknown', 'params_hash': m.hash(str(params))}
            token = auth.token(data=token_data)
            client = auth.key.address
        except Exception as e:
            pass  # token auth optional — don't block CLI if key unavailable

        result = fn(*params['args'], **params['kwargs']) if callable(fn) else fn
        duration = m.time() - t0

        # Save transaction
        try:
            from mod.core.tx import Tx
            tx = Tx()
            tx.save(
                fn=self._fn_name or 'unknown',
                params=params,
                result=result,
                client=client or 'unknown',
                token=token or '',
            )
        except Exception:
            pass  # tx saving is best-effort

        self.print_result(result)
        print(f'\u0394t = {duration:.4f}s ', color='cyan')

    # ── Background jobs ──────────────────────────────────────────────────

    def _pop_bg_flag(self) -> bool:
        """Remove --bg or bg=True from argv if present."""
        for i, arg in enumerate(self.argv):
            if arg in ('--bg', '--b', 'bg=True', 'bg=true', 'bg=1'):
                self.argv.pop(i)
                return True
        return False

    def _run_bg(self):
        """Fork the current CLI command into a detached background process."""
        import shutil
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        job_id = f'{int(time.time())}_{os.getpid()}'
        job_file = JOB_DIR / f'{job_id}.json'

        # Reconstruct the original argv without bg flag
        child_argv = list(self.argv)
        m_bin = shutil.which('m') or 'm'
        cmd = [m_bin] + child_argv

        env = os.environ.copy()
        env['__MOD_BG_CHILD'] = str(job_file)

        # Write initial job status
        job_meta = {
            'id': job_id,
            'cmd': ' '.join(['m'] + child_argv),
            'status': 'running',
            'pid': None,
            'file': str(job_file),
            'started': time.time(),
        }
        job_file.write_text(json.dumps(job_meta, indent=2))

        # Launch detached — survives parent exit
        log_file = JOB_DIR / f'{job_id}.log'
        log_fd = open(log_file, 'w')
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=log_fd, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        job_meta['pid'] = proc.pid
        job_file.write_text(json.dumps(job_meta, indent=2))

        print(f'bg job started: {job_id}', color='green')
        print(f'  pid:  {proc.pid}', color='cyan')
        print(f'  file: {job_file}', color='cyan')
        print(f'  log:  {log_file}', color='cyan')
        print(f'  check: m job {job_id}', color='cyan')

    def _run_bg_child(self):
        """Execute inside the forked background child — write results to job file."""
        job_file = Path(os.environ['__MOD_BG_CHILD'])
        job_meta = json.loads(job_file.read_text())
        job_meta['pid'] = os.getpid()
        job_file.write_text(json.dumps(job_meta, indent=2))

        try:
            fn = self.get_fn()
            params = self.get_params()
            result = fn(*params['args'], **params['kwargs']) if callable(fn) else fn
            # Ensure JSON-serializable
            try:
                json.dumps(result)
            except (TypeError, ValueError):
                result = json.loads(json.dumps(result, default=str))
            job_meta['status'] = 'done'
            job_meta['result'] = result
        except Exception as e:
            job_meta['status'] = 'error'
            job_meta['error'] = str(e)

        job_meta['finished'] = time.time()
        job_meta['duration'] = job_meta['finished'] - job_meta.get('started', job_meta['finished'])
        job_file.write_text(json.dumps(job_meta, indent=2, default=str))

    def print_result(self, result):
        if self.is_generator(result):
            for item in result:
                if isinstance(item, dict):
                    print(item)
                else:
                    print(item, end='')
        else:
            print(result, color='green')

    def _local_mod(self, init_kwargs=None):
        """Check for a mod.py in the CWD and load its Mod class if present."""
        import os, importlib.util
        cwd = os.getcwd()
        mod_py = os.path.join(cwd, 'mod.py')
        if not os.path.isfile(mod_py):
            return None
        try:
            spec = importlib.util.spec_from_file_location('_local_mod', mod_py)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls = getattr(module, 'Mod', None)
            if cls is None:
                return None
            return cls(**(init_kwargs or {}))
        except Exception as e:
            print(f'Warning: failed to load local mod.py: {e}', color='yellow')
            return None

    def get_fn(self) -> tuple:
        """
        Get the function object from the mod and function name.

        Routing rules:
          {mod}/{fn} *args **kwargs
          mod is dot separated: mod.submod.subsubmod/fn
          multiple slashes: mod/submod/fn
        """
        argv = self.argv
        mod = self.mod
        init_kwargs = self.get_init_params()
        if init_kwargs:
            print(f'Init params: {init_kwargs}', color='cyan')

        # Check for local mod.py in CWD first
        if len(argv) > 0 and '/' not in argv[0]:
            local = self._local_mod(init_kwargs)
            if local is not None and hasattr(local, argv[0]):
                mod = local
                fn = argv.pop(0)
                self._fn_name = fn if isinstance(fn, str) else getattr(fn, '__name__', 'unknown')
                return getattr(mod, fn)

        if len(argv) == 0:
            fn = 'go'
        elif hasattr(mod, argv[0]):
            fn = argv.pop(0)
        elif argv[0].endswith('/'):
            mod = m.mod(argv.pop(0)[:-1])()
            fn = self.fn
        elif argv[0].startswith('/'):
            fn = argv.pop(0)[1:]
        elif len(argv[0].split('/')) == 2:
            mod, fn = argv.pop(0).split('/')
            mod = m.mod(mod)(**init_kwargs)
        elif len(argv[0].split('/')) >= 2:
            parts = argv.pop(0).split('/')
            fn = parts.pop(-1)
            mod = m.mod(parts.pop(0))(**init_kwargs)
            for part in parts:
                mod = getattr(mod, part)
        elif m.mod_exists(argv[0]):
            mod = m.mod(argv.pop(0))(**init_kwargs)
            fn = argv.pop(0)
        else:
            raise Exception(f'Function was not extracted from {argv}')

        self._fn_name = fn if isinstance(fn, str) else getattr(fn, '__name__', 'unknown')
        return getattr(mod, fn)

    def get_init_params(self) -> dict:
        """Get --key=value init params (double-dash prefix)."""
        argv = self.argv
        init_params = {}
        if len(argv) > 0:
            for arg in argv:
                if arg.startswith('--') and '=' in arg:
                    key, value = arg[2:].split('=')
                    init_params[key] = self.str2python(value)
                    argv.remove(arg)
        self.argv = argv
        return init_params

    def get_params(self) -> tuple:
        """Parse positional and keyword arguments from argv."""
        argv = self.argv
        params = {'args': [], 'kwargs': {}}
        parsing_kwargs = False
        json_object_detected = False
        if len(argv) > 0:
            for arg in argv:
                if json_object_detected:
                    last_key = list(params['kwargs'].keys())[-1]
                    params['kwargs'][last_key] += ' ' + arg
                    if arg.endswith('}'):
                        json_object_detected = False
                        print(f'Parsing json object for key {last_key}: {params["kwargs"][last_key]}', argv)
                        params['kwargs'][last_key] = json.loads(params['kwargs'][last_key])
                    continue
                if '=' in arg:
                    parsing_kwargs = True
                    key, value = arg.split('=')
                    if value.startswith('{'):
                        json_object_detected = True
                        if value.endswith('}'):
                            json_object_detected = False
                            try:
                                value = json.loads(value)
                            except Exception:
                                pass
                    params['kwargs'][key] = self.str2python(value)
                else:
                    assert parsing_kwargs is False, f'Cannot mix positional and keyword arguments {argv}'
                    params['args'].append(self.str2python(arg))
        return params

    _object_cache = {}

    def shorten(self, x: str, n=12):
        if len(x) > n:
            return x[:n] + '...' + x[-n:]
        return x

    def is_generator(self, obj):
        if isinstance(obj, str):
            if not hasattr(self, obj):
                return False
            obj = getattr(self, obj)
        if not callable(obj):
            result = inspect.isgenerator(obj)
        else:
            result = inspect.isgeneratorfunction(obj)
        return result

    def str2python(self, x):
        x = str(x)
        if isinstance(x, str):
            if x.startswith('py(') and x.endswith(')'):
                try:
                    return eval(x[3:-1])
                except Exception:
                    return x
        if x.lower() in ['null'] or x == 'None':
            return None
        elif x.lower() in ['true', 'false']:
            return bool(x.lower() == 'true')
        elif x.startswith('[') and x.endswith(']'):
            try:
                list_items = x[1:-1].split(',')
                x = [self.str2python(item.strip()) for item in list_items]
                if len(x) == 1 and x[0] == '':
                    x = []
                return x
            except Exception:
                return x
        elif x.startswith('{') and x.endswith('}'):
            if len(x) == 2:
                return {}
            try:
                dict_items = x[1:-1].split(',')
                return {key.strip(): self.str2python(value.strip()) for key, value in [item.split(':', 1) for item in dict_items]}
            except Exception:
                return x
        else:
            for type_fn in [int, float]:
                try:
                    return type_fn(x)
                except ValueError:
                    pass
        return x
