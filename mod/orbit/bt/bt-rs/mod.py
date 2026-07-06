import os
import mod as m

class Mod:
    description = """bt-rs"""
    path = r'/Users/broski/mod/mod/orbit/bt/bt-rs'

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """Return module info."""
        return {
            'name': 'bt-rs',
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
        """Install Python project."""
        import subprocess
        return subprocess.run(['pip', 'install', '-e', '.'], cwd=r'/Users/broski/mod/mod/orbit/bt/bt-rs', capture_output=True, text=True).stdout
