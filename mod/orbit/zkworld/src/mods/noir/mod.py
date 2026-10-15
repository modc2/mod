"""zkworld.noir — the Noir framework, a running submod of zkworld.

One dir per framework under src/mods/<id>/, addressed `zkworld.noir`
(path2name drops `src` and `mods`). It is a service of its own: a small
stdlib server (src/api/fwserver.py) on port 51355 that the parent keeps
up and proxies at /zkworld/noir/. Every project written in Noir is a
submod of zkworld that this framework mod owns: projects() lists them and
new/build/test/witness only take those. The toolchain logic itself stays in
one place (orbit/zkworld/src/frameworks.py, the noir adapter).

    m zkworld.noir/info
    m zkworld.noir/projects
    m zkworld.noir/new name=my_proof
    m zkworld.noir/serve
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FID = os.path.basename(HERE)
SRC = os.path.dirname(os.path.dirname(HERE))      # orbit/zkworld/src
PARENT = os.path.join(SRC, 'mod.py')
SERVER = os.path.join(SRC, 'api', 'fwserver.py')


def _parent():
    # by path under a unique name: a bare `import mod` is the protocol package
    spec = importlib.util.spec_from_file_location('zkworld_anchor', PARENT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Mod:
    path = HERE
    config = json.load(open(os.path.join(HERE, 'config.json')))
    port = config['port']
    description = config['description']

    def __init__(self):
        self.parent = _parent()
        self.zk = self.parent.Mod()
        self.fw = self.parent.FW.get(FID)

    # ---- what this framework is ---------------------------------------------

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        cat = [c for c in self.parent.FW.catalog() if c['id'] == FID]
        return {'name': f'zkworld.{FID}', 'framework': FID,
                'submod_of': 'zkworld', 'port': self.port,
                'running': self.running(), 'pid': os.getpid(),
                'url': f"{self.zk.base}/{FID}/",
                **self.fw.describe(deep=True),
                'catalog': cat[0] if cat else None,
                'projects': self.projects()['projects'],
                'fns': self.config['fns']}

    def health(self):
        return {'ok': True, 'framework': FID, 'installed': self.fw.installed()}

    def toolchain(self):
        return {'framework': FID, 'installed': self.fw.installed(),
                'install': self.fw.install, 'tools': self.fw.toolchain()}

    def templates(self):
        return {'framework': FID, 'templates': self.fw.templates()}

    def projects(self):
        """zkworld's project submods written in this framework (custom: the
        runner-protocol ones, whatever catalog framework they name)."""
        rows = [c for c in self.zk._catalog()
                if self.zk._fw(c['path']) is self.fw]
        return {'framework': FID, 'count': len(rows), 'projects': [
            {**{k: c.get(k) for k in ('name', 'framework', 'state', 'statement',
                                      'template', 'description')},
             'mod': f"zkworld.{c['name']}", 'app': f"{self.zk.base}/{c['name']}/"}
            for c in rows]}

    # ---- the project verbs, held to this framework --------------------------

    def _mine(self, name):
        pkg, d = self.zk._subdir(name)
        if self.zk._fw(d) is not self.fw:
            raise ValueError(f'{pkg!r} is not a {FID} project; see projects()')
        return pkg

    def new(self, name, desc=None, template=None):
        if FID == 'custom':
            raise ValueError('custom projects name their catalog framework: '
                             'use zkworld new(framework=risc0|sp1|...)')
        return self.zk.new(name, desc=desc, template=template, framework=FID)

    def build(self, name, timeout=900):
        return self.zk.build(self._mine(name), timeout=timeout)

    def test(self, name, timeout=1200):
        return self.zk.test(self._mine(name), timeout=timeout)

    def vibe(self, name, prompt, model=None, verify=True, timeout=1800, on_step=None):
        return self.zk.vibe(self._mine(name), prompt, model=model, verify=verify,
                            timeout=timeout, on_step=on_step)

    def clean(self, name):
        return self.zk.clean(self._mine(name))

    def abi(self, name):
        return self.zk.abi(self._mine(name))

    def witness(self, name, input=None):
        return self.zk.witness(self._mine(name), input=input)

    # ---- the service ----------------------------------------------------------

    def running(self):
        try:
            with socket.create_connection(('127.0.0.1', self.port), timeout=0.3):
                return True
        except OSError:
            return False

    def serve(self, port=None, background=True):
        """Run this framework's server (src/api/fwserver.py) on its port."""
        port = int(port or self.port)
        cmd = [sys.executable, SERVER, FID, str(port)]
        if not background:
            os.execv(sys.executable, cmd)
        self.kill(port)
        log = open(os.path.join(HERE, 'server.log'), 'a')
        p = subprocess.Popen(cmd, stdout=log, stderr=log, cwd=HERE,
                             start_new_session=True)
        return {'pid': p.pid, 'port': port, 'framework': FID}

    def kill(self, port=None):
        port = int(port or self.port)
        out = subprocess.run(['fuser', '-k', f'{port}/tcp'],
                             capture_output=True, text=True)
        return {'port': port, 'killed': out.returncode == 0}


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        Mod().serve(background=False)
    else:
        print(json.dumps(Mod().info(), indent=2, default=str))
