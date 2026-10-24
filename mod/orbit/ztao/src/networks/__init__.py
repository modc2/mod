"""Network registry: every sub-package here that exports `ADAPTER` (a
networks.base.Network subclass) is a chain ztao can run on. Packages whose
name starts with `_` (like _template) are skipped. See README.md."""
import importlib
import os
import pkgutil

from .base import REQUIRED, Network  # noqa: F401

_HERE = os.path.dirname(os.path.abspath(__file__))


def adapter_classes():
    out = []
    for m in pkgutil.iter_modules([_HERE]):
        if not m.ispkg or m.name.startswith('_'):
            continue
        cls = getattr(importlib.import_module(f'{__name__}.{m.name}'),
                      'ADAPTER', None)
        if cls is not None:
            out.append(cls)
    return sorted(out, key=lambda c: (c.order, c.chain))


class Registry:
    """chain -> adapter instance, and network name -> adapter."""

    def __init__(self, signer):
        self.chains = {c.chain: c(signer) for c in adapter_classes()}
        self.by_net = {}
        for a in self.chains.values():
            for n in a.nets:
                if n in self.by_net:
                    raise RuntimeError(f'network {n!r} claimed by both '
                                       f'{self.by_net[n].chain} and {a.chain}')
                self.by_net[n] = a

    def __iter__(self):
        return iter(self.chains.values())

    def net(self, network):
        a = self.by_net.get(network)
        if a is None:
            raise ValueError(f'network must be one of {sorted(self.by_net)}')
        return a

    def chain(self, chain):
        a = self.chains.get(chain)
        if a is None:
            raise ValueError(f'chain must be one of {sorted(self.chains)}')
        return a
