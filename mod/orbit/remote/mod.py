import os
import json
import urllib.request
import urllib.error
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

    def call(self, mod: str = '', fn: str = '', **kwargs):
        """POST to another orbit module via the local gateway and return its JSON response."""
        if not mod:
            return {'error': 'mod is required'}
        url = f'http://localhost:3000/{mod}/api'
        body = json.dumps({'fn': fn, **kwargs}).encode()
        req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return json.loads(body)
            except Exception:
                return {'error': str(e), 'status': e.code, 'mod': mod, 'fn': fn}
        except Exception as e:
            return {'error': str(e), 'mod': mod, 'fn': fn}

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        info = self.info()
        return f"# {info['name']}\n\n{info['description']}"
