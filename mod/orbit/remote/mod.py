import os
import json
import mod as m

class Mod:
    description = """Remote access and execution utilities for mod orbit modules."""
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        config_path = os.path.join(self.path, 'config.json')
        try:
            with open(config_path) as f:
                config = json.load(f)
            version = config.get('version')
        except Exception:
            version = None
        return {
            'name': 'remote',
            'description': self.description,
            'version': version,
            'files': [f for f in os.listdir(self.path) if not f.startswith('_') and not f.endswith('.py')],
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return f"# {self.info()['name']}\n\n{self.description}"
