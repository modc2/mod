"""Score functions as programs — the rule that turns a miss into a payout.

Every pot in PreFi is split by `dollars × accuracy`, and *accuracy* is a
function of one number: the relative miss

    e = |called − actual| / actual          # 0.01 == 1% off

This module is where that function comes from. It used to be four hardcoded
Python curves; it is now a tiny expression language, so the rule is **data** —
a string like `max(0, 1 - e/tol)` plus a dict of parameters — which means:

* the defaults (`linear`, `l2`, `exponential`, `threshold`, and a few more)
  are written in the same language anyone else's function is;
* a pool owner can write their own and switch the pool to it;
* a function is one JSON object, so it travels: a share code you paste, or a
  CID in the fleet's store.

The language is deliberately small. An expression may use the variable `e`
(alias `err`), the function's own parameters by name, numbers, arithmetic
(`+ - * / ** %`), comparisons, `and`/`or`/`not`, `x if cond else y`, and the
functions in `FUNCS`. Nothing else: no attribute access, no subscripts, no
assignment, no imports, no names that are not declared parameters. It is
walked as an AST and evaluated by this file — never handed to `eval` — so a
function cannot touch anything but its inputs, and it always terminates.

Everything that comes out is clamped to [0, 1]; a division by zero, an
overflow or a NaN scores 0 rather than raising, because a bad function must
not be able to stall a settlement.

The snapshot a round (or a prediction) stores is `{name, expr, params}` —
the program itself, not a reference to it. Editing or deleting a saved
function later cannot re-price a bet that was placed under it.
"""

import ast
import base64
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

KIND = 'prefi.fn/1'
CODE_PREFIX = 'prefi.fn.'

MAX_EXPR_LEN = 400
MAX_NODES = 250
MAX_PARAMS = 12
MAX_DESCRIPTION = 280
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,31}$')
PARAM_RE = re.compile(r'^[a-z][a-z0-9_]{0,23}$')

ERROR_NAMES = ('e', 'err')

# The probe grid: where a function is sampled for the chart, the report and
# the validation pass. Dense near zero — that is where the money is decided.
GRID = [0.0, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1,
        0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 5.0]


class ExprError(ValueError):
    """The expression is not in the language."""


# ── Safe functions ───────────────────────────────────────────────────

def _exp(x):
    try:
        return math.exp(x)
    except OverflowError:
        return math.inf


def _log(x):
    return math.log(x) if x > 0 else -math.inf


def _sqrt(x):
    return math.sqrt(x) if x >= 0 else math.nan


def _pow(a, b):
    try:
        return math.pow(a, b)
    except (OverflowError, ValueError):
        return math.nan


def _clamp(x, lo, hi):
    return min(max(x, lo), hi)


def _where(cond, a, b):
    return a if cond else b


def _sign(x):
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


FUNCS: Dict[str, Callable] = {
    'abs': abs, 'min': min, 'max': max,
    'exp': _exp, 'log': _log, 'sqrt': _sqrt, 'pow': _pow,
    'tanh': math.tanh, 'floor': math.floor, 'ceil': math.ceil, 'round': round,
    'clamp': _clamp, 'where': _where, 'sign': _sign,
}

FUNC_DOCS = {
    'abs': 'abs(x)', 'min': 'min(a, b, …)', 'max': 'max(a, b, …)',
    'exp': 'exp(x)', 'log': 'log(x) — natural, -inf at 0', 'sqrt': 'sqrt(x)',
    'pow': 'pow(a, b)', 'tanh': 'tanh(x)', 'floor': 'floor(x)', 'ceil': 'ceil(x)',
    'round': 'round(x)', 'clamp': 'clamp(x, lo, hi)',
    'where': 'where(cond, a, b) — a if cond else b', 'sign': 'sign(x) — -1, 0 or 1',
}

RESERVED = set(FUNCS) | set(ERROR_NAMES) | {'True', 'False', 'None', 'and', 'or', 'not', 'if', 'else'}

