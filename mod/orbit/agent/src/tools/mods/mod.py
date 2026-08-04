"""
mods - every module in the fleet, as a potential tool

The host runs hundreds of mod-protocol modules — a git tracker, a chain, a
store, a market indexer. Each one is already a described object with named
functions, which is exactly the shape of a tool. This registry wraps the fleet
so the agent can call any of them: `mod.git` with fn='status', `mod.store`
with fn='get', and so on.

Tool name is the module name behind a `mod.` prefix, so a fleet module can
never shadow a built-in (`git` the tool vs `mod.git` the module) and the
console can tell at a glance where a call is going.

Two things keep this from being a foot-gun:
  - they are *potential* tools — the fleet is off the default loadout, so an
    agent only gets these when someone snaps them on (see tools/mod.py)
  - a sandboxed run (any non-host portal) can't call them at all, the same
    rule custom shell tools live under (see Mod.run_plan)

Usage:
    mods = ModTools()
    mods.ls()                                  # ['mod.bt', 'mod.chain', …]
    mods.run('mod.git', fn='status')
    mods.schema(['mod.git'])                   # LLM view of one module
"""
import ast
import json
import time
from typing import Any, Dict, List, Optional

try:
    import mod as m
except ImportError:
    m = None

PREFIX = 'mod.'
# fleet scan is ~300 config reads — cheap enough to redo, too slow to redo per call
INDEX_TTL = 300
MAX_DESC = 220
MAX_FNS = 40
MAX_OUTPUT = 20_000
# calling ourselves through the bridge would start a second agent loop inside
# the one already running — the `task` tool is the supported way to delegate
SKIP = ('agent',)


def _jsonable(value: Any) -> Any:
    """Coerce a module's return value into something the loop can print."""
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)[:MAX_OUTPUT]


