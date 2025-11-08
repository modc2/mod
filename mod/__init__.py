from .core.mod import Mod
_mod = Mod()
for _fn in dir(_mod):
    globals()[_fn] = getattr(_mod, _fn)

def main( *args, **kwargs):
    """
    Main function to run the mod
    """
    _mod.mod('cli')().forward()