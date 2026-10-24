import os
import mod as m

class Mod:
    description = """rb"""
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, method='info', **kwargs):
        """Default entry point. Dispatches to the named public method."""
        handler = getattr(self, method, None)
        if callable(handler) and not method.startswith('_'):
            return handler(**kwargs)
        if method == 'info':
            return self.info()
        available = sorted(n for n in dir(self) if not n.startswith('_') and callable(getattr(self, n)))
        return {'error': 'unknown method', 'method': method, 'available': available}

    def info(self):
        """Return module info."""
        import json
        config_path = os.path.join(self.path, 'config.json')
        version = None
        name = 'rb'
        description = self.description
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
                version = cfg.get('version')
                name = cfg.get('name', name)
                description = cfg.get('description', description)
        files = [e for e in os.listdir(self.path) if not e.startswith('__') and not e.startswith('.')]
        methods = sorted(n for n in dir(self) if not n.startswith('_') and n != 'forward' and callable(getattr(self, n)))
        return {
            'name': name,
            'description': description,
            'version': version,
            'files': files,
            'methods': methods,
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return {'found': True, 'content': m.get_text(p), 'filename': name}
        return {'found': False, 'content': None}