_BIN = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b if b != 0 else (math.inf if a > 0 else -math.inf if a < 0 else math.nan),
    ast.Mod: lambda a, b: math.fmod(a, b) if b != 0 else math.nan,
    ast.Pow: _pow,
    ast.FloorDiv: lambda a, b: math.floor(a / b) if b != 0 else math.nan,
}
_CMP = {
    ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
}


# ── Compiler ─────────────────────────────────────────────────────────

def _check(node: ast.AST, names: set, count: List[int]):
    """Walk the tree once: refuse anything outside the language, and every
    name that is not a declared parameter. Done at compile time so a bad
    function is rejected when it is written, not when a pot settles."""
    count[0] += 1
    if count[0] > MAX_NODES:
        raise ExprError(f'expression is too big (> {MAX_NODES} nodes)')

    if isinstance(node, ast.Expression):
        _check(node.body, names, count)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or isinstance(node.value, (int, float)):
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise ExprError('constants must be finite')
        else:
            raise ExprError(f'only numbers are allowed as constants, not {node.value!r}')
    elif isinstance(node, ast.Name):
        if node.id in FUNCS:
            raise ExprError(f'`{node.id}` is a function — call it')
        if node.id not in names:
            raise ExprError(f'unknown name `{node.id}` — declare it as a parameter, '
                            f"or use `e` for the error")
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN:
            raise ExprError(f'operator {type(node.op).__name__} is not allowed')
        _check(node.left, names, count)
        _check(node.right, names, count)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            raise ExprError(f'operator {type(node.op).__name__} is not allowed')
        _check(node.operand, names, count)
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            _check(v, names, count)
    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _CMP:
                raise ExprError(f'comparison {type(op).__name__} is not allowed')
        _check(node.left, names, count)
        for c in node.comparators:
            _check(c, names, count)
    elif isinstance(node, ast.IfExp):
        _check(node.test, names, count)
        _check(node.body, names, count)
        _check(node.orelse, names, count)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
            raise ExprError('only these functions may be called: '
                            + ', '.join(sorted(FUNCS)))
        if node.keywords:
            raise ExprError('keyword arguments are not allowed')
        for a in node.args:
            _check(a, names, count)
    else:
        raise ExprError(f'{type(node).__name__} is not allowed in a score function')


def _num(v) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if not isinstance(v, (int, float)):
        raise ExprError(f'{type(v).__name__} is not a number')
    return float(v)


def _eval(node: ast.AST, env: Dict[str, float]):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        return _num(node.value)
    if isinstance(node, ast.Name):
        return _num(env[node.id])
    if isinstance(node, ast.BinOp):
        a, b = _num(_eval(node.left, env)), _num(_eval(node.right, env))
        try:
            return _BIN[type(node.op)](a, b)
        except (OverflowError, ValueError, ZeroDivisionError):
            return math.nan
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not v
        return -_num(v) if isinstance(node.op, ast.USub) else _num(v)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            out = True
            for v in node.values:
                out = _eval(v, env)
                if not out:
                    return out
            return out
        out = False
        for v in node.values:
            out = _eval(v, env)
            if out:
                return out
        return out
    if isinstance(node, ast.Compare):
        left = _num(_eval(node.left, env))
        for op, comp in zip(node.ops, node.comparators):
            right = _num(_eval(comp, env))
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    if isinstance(node, ast.Call):
        args = [_eval(a, env) for a in node.args]
        try:
            return FUNCS[node.func.id](*args)
        except (TypeError, ValueError, OverflowError, ZeroDivisionError):
            return math.nan
    raise ExprError(f'{type(node).__name__} is not allowed')


