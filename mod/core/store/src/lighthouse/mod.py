"""
core/store/lighthouse — Lighthouse Labs backend adapter for the store module.

Thin proxy over `mod/orbit/lighthouse` (lighthouse.storage — perpetual
IPFS/Filecoin pinning behind an API key). Lets the store namespace expose
Lighthouse operations: `m store/lighthouse/put`, `m store/lighthouse/set_key`, etc.
"""
import mod as m


class Mod:
    description = "Lighthouse Labs backend adapter (delegates to orbit/lighthouse)."

    expose = ['put', 'get', 'pin', 'list', 'status', 'usage', 'uploads',
              'set_key', 'key_status']

    def __init__(self, **kw):
        self._impl = m.mod('lighthouse')(**kw)

    def forward(self, **kw):
        return self._impl.status()

    def put(self, path: str, owner: str = None, key: str = None, **kw):
        return self._impl.put(path=path, owner=owner, key=key)

    def get(self, cid: str, out: str = None, **kw):
        return self._impl.get(cid=cid, out=out)

    def pin(self, cid: str, owner: str = None, **kw):
        return self._impl.pin(cid=cid, owner=owner)

    def list(self, owner: str = None, limit: int = 100, **kw):
        return self._impl.list(owner=owner, limit=limit)

    def status(self, **kw):
        return self._impl.status()

    def usage(self, **kw):
        return self._impl.usage()

    def uploads(self, page: int = 1, **kw):
        return self._impl.uploads(page=page)

    def set_key(self, api_key: str, **kw):
        return self._impl.set_key(api_key=api_key)

    def key_status(self, **kw):
        return self._impl.key_status()
