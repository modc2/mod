"""
mod2mcp.runtime — load mods through core, keep the deployment registry,
execute calls (the EVM, if the compiler is solc).

Deployments persist in ~/.mod/mod2mcp/deployments.json so a server restart
redeploys everything. Calls on a readonly deployment that target a write
function revert before the mod is ever touched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import compiler

MODULE_DIR = Path(__file__).resolve().parents[1]
LIB_ROOT = MODULE_DIR.parents[2]          # orbit/mod2mcp -> orbit -> mod -> lib root
ORBIT_DIR = MODULE_DIR.parent
STATE_DIR = Path(os.environ.get('MOD2MCP_HOME', str(Path.home() / '.mod' / 'mod2mcp')))
REGISTRY = STATE_DIR / 'deployments.json'

# Appended, never prepended: `mod` itself must keep winning.
for p in (str(LIB_ROOT), str(MODULE_DIR)):
    if p not in sys.path:
        sys.path.append(p)

_core = None
_instances: Dict[str, Any] = {}
_manifests: Dict[str, dict] = {}


def core():
    global _core
    if _core is None:
        if os.getcwd() in (os.path.expanduser('~'), '/'):
            os.chdir(MODULE_DIR)           # core refuses to boot from ~ or /
        _ensure_mod_package()
        from mod.core.mod import Mod as Core
        _core = Core()
    return _core


def _ensure_mod_package() -> None:
    """Every orbit module ships a mod.py anchor, so when cwd is a module dir a
    bare `import mod` picks the anchor over the real package. Bind sys.modules
    ['mod'] to the real package by file path before anything imports it."""
    import importlib.util
    pkg_dir = LIB_ROOT / 'mod'
    existing = sys.modules.get('mod')
    if existing is not None and getattr(existing, '__file__', '') == str(pkg_dir / '__init__.py'):
        return
    if existing is not None:
        del sys.modules['mod']
    spec = importlib.util.spec_from_file_location(
        'mod', pkg_dir / '__init__.py', submodule_search_locations=[str(pkg_dir)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules['mod'] = pkg
    spec.loader.exec_module(pkg)


def mods() -> List[str]:
    """Mods that can be compiled: orbit dirs with an anchor (mod.py) or config.json."""
    out = []
    for d in sorted(ORBIT_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith(('_', '.')) or d.name == 'mod2mcp':
            continue
        if (d / 'mod.py').exists() or (d / 'config.json').exists():
            out.append(d.name)
    return out


def load(mod_name: str) -> Any:
    """Instantiate a mod's Mod class (cached)."""
    if mod_name not in _instances:
        cls = core().mod(mod_name)
        _instances[mod_name] = cls()
    return _instances[mod_name]


def _config_fns(mod_name: str) -> Optional[List[str]]:
    cfg = ORBIT_DIR / mod_name / 'config.json'
    try:
        fns = json.loads(cfg.read_text()).get('fns')
        return list(fns) if fns else None
    except Exception:
        return None


def contract(mod_name: str, fns: Optional[List[str]] = None,
             exclude: Optional[List[str]] = None, readonly: bool = False,
             cache: bool = True) -> dict:
    """Compile a mod into its manifest. `fns` overrides; else the mod's own
    config.json `fns` list is the declared interface; else every public method."""
    key = f'{mod_name}|{fns}|{exclude}|{readonly}'
    if cache and key in _manifests:
        return _manifests[key]
    obj = load(mod_name)
    include = fns or _config_fns(mod_name)
    manifest = compiler.compile_contract(mod_name, obj, include=include,
                                         exclude=exclude, readonly=readonly)
    _manifests[key] = manifest
    return manifest


def call(mod_name: str, fn: str, args: Optional[dict] = None,
         readonly: bool = False) -> Any:
    """Execute one function on a mod. Reverts (raises) on denied or
    readonly-violating calls before the mod is touched."""
    if fn.startswith('_') or fn in compiler.DENY:
        raise PermissionError(f'{fn!r} is not part of the ABI')
    if readonly and compiler.mutability(fn) == 'write':
        raise PermissionError(f'revert: {fn!r} is a write function on a readonly deployment')
    obj = load(mod_name)
    target = getattr(obj, fn, None)
    if not callable(target):
        raise AttributeError(f'{mod_name} has no function {fn!r}')
    return target(**(args or {}))


# ── deployment registry ─────────────────────────────────────────────────────

def deployments() -> Dict[str, dict]:
    try:
        return json.loads(REGISTRY.read_text())
    except Exception:
        return {}


def _save(reg: Dict[str, dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix('.tmp')
    tmp.write_text(json.dumps(reg, indent=2, default=str))
    tmp.replace(REGISTRY)


def deploy(mod_name: str, fns: Optional[List[str]] = None,
           exclude: Optional[List[str]] = None, readonly: bool = False) -> dict:
    """Compile and register a mod as a live MCP contract. Compilation must
    succeed (the mod imports and instantiates) before anything is recorded."""
    manifest = contract(mod_name, fns=fns, exclude=exclude, readonly=readonly, cache=False)
    reg = deployments()
    reg[mod_name] = {
        'address': manifest['address'],
        'readonly': bool(readonly),
        'fns': fns, 'exclude': exclude,
        'functions': len(manifest['functions']),
        'deployed_at': int(time.time()),
    }
    _save(reg)
    return {'deployed': mod_name, 'address': manifest['address'],
            'readonly': bool(readonly), 'functions': len(manifest['functions']),
            'tools': [t['name'] for t in manifest['tools']]}


def undeploy(mod_name: str) -> dict:
    reg = deployments()
    existed = reg.pop(mod_name, None)
    _save(reg)
    return {'undeployed': mod_name, 'existed': existed is not None}


def deployed_manifest(mod_name: str) -> Optional[dict]:
    d = deployments().get(mod_name)
    if d is None:
        return None
    return contract(mod_name, fns=d.get('fns'), exclude=d.get('exclude'),
                    readonly=bool(d.get('readonly')))
