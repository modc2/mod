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
        except Exception:
            self._name = 'selectfn'
            self._description = self.description

    def forward(self, **kwargs):
        """Default entry point. Pass fn=<method_name> to call a named method."""
        fn = kwargs.pop('fn', 'info')
        method = getattr(self, fn, None)
        return method(**kwargs) if callable(method) else self.info()

    def info(self):
        """Return module info."""
        files = sorted(
            e for e in os.listdir(self.path) if not e.startswith('__')
        )
        return {
            'name': self._name,
            'description': self._description,
            'path': self.path,
            'files': files,
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
