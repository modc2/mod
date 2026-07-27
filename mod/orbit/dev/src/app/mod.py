import os
import src.mod as m

class Mod:
    description = """app"""
    path = r'/Users/broski/mod/mod/orbit/claude/app'

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
        return subprocess.run(['npm', 'install'], cwd=r'/Users/broski/mod/mod/orbit/claude/app', capture_output=True, text=True).stdout

    def build(self):
        """Build the project."""
        import subprmocess
        return subprocess.run(['npm', 'run', 'build'], cwd=r'/Users/broski/mod/mod/orbit/claude/app', capture_output=True, text=True).stdout
  