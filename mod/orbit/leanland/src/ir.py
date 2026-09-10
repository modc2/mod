"""
The lean core: types, expressions, the primitive table, a typechecker and a
reference interpreter.

This is the whole language. Everything else in the module is either a way of
getting *into* it (the .lean parser, the LLM elaborator) or a way of getting
*out* of it (the Python / Rust / TypeScript / notebook backends). Keeping the
language small is the point: a Def is a name, typed parameters, a pure
expression, provenance, and worked examples — nothing else, because everything
else is what makes four backends disagree.

Design notes
------------
* Types are strings ('Real', 'Nat', 'Vec Real', ...) so a Def round-trips
  through JSON with no schema layer.
* Numeric promotion is Nat < Int < Real. `div` and `pow` always land in Real,
  because integer division silently disagreeing between Python and Rust is
  exactly the class of bug this module exists to prevent.
* Every primitive carries its three emission templates on the same row as its
  type signature. Adding a primitive is one line and all backends gain it at
  once; there is no way to add it to Python and forget Rust.
* `let` binds only at the head of a body. That restriction is what lets every
  backend emit a flat prelude of statements instead of growing an expression
  compiler. Need a local inside a loop? Name it — give it its own Def.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------

SCALARS = ('Real', 'Int', 'Nat', 'Bool')
NUMS = ('Nat', 'Int', 'Real')            # in promotion order
VECS = ('Vec Real', 'Vec Int')
TYPES = SCALARS + VECS


def is_num(t: str) -> bool:
    return t in NUMS


def elem(t: str) -> str:
    """Element type of a vector type."""
    if not t.startswith('Vec '):
        raise TypeError(f'expected a vector, got {t}')
    return t[4:]


def promote(*ts: str) -> str:
    """Numeric join: the narrowest type that holds all of them."""
    for t in ts:
        if not is_num(t):
            raise TypeError(f'{t} is not numeric')
    return NUMS[max(NUMS.index(t) for t in ts)]


def fits(got: str, want: str) -> bool:
    """Is `got` acceptable where `want` is expected (widening only)?"""
    if got == want:
        return True
    if is_num(got) and is_num(want):
        return NUMS.index(got) <= NUMS.index(want)
    if got.startswith('Vec ') and want.startswith('Vec '):
        return fits(elem(got), elem(want))
    return False


# ---------------------------------------------------------------------------
# the primitive table  —  one row, three languages
# ---------------------------------------------------------------------------
# sig kinds:
#   'arith'  (a, a) -> a          promoted
#   'real2'  (num, num) -> Real
#   'real1'  (num) -> Real
#   'same1'  (a) -> a             promoted
#   'cmp'    (num, num) -> Bool
#   'logic'  (Bool, ...) -> Bool
#   explicit tuple: (argtypes, ret)

PRIMS: dict[str, dict] = {
    # arithmetic
    'add':  dict(sig='arith', py='({0} + {1})',   rs='({0} + {1})',        ts='({0} + {1})'),
    'sub':  dict(sig='arith', py='({0} - {1})',   rs='({0} - {1})',        ts='({0} - {1})'),
    'mul':  dict(sig='arith', py='({0} * {1})',   rs='({0} * {1})',        ts='({0} * {1})'),
    'div':  dict(sig='real2', py='({0} / {1})',   rs='({0} / {1})',        ts='({0} / {1})'),
    'pow':  dict(sig='real2', py='({0} ** {1})',  rs='({0}).powf({1})',    ts='Math.pow({0}, {1})'),
    'neg':  dict(sig='same1', py='(-{0})',        rs='(-{0})',             ts='(-{0})'),
    'mod':  dict(sig=(('Int', 'Int'), 'Int'),
                 py='({0} % {1})', rs='({0}).rem_euclid({1})', ts='(({0} % {1} + {1}) % {1})'),

    # transcendental / rounding
    'exp':   dict(sig='real1', py='math.exp({0})',   rs='({0}).exp()',   ts='Math.exp({0})'),
    'log':   dict(sig='real1', py='math.log({0})',   rs='({0}).ln()',    ts='Math.log({0})'),
    'sqrt':  dict(sig='real1', py='math.sqrt({0})',  rs='({0}).sqrt()',  ts='Math.sqrt({0})'),
    'sin':   dict(sig='real1', py='math.sin({0})',   rs='({0}).sin()',   ts='Math.sin({0})'),
    'cos':   dict(sig='real1', py='math.cos({0})',   rs='({0}).cos()',   ts='Math.cos({0})'),
    'tanh':  dict(sig='real1', py='math.tanh({0})',  rs='({0}).tanh()',  ts='Math.tanh({0})'),
    'floor': dict(sig=(('Real',), 'Int'), py='math.floor({0})', rs='({0}).floor() as i64', ts='Math.floor({0})'),
    'ceil':  dict(sig=(('Real',), 'Int'), py='math.ceil({0})',  rs='({0}).ceil() as i64',  ts='Math.ceil({0})'),
    'abs':   dict(sig='same1', py='abs({0})', rs='({0}).abs()', ts='Math.abs({0})'),
    'min':   dict(sig='arith', py='min({0}, {1})', rs='({0}).min({1})', ts='Math.min({0}, {1})'),
    'max':   dict(sig='arith', py='max({0}, {1})', rs='({0}).max({1})', ts='Math.max({0}, {1})'),

    # comparison / logic
    'lt':  dict(sig='cmp', py='({0} < {1})',  rs='({0} < {1})',  ts='({0} < {1})'),
    'le':  dict(sig='cmp', py='({0} <= {1})', rs='({0} <= {1})', ts='({0} <= {1})'),
    'gt':  dict(sig='cmp', py='({0} > {1})',  rs='({0} > {1})',  ts='({0} > {1})'),
    'ge':  dict(sig='cmp', py='({0} >= {1})', rs='({0} >= {1})', ts='({0} >= {1})'),
    'eq':  dict(sig='cmp', py='({0} == {1})', rs='({0} == {1})', ts='({0} === {1})'),
    'ne':  dict(sig='cmp', py='({0} != {1})', rs='({0} != {1})', ts='({0} !== {1})'),
    'and': dict(sig='logic', py='({0} and {1})', rs='({0} && {1})', ts='({0} && {1})'),
    'or':  dict(sig='logic', py='({0} or {1})',  rs='({0} || {1})', ts='({0} || {1})'),
    'not': dict(sig='logic1', py='(not {0})', rs='(!{0})', ts='(!{0})'),

    # vectors
    'idx':  dict(sig='idx',  py='{0}[int({1})]', rs='{0}[({1}) as usize]', ts='{0}[{1}]'),
    'len':  dict(sig='len',  py='len({0})', rs='({0}).len() as i64', ts='{0}.length'),
    'vsum': dict(sig=(('Vec Real',), 'Real'),
                 py='sum({0})', rs='({0}).iter().sum::<f64>()',
                 ts='{0}.reduce((a, b) => a + b, 0)'),
    'mean': dict(sig=(('Vec Real',), 'Real'),
                 py='(sum({0}) / len({0}))',
                 rs='(({0}).iter().sum::<f64>() / ({0}).len() as f64)',
                 ts='({0}.reduce((a, b) => a + b, 0) / {0}.length)'),
    'dot':  dict(sig=(('Vec Real', 'Vec Real'), 'Real'),
                 py='sum(_a * _b for _a, _b in zip({0}, {1}))',
                 rs='({0}).iter().zip(({1}).iter()).map(|(a, b)| a * b).sum::<f64>()',
                 ts='{0}.reduce((s, a, i) => s + a * {1}[i], 0)'),
    'norm': dict(sig=(('Vec Real',), 'Real'),
                 py='math.sqrt(sum(_a * _a for _a in {0}))',
                 rs='({0}).iter().map(|a| a * a).sum::<f64>().sqrt()',
                 ts='Math.sqrt({0}.reduce((s, a) => s + a * a, 0))'),
}

# Deliberately absent: erf, gamma, and friends. Binding a primitive to
# math.erf / f64::erf / a JS polyfill means three different algorithms wearing
# one name, and parity would be measuring the tolerance rather than the code.
# Special functions belong in the library as ordinary Defs — see lib/special.lean,
# where the approximation used is visible, cited, and identical everywhere.


def prim_type(op: str, args: list[str]) -> str:
    """Type of `op` applied to arguments of types `args`. Raises TypeError."""
    p = PRIMS[op]
    sig = p['sig']
    if sig == 'arith':
        _arity(op, args, 2)
        return promote(*args)
    if sig == 'real2':
        _arity(op, args, 2)
        promote(*args)
        return 'Real'
    if sig == 'real1':
        _arity(op, args, 1)
        promote(*args)
        return 'Real'
    if sig == 'same1':
        _arity(op, args, 1)
        return promote(*args)
    if sig == 'cmp':
        _arity(op, args, 2)
        promote(*args)
        return 'Bool'
    if sig == 'logic':
        _arity(op, args, 2)
        if args != ['Bool', 'Bool']:
            raise TypeError(f'{op} wants two Bools, got {args}')
        return 'Bool'
    if sig == 'logic1':
        _arity(op, args, 1)
        if args != ['Bool']:
            raise TypeError(f'{op} wants a Bool, got {args}')
        return 'Bool'
    if sig == 'idx':
        _arity(op, args, 2)
        if not args[0].startswith('Vec '):
            raise TypeError(f'cannot index into {args[0]}')
        if not fits(args[1], 'Int'):
            raise TypeError(f'index must be Nat or Int, got {args[1]}')
        return elem(args[0])
    if sig == 'len':
        _arity(op, args, 1)
        if not args[0].startswith('Vec '):
            raise TypeError(f'len wants a vector, got {args[0]}')
        return 'Nat'
    want, ret = sig
    _arity(op, args, len(want))
    for got, w in zip(args, want):
        if not fits(got, w):
            raise TypeError(f'{op} wants {w}, got {got}')
    return ret


def _arity(op: str, args: list, n: int):
    if len(args) != n:
        raise TypeError(f'{op} takes {n} argument(s), got {len(args)}')


# ---------------------------------------------------------------------------
# expressions  (plain dicts — JSON is the wire format)
# ---------------------------------------------------------------------------

def lit(v, t: str) -> dict:   return {'k': 'lit', 'v': v, 't': t}
def var(n: str) -> dict:      return {'k': 'var', 'n': n}
def app(op: str, *a) -> dict: return {'k': 'app', 'op': op, 'a': list(a)}
def vec(items) -> dict:       return {'k': 'vec', 'a': list(items)}
def iff(c, t, e) -> dict:     return {'k': 'if', 'c': c, 't': t, 'e': e}
def summ(i, lo, hi, b) -> dict: return {'k': 'sum', 'i': i, 'lo': lo, 'hi': hi, 'b': b}


class Def:
    """One named computation: the unit the whole module trades in."""

    def __init__(self, name, params, ret, body, doc='', source=None,
                 examples=None, lets=None, lean=''):
        self.name = name
        self.params = list(params)            # [(name, type), ...]
        self.ret = ret
        self.body = body
        self.doc = doc
        self.source = source or {}            # {'key': 'kelly1956', 'eq': '1'}
        self.examples = list(examples or [])  # [{'args': [...], 'expect': v, 'tol': 1e-9}]
        self.lets = list(lets or [])          # [(name, expr), ...] head-position only
        self.lean = lean                      # verbatim surface syntax, for display

    @property
    def deps(self) -> list[str]:
        """Other defs this one references, in first-seen order — applications and
        bare constants alike, since `pi` is as much a dependency as `norm_pdf`."""
        out: list[str] = []
        bound = {n for n, _ in self.params}
        for name, e in [*self.lets, (None, self.body)]:
            for ref in _refs(e, bound):
                if ref not in PRIMS and ref not in out:
                    out.append(ref)
            if name:
                bound = bound | {name}
        return out

    def to_dict(self) -> dict:
        return dict(name=self.name, params=self.params, ret=self.ret, body=self.body,
                    doc=self.doc, source=self.source, examples=self.examples,
                    lets=self.lets, lean=self.lean, deps=self.deps)

    @classmethod
    def from_dict(cls, d: dict) -> 'Def':
        return cls(d['name'], [tuple(p) for p in d['params']], d['ret'], d['body'],
                   d.get('doc', ''), d.get('source'), d.get('examples'),
                   [tuple(l) for l in d.get('lets', [])], d.get('lean', ''))

    def signature(self) -> str:
        ps = ' '.join(f'({n} : {t})' for n, t in self.params)
        return f'{self.name} {ps} : {self.ret}'.replace('  ', ' ')

    def __repr__(self):
        return f'<Def {self.signature()}>'


def _refs(e: dict, bound: set):
    """Every name an expression reaches for that it did not bind itself."""
    if not isinstance(e, dict):
        return
    k = e['k']
    if k == 'var':
        if e['n'] not in bound:
            yield e['n']
    elif k == 'app':
        yield e['op']
        for a in e['a']:
            yield from _refs(a, bound)
    elif k == 'vec':
        for a in e['a']:
            yield from _refs(a, bound)
    elif k == 'if':
        for a in (e['c'], e['t'], e['e']):
            yield from _refs(a, bound)
    elif k == 'sum':
        for a in (e['lo'], e['hi']):
            yield from _refs(a, bound)
        yield from _refs(e['b'], bound | {e['i']})


# ---------------------------------------------------------------------------
# typechecking
# ---------------------------------------------------------------------------

def type_of(e: dict, env: dict[str, str], defs: dict[str, 'Def']) -> str:
    k = e['k']
    if k == 'lit':
        return e['t']
    if k == 'var':
        if e['n'] in env:
            return env[e['n']]
        d = defs.get(e['n'])
        if d is not None and not d.params:      # a constant: `def pi : Real := ...`
            return d.ret
        raise TypeError(f"unbound name '{e['n']}'")
    if k == 'vec':
        if not e['a']:
            raise TypeError('empty vector literal has no type')
        # Deliberately always Vec Real: a literal is written by a human reading a
        # paper, and `[1, 2, 3]` typing as Vec Nat only to be rejected by a
        # Vec Real parameter is a papercut with no upside. Declare a parameter
        # `Vec Int` when integer elements actually matter.
        for a in e['a']:
            t = type_of(a, env, defs)
            if not is_num(t):
                raise TypeError(f'vector elements must be numeric, got {t}')
        return 'Vec Real'
    if k == 'if':
        if type_of(e['c'], env, defs) != 'Bool':
            raise TypeError('if condition must be Bool')
        a, b = type_of(e['t'], env, defs), type_of(e['e'], env, defs)
        if a == b:
            return a
        if is_num(a) and is_num(b):
            return promote(a, b)
        raise TypeError(f'if branches disagree: {a} vs {b}')
    if k == 'sum':
        for bound in ('lo', 'hi'):
            t = type_of(e[bound], env, defs)
            if not fits(t, 'Int'):
                raise TypeError(f'sum {bound} bound must be Nat or Int, got {t}')
        inner = dict(env, **{e['i']: 'Int'})
        t = type_of(e['b'], inner, defs)
        if not is_num(t):
            raise TypeError(f'sum body must be numeric, got {t}')
        return 'Real' if t == 'Real' else 'Int'
    if k == 'app':
        args = [type_of(a, env, defs) for a in e['a']]
        op = e['op']
        if op in PRIMS:
            return prim_type(op, args)
        d = defs.get(op)
        if d is None:
            raise TypeError(f"unknown function '{op}' — not a primitive and not in the library")
        if len(args) != len(d.params):
            raise TypeError(f'{op} takes {len(d.params)} argument(s), got {len(args)}')
        for got, (pn, want) in zip(args, d.params):
            if not fits(got, want):
                raise TypeError(f'{op}: parameter {pn} wants {want}, got {got}')
        return d.ret
    raise TypeError(f"unknown expression node '{k}'")


def check(d: 'Def', defs: dict[str, 'Def']) -> dict[str, str]:
    """Typecheck a Def against the library. Returns the local type environment.

    Raises TypeError with a message aimed at whoever wrote the .lean file —
    the LLM elaborator hands this text straight back to the model, so it has
    to name the offending thing rather than just fail.
    """
    for _, t in d.params:
        if t not in TYPES:
            raise TypeError(f"{d.name}: unknown type '{t}'")
    if d.ret not in TYPES:
        raise TypeError(f"{d.name}: unknown return type '{d.ret}'")
    env = {n: t for n, t in d.params}
    if len(env) != len(d.params):
        raise TypeError(f'{d.name}: duplicate parameter name')
    for n, e in d.lets:
        env[n] = type_of(e, env, defs)
    got = type_of(d.body, env, defs)
    if not fits(got, d.ret):
        raise TypeError(f'{d.name}: body has type {got}, declared {d.ret}')
    if d.name in d.deps:
        raise TypeError(f'{d.name}: recursion is not allowed in the lean core')
    return env


# ---------------------------------------------------------------------------
# reference interpreter  —  this is what "the library says" means
# ---------------------------------------------------------------------------

_PY = {
    'add': lambda a, b: a + b, 'sub': lambda a, b: a - b, 'mul': lambda a, b: a * b,
    'div': lambda a, b: a / b, 'pow': lambda a, b: a ** b, 'neg': lambda a: -a,
    'mod': lambda a, b: a % b,
    'exp': math.exp, 'log': math.log, 'sqrt': math.sqrt, 'sin': math.sin,
    'cos': math.cos, 'tanh': math.tanh,
    'floor': lambda a: math.floor(a), 'ceil': lambda a: math.ceil(a), 'abs': abs,
    'min': min, 'max': max,
    'lt': lambda a, b: a < b, 'le': lambda a, b: a <= b, 'gt': lambda a, b: a > b,
    'ge': lambda a, b: a >= b, 'eq': lambda a, b: a == b, 'ne': lambda a, b: a != b,
    'and': lambda a, b: a and b, 'or': lambda a, b: a or b, 'not': lambda a: not a,
    'idx': lambda v, i: v[int(i)], 'len': lambda v: len(v),
    # naive summation, not fsum: the reference must do exactly what the three
    # backends do, or "parity" measures the interpreter instead of the code
    'vsum': lambda v: sum(v), 'mean': lambda v: sum(v) / len(v),
    'dot': lambda a, b: sum(x * y for x, y in zip(a, b)),
    'norm': lambda v: math.sqrt(sum(x * x for x in v)),
}


def evaluate(e: dict, env: dict, defs: dict[str, 'Def']):
    k = e['k']
    if k == 'lit':
        return e['v']
    if k == 'var':
        if e['n'] in env:
            return env[e['n']]
        return call(defs[e['n']], [], defs)             # a constant
    if k == 'vec':
        return [evaluate(a, env, defs) for a in e['a']]
    if k == 'if':
        return evaluate(e['t'] if evaluate(e['c'], env, defs) else e['e'], env, defs)
    if k == 'sum':
        lo, hi = int(evaluate(e['lo'], env, defs)), int(evaluate(e['hi'], env, defs))
        total = 0
        for i in range(lo, hi + 1):
            total += evaluate(e['b'], dict(env, **{e['i']: i}), defs)
        return total
    if k == 'app':
        args = [evaluate(a, env, defs) for a in e['a']]
        if e['op'] in _PY:
            return _PY[e['op']](*args)
        return call(defs[e['op']], args, defs)
    raise TypeError(f"unknown expression node '{k}'")


def call(d: 'Def', args: list, defs: dict[str, 'Def']):
    """Apply a Def to concrete arguments using the reference semantics."""
    if len(args) != len(d.params):
        raise TypeError(f'{d.name} takes {len(d.params)} argument(s), got {len(args)}')
    env = {n: v for (n, _), v in zip(d.params, args)}
    for n, e in d.lets:
        env[n] = evaluate(e, env, defs)
    return evaluate(d.body, env, defs)
