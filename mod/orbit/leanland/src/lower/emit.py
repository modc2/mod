"""
Lowering: one IR walk, three languages.

The walk is shared and the differences live in three small Target classes plus
the `py`/`rs`/`ts` columns of ir.PRIMS. That is the whole reason a def can be
trusted in three places at once — nobody hand-writes the Rust version of a
formula, so nobody can hand-write it differently.

Rust is the only target that needs type information during emission (i64 does
not silently become f64), so the walker threads the *wanted* type through and
lets each target decide whether to coerce. Python and TypeScript ignore it.
"""
from __future__ import annotations

from .. import ir
from ..ir import Def, PRIMS, promote, type_of

# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------


class Target:
    key = ''
    prim_key = ''          # which column of ir.PRIMS to read (defaults to key)
    ext = ''
    types: dict[str, str] = {}
    indent = '    '
    doc_style = '//'

    def ty(self, t: str) -> str:
        return self.types[t]

    def lit(self, v, t: str) -> str:
        raise NotImplementedError

    def coerce(self, src: str, got: str, want: str) -> str:
        return src

    def vec(self, items: list[str], t: str) -> str:
        return '[' + ', '.join(items) + ']'

    def cond(self, c, a, b, t) -> str:
        raise NotImplementedError

    def sigma(self, i, lo, hi, body, t) -> str:
        raise NotImplementedError

    def fn(self, d: Def, lines: list[str], ret: str) -> str:
        raise NotImplementedError


class Py(Target):
    key, ext, doc_style = 'py', 'py', '"""'
    types = {'Real': 'float', 'Int': 'int', 'Nat': 'int', 'Bool': 'bool',
             'Vec Real': 'list[float]', 'Vec Int': 'list[int]'}

    def lit(self, v, t):
        return 'True' if v is True else 'False' if v is False else repr(v)

    def cond(self, c, a, b, t):
        return f'({a} if {c} else {b})'

    def sigma(self, i, lo, hi, body, t):
        return f'sum({body} for {i} in range({lo}, ({hi}) + 1))'

    def fn(self, d, lines, ret):
        ps = ', '.join(f'{n}: {self.ty(t)}' for n, t in d.params)
        out = [f'def {d.name}({ps}) -> {self.ty(d.ret)}:']
        out += [self.indent + l for l in _docstring(d, self.doc_style)]
        out += [self.indent + l for l in lines]
        out.append(f'{self.indent}return {ret}')
        return '\n'.join(out)

    def let(self, n, t, s):
        return f'{n}: {self.ty(t)} = {s}'


class Rs(Target):
    key, ext, doc_style = 'rs', 'rs', '///'
    types = {'Real': 'f64', 'Int': 'i64', 'Nat': 'i64', 'Bool': 'bool',
             'Vec Real': '&[f64]', 'Vec Int': '&[i64]'}

    def lit(self, v, t):
        if t == 'Bool':
            return 'true' if v else 'false'
        if t == 'Real':
            return f'{float(v)!r}_f64'
        return f'{int(v)}_i64'

    _INT_LIT = __import__('re').compile(r'^-?\d+_i64$')

    def coerce(self, src, got, want):
        if want == 'Real' and got in ('Nat', 'Int'):
            if self._INT_LIT.match(src):                # 2_i64 -> 2.0_f64, not a cast
                return f'{float(src[:-4])!r}_f64'
            return f'({src} as f64)'
        return src

    def vec(self, items, t):
        return '&[' + ', '.join(items) + ']'

    def cond(self, c, a, b, t):
        return f'(if {c} {{ {a} }} else {{ {b} }})'

    def sigma(self, i, lo, hi, body, t):
        acc = 'f64' if t == 'Real' else 'i64'
        return f'(({lo})..=({hi})).map(|{i}| {body}).sum::<{acc}>()'

    def fn(self, d, lines, ret):
        ps = ', '.join(f'{n}: {self.ty(t)}' for n, t in d.params)
        out = list(_docstring(d, self.doc_style))
        out.append(f'pub fn {d.name}({ps}) -> {self.ty(d.ret)} {{')
        out += [self.indent + l for l in lines]
        out.append(f'{self.indent}{ret}')
        out.append('}')
        return '\n'.join(out)

    def let(self, n, t, s):
        return f'let {n}: {self.ty(t)} = {s};'


class Ts(Target):
    key, ext, doc_style = 'ts', 'ts', '//'
    types = {'Real': 'number', 'Int': 'number', 'Nat': 'number', 'Bool': 'boolean',
             'Vec Real': 'number[]', 'Vec Int': 'number[]'}
    indent = '  '

    def lit(self, v, t):
        return 'true' if v is True else 'false' if v is False else repr(float(v) if t == 'Real' else v)

    def cond(self, c, a, b, t):
        return f'({c} ? {a} : {b})'

    def sigma(self, i, lo, hi, body, t):
        return (f'(() => {{ let _s = 0; for (let {i} = {lo}; {i} <= {hi}; {i}++) '
                f'{{ _s += {body}; }} return _s; }})()')

    def fn(self, d, lines, ret):
        ps = ', '.join(f'{n}: {self.ty(t)}' for n, t in d.params)
        out = list(_docstring(d, self.doc_style))
        out.append(f'export function {d.name}({ps}): {self.ty(d.ret)} {{')
        out += [self.indent + l for l in lines]
        out.append(f'{self.indent}return {ret};')
        out.append('}')
        return '\n'.join(out)

    def let(self, n, t, s):
        return f'const {n}: {self.ty(t)} = {s};'


