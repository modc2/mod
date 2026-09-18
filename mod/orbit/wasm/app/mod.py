import os
import mod as m


class Mod:
    description = """wasm"""
    # Resolve from this file, not a baked absolute path — the module is
    # served from wherever it is checked out, and a hardcoded one made every
    # fn that touched it (info, readme) raise on a foreign host.
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        return {
            'name': 'wasm',
            'description': self.description,
            'path': self.path,
            'files': sorted(os.listdir(self.path)),
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
