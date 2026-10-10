import inspect
import json
import os
import mod as m

class Mod:
    description = """selectfn"""

    def __init__(self):
        self.path = os.path.dirname(os.path.abspath(__file__))
        try:
            with open(os.path.join(self.path, 'config.json')) as f:
                cfg = json.load(f)
            self._name = cfg.get('name', 'selectfn')
            self._description = cfg.get('description', self.description)
            self._version = cfg.get('version', '')
        except Exception:
            self._name = 'selectfn'
            self._description = self.description
            self._version = ''

    def _public_methods(self):
        return [name for name in dir(self) if not name.startswith('_') and name != 'forward' and callable(getattr(self, name))]

    def forward(self, **kwargs):
        """Default entry point. Pass fn=<method_name> to call a named method."""
        fn = kwargs.pop('fn', 'info')
        available = self._public_methods()
        if not isinstance(fn, str):
            return {'ok': False, 'error': 'fn must be a string', 'fn': repr(fn), 'available': available}
        if fn not in available:
            return {'ok': False, 'error': 'unknown fn', 'fn': fn, 'available': available}
        method = getattr(self, fn)
        try:
            result = method(**kwargs)
            if not isinstance(result, dict):
                result = {'result': result}
            result.setdefault('ok', True)
            return result
        except Exception as e:
            return {'ok': False, 'error': str(e), 'fn': fn, 'error_type': type(e).__name__}

    def ping(self, **_):
        """Return a lightweight liveness response."""
        return {'ok': True, 'name': self._name, 'version': self._version}

    def info(self, **_):
        """Return module info."""
        files = sorted(
            e for e in os.listdir(self.path) if not e.startswith('__') and not e.startswith('.')
        )
        return {
            'name': self._name,
            'description': self._description,
            'version': self._version,
            'files': files,
            'methods': {name: {'doc': (getattr(self, name).__doc__ or ''), 'sig': str(inspect.signature(getattr(self, name)))} for name in self._public_methods()},
        }

    def readme(self, **_):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                try:
                    return {'content': m.get_text(p), 'file': os.path.basename(p)}
                except Exception:
                    continue
        return {'content': self._description, 'source': 'description'}
