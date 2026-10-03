"""
`import mod` — the protocol package, not this module's own mod.py.

Every mod ships a `mod.py`, so whichever directory sits first on sys.path
decides what `import mod` means. Run from inside this module it resolves to the
wrong one and fails as "module 'mod' has no attribute 'mod'". So the import is
done once here, with this module's own directories taken off the path.
"""
import importlib
import sys
from pathlib import Path

MINE = {str(Path(__file__).resolve().parent),
        str(Path(__file__).resolve().parent / 'api'),
        str(Path(__file__).resolve().parent / 'app')}


def protocol():
    got = sys.modules.get('mod')
    if got is not None and hasattr(got, 'mod'):
        return got
    saved = list(sys.path)
    sys.modules.pop('mod', None)
    try:
        sys.path = [p for p in sys.path
                    if p and str(Path(p).resolve()) not in MINE]
        return importlib.import_module('mod')
    finally:
        sys.path = saved


def auth(key=None, max_age: int = 86_400):
    """The fleet's one shared identity auth — `m.mod('auth')`.

    `key` is passed to the constructor rather than to `.token(key=…)`: the auth
    mod builds the token envelope from `self.key` and signs with the argument,
    so a key given per-call mints an envelope whose address is somebody else's
    and fails its own verification.

    Do NOT reach for `m.mod('auth.base')` here — it signs a payload with a
    nonce and its tokens will not cross-verify with the ones every other module
    in this fleet mints.
    """
    return protocol().mod('auth')(key=key, max_age=max_age)