def _as_dict(value: Any) -> Dict[str, Any]:
    """Whatever the model sent for `params`, as a dict.

    Every module takes its arguments as one object, and models routinely hand
    that object back as a *string* of JSON (or of a Python dict) instead of the
    real thing. Parse it here rather than passing a str where kwargs go.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        for parse in (json.loads, ast.literal_eval):
            try:
                parsed = parse(text)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}
    return {}


def _fn_name(fn: Any, default: str = 'forward') -> str:
    """Normalise the function name: 'mod.git/status', 'git/status' -> 'status'."""
    fn = str(fn or '').strip().strip('/')
    if not fn:
        return default
    return fn.rsplit('/', 1)[-1] or default


def _clip(text: str, limit: int = MAX_DESC) -> str:
    """First sentence-ish of a module description — enough for the model to pick."""
    text = ' '.join((text or '').split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = cut.rfind('. ')
    return (cut[:stop + 1] if stop > limit // 2 else cut.rstrip() + '…')


class ModTools:
    """The fleet as a tool registry — one tool per installed module."""
    description = "Fleet registry - every mod-protocol module the host runs, callable as a tool"

    def __init__(self, ttl: int = INDEX_TTL, **kwargs):
        self._ttl = ttl
        self._index: Dict[str, Dict] = {}
        self._read = 0.0

    # ── the fleet index ──────────────────────────────────────────────

    def index(self, fresh: bool = False) -> Dict[str, Dict]:
        """name -> {description, fns}. Cached: a scan is ~300 config reads."""
        if m is None:
            return {}
        if self._index and not fresh and (time.time() - self._read) < self._ttl:
            return self._index
        index = {}
        for name in sorted(m.mods()):
            # `_outer` and friends are scaffolding directories, not modules
            if name in SKIP or name.startswith(('_', '.')):
                continue
            try:
                cfg = m.config(name) or {}
            except Exception:
                cfg = {}
            fns = [f for f in (cfg.get('fns') or []) if isinstance(f, str)]
            index[name] = {
                'description': _clip(cfg.get('description') or f"the {name} module"),
                'fns': fns[:MAX_FNS],
                'version': cfg.get('version'),
            }
        self._index, self._read = index, time.time()
        return index

    @staticmethod
    def tool_name(mod_name: str) -> str:
        return PREFIX + mod_name

    @staticmethod
    def mod_name(tool_name: str) -> str:
        return tool_name[len(PREFIX):] if tool_name.startswith(PREFIX) else tool_name

    def ls(self, fresh: bool = False) -> List[str]:
        """Every fleet module as a tool name."""
        return [self.tool_name(n) for n in self.index(fresh)]

    def exists(self, name: str) -> bool:
        return name.startswith(PREFIX) and self.mod_name(name) in self.index()

    def get(self, name: str) -> Dict[str, Any]:
        """One entry: what the module is and which functions it offers."""
        mod_name = self.mod_name(name)
        entry = self.index().get(mod_name)
        if entry is None:
            raise KeyError(f"module not found: {mod_name}")
        return {'name': self.tool_name(mod_name), 'mod': mod_name,
                'description': entry['description'], 'fns': entry['fns'],
                'version': entry.get('version'), 'kind': 'mod'}

    def items(self, q: str = '', limit: int = None) -> List[Dict[str, Any]]:
        """Console listing, optionally narrowed by a search term."""
        q = (q or '').strip().lower()
        out = []
        for mod_name, entry in self.index().items():
            if q and q not in mod_name and q not in entry['description'].lower():
                continue
            out.append(self.get(mod_name))
            if limit and len(out) >= limit:
                break
        return out

    # ── the LLM view ─────────────────────────────────────────────────

    def schema(self, names: List[str] = None) -> Dict[str, Dict]:
        """Tool schemas, in the shape the built-ins produce.

        Every module takes the same two params — which function to call and
        the arguments for it — so the model learns one calling convention for
        the whole fleet. The functions a module declares ride along as a hint.
        """
        names = self.ls() if names is None else [n for n in names if self.exists(n)]
        out = {}
        for name in names:
            entry = self.get(name)
            fns = entry['fns']
            hint = ('one of: ' + ', '.join(fns[:MAX_FNS])) if fns else \
                   'a function on the module; forward() is the default entry point'
            out[name] = {
                'description': f"{entry['mod']} module — {entry['description']}",
                'params': {
                    'fn': {'type': 'string', 'required': False, 'default': 'forward',
                           'hint': hint},
                    'params': {'type': 'object', 'required': False,
                               'hint': 'keyword arguments for that function'},
                },
                'mod': entry['mod'],
                'kind': 'mod',
            }
        return out

    # ── calling ──────────────────────────────────────────────────────

    def run(self, name: str, fn: str = 'forward', params: Dict = None,
            **kwargs) -> Dict[str, Any]:
        """Call one function on one module.

        Params may arrive nested (`params={...}`, what the schema asks for) or
        flat (`path='x'`, what models do anyway) — both are accepted, and a
        params object handed back as a string of JSON is parsed, not passed on.
        """
        if m is None:
            return {'success': False, 'error': 'mod protocol not importable'}
        mod_name = self.mod_name(name)
        if mod_name not in self.index():
            return {'success': False, 'error': f"module not found: {mod_name}"}
        fn = _fn_name(fn)
        if fn.startswith('_'):
            return {'success': False, 'error': f"private function: {fn}"}
        args = {**_as_dict(params), **kwargs}
        try:
            result = m.call(f"{mod_name}/{fn}", args)
            return {'success': True, 'mod': mod_name, 'fn': fn,
                    'result': _jsonable(result)}
        except Exception as e:
            return {'success': False, 'mod': mod_name, 'fn': fn,
                    'error': f"{type(e).__name__}: {e}"[:2000]}

    def forward(self, name: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                                   -> the fleet index
        forward("mod.git")                          -> one module
        forward(action="run", name=…, fn=…, params=…) -> call it
        """
        if kwargs.get('action') == 'run':
            return self.run(kwargs.get('name', name or ''),
                            kwargs.get('fn', 'forward'),
                            _as_dict(kwargs.get('params')))
        if name is None:
            items = self.items(kwargs.get('q', ''), kwargs.get('limit'))
            return {'mods': items, 'total': len(items)}
        return self.get(name)

    def test(self) -> Dict[str, Any]:
        # a params object the model stringified still lands as kwargs
        assert _as_dict('{"path": "/tmp", "n": 2}') == {'path': '/tmp', 'n': 2}
        assert _as_dict("{'path': '/tmp'}") == {'path': '/tmp'}
        assert _as_dict('not json') == {} and _as_dict(None) == {}
        assert _fn_name('mod.git/status') == 'status'
        assert _fn_name('') == 'forward' and _fn_name(None) == 'forward'
        if m is None:
            return {'passed': True, 'skipped': 'no mod protocol'}
        names = self.ls()
        assert names, 'a fleet should have modules'
        assert all(n.startswith(PREFIX) for n in names)
        assert self.tool_name('git') not in SKIP
        one = self.get(names[0])
        assert one['kind'] == 'mod' and one['description']
        schema = self.schema([names[0]])
        assert set(schema[names[0]]['params']) == {'fn', 'params'}
        bad = self.run('mod.__nope__')
        assert bad['success'] is False
        return {'passed': True, 'mods': len(names)}
