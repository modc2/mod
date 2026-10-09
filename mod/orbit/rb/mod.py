import os
import mod as m

class Mod:
    description = """rb"""
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        import json
        config_path = os.path.join(self.path, 'config.json')
        version = None
        if os.path.exists(config_path):
            with open(config_path) as f:
                version = json.load(f).get('version')
        files = [e for e in os.listdir(self.path) if not e.startswith('__') and not e.startswith('.')]
        return {
            'name': 'rb',
            'description': self.description,
            'version': version,
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
