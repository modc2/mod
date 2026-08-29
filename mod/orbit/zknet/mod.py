import os
import mod as m

class Mod:
    description = """zknet"""
    path = r'/root/mod/mod/orbit/zknet'

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        return {
            'name': 'zknet',
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
        return subprocess.run(['npm', 'install'], cwd=r'/root/mod/mod/orbit/zknet', capture_output=True, text=True).stdout

    def build(self):
        """Build the project."""
        import subprocess
        return subprocess.run(['npm', 'run', 'build'], cwd=r'/root/mod/mod/orbit/zknet', capture_output=True, text=True).stdout
