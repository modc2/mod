"""
The surface syntax: `.lean` text <-> IR.

The library is text on disk, not a database, because the source of truth has to
be reviewable, diffable and writable by hand. This is a Lean-*flavoured*
notation, not Lean itself — a deliberately small fragment (def, let, if, sum,
comparisons, application) that a mathematician reads without a manual and a
compiler lowers without an inference engine. If the real Lean toolchain is
installed, `lower.lean` emits genuine Lean 4 for it to check; nothing here
depends on that.

    /-- Optimal fraction of bankroll to stake. -/
    @[source kelly1956, eq 1]
    def kelly (p : Real) (b : Real) : Real :=
      (p * (b + 1) - 1) / b

    #example kelly 0.6 1.0 = 0.2
"""
from __future__ import annotations

from .ir import Def, PRIMS, TYPES, app, iff, lit, summ, var, vec

KEYWORDS = {'def', 'let', 'if', 'then', 'else', 'sum', 'in', 'tol', '#example',
            'true', 'false'}
ITEM_START = {'def', '#example', '@[', 'DOC'}

# name -> primitive, for infix and prefix operators
INFIX = {
    '∨': ('or', 10, 'L'), 'or': ('or', 10, 'L'),
    '∧': ('and', 20, 'L'), 'and': ('and', 20, 'L'),
    '=': ('eq', 30, 'N'), '==': ('eq', 30, 'N'), '≠': ('ne', 30, 'N'), '!=': ('ne', 30, 'N'),
    '<': ('lt', 30, 'N'), '≤': ('le', 30, 'N'), '<=': ('le', 30, 'N'),
    '>': ('gt', 30, 'N'), '≥': ('ge', 30, 'N'), '>=': ('ge', 30, 'N'),
    '≈': ('approx', 30, 'N'), '~=': ('approx', 30, 'N'),
    '+': ('add', 40, 'L'), '-': ('sub', 40, 'L'),
    '*': ('mul', 50, 'L'), '·': ('mul', 50, 'L'), '/': ('div', 50, 'L'), '%': ('mod', 50, 'L'),
    '^': ('pow', 60, 'R'),
}
RANGE_BP = 35
PREFIX = {'-': ('neg', 55), '¬': ('not', 25), 'not': ('not', 25)}
PUNCT = ['#example', '@[', ':=', '..', '==', '!=', '<=', '>=', '~=',
         '(', ')', '[', ']', ',', ':', '@', '#']


class LeanError(SyntaxError):
    pass


# ---------------------------------------------------------------------------
# tokenizer
# ---------------------------------------------------------------------------

class Tok:
    __slots__ = ('kind', 'val', 'line', 'ws')

    def __init__(self, kind, val, line, ws):
        self.kind, self.val, self.line, self.ws = kind, val, line, ws

    def __repr__(self):
        return f'{self.kind}:{self.val}'


