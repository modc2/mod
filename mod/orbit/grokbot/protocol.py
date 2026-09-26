"""
`import mod` — the protocol package, not this module's own mod.py.

Every mod ships a `mod.py`, so whichever directory sits first on sys.path
decides what `import mod` means. Run from inside this module it resolves to the
wrong one. The import is done once here, with this module's own directory taken
off the path.
"""
import importlib
import sys
from pathlib import Path

MINE = {str(Path(__file__).resolve().parent)}


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


def auth(key=None, max_age: int = 604_800):
    """The fleet's one shared identity — `m.mod('auth')`.

    `key` goes to the constructor, not to `.token(key=…)`: the auth mod builds
    the envelope from `self.key` and signs with the argument, so a per-call key
    mints an envelope addressed to somebody else and fails its own verify.
    """
    return protocol().mod('auth')(key=key, max_age=max_age)
