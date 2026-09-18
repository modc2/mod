"""
Fleet entry point for the monero module.

The implementation lives in `monero/mod.py`, next to the code it drives. This
file exists because a stub is generated at the module root, and a stub here
shadows the real class: the fleet would load it, find `info` and `readme`, and
report every function in config.json as missing.

It deliberately does not `from mod import Mod` -- this file *is* `mod`, so that
import resolves back to itself and fails half-initialised.
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_IMPL = os.path.join(_HERE, "monero", "mod.py")

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from monero.mod import Mod                       # noqa: F401
except ImportError:
    # Loaded without `monero` importable as a package -- load the file directly.
    _spec = importlib.util.spec_from_file_location("monero_impl", _IMPL)
    _impl = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_impl)
    Mod = _impl.Mod

__all__ = ["Mod"]
