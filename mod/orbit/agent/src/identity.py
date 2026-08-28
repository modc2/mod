"""
identity - who owns what

One rule, shared by every collection that can be owned (agents, prompts,
memory notes, installed tool docs):

    an item's owner is the address that created it; an item with no
    recorded owner belongs to the HOST — the module owner.

The host is therefore the admin of everything unowned: shipped agents, seeded
prompts, and anything created before ownership was recorded. The host can edit
or remove any item; everyone else can only manage what they own.

Which means creating is for signed-in callers only: an anonymous create has
no owner to record, so the item would land on the host — the maker could never
edit or delete their own work, and the host would inherit everyone's.

Usage:
    ident = Identity(host="0xabc…", resolve=mod._resolve_address, is_host=mod.is_owner)
    ident.owner_of(None)          -> {"owner": "0xabc…", "owner_source": "host"}
    ident.can_manage(None, key)   -> True only for the host
    ident.require_signed_in(key, "create prompt")
    ident.require(owner, key, "remove prompt 'x'")
"""
from typing import Any, Callable, Dict, Optional

try:
    import mod as m
except ImportError:
    m = None


class Identity:
    description = "Owner resolution + permission checks for owned items"

    def __init__(self, host: str = None,
                 resolve: Callable[[Any], str] = None,
                 is_host: Callable[[Any], bool] = None):
        self.host = (host or "").lower() or None
        self._resolve = resolve
        self._is_host = is_host

    # ── caller resolution ────────────────────────────────────────────

    def addr(self, key: Any = None) -> Optional[str]:
        """Resolve an auth token / address / key object to an address."""
        if key is None:
            return None
        if self._resolve is not None:
            try:
                a = self._resolve(key)
            except Exception:
                a = None
        else:
            a = self._standalone_addr(key)
        return a.lower() if isinstance(a, str) and a else None

    @staticmethod
    def _standalone_addr(key: Any) -> Optional[str]:
        """Address resolution without a bound module — verify via the auth mod."""
        if hasattr(key, "address"):
            return key.address
        key_str = str(key)
        if key_str.startswith("0x") and len(key_str) in (42, 66):
            return key_str
        if m:
            try:
                return m.mod("auth")().verify(key_str)["key"]
            except Exception:
                pass
        return None

    def is_the_host(self, key: Any = None) -> bool:
        """True when the caller is the host (module owner).

        Unbound — no host address and no owner check — means the host is
        unknown, so nobody gets host rights.
        """
        if self._is_host is not None:
            try:
                return bool(self._is_host(key))
            except Exception:
                return False
        if not self.host:
            return False
        return self.addr(key) == self.host

    @property
    def host_known(self) -> bool:
        return bool(self.host) or self._is_host is not None

    # ── ownership ────────────────────────────────────────────────────

    def owner_of(self, owner: str = None) -> Dict[str, Optional[str]]:
        """Effective owner of an item. No recorded owner -> the host owns it."""
        if owner:
            return {"owner": owner.lower(), "owner_source": "item"}
        if self.host:
            return {"owner": self.host, "owner_source": "host"}
        return {"owner": None, "owner_source": None}

    def can_manage(self, owner: str = None, key: Any = None,
                   builtin: bool = False) -> bool:
        """Can this caller edit/remove an item owned by `owner`?

        host            -> anything, built-ins included
        item's owner    -> their own item
        nobody knows the host (local/dev) -> unowned, non-builtin items are open
        """
        if self.is_the_host(key):
            return True
        if builtin:
            return False
        if owner:
            return self.addr(key) == owner.lower()
        return not self.host_known

    def signed_in(self, key: Any = None) -> bool:
        """True when the caller's token resolves to a verified address."""
        return bool(self.addr(key))

    def can_create(self, key: Any = None) -> bool:
        """Only a signed-in caller can make a new item — see the module
        docstring. The host may create without one (they own the unowned),
        and unbound (no host known — standalone/dev) stays open."""
        return (not self.host_known) or self.signed_in(key) or self.is_the_host(key)

    def require_signed_in(self, key: Any = None, operation: str = "create"):
        """Raise PermissionError unless the caller may create."""
        if self.can_create(key):
            return
        raise PermissionError(f"Permission denied: cannot {operation} — sign in first")

    def require(self, owner: str = None, key: Any = None,
                operation: str = "this operation", builtin: bool = False):
        """Raise PermissionError unless the caller may manage the item."""
        if self.can_manage(owner, key, builtin):
            return
        if builtin:
            raise PermissionError(f"Permission denied: {operation} — host only")
        who = self.owner_of(owner)
        raise PermissionError(
            f"Permission denied: {operation} — owned by {who['owner'] or 'the host'}"
        )

    def test(self) -> bool:
        host = "0xaaa0000000000000000000000000000000000001"
        user = "0xbbb0000000000000000000000000000000000002"
        ident = Identity(host=host)
        assert ident.owner_of(None) == {"owner": host, "owner_source": "host"}
        assert ident.owner_of(user.upper())["owner_source"] == "item"
        assert ident.is_the_host(host) and not ident.is_the_host(user)
        # host manages everything, users only their own
        assert ident.can_manage(None, host, builtin=True)
        assert not ident.can_manage(None, user)
        assert ident.can_manage(user, user)
        assert not ident.can_manage(user, None)
        # creating needs a sign-in; the host is always signed in enough
        assert ident.can_create(user) and ident.can_create(host)
        assert not ident.can_create(None)
        try:
            ident.require_signed_in(None, "create agent")
            raise AssertionError("anonymous create allowed")
        except PermissionError:
            pass
        # unbound: unowned items are open, built-ins never are
        free = Identity()
        assert free.can_manage(None, None)
        assert not free.can_manage(None, None, builtin=True)
        assert not free.can_manage(user, None)
        assert free.can_create(None)      # standalone/dev — no host to answer to
        return True
