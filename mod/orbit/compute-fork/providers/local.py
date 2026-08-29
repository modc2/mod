"""Your own boxes — the sibling `Compute` class in this module's compute.py.

Docker containers on hardware you control, billed on-chain per hour through the
Market contract. It is a provider like any other here, which is the point: the
same `computefork_search` that prices Akash prices the machine under your desk.

Off by default (`COMPUTE_FORK_LOCAL=1` to enable) because it touches docker and the
chain, and a search fan-out should not block on either.
"""

import os

from .base import Provider, ProviderError, num, offer, instance


class Local(Provider):
    name = 'local'
    title = 'Local / self-hosted (docker + on-chain billing)'
    upstream = ''
    docs = 'm compute-fork/local'
    kyc = 'none'
    pay = ('market tokens',)
    custody = 'local'
    caps = ('search', 'rent', 'instances', 'status', 'logs', 'exec', 'stop')
    key_hint = 'set COMPUTE_FORK_LOCAL=1 to include your own docker hosts'

    def __init__(self, key=None):
        super().__init__(key)
        self._c = None

    @property
    def enabled(self):
        return os.environ.get('COMPUTE_FORK_LOCAL', '') not in ('', '0', 'false')

    def _compute(self):
        if not self.enabled:
            raise ProviderError('local: disabled — set COMPUTE_FORK_LOCAL=1',
                                provider=self.name)
        if self._c is None:
            import importlib.util
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            spec = importlib.util.spec_from_file_location(
                'computefork_local_impl', os.path.join(here, 'compute.py'))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._c = mod.Compute()
        return self._c

    def has_key(self):
        return self.enabled

    def key_state(self):
        return 'enabled' if self.enabled else 'disabled (set COMPUTE_FORK_LOCAL=1)'

    def search(self, f):
        if not self.enabled:
            return []
        out = []
        for name, o in (self._compute().offers() or {}).items():
            out.append(offer(
                self.name, name, usd_hr=num(o.get('rate')),
                gpu='gpu' if o.get('gpu') else None,
                cpu=num(o.get('cpus')), ram_gb=num(str(o.get('memory') or '').rstrip('gG')),
                available=True, kind='gpu' if o.get('gpu') else 'cpu',
                note=f"{o.get('description')} · host {o.get('provider')} · "
                     f"rate is in Market tokens/hr, not USD",
                raw=o))
        return [o for o in out if f.match(o)]

    def rent(self, ref, name='mod', host=None, **opts):
        if not host:
            o = (self._compute().offers() or {}).get(ref) or {}
            host = o.get('provider')
        r = self._compute().rent(name, host=host, offer=ref, **opts)
        return instance(self.name, name, name=name, status='running', raw=r)

    def instances(self):
        return [instance(self.name, k, name=k, status=v.get('status'),
                         usd_hr=num(v.get('rate')), raw=v)
                for k, v in (self._compute().ps() or {}).items()]

    def logs(self, ref, tail=200):
        return {'logs': self._compute().logs(ref, tail=tail)}

    def exec(self, ref, cmd):
        return {'output': self._compute().exec(ref, cmd)}

    def stop(self, ref):
        return {'stopped': ref, 'result': self._compute().release(ref)}