def compile_expr(expr: str, params: Dict[str, float]) -> Callable[[float], float]:
    """Parse and check an expression once; get back `f(e) → raw value`.

    The returned callable is the compiled program. It is not clamped — see
    `evaluate` for the score — so a caller can inspect what the function
    really does before it is squeezed into [0, 1].
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError('expression is empty')
    expr = expr.strip()
    if len(expr) > MAX_EXPR_LEN:
        raise ExprError(f'expression is longer than {MAX_EXPR_LEN} characters')
    if '\n' in expr or '\r' in expr:
        raise ExprError('expression must be a single line')
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError as exc:
        raise ExprError(f'syntax: {exc.msg} at column {exc.offset}') from None

    names = set(ERROR_NAMES) | set(params)
    _check(tree, names, [0])
    base = {k: float(v) for k, v in params.items()}

    def run(e: float, overrides: Dict[str, float] = None) -> float:
        env = dict(base)
        if overrides:
            env.update({k: float(v) for k, v in overrides.items()})
        env['e'] = env['err'] = float(e)
        try:
            return _num(_eval(tree, env))
        except (OverflowError, ValueError, ZeroDivisionError):
            return math.nan

    return run


# ── Specs ────────────────────────────────────────────────────────────

def _params_ok(params) -> Dict[str, float]:
    if params is None:
        return {}
    if isinstance(params, str):
        try:
            params = json.loads(params or '{}')
        except json.JSONDecodeError as exc:
            raise ExprError(f'params must be JSON: {exc.msg}') from None
    if not isinstance(params, dict):
        raise ExprError('params must be an object of name → number')
    if len(params) > MAX_PARAMS:
        raise ExprError(f'at most {MAX_PARAMS} parameters')
    out = {}
    for k, v in params.items():
        if not isinstance(k, str) or not PARAM_RE.match(k):
            raise ExprError(f'bad parameter name {k!r} — lowercase letters, digits, _')
        if k in RESERVED:
            raise ExprError(f'`{k}` is reserved')
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ExprError(f'parameter `{k}` must be a number') from None
        v = float(v)
        if not math.isfinite(v):
            raise ExprError(f'parameter `{k}` must be finite')
        out[k] = v
    return out


def validate_spec(spec: Dict, name_required: bool = True) -> Dict:
    """Normalise a function spec and refuse a bad one. Returns a clean copy
    with exactly the keys a snapshot carries, plus the descriptive ones."""
    if not isinstance(spec, dict):
        raise ExprError('a function is an object: {name, expr, params, description}')
    name = str(spec.get('name') or '').strip().lower()
    if name_required or name:
        if not NAME_RE.match(name):
            raise ExprError('name must be 2–32 chars: a lowercase letter, then '
                            'letters, digits or _')
        if name in RESERVED:
            raise ExprError(f'`{name}` is a reserved word')
    params = _params_ok(spec.get('params'))
    expr = str(spec.get('expr') or '').strip()
    run = compile_expr(expr, params)
    # Run it across the grid now: a program that only fails on some error
    # value would otherwise fail at settlement time.
    for e in GRID:
        run(e)
    description = str(spec.get('description') or '').strip()[:MAX_DESCRIPTION]
    out = {'name': name, 'expr': expr, 'params': params, 'description': description}
    for key in ('author', 'origin_cid', 'origin', 'created_at', 'updated_at',
                'builtin', 'owner'):
        if spec.get(key) is not None:
            out[key] = spec[key]
    return out


def snapshot(spec: Dict) -> Dict:
    """The part of a function that decides money — what a round stores."""
    return {'name': spec['name'], 'expr': spec['expr'],
            'params': {k: float(v) for k, v in (spec.get('params') or {}).items()}}


def digest(spec: Dict) -> str:
    """Short content hash — what a wallet signs when saving, and how two
    copies of a shared function are told apart."""
    canon = json.dumps({'name': spec.get('name', ''),
                        'description': spec.get('description', ''),
                        'expr': spec.get('expr', ''),
                        'params': spec.get('params') or {}},
                       sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


_compiled: Dict[str, Callable] = {}


def _runner(spec: Dict) -> Callable:
    key = json.dumps([spec['expr'], sorted((spec.get('params') or {}).items())])
    fn = _compiled.get(key)
    if fn is None:
        fn = compile_expr(spec['expr'], _params_ok(spec.get('params')))
        if len(_compiled) > 512:
            _compiled.clear()
        _compiled[key] = fn
    return fn


def raw(spec: Dict, e: float, overrides: Dict[str, float] = None) -> float:
    """The function's own output, unclamped (may be nan/inf)."""
    return _runner(spec)(e, overrides)


