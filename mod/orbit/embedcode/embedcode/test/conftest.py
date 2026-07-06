"""Stub out the mod framework so tests load embedcode/mod.py directly."""
import sys
import os
import types
import importlib.util

# prevent the mod framework from loading
sys.modules['mod'] = types.ModuleType('mod')

# load embedcode mod.py directly and make it importable
_mod_path = os.path.join(os.path.dirname(__file__), '..', 'mod.py')
_spec = importlib.util.spec_from_file_location("embedcode_mod", _mod_path)
embedcode_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(embedcode_mod)
sys.modules['embedcode_mod'] = embedcode_mod
