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
        self.loaded_at = time.time()

    def forward(self, **kwargs):
        """Default entry point."""
        action = kwargs.get('action', 'info')
        if action == 'info':
            return self.info()
        if action == 'readme':
            return self.readme()
        return {'error': f'unknown action: {action}', 'available': ['info', 'readme']}

    def info(self):
        """Return module info."""
        return {
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'path': self.path,
            'files': [f for f in os.listdir(self.path) if not f.startswith('__')],
            'loaded_at': self.loaded_at,
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return {'content': m.get_text(p), 'path': p}
        content = f"# {self.name}\n\n{self.description}\n\nVersion: {self.version}\n"
        return {'content': content, 'synthesized': True}
