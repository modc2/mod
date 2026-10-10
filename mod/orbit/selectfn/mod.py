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
        return [m for m in dir(self) if not m.startswith('_') and m != 'forward' and callable(getattr(self, m))]

    def forward(self, **kwargs):
        """Default entry point. Pass fn=<method_name> to call a named method."""
        fn = kwargs.pop('fn', 'info')
        if fn.startswith('_'):
            return {'error': 'fn not allowed', 'fn': fn}
        method = getattr(self, fn, None)
        if not callable(method):
            return {'error': 'unknown fn', 'fn': fn, 'available': self._public_methods()}
        try:
            return method(**kwargs)
        except Exception as e:
            return {'error': str(e), 'fn': fn, 'error_type': type(e).__name__}

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
            'methods': {name: (getattr(self, name).__doc__ or '') for name in self._public_methods()},
        }

    def readme(self, **_):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return {'content': m.get_text(p)}
        return {'error': 'no readme found'}