def evaluate(spec: Dict, e: float, overrides: Dict[str, float] = None) -> float:
    """The accuracy a miss of `e` earns under this function: raw output
    clamped to [0, 1], with any non-number scoring 0."""
    if e is None or not math.isfinite(e) or e < 0:
        return 0.0
    v = raw(spec, e, overrides)
    if v is None or not math.isfinite(v):
        return 0.0
    return min(1.0, max(0.0, float(v)))


def sample(spec: Dict, points: List[float] = None,
           overrides: Dict[str, float] = None) -> List[Dict]:
    """The curve, point by point — what the console draws."""
    return [{'e': e, 'score': round(evaluate(spec, e, overrides), 6)}
            for e in (points or GRID)]


def report(spec: Dict, overrides: Dict[str, float] = None) -> Dict:
    """Facts about a curve a person should see before they adopt it."""
    pts = sample(spec, GRID, overrides)
    scores = [p['score'] for p in pts]
    monotone = all(a >= b for a, b in zip(scores, scores[1:]))
    zero_from = next((p['e'] for p in pts if p['score'] == 0.0), None)
    raws = [raw(spec, e, overrides) for e in GRID]
    clipped = sum(1 for v in raws if v is not None and math.isfinite(v) and (v < 0 or v > 1))
    broken = sum(1 for v in raws if v is None or not math.isfinite(v))
    warnings = []
    if scores[0] < 1.0:
        warnings.append(f'a perfect call scores {scores[0]}, not 1 — the whole '
                        'pot still splits pro-rata, but nobody can hit full accuracy')
    if not monotone:
        warnings.append('not monotone — a worse miss can score higher than a better one')
    if zero_from is None:
        warnings.append('never reaches 0 — every stake keeps some share of the pot')
    if clipped:
        warnings.append(f'{clipped} of {len(GRID)} probe points fall outside 0..1 '
                        'and are clamped')
    if broken:
        warnings.append(f'{broken} of {len(GRID)} probe points are undefined '
                        '(divide by zero / overflow) and score 0')
    return {'at_zero': scores[0], 'monotone': monotone, 'zero_from': zero_from,
            'half_at': next((p['e'] for p in pts if p['score'] <= 0.5), None),
            'clipped': clipped, 'undefined': broken, 'warnings': warnings,
            'sample': pts}


# ── Defaults ─────────────────────────────────────────────────────────
# The four the pool has always had, plus four that show what the language
# can say. Every one is an ordinary spec — nothing here is special-cased.

BUILTINS: Dict[str, Dict] = {}


def _builtin(name: str, description: str, expr: str, **params):
    spec = validate_spec({'name': name, 'description': description,
                          'expr': expr, 'params': params})
    spec['builtin'] = True
    spec['author'] = 'prefi'
    BUILTINS[name] = spec


_builtin('linear',
         'Straight ramp to zero at `tol`. Miss by more than tol → 0. '
         'At tol = 1 this is exactly 1 − relative error.',
         'max(0, 1 - e/tol)', tol=1.0)
_builtin('l2',
         'Inverse-square decay — 1/(1+(e/tol)²). Never quite reaches zero, '
         'so a wild miss still scores something.',
         '1 / (1 + (e/tol)**2)', tol=1.0)
_builtin('exponential',
         'Exponential decay, 1/e at `tol`. Punishes the tail harder than l2.',
         'exp(-e/tol)', tol=1.0)
_builtin('threshold',
         'All or nothing — inside `tol` pays full, outside pays zero.',
         '1 if e <= tol else 0', tol=1.0)
_builtin('gaussian',
         'Bell curve, 1/e at `tol`. Flat shoulder: a near-miss costs almost '
         'nothing, then it falls away fast.',
         'exp(-(e/tol)**2)', tol=1.0)
_builtin('tiered',
         'A ladder: full inside `tol`, half inside 2×tol, a quarter inside '
         '4×tol, nothing beyond. Coarse on purpose — ties are common.',
         '1 if e <= tol else (0.5 if e <= 2*tol else (0.25 if e <= 4*tol else 0))',
         tol=1.0)
