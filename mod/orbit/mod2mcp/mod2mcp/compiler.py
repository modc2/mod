"""
mod2mcp.compiler — compile a Mod class into a deterministic ABI + MCP tool list.

The smart-contract framing: a mod's public methods are the contract's
functions. Each one is classified view (reads) or write (mutates), typed from
its signature, and given a selector. The interface — names, input types,
mutability, nothing else — is hashed into a stable address: same interface,
same address, every time. Docstrings and bodies don't move the address, just
like Solidity ABIs.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from typing import Any, Dict, List, Optional

# Lifecycle / process management is never part of the ABI — a contract does
# not expose its own constructor or selfdestruct to callers.
DENY = frozenset([
    'serve', 'kill', 'kill_all', 'install', 'build', 'test', 'mcp', 'app',
])

VIEW_PREFIXES = (
    'get', 'list', 'ls', 'show', 'read', 'search', 'status', 'info', 'health',
    'price', 'balance', 'sources', 'feeds', 'schema', 'fns', 'query', 'find',
    'head', 'peek', 'stat', 'history', 'headlines', 'abi', 'deployments',
    'mods', 'tools', 'manifest', 'whoami', 'preview', 'quote', 'check',
    'resolve', 'explain', 'describe', 'view', 'is_', 'has_', 'count',
)
VIEW_NAMES = frozenset(['config', 'description', 'forward', 'help'])

PYTYPE = {str: 'string', int: 'integer', float: 'number', bool: 'boolean',
          list: 'array', tuple: 'array', dict: 'object'}


def mutability(name: str) -> str:
    n = name.lower()
    if n in VIEW_NAMES or any(n.startswith(p) for p in VIEW_PREFIXES):
        return 'view'
    return 'write'


def _json_type(param: inspect.Parameter) -> Optional[str]:
    ann = param.annotation
    if ann is not inspect.Parameter.empty:
        if ann in PYTYPE:
            return PYTYPE[ann]
        m = re.match(r'(str|int|float|bool|list|dict)', str(ann).replace('typing.', '').lower())
        if m:
            return PYTYPE[{'str': str, 'int': int, 'float': float, 'bool': bool,
                           'list': list, 'dict': dict}[m.group(1)]]
    if param.default is not inspect.Parameter.empty and param.default is not None:
        return PYTYPE.get(type(param.default))
    return None


def fn_abi(name: str, fn: Any) -> Optional[dict]:
    """One function -> one ABI entry, or None if it can't be introspected."""
    if not callable(fn):
        return None
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return None
    inputs: List[dict] = []
    variadic = False
    for pname, p in sig.parameters.items():
        if pname in ('self', 'cls'):
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            variadic = True
            continue
        entry: Dict[str, Any] = {'name': pname, 'type': _json_type(p)}
        if p.default is inspect.Parameter.empty:
            entry['required'] = True
        else:
            entry['default'] = p.default if _is_json(p.default) else repr(p.default)
        inputs.append(entry)
    types = ','.join(str(i['type'] or 'any') for i in inputs)
    return {
        'name': name,
        'selector': hashlib.sha256(f'{name}({types})'.encode()).hexdigest()[:8],
        'mutability': mutability(name),
        'inputs': inputs,
        'variadic': variadic,
        'docs': (inspect.getdoc(fn) or '')[:500],
        'cost': getattr(fn, '__cost__', 0),
    }


def _is_json(v: Any) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def _address(functions: List[dict]) -> str:
    """Hash of the interface only: names, input names/types/required, mutability."""
    sig = [[f['name'], f['mutability'],
            [[i['name'], i.get('type'), bool(i.get('required'))] for i in f['inputs']]]
           for f in functions]
    return '0x' + hashlib.sha256(json.dumps(sig, sort_keys=True).encode()).hexdigest()[:40]


def _tool_schema(f: dict) -> dict:
    props, required = {}, []
    for i in f['inputs']:
        p: Dict[str, Any] = {}
        if i.get('type'):
            p['type'] = i['type']
        if 'default' in i:
            p['default'] = i['default']
        props[i['name']] = p
        if i.get('required'):
            required.append(i['name'])
    schema: Dict[str, Any] = {'type': 'object', 'properties': props,
                              'additionalProperties': bool(f['variadic'])}
    if required:
        schema['required'] = required
    return schema


def sanitize(mod_name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_]', '_', mod_name)


def compile_contract(mod_name: str, obj: Any, include: Optional[List[str]] = None,
                     exclude: Optional[List[str]] = None, readonly: bool = False) -> dict:
    """Compile a Mod instance (or class) into {contract, address, functions, tools}."""
    names = [n for n in sorted(set(dir(obj)))
             if not n.startswith('_') and n not in DENY]
    if include:
        names = [n for n in names if n in set(include)]
    names = [n for n in names if n not in set(exclude or [])]

    functions = []
    for n in names:
        entry = fn_abi(n, getattr(obj, n))
        if entry is not None:
            functions.append(entry)
    if readonly:
        functions = [f for f in functions if f['mutability'] == 'view']

    prefix = sanitize(mod_name)
    tools = []
    for f in functions:
        desc = f['docs'].split('\n')[0] if f['docs'] else f"{mod_name}.{f['name']}"
        tools.append({
            'name': f"{prefix}_{f['name']}",
            'fn': f['name'],
            'description': f"[{f['mutability']}] {desc}",
            'inputSchema': _tool_schema(f),
            'annotations': {'readOnlyHint': f['mutability'] == 'view',
                            'destructiveHint': f['mutability'] == 'write',
                            'openWorldHint': True},
        })
    return {
        'contract': mod_name,
        'address': _address(functions),
        'readonly': bool(readonly),
        'functions': functions,
        'tools': tools,
    }
