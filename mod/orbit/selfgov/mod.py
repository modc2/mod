import json
import os
import time
import mod as m

class Mod:
    path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self):
        try:
            with open(os.path.join(self.path, 'config.json')) as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        self.name = cfg.get('name', 'selfgov')
        self.description = cfg.get('description', 'selfgov')
        self.version = cfg.get('version', '0.0.0')
        self.schema = cfg.get('schema', '')
        self.loaded_at = time.time()

    def _actions(self):
        return [n for n in dir(self) if not n.startswith('_') and n != 'forward' and callable(getattr(self, n))]

    def forward(self, **kwargs):
        """Default entry point."""
        action = kwargs.get('action', 'info')
        if action in self._actions():
            return getattr(self, action)(**{k: v for k, v in kwargs.items() if k != 'action'})
        return {'error': f'unknown action: {action}', 'available': self._actions()}

    def info(self):
        """Return module info."""
        return {
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'schema': self.schema,
            'path': self.path,
            'files': [f for f in os.listdir(self.path) if f != '__pycache__' and not f.startswith('.')],
            'loaded_at': self.loaded_at,
            'actions': self._actions(),
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return {'content': m.get_text(p), 'path': p}
        content = f"# {self.name}\n\n{self.description}\n\nVersion: {self.version}\n"
        return {'content': content, 'synthesized': True}
