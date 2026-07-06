import os
import subprocess
import mod as m


class Mod:
    description = "commune (commune-ai/commune) installer"
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {
            'name': 'commune',
            'description': self.description,
            'path': self.path,
            'files': os.listdir(self.path),
        }

    def setup(self, dest: str = None, repo: str = None) -> dict:
        """Run setup.sh to install commune.

        Usage:
            m commune/setup
            m commune/setup dest=~/code/commune
            m commune/setup repo=https://github.com/commune-ai/commune
        """
        script = os.path.join(self.path, 'setup.sh')
        env = os.environ.copy()
        if repo:
            env['COMMUNE_REPO'] = repo
        cmd = ['bash', script]
        if dest:
            cmd.append(os.path.expanduser(dest))
        r = subprocess.run(cmd, env=env)
        return {'ok': r.returncode == 0, 'returncode': r.returncode}

    def readme(self):
        for name in ['README.md', 'readme.md', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