def tokenize(src: str) -> list[Tok]:
    toks, i, line, ws = [], 0, 1, True
    n = len(src)
    while i < n:
        c = src[i]
        if c == '\n':
            line += 1
            i += 1
            ws = True
            continue
        if c in ' \t\r':
            i += 1
            ws = True
            continue
        if src.startswith('/--', i):                       # doc comment
            j = src.find('-/', i + 3)
            if j < 0:
                raise LeanError(f'line {line}: unterminated /-- doc comment')
            toks.append(Tok('DOC', src[i + 3:j].strip(), line, ws))
            line += src.count('\n', i, j)
            i = j + 2
            ws = True
            continue
        if src.startswith('/-', i):                        # block comment
            j = src.find('-/', i + 2)
            j = n if j < 0 else j + 2
            line += src.count('\n', i, j)
            i = j
            ws = True
            continue
        if src.startswith('--', i):                        # line comment
            i = src.find('\n', i)
            i = n if i < 0 else i
            continue
        if c.isdigit():
            j = i
            while j < n and (src[j].isdigit() or src[j] == '.' or
                             (src[j] in 'eE' and j + 1 < n and (src[j + 1].isdigit() or src[j + 1] in '+-')) or
                             (src[j] in '+-' and src[j - 1] in 'eE')):
                if src[j] == '.' and j + 1 < n and src[j + 1] == '.':
                    break                                  # `0..n` is a range, not a float
                j += 1
            toks.append(Tok('NUM', src[i:j], line, ws))
            i = j
            ws = False
            continue
        if c.isalpha() or c == '_':
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_'"):
                j += 1
            toks.append(Tok('ID', src[i:j], line, ws))
            i = j
            ws = False
            continue
        for p in PUNCT:
            if src.startswith(p, i):
                toks.append(Tok('OP', p, line, ws))
                i += len(p)
                ws = False
                break
        else:
            toks.append(Tok('OP', c, line, ws))
            i += 1
            ws = False
    toks.append(Tok('EOF', '', line, True))
    return toks


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, src: str, filename: str = '<lean>'):
        self.src = src
        self.file = filename
        self.toks = tokenize(src)
        self.p = 0
        # Indentation per line. Application is juxtaposition, so something has to
        # say where a call stops: an argument must sit on the head's own line, or
        # on a line indented further. Without that, `let n := len v` followed by
        # the body on the next line reads as `len v (body)`.
        self.indent = {i + 1: len(l) - len(l.lstrip())
                       for i, l in enumerate(src.splitlines())}

    # -- plumbing ----------------------------------------------------------
    @property
    def cur(self) -> Tok:
        return self.toks[self.p]

    def next(self) -> Tok:
        t = self.toks[self.p]
        self.p += 1
        return t

    def at(self, val) -> bool:
        return self.cur.val == val and self.cur.kind in ('OP', 'ID')

    def eat(self, val) -> bool:
        if self.at(val):
            self.p += 1
            return True
        return False

    def expect(self, val) -> Tok:
        if not self.at(val):
            raise LeanError(f'{self.file}:{self.cur.line}: expected {val!r}, got {self.cur.val!r}')
        return self.next()

    def fail(self, msg) -> None:
        raise LeanError(f'{self.file}:{self.cur.line}: {msg}')

    # -- items -------------------------------------------------------------
    def parse_file(self) -> list[Def]:
        defs: list[Def] = []
        doc, attrs = '', {}
        while self.cur.kind != 'EOF':
            t = self.cur
            if t.kind == 'DOC':
                doc = self.next().val
            elif t.val == '@[':
                attrs = self.parse_attrs()
            elif t.val == 'def':
                d = self.parse_def(doc, attrs)
                defs.append(d)
                doc, attrs = '', {}
            elif t.val == '#example':
                if not defs:
                    self.fail('#example before any def')
                self.parse_example(defs)
            else:
                self.fail(f'unexpected {t.val!r} at top level')
        return defs

    def parse_attrs(self) -> dict:
        self.expect('@[')
        out = {}
        while not self.at(']'):
            key = self.next().val
            val = None
            if not self.at(',') and not self.at(']'):
                val = self.next().val
            out[key] = val if val is not None else True
            if not self.eat(','):
                break
        self.expect(']')
        return out

    def parse_def(self, doc: str, attrs: dict) -> Def:
        start = self.p
        self.expect('def')
        name = self.next().val
        params = []
        while self.at('('):
            self.expect('(')
            pn = self.next().val
            self.expect(':')
            params.append((pn, self.parse_type()))
            self.expect(')')
        self.expect(':')
        ret = self.parse_type()
        self.expect(':=')
        lets = []
        while self.at('let'):
            self.expect('let')
            ln = self.next().val
            self.expect(':=')
            lets.append((ln, self.expr(0)))
        body = self.expr(0)
        src = self._slice(start)
        source = {}
        if 'source' in attrs and attrs['source'] is not True:
            source['key'] = attrs['source']
        if attrs.get('convention') is True:
            # deliberately uncited: a convention or a definition, not a result
            source['convention'] = True
        for k in ('eq', 'thm', 'section', 'page'):
            if k in attrs and attrs[k] is not True:
                source[k] = str(attrs[k])
        return Def(name, params, ret, body, doc=doc, source=source, lets=lets, lean=src)

    def parse_example(self, defs: list[Def]):
        self.expect('#example')
        e = self.expr(0)
        tol = 1e-9
        if self.eat('tol'):
            tol = float(self.next().val)
        if e['k'] != 'app' or e['op'] not in ('eq', 'approx'):
            self.fail('an #example must be `f a b = expected`')
        if e['op'] == 'approx':
            tol = max(tol, 1e-6)
        lhs, rhs = e['a']
        if lhs['k'] != 'app':
            self.fail('the left side of an #example must be a call')
        target = next((d for d in defs if d.name == lhs['op']), None)
        if target is None:
            self.fail(f"#example refers to '{lhs['op']}', which is not defined above it")
        target.examples.append({'args': lhs['a'], 'expect': rhs, 'tol': tol})

    def parse_type(self) -> str:
        t = self.next().val
        if t == 'Vec':
            t = 'Vec ' + self.next().val
        if t not in TYPES:
            raise LeanError(f'{self.file}:{self.cur.line}: unknown type {t!r} '
                            f'(known: {", ".join(TYPES)})')
        return t

    def _slice(self, start_tok: int) -> str:
        """Verbatim source text of the tokens from `start_tok` to the cursor."""
        lines = self.src.splitlines()
        a = self.toks[start_tok].line - 1
        b = self.toks[max(self.p - 1, start_tok)].line
        return '\n'.join(lines[a:b]).rstrip()

    # -- expressions (Pratt) -----------------------------------------------
    def expr(self, min_bp: int) -> dict:
        t = self.cur
        if t.val in PREFIX and t.kind == 'OP' or (t.kind == 'ID' and t.val in PREFIX):
            op, bp = PREFIX[self.next().val]
            left = app(op, self.expr(bp))
        elif t.val == 'if':
            self.expect('if')
            c = self.expr(0)
            self.expect('then')
            a = self.expr(0)
            self.expect('else')
            left = iff(c, a, self.expr(0))
        elif t.val in ('sum', '∑'):
            self.next()
            i = self.next().val
            if not self.eat('in'):
                self.fail('expected `in` after the sum index')
            lo = self.expr(RANGE_BP + 1)
            self.expect('..')
            hi = self.expr(RANGE_BP + 1)
            self.expect(',')
            left = summ(i, lo, hi, self.expr(0))
        else:
            left = self.application()

        while True:
            t = self.cur
            key = t.val
            if key not in INFIX or (t.kind == 'ID' and key not in ('and', 'or')):
                break
            op, bp, assoc = INFIX[key]
            if bp < min_bp:
                break
            self.next()
            right = self.expr(bp + (0 if assoc == 'R' else 1))
            left = app(op, left, right)
            if assoc == 'N':
                break
        return left

    def application(self) -> dict:
        head_tok = self.cur
        head = self.atom()
        args = []
        while self._starts_atom() and self._continues(head_tok):
            args.append(self.atom())
        if not args:
            return head
        if head['k'] != 'var':
            self.fail('only a named function can be applied')
        return app(head['n'], *args)

    def _continues(self, head: Tok) -> bool:
        """May the current token be an argument of an application headed by `head`?"""
        return (self.cur.line == head.line
                or self.indent.get(self.cur.line, 0) > self.indent.get(head.line, 0))

    def _starts_atom(self) -> bool:
        t = self.cur
        if t.kind == 'NUM':
            return True
        if t.kind == 'ID':
            return t.val not in KEYWORDS and t.val not in INFIX and t.val not in PREFIX
        if t.kind == 'OP':
            return t.val == '(' or (t.val == '[' and t.ws)
        return False

    def atom(self) -> dict:
        t = self.next()
        if t.kind == 'NUM':
            e = lit(float(t.val), 'Real') if ('.' in t.val or 'e' in t.val or 'E' in t.val) \
                else lit(int(t.val), 'Nat')
        elif t.val == 'true':
            e = lit(True, 'Bool')
        elif t.val == 'false':
            e = lit(False, 'Bool')
        elif t.val == '(':
            e = self.expr(0)
            self.expect(')')
        elif t.val == '[':
            items = []
            while not self.at(']'):
                items.append(self.expr(0))
                if not self.eat(','):
                    break
            self.expect(']')
            e = vec(items)
        elif t.kind == 'ID':
            e = var(t.val)
        else:
            raise LeanError(f'{self.file}:{t.line}: unexpected {t.val!r}')
        while self.at('[') and not self.cur.ws:            # postfix index: v[i]
            self.expect('[')
            e = app('idx', e, self.expr(0))
            self.expect(']')
        return e