class Js(Ts):
    """The TypeScript emitter with the annotations taken off.

    Not a fourth language: it reads the same 'ts' column of ir.PRIMS and shares
    the same expression walk, so running the JS is running the TS. It exists
    because the parity harness has to *execute* the web target, and a box with
    node but no transpiler cannot execute TypeScript."""
    key, ext, prim_key = 'js', 'js', 'ts'

    def fn(self, d, lines, ret):
        ps = ', '.join(n for n, _ in d.params)
        out = list(_docstring(d, self.doc_style))
        out.append(f'export function {d.name}({ps}) {{')
        out += [self.indent + l for l in lines]
        out.append(f'{self.indent}return {ret};')
        out.append('}')
        return '\n'.join(out)

    def let(self, n, t, s):
        return f'const {n} = {s};'


TARGETS = {t.key: t() for t in (Py, Rs, Ts, Js)}


def _docstring(d: Def, style: str) -> list[str]:
    """Doc + provenance. Provenance is not decoration: the generated file has to
    say which paper and which equation it came from, or the artifact outlives
    the reason anyone believed it."""
    body = (d.doc or d.name).splitlines() or [d.name]
    if d.source.get('key'):
        where = ' '.join(f'{k} {v}' for k, v in d.source.items()
                         if k not in ('key', 'convention'))
        body += ['', f'source: {d.source["key"]}{" " + where if where else ""}']
    elif d.source.get('convention'):
        body += ['', 'a convention, not a result: nothing to cite']
    body += ['', 'generated by leanland from lib/ — do not edit']
    body = [l.strip() for l in body]
    if style == '"""':
        return ['"""' + body[0]] + body[1:] + ['"""']
    return [f'{style} {l}'.rstrip() for l in body]


# ---------------------------------------------------------------------------
# the walk
# ---------------------------------------------------------------------------

def expr(e: dict, env: dict, defs: dict, tgt: Target, want: str | None = None) -> str:
    got = type_of(e, env, defs)
    s = _expr(e, env, defs, tgt)
    return tgt.coerce(s, got, want) if want and want != got else s


def _expr(e: dict, env: dict, defs: dict, tgt: Target) -> str:
    k = e['k']
    if k == 'lit':
        return tgt.lit(e['v'], e['t'])
    if k == 'var':
        return e['n'] if e['n'] in env else f"{e['n']}()"   # bare name = a constant def
    if k == 'vec':
        t = type_of(e, env, defs)
        el = ir.elem(t)
        return tgt.vec([expr(a, env, defs, tgt, el) for a in e['a']], t)
    if k == 'if':
        t = type_of(e, env, defs)
        return tgt.cond(expr(e['c'], env, defs, tgt),
                        expr(e['t'], env, defs, tgt, t),
                        expr(e['e'], env, defs, tgt, t), t)
    if k == 'sum':
        t = type_of(e, env, defs)
        inner = dict(env, **{e['i']: 'Int'})
        return tgt.sigma(e['i'],
                         expr(e['lo'], env, defs, tgt, 'Int'),
                         expr(e['hi'], env, defs, tgt, 'Int'),
                         expr(e['b'], inner, defs, tgt, t), t)
    if k == 'app':
        op, args = e['op'], e['a']
        ats = [type_of(a, env, defs) for a in args]
        if op in PRIMS:
            wants = _prim_wants(op, ats)
            parts = [expr(a, env, defs, tgt, w) for a, w in zip(args, wants)]
            return PRIMS[op][tgt.prim_key or tgt.key].format(*parts)
        d = defs[op]
        parts = [expr(a, env, defs, tgt, t) for a, (_, t) in zip(args, d.params)]
        return f'{op}({", ".join(parts)})'
    raise TypeError(f"cannot lower node '{k}'")


def _prim_wants(op: str, ats: list[str]) -> list[str | None]:
    """What each argument of a primitive should be coerced to before emission.

    Only Rust acts on this, but it is computed once here so the two languages
    that do not care cannot drift from the one that does."""
    sig = PRIMS[op]['sig']
    if sig in ('arith',):
        return [promote(*ats)] * len(ats)
    if sig in ('real2', 'real1'):
        return ['Real'] * len(ats)
    if sig == 'same1':
        return [promote(*ats)]
    if sig == 'cmp':
        return [promote(*ats)] * 2
    if sig in ('logic', 'logic1'):
        return [None] * len(ats)
    if sig == 'idx':
        return [None, 'Int']
    if sig == 'len':
        return [None]
    return list(sig[0])


def function(d: Def, defs: dict, tgt: Target) -> str:
    """Lower one Def into one function in the target language."""
    env = {n: t for n, t in d.params}
    lines = []
    for n, e in d.lets:
        t = type_of(e, env, defs)
        lines.append(tgt.let(n, t, expr(e, env, defs, tgt)))
        env[n] = t
    return tgt.fn(d, lines, expr(d.body, env, defs, tgt, d.ret))
