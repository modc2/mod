"""
Resolving `import mod` to the protocol package rather than to ours.

Every mod in the fleet ships a `mod.py`, so whichever directory happens to sit
first on `sys.path` decides what `import mod` means. The API server puts this
module's own root on the path so it can `from src import ...` — and from that
moment `import mod` finds `shelf/mod.py`, which has no `.mod()` on it. The
failure is quiet in the worst way: `_localfs()` catches the exception and
reports "localfs unavailable", so snapshots stopped being pinned and said so in
a footnote. It worked from the CLI and not from the server, which is the shape
of bug that costs an afternoon.

So the import is done once, with this module's own directories taken off the
path for the duration, and the result cached. wasmland solves it the same way
in its `src/storage.py`; the trap is structural, not specific to either.
"""
import importlib
import sys
from pathlib import Path

_PROTOCOL = None


def protocol():
    """The `mod` protocol package — never this module's own mod.py."""
    global _PROTOCOL
    if _PROTOCOL is not None:
        return _PROTOCOL

    got = sys.modules.get('mod')
    if got is not None and hasattr(got, 'mod'):
        _PROTOCOL = got
        return _PROTOCOL

    mine = {str(Path(__file__).resolve().parent),          # src/
            str(Path(__file__).resolve().parent.parent)}   # the module root
    saved_path = list(sys.path)
    saved_mod = sys.modules.pop('mod', None)
    try:
        sys.path = [p for p in sys.path
                    if p and str(Path(p).resolve()) not in mine]
        _PROTOCOL = importlib.import_module('mod')
        return _PROTOCOL
    except Exception:
        # Put back whatever was there; a missing protocol is a degraded
        # feature (no pinning), never a crash in a diagnostic tool.
        if saved_mod is not None:
            sys.modules['mod'] = saved_mod
        return None
    finally:
        sys.path = saved_path


def mod(name: str):
    """Instantiate another mod by name, or None if it is not on this box."""
    package = protocol()
    if package is None:
        return None
    try:
        return package.mod(name)()
    except Exception:
        return None
