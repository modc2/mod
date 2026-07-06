from .core.mod import Mod, main
_mod = Mod()
for _fn in dir(_mod):
    globals()[_fn] = getattr(_mod, _fn)
