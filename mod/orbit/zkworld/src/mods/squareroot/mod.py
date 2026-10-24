"""squareroot — a Noir project mod, a submod of zkworld.

This dir is a self-contained project of one ZK framework (see `framework` in
config.json) and a mod in its own right: the protocol addresses it as
`zkworld.squareroot` (path2name drops the `src` and `mods` segments). Every fn
is the parent workbench's, bound to this one project, so the toolchain
logic lives in one place (orbit/zkworld/src/mod.py + src/frameworks.py).

    m zkworld.squareroot/build
    m zkworld.squareroot/test
    m zkworld.squareroot/vibe prompt="..."
"""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = os.path.basename(HERE)
# orbit/zkworld/src/mods/<name>/mod.py -> orbit/zkworld/src/mod.py
PARENT = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'mod.py')


def _parent():
    # load by path under a unique name: a bare `import mod` would resolve to
    # the protocol's own `mod` package, not zkworld's anchor
    spec = importlib.util.spec_from_file_location('zkworld_anchor', PARENT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Mod()


def _ext():
    # optional ext.py next to this file: a class `Ext` whose methods become
    # this contract's own extra fns (listed in config.json `ext_fns`). Kept
    # out of mod.py because adopt() re-renders mod.py from the template.
    p = os.path.join(HERE, 'ext.py')
    if not os.path.exists(p):
        return object
    spec = importlib.util.spec_from_file_location(f'zkworld_ext_{NAME}', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Ext


class Mod(_ext()):
    description = 'Prove you know a square root of a public number without revealing it.'
    path = HERE

    def __init__(self):
        self.zk = _parent()

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return self.zk.get(NAME)

    def build(self, timeout=900):
        return self.zk.build(NAME, timeout=timeout)

    def test(self, timeout=1200):
        return self.zk.test(NAME, timeout=timeout)

    def vibe(self, prompt, model=None, verify=True, timeout=1800):
        return self.zk.vibe(NAME, prompt, model=model, verify=verify,
                               timeout=timeout)

    def abi(self):
        return self.zk.abi(NAME)

    def tree(self):
        return self.zk.tree(NAME)

    def read(self, path):
        return self.zk.read(NAME, path)

    def write(self, path, content):
        return self.zk.write(NAME, path, content)

    def clean(self):
        return self.zk.clean(NAME)

    # the app: /zkworld/squareroot/ — cairo: a page over the contract on the local
    # devnet; circuits: a page that runs witness()
    def app(self):
        return self.zk.app(NAME)

    def deploy(self, args=None, contract=None, account='alice'):
        return self.zk.deploy(NAME, args=args, contract=contract,
                                 account=account)

    def call(self, fn, args=None, account='alice'):
        return self.zk.call(NAME, fn, args=args, account=account)

    def events(self, limit=50):
        return self.zk.events(NAME, limit=limit)

    # circuits (circom): run on an input, get the public outputs
    def witness(self, input=None):
        return self.zk.witness(NAME, input=input)
