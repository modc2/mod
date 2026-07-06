"""
core/store/hippius — Hippius backend adapter for the store module.

Thin proxy over `mod/orbit/hippius`. Lets the store namespace expose Hippius
operations: `m store/hippius/put`, `m store/hippius/get`, etc.
"""
import mod as m


class Mod:
    description = "Hippius backend adapter (delegates to orbit/hippius)."

    expose = ['put', 'get', 'pin', 'list', 'status', 'peers', 'account', 'start_node', 'stop_node']

    def __init__(self, **kw):
        self._impl = m.mod('hippius')(**kw)

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

    def peers(self, **kw):
        return self._impl.peers()

    def account(self, **kw):
        return self._impl.account()

    def start_node(self, **kw):
        return self._impl.start_node()

    def stop_node(self, **kw):
        return self._impl.stop_node()
