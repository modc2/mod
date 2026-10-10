import json
import os
import mod as m

class Mod:
    path = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(path, 'config.json')) as _f:
        _cfg = json.load(_f)
    description = _cfg.get('description', '')

    def forward(self, **kwargs):
        """Default entry point."""
        method = kwargs.get('method') or kwargs.get('action')
        if method == 'readme':
            return self.readme()
        if not method or method == 'info':
            return self.info()
        return {'error': f'unknown method: {method}'}

    def info(self):
        """Return module info."""
        cfg = self._cfg
        return {
            'name': cfg.get('name', ''),
            'description': cfg.get('description', ''),
            'version': cfg.get('version', ''),
            'schema': cfg.get('schema', ''),
            'files': sorted(f for f in os.listdir(self.path)
                            if f != '__pycache__' and not f.startswith('.')),
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                try:
                    return {'content': m.get_text(p), 'filename': name}
                except Exception:
                    break
        cfg = self._cfg
        content = f"# {cfg.get('name', '')}\n\n{cfg.get('description', '')}\n\nVersion: {cfg.get('version', '')}"
        return {'content': content, 'filename': None}
