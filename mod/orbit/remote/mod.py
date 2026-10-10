import os
import json
import mod as m

class Mod:
    description = """Remote access and execution utilities for mod orbit modules."""
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, fn: str = '', **kwargs):
        """Default entry point; fn selects a named method."""
        dispatch = {
            k: getattr(self, k)
            for k in dir(self)
            if not k.startswith('_') and k != 'forward' and callable(getattr(self, k))
        }
        if fn:
            if fn not in dispatch:
                return {'error': f'unknown fn: {fn!r}', 'available': list(dispatch)}
            return dispatch[fn](**kwargs)
        return self.info(**kwargs)

    def info(self):
        """Return module info."""
        config_path = os.path.join(self.path, 'config.json')
        try:
            with open(config_path) as f:
                config = json.load(f)
            name = config.get('name', 'remote')
            version = config.get('version')
            description = config.get('description', self.description)
        except Exception:
            version = None
            description = self.description
            name = 'remote'
        return {
            'name': name,
            'description': description,
            'version': version,
            'fns': [k for k in dir(self) if not k.startswith('_') and k != 'forward' and callable(getattr(self, k))],
            'files': [f for f in os.listdir(self.path) if not f.startswith('_') and not f.endswith('.py')],
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        info = self.info()
        return f"# {info['name']}\n\n{info['description']}"
