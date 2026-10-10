import os
import json
import mod as m

class Mod:
    description = """Remote access and execution utilities for mod orbit modules."""
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, fn: str = '', **kwargs):
        """Default entry point; fn selects a named method."""
        dispatch = {'info': self.info, 'readme': self.readme}
        if fn:
            if fn not in dispatch:
                return {'error': f'unknown fn: {fn!r}', 'available': list(dispatch)}
            return dispatch[fn]()
        return self.info()

    def info(self):
        """Return module info."""
        config_path = os.path.join(self.path, 'config.json')
        try:
            with open(config_path) as f:
                config = json.load(f)
            version = config.get('version')
            description = config.get('description', self.description)
        except Exception:
            version = None
            description = self.description
        return {
            'name': 'remote',
            'description': description,
            'version': version,
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
