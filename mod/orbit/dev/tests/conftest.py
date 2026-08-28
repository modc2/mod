"""Make the real `mod` framework package importable during tests.

pytest's default (prepend) import mode prepends this `tests/` directory to
`sys.path` each time it imports a test module. Because `tests/` ships a
vestigial `mod.py` framework-submodule stub, a bare ``import mod`` inside the
SDK (`src/mod.py`) would resolve to that stub instead of the real framework
package (`…/mod/__init__.py`), which lacks `key()` and the rest of the API.
That made every test constructing `Mod()` error with
``AttributeError: module 'mod' has no attribute 'key'``.

Path-order tweaks don't survive pytest re-prepending `tests/` per module, so
instead we import the real package once here (conftest is imported before any
test module) and bind it in `sys.modules['mod']`. The import cache then wins
over `sys.path` order for every subsequent ``import mod``.
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))

# Find the ancestor dir that holds the real `mod/__init__.py` package.
_root = None
_d = _here
while True:
    if os.path.isfile(os.path.join(_d, "mod", "__init__.py")):
        _root = _d
        break
    parent = os.path.dirname(_d)
    if parent == _d:
        break
    _d = parent

if _root is not None:
    # Import the real package with `tests/` stripped from the path so its
    # `mod.py` stub can't shadow it, then leave it cached in sys.modules.
    _cached = sys.modules.pop("mod", None)
    _saved = list(sys.path)
    sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _here]
    sys.path.insert(0, _root)
    try:
        import mod  # noqa: F401  — binds the real package into sys.modules
    except Exception:
        # Restore whatever was there if the real package fails to load.
        if _cached is not None:
            sys.modules["mod"] = _cached
    finally:
        sys.path[:] = _saved