def parse(src: str, filename: str = '<lean>') -> list[Def]:
    """Parse a .lean source file into Defs (untyped — run ir.check next)."""
    return Parser(src, filename).parse_file()


# ---------------------------------------------------------------------------
# printer  —  IR back to surface syntax (for defs the LLM wrote as IR)
# ---------------------------------------------------------------------------

_SYM = {v[0]: k for k, v in reversed(list(INFIX.items()))}


def show(e: dict, bp: int = 0) -> str:
    k = e['k']
    if k == 'lit':
        return 'true' if e['v'] is True else 'false' if e['v'] is False else repr(e['v'])
    if k == 'var':
        return e['n']
    if k == 'vec':
        return '[' + ', '.join(show(a) for a in e['a']) + ']'
    if k == 'if':
        s = f"if {show(e['c'])} then {show(e['t'])} else {show(e['e'])}"
        return f'({s})' if bp else s
    if k == 'sum':
        s = f"sum {e['i']} in {show(e['lo'], RANGE_BP + 1)}..{show(e['hi'], RANGE_BP + 1)}, {show(e['b'])}"
        return f'({s})' if bp else s
    op, args = e['op'], e['a']
    if op == 'idx':
        return f'{show(args[0], 100)}[{show(args[1])}]'
    if op == 'neg':
        return f'-{show(args[0], 55)}'
    if op == 'not':
        return f'¬{show(args[0], 25)}'
    if op in _SYM and len(args) == 2:
        sym = _SYM[op]
        _, obp, assoc = INFIX[sym]
        s = f'{show(args[0], obp + (1 if assoc == "R" else 0))} {sym} ' \
            f'{show(args[1], obp + (0 if assoc == "R" else 1))}'
        return f'({s})' if obp < bp else s
    inner = ' '.join(show(a, 100) for a in args)
    s = f'{op} {inner}' if args else op
    return f'({s})' if bp >= 90 and args else s


def render(d: Def) -> str:
    """Print a Def as .lean source. Round-trips through parse()."""
    out = []
    if d.doc:
        out.append(f'/-- {d.doc} -/')
    if d.source:
        bits = [f"source {d.source['key']}"] if d.source.get('key') else []
        bits += [f'{k} {v}' for k, v in d.source.items() if k != 'key']
        if bits:
            out.append('@[' + ', '.join(bits) + ']')
    ps = ''.join(f' ({n} : {t})' for n, t in d.params)
    out.append(f'def {d.name}{ps} : {d.ret} :=')
    for n, e in d.lets:
        out.append(f'  let {n} := {show(e)}')
    out.append(f'  {show(d.body)}')
    for ex in d.examples:
        args = ' '.join(show(a, 100) for a in ex['args'])
        rel = '=' if ex['tol'] <= 1e-9 else '≈'
        tol = '' if ex['tol'] <= 1e-9 else f" (tol {ex['tol']:g})"
        out.append(f"#example {d.name} {args} {rel} {show(ex['expect'])}{tol}")
    return '\n'.join(out) + '\n'
