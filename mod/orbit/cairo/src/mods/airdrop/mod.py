"""airdrop — a Cairo contract mod, a submod of cairo.

This dir is a self-contained Scarb project (Scarb.toml, src/lib.cairo,
tests/) and a mod in its own right: the protocol addresses it as
`cairo.airdrop` (path2name drops the `src` and `mods` segments). Every fn
is the parent workbench's, bound to this one contract, so the toolchain
logic lives in one place (orbit/cairo/src/mod.py).

    m cairo.airdrop/build
    m cairo.airdrop/test
    m cairo.airdrop/vibe prompt="..."
"""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = os.path.basename(HERE)
# orbit/cairo/src/mods/<name>/mod.py -> orbit/cairo/src/mod.py
PARENT = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'mod.py')


def _parent():
    # load by path under a unique name: a bare `import mod` would resolve to
    # the protocol's own `mod` package, not cairo's anchor
    spec = importlib.util.spec_from_file_location('cairo_anchor', PARENT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Mod()


class Mod:
    description = 'Airdrop: claim your allocation with a Poseidon Merkle proof.'
    path = HERE

    def __init__(self):
        self.cairo = _parent()

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return self.cairo.get(NAME)

    def build(self, timeout=900):
        return self.cairo.build(NAME, timeout=timeout)

    def test(self, timeout=1200):
        return self.cairo.test(NAME, timeout=timeout)

    def vibe(self, prompt, model=None, verify=True, timeout=1800):
        return self.cairo.vibe(NAME, prompt, model=model, verify=verify,
                               timeout=timeout)

    def abi(self):
        return self.cairo.abi(NAME)

    def tree(self):
        return self.cairo.tree(NAME)

    def read(self, path):
        return self.cairo.read(NAME, path)

    def write(self, path, content):
        return self.cairo.write(NAME, path, content)

    def clean(self):
        return self.cairo.clean(NAME)

    # the app: /cairo/airdrop/ — a page over this contract on the local devnet
    def app(self):
        return self.cairo.app(NAME)

    def deploy(self, args=None, contract=None, account='alice'):
        return self.cairo.deploy(NAME, args=args, contract=contract,
                                 account=account)

    def call(self, fn, args=None, account='alice'):
        return self.cairo.call(NAME, fn, args=args, account=account)

    def events(self, limit=50):
        return self.cairo.events(NAME, limit=limit)
