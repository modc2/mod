"""
core/store/filecoin — Filecoin backend adapter for the store module.

Thin proxy over `mod/orbit/filecoin`. Lets the store namespace expose Filecoin
operations: `m store/filecoin/put`, `m store/filecoin/get`, etc.
"""
import mod as m


class Mod:
    description = "Filecoin backend adapter (delegates to orbit/filecoin)."

    expose = ['put', 'get', 'pin', 'list', 'status', 'wallet', 'deals', 'start_node', 'stop_node']

    def __init__(self, **kw):
        self._impl = m.mod('filecoin')(**kw)

    def forward(self, **kw):
        return self._impl.status()

    def put(self, path: str, owner: str = None, deal: bool = False, **kw):
        return self._impl.put(path=path, owner=owner, deal=deal)

    def get(self, cid: str, out: str = None, **kw):
        return self._impl.get(cid=cid, out=out)

    def pin(self, cid: str, owner: str = None, **kw):
        return self._impl.pin(cid=cid, owner=owner)

    def list(self, owner: str = None, limit: int = 100, **kw):
        return self._impl.list(owner=owner, limit=limit)

    def status(self, **kw):
        return self._impl.status()

    def wallet(self, **kw):
        return self._impl.wallet()

    def deals(self, limit: int = 50, **kw):
        return self._impl.deals(limit=limit)

    def start_node(self, **kw):
        return self._impl.start_node()

    def stop_node(self, **kw):
        return self._impl.stop_node()
