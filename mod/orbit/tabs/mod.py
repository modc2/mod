import json
import os
import mod as m

class Mod:
    description = """tabs"""
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, action=None, **kwargs):
        """Default entry point."""
        if action == 'readme':
            return self.readme()
        elif action is not None:
            return {'error': f'Unknown action: {action!r}', 'valid_actions': ['readme']}
        return self.info()

    def info(self):
        """Return module info."""
        config_path = os.path.join(self.path, 'config.json')
        with open(config_path) as f:
            config = json.load(f)
        files = sorted(e for e in os.listdir(self.path) if e != '__pycache__' and not e.startswith('.'))
        return {
            'name': config.get('name', 'tabs'),
            'version': config.get('version', ''),
            'description': config.get('description', self.description),
            'path': self.path,
            'files': files,
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return {'error': 'No README found'}
