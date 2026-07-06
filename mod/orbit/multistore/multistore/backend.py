"""
StorageBackend — the standard interface every backend in multistore implements.

Implements localfs is the reference (mod/core/store). Wrap a new backend by:

    from multistore.backend import StorageBackend

    class Mod(StorageBackend):
        name = 'mybackend'

        def put(self, path_or_data, owner=None, key=None, **kw):
            ...
            return {'cid': cid, 'size': size}

        def get(self, cid, out=None, **kw):
            ...

        # Optional but recommended:
        def pin(self, cid, owner=None, **kw): ...
        def rm(self, cid, caller=None, **kw): ...
        def list(self, owner=None, limit=100, offset=0, **kw): ...
        def status(self): ...

Once a module is in mod/orbit/<name>/ and exposes this surface, append its
name to multistore.BACKEND_NAMES (or the multistore.config.json `backends`
list) and it shows up in the dashboard with no other changes.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable, Union


# ── Required shape ────────────────────────────────────────────────

class PutResult(Dict[str, Any]):
    """Minimum keys: cid (str), size (int). Backends may add extras."""


class GetResult(Dict[str, Any]):
    """Either {path: str} for files retrieved to disk, or {data: bytes} /
    {data: dict} for inline retrieval. Backends pick whichever fits."""


@runtime_checkable
class StorageBackendProtocol(Protocol):
    """Structural type — anything that quacks like a StorageBackend works."""
    name: str

    def put(self, path: str, owner: Optional[str] = None,
            key: Optional[str] = None, **kw: Any) -> Dict[str, Any]: ...

    def get(self, cid: str, out: Optional[str] = None, **kw: Any) -> Any: ...


# ── Reference base class ──────────────────────────────────────────

class StorageBackend:
    """Concrete base — gives every method a sensible default + an `expose`
    list that the auto-UI / multistore facade reads.

    Subclasses MUST override `put` and `get`. Everything else has a fallback.
    """
    name: str = 'storage'
    # Methods exposed via the mod auto-server. Matches multistore.fns surface.
    expose: List[str] = [
        'put', 'get', 'pin', 'rm', 'list', 'exists', 'status',
        'pins', 'owner', 'is_owner', 'set_owner',
    ]

    # ── Required (override these) ────────────────────────────────

    def put(self, path: str, owner: Optional[str] = None,
            key: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        raise NotImplementedError(f'{self.name}.put() must be implemented')

    def get(self, cid: str, out: Optional[str] = None, **kw: Any) -> Any:
        raise NotImplementedError(f'{self.name}.get() must be implemented')

    # ── Recommended (sensible defaults) ──────────────────────────

    def pin(self, cid: str, owner: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        """Pin-equivalent for backends without a separate pin step."""
        return {'ok': True, 'cid': cid, 'note': f'{self.name} stores by default; pin is a no-op'}

    def rm(self, cid: str, caller: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        raise NotImplementedError(f'{self.name}.rm() not supported')

    def list(self, owner: Optional[str] = None, limit: int = 100,
             offset: int = 0, search: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        """Default: empty. Backends with a local index should override."""
        return {'total': 0, 'count': 0, 'offset': offset, 'limit': limit, 'objects': []}

    def exists(self, cid: str) -> bool:
        """Cheapest possible existence check. Default: try `get` and swallow errors."""
        try:
            self.get(cid)
            return True
        except Exception:
            return False

    def status(self) -> Dict[str, Any]:
        """Lightweight health surface for the dashboard cards."""
        return {'name': self.name, 'alive': True}

    # ── Owner gate (optional, mirrors ipfs/store pattern) ────────

    _owner_path: Optional[str] = None  # set in subclasses, e.g. ~/.mod/<name>/owner.json

    def owner(self) -> Optional[str]:
        import json, os
        p = self._owner_path
        if not p or not os.path.exists(p):
            return None
        try:
            return (json.load(open(p)).get('owner') or '').lower() or None
        except Exception:
            return None

    def is_owner(self, address: Optional[str]) -> bool:
        cur = self.owner()
        if not cur:
            return True  # bootstrap mode: anyone may act
        return (address or '').lower() == cur

    def set_owner(self, address: str, caller: Optional[str] = None) -> Dict[str, Any]:
        import json, os
        addr = (address or '').strip().lower()
        if not addr.startswith('0x') or len(addr) != 42:
            return {'ok': False, 'error': 'address must be 0x-prefixed 42 chars'}
        cur = self.owner()
        if cur and (caller or '').lower() != cur:
            return {'ok': False, 'error': 'only current owner can transfer'}
        if not self._owner_path:
            return {'ok': False, 'error': f'{self.name} did not configure _owner_path'}
        os.makedirs(os.path.dirname(self._owner_path), exist_ok=True)
        with open(self._owner_path, 'w') as f:
            json.dump({'owner': addr}, f, indent=2)
        return {'ok': True, 'owner': addr, 'previous': cur}


# ── Adapter for legacy modules that don't inherit StorageBackend ──

class LegacyAdapter(StorageBackend):
    """Wrap an existing module (filecoin, hippius, ipfs) so multistore can
    treat it as a StorageBackend without modifying the source. Reads
    common method names and forwards them; raises NotImplementedError for
    anything the wrapped module doesn't expose."""

    def __init__(self, impl: Any, name: str):
        self._impl = impl
        self.name = name

    def _call(self, *candidates: str, **kwargs: Any) -> Any:
        for fn_name in candidates:
            fn = getattr(self._impl, fn_name, None)
            if callable(fn):
                # Filter kwargs to ones the target accepts.
                accepted = set(getattr(fn, '__code__', None).co_varnames if getattr(fn, '__code__', None) else kwargs.keys())
                kw = {k: v for k, v in kwargs.items() if k in accepted}
                return fn(**kw)
        raise NotImplementedError(f'{self.name} exposes none of {candidates}')

    def put(self, path: str, owner: Optional[str] = None,
            key: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        r = self._call('put', 'add', 'add_file', path=path, owner=owner, key=key)
        if isinstance(r, dict):
            return r
        if isinstance(r, str):
            return {'cid': r}
        return {'result': r}

    def get(self, cid: str, out: Optional[str] = None, **kw: Any) -> Any:
        return self._call('get', 'cat', 'get_file', cid=cid, out=out)

    def pin(self, cid: str, owner: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        try:
            return self._call('pin', 'pin_add', cid=cid, owner=owner)
        except NotImplementedError:
            return super().pin(cid, owner)

    def rm(self, cid: str, caller: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        return self._call('rm', 'pin_rm', cid=cid, caller=caller)

    def list(self, owner: Optional[str] = None, limit: int = 100,
             offset: int = 0, search: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        try:
            r = self._call('list', owner=owner, limit=limit, offset=offset, search=search)
            if isinstance(r, dict):
                return r
            if isinstance(r, list):
                return {'total': len(r), 'count': len(r), 'offset': offset, 'limit': limit, 'objects': r}
            return {'objects': [], 'total': 0, 'count': 0, 'offset': offset, 'limit': limit}
        except NotImplementedError:
            return super().list(owner, limit, offset, search)

    def status(self) -> Dict[str, Any]:
        try:
            return self._call('status') or super().status()
        except NotImplementedError:
            return super().status()