_builtin('cushion',
         'Linear ramp that never drops below `base` — every stake keeps a '
         'floor of the pot, nobody is zeroed for a bad week.',
         'max(base, 1 - e/tol)', tol=1.0, base=0.1)
_builtin('hinge',
         'Flat shoulder then a cliff: `power` sets how sharp the corner is '
         '(1 = linear, 2 = gentle, 8 = almost a threshold).',
         'max(0, 1 - (e/tol)**power)', tol=1.0, power=2.0)


def is_builtin(name: str) -> bool:
    return name in BUILTINS


# ── Library ──────────────────────────────────────────────────────────

class Library:
    """Saved functions, on disk beside the ledger. Built-ins are always
    present and cannot be overwritten; a saved name belongs to the address
    that saved it."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> Dict[str, Dict]:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: Dict[str, Dict]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    def saved(self) -> Dict[str, Dict]:
        return self._read()

    def get(self, name: str) -> Optional[Dict]:
        name = (name or '').strip().lower()
        if name in BUILTINS:
            return dict(BUILTINS[name])
        return self._read().get(name)

    def all(self) -> List[Dict]:
        out = [dict(spec) for spec in BUILTINS.values()]
        out += sorted(self._read().values(), key=lambda s: s.get('created_at', 0))
        return out

    def names(self) -> List[str]:
        return [s['name'] for s in self.all()]

    def save(self, spec: Dict, owner: str, origin: Dict = None) -> Dict:
        """Write a function under `owner`. Refuses to touch a built-in, or a
        name somebody else saved. `origin` records where an import came from."""
        clean = validate_spec(spec)
        name = clean['name']
        if name in BUILTINS:
            raise ExprError(f'`{name}` is a default — pick another name')
        owner = (owner or '').strip().lower()
        if not owner:
            raise ExprError('an owner address is required to save a function')
        with self._lock:
            data = self._read()
            existing = data.get(name)
            if existing and existing.get('owner') != owner:
                raise ExprError(f'`{name}` is already taken by {existing.get("owner")} '
                                '— save it under another name')
            now = time.time()
            record = {
                'name': name, 'expr': clean['expr'], 'params': clean['params'],
                'description': clean['description'],
                'author': clean.get('author') or owner,
                'owner': owner,
                'created_at': existing['created_at'] if existing else now,
                'updated_at': now,
                'digest': digest(clean),
            }
            if origin:
                record.update({k: v for k, v in origin.items() if v})
            elif existing:
                for key in ('origin_cid', 'origin'):
                    if existing.get(key):
                        record[key] = existing[key]
            data[name] = record
            self._write(data)
        return record

    def annotate(self, name: str, **fields) -> Dict:
        """Attach host facts (a store CID, say) to a saved function without
        touching the program itself."""
        name = (name or '').strip().lower()
        with self._lock:
            data = self._read()
            if name not in data:
                raise ExprError(f'no saved function named `{name}`')
            data[name].update({k: v for k, v in fields.items() if v is not None})
            self._write(data)
            return data[name]

    def delete(self, name: str, owner: str) -> Dict:
        name = (name or '').strip().lower()
        if name in BUILTINS:
            raise ExprError(f'`{name}` is a default and cannot be deleted')
        owner = (owner or '').strip().lower()
        with self._lock:
            data = self._read()
            existing = data.get(name)
            if not existing:
                raise ExprError(f'no function named `{name}`')
            if existing.get('owner') != owner:
                raise ExprError(f'`{name}` belongs to {existing.get("owner")}')
            del data[name]
            self._write(data)
        return existing


# ── Resolution ───────────────────────────────────────────────────────

def resolve(model, library: Library = None, tolerance: float = None,
            params: Dict = None, fallback: Dict = None) -> Dict:
    """Turn a model name (or a spec) plus the pool's knobs into a snapshot.

    `tolerance` is the pool's one first-class knob; it lands on the
    function's `tol` parameter when it has one. `params` overrides any other
    parameter by name — an unknown name is an error, not a silent no-op.
    `fallback` is a snapshot to use when the name is not in the library any
    more (a deleted function on an old round still has to settle).
    """
    if isinstance(model, dict):
        spec = validate_spec(model, name_required=False)
        if not spec['name']:
            spec['name'] = 'custom'
    else:
        name = str(model or '').strip().lower()
        spec = None
        if library is not None:
            spec = library.get(name)
        elif name in BUILTINS:
            spec = dict(BUILTINS[name])
        if spec is None and fallback and fallback.get('name') == name:
            spec = validate_spec(fallback, name_required=False)
        if spec is None:
            have = library.names() if library is not None else sorted(BUILTINS)
            raise ValueError(f"unknown model '{name}' — have {have}")

    merged = dict(spec.get('params') or {})
    if tolerance is not None and 'tol' in merged:
        tolerance = float(tolerance)
        if tolerance <= 0:
            raise ValueError('tolerance must be > 0')
        merged['tol'] = tolerance
    for k, v in _params_ok(params).items():
        if k not in merged:
            raise ValueError(f"`{spec['name']}` has no parameter `{k}` — "
                             f'it takes {sorted(merged) or "none"}')
        merged[k] = v
    out = {'name': spec['name'], 'expr': spec['expr'], 'params': merged}
    validate_spec(out, name_required=False)
    return out


def describe(library: Library = None) -> Dict[str, str]:
    """name → one line, for a picker."""
    specs = library.all() if library is not None else list(BUILTINS.values())
    return {s['name']: s.get('description', '') for s in specs}


# ── Sharing ──────────────────────────────────────────────────────────

def bundle(spec: Dict) -> Dict:
    """The portable form: everything needed to run and credit it, nothing
    tied to this host."""
    clean = validate_spec(spec)
    out = {'kind': KIND, 'name': clean['name'], 'description': clean['description'],
           'expr': clean['expr'], 'params': clean['params'],
           'author': spec.get('author') or spec.get('owner') or '',
           'digest': digest(clean)}
    if spec.get('origin_cid'):
        out['origin_cid'] = spec['origin_cid']
    return out


def to_code(spec: Dict) -> str:
    """A share code: paste it anywhere PreFi runs. It is the bundle, base64url."""
    raw_ = json.dumps(bundle(spec), sort_keys=True, separators=(',', ':')).encode()
    return CODE_PREFIX + base64.urlsafe_b64encode(raw_).decode().rstrip('=')


def is_code(text: str) -> bool:
    return isinstance(text, str) and text.strip().startswith(CODE_PREFIX)


def from_code(code: str) -> Dict:
    text = (code or '').strip()
    if not text.startswith(CODE_PREFIX):
        raise ExprError(f'a share code starts with `{CODE_PREFIX}`')
    body = text[len(CODE_PREFIX):]
    try:
        raw_ = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
        data = json.loads(raw_.decode())
    except Exception:
        raise ExprError('share code is corrupt') from None
    return from_bundle(data)


def from_bundle(data: Dict) -> Dict:
    if not isinstance(data, dict) or data.get('kind') != KIND:
        raise ExprError(f'not a PreFi function bundle (kind must be {KIND})')
    spec = validate_spec(data)
    if data.get('author'):
        spec['author'] = str(data['author']).lower()
    if data.get('origin_cid'):
        spec['origin_cid'] = data['origin_cid']
    want = data.get('digest')
    if want and want != digest(spec):
        raise ExprError('bundle digest does not match its contents')
    return spec


def language() -> Dict:
    """What can be written — the console's cheat sheet."""
    return {
        'variables': {'e': 'relative miss |called − actual| / actual (alias err)'},
        'operators': ['+', '-', '*', '/', '**', '%', '//', '<', '<=', '>', '>=',
                      '==', '!=', 'and', 'or', 'not', 'x if cond else y'],
        'functions': FUNC_DOCS,
        'limits': {'expr_chars': MAX_EXPR_LEN, 'nodes': MAX_NODES,
                   'params': MAX_PARAMS},
        'notes': [
            'output is clamped to 0..1; anything undefined scores 0',
            'the pool\'s `tolerance` sets the `tol` parameter when the function has one',
            'a round snapshots the whole function when it opens — later edits never re-price it',
        ],
    }
