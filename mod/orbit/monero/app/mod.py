import os
import subprocess

_DIR = os.path.dirname(os.path.abspath(__file__))


class Mod:
    """The monero web app. The module itself lives one level up in monero/mod.py."""

    description = "Next.js front end for the monero module"
    path = _DIR

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {'name': 'monero-app', 'description': self.description,
                'path': self.path, 'files': os.listdir(self.path)}

    def install(self):
        """Install dependencies."""
        return subprocess.run(['npm', 'install'], cwd=self.path,
                              capture_output=True, text=True).stdout

    def build(self):
        """Build the production bundle."""
        env = dict(os.environ, NEXT_PUBLIC_BASE_PATH=os.environ.get(
            'NEXT_PUBLIC_BASE_PATH', '/monero'))
        return subprocess.run(['npm', 'run', 'build'], cwd=self.path, env=env,
                              capture_output=True, text=True).stdout
