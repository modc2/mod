import os
import mod as m

class Mod:
    description = """bt console — Next.js, exported static and served by bt.server (no node at runtime)"""
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        return {
            'name': 'bt/app',
            'published': os.path.realpath(os.path.join(self.path, 'dist')) if os.path.isdir(os.path.join(self.path, 'dist')) else None,
            'description': self.description,
            'path': self.path,
            'files': os.listdir(self.path),
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None

    def install(self):
        """Install project dependencies."""
        import subprocess
        return subprocess.run(['npm', 'install'], cwd=os.path.dirname(os.path.abspath(__file__)), capture_output=True, text=True).stdout

    def build(self):
        """Type-check, build and publish (atomic symlink swap; no restart needed)."""
        import subprocess
        r = subprocess.run(['bash', 'build.sh'], cwd=self.path, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'out': r.stdout[-2000:], 'err': r.stderr[-2000:]}
