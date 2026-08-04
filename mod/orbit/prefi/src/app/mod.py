import os
import mod as m

HERE = os.path.dirname(os.path.abspath(__file__))


class Mod:
    description = """PreFi web app — Next.js console for the prefi trading protocol"""
    path = HERE

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        return {
            'name': 'app',
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
        return subprocess.run(['npm', 'install'], cwd=HERE,
                              capture_output=True, text=True).stdout

    def build(self):
        """Build the project."""
        import subprocess
        return subprocess.run(['npm', 'run', 'build'], cwd=HERE,
                              capture_output=True, text=True).stdout
