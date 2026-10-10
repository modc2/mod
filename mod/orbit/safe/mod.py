import json
import os
import mod as m

class Mod:
    path = os.path.dirname(os.path.abspath(__file__))
    _cfg = json.load(open(os.path.join(path, 'config.json')))
    description = _cfg.get('description', '')

    def forward(self, **kwargs):
        """Default entry point."""
        if kwargs.get('method') == 'readme' or kwargs.get('action') == 'readme':
            return self.readme()
        return self.info()

    def info(self):
        """Return module info."""
        cfg = self._cfg
        return {
            'name': cfg.get('name', ''),
            'description': cfg.get('description', ''),
            'version': cfg.get('version', ''),
            'schema': cfg.get('schema', ''),
            'files': [f for f in os.listdir(self.path)
                      if f != '__pycache__' and not f.startswith('.')],
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
