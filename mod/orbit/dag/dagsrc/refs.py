"""refs — the ${...} that turns a list of calls into a graph.

A step never says what it depends on. It says what it *wants*:

    {"id": "token", "tool": "solana__sol_token", "args": {"mint": "${price.prices[0].mint}"}}

`price` is another step, so `token` runs after it and gets its output. The edge
was never declared; it was read off the argument. This is the whole reason the
module exists — a graph you have to wire by hand is a graph you will wire wrong.

Grammar, deliberately small:

    ${steps.<id>.out.<path>}   an upstream step's output
    ${<id>.<path>}             the same thing, when <id> is a step (the usual form)
    ${inputs.<name>}           a run parameter
    ${item} ${index}           the current element, inside a foreach
    ${env.NAME}                one environment variable
    ${run.id}                  this run

A path may index: `prices[0].usd`, `a.b[2][0]`. A trailing `?` makes a missing
value None instead of an error — `${steps.probe.out.balance?}`.

A string that is EXACTLY one reference resolves to the value itself, with its
type: `"${item}"` hands a dict to the next tool, not the word "dict". A
reference inside a longer string interpolates, and a non-scalar is JSON there,
because that is the only reading of "put this object in a sentence" that a
downstream tool can parse.
"""

import json
import os
import re

REF = re.compile(r'\$\{([^{}]+)\}')
WHOLE = re.compile(r'^\$\{([^{}]+)\}$')
ROOTS = ('steps', 'inputs', 'env', 'run', 'item', 'index')
_TOKEN = re.compile(r'([^.\[\]]+)|\[(-?\d+)\]')


class RefError(Exception):
    """A reference that cannot be resolved. Carries the expression, because
    the whole point of failing here is to say which one."""

    def __init__(self, expr, why):
        super().__init__(f'${{{expr}}} — {why}')
        self.expr, self.why = expr, why


def tokens(path):
    """'prices[0].usd' -> ['prices', 0, 'usd']"""
    out, pos = [], 0
    path = path.strip()
    while pos < len(path):
        if path[pos] == '.':
            pos += 1
            continue
        m = _TOKEN.match(path, pos)
        if not m:
            raise RefError(path, f'cannot read the path at character {pos}')
        out.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    return out


def dig(root, path, expr=None):
    """Walk a path into nested dicts/lists. Missing raises; that is the point."""
    cur = root
    for i, tok in enumerate(tokens(path)):
        so_far = path if i == 0 else '.'.join(str(t) for t in tokens(path)[:i])
        if isinstance(tok, int):
            if not isinstance(cur, (list, tuple)):
                raise RefError(expr or path, f'[{tok}] on a {type(cur).__name__}, '
                                             f'which is not a list')
            if tok >= len(cur) or tok < -len(cur):
                raise RefError(expr or path,
                               f'index {tok} but {so_far or "it"} has {len(cur)} items')
            cur = cur[tok]
        elif isinstance(cur, dict):
            if tok not in cur:
                have = ', '.join(list(cur)[:8]) or 'nothing'
                raise RefError(expr or path, f'no key {tok!r} — has {have}')
            cur = cur[tok]
        elif isinstance(cur, (list, tuple)):
            # `.field` over a list maps it: ${positions.mint} is every mint.
            cur = [dig(x, str(tok), expr) for x in cur]
        else:
            raise RefError(expr or path,
                           f'.{tok} on a {type(cur).__name__}, which has no fields')
    return cur


def lookup(expr, ctx, step_ids=()):
    """One expression -> one value."""
    raw = expr.strip()
    optional = raw.endswith('?')
    if optional:
        raw = raw[:-1].strip()
    if not raw:
        raise RefError(expr, 'empty reference')
    head = tokens(raw)[0]
    # A bare step id is the common case: ${price.prices[0].usd}
    if head not in ROOTS and head in step_ids:
        raw = f'steps.{raw}'
        parts = tokens(raw)
        # ${price} alone means that step's output, not its record.
        raw = 'steps.' + str(parts[1]) + '.out' + (
            '.' + '.'.join(_fmt(p) for p in parts[2:]) if len(parts) > 2 else '')
    elif head == 'steps':
        parts = tokens(raw)
        if len(parts) == 2:
            raw += '.out'
    try:
        return dig(ctx, raw, expr)
    except RefError:
        if optional:
            return None
        raise


def _fmt(tok):
    return f'[{tok}]' if isinstance(tok, int) else str(tok)


def refs(value):
    """Every expression inside a value, at any depth."""
    found = []
    if isinstance(value, str):
        found += REF.findall(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            found += refs(k) + refs(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            found += refs(v)
    return found


def depends_on(value, step_ids):
    """Which steps a value references. This is the edge set of the graph."""
    out = set()
    for expr in refs(value):
        raw = expr.strip().rstrip('?').strip()
        try:
            parts = tokens(raw)
        except RefError:
            continue
        if not parts:
            continue
        if parts[0] == 'steps' and len(parts) > 1:
            out.add(str(parts[1]))
        elif parts[0] in step_ids:
            out.add(str(parts[0]))
    return out


def resolve(value, ctx, step_ids=()):
    """Substitute every reference in a value, preserving type where it can."""
    if isinstance(value, str):
        whole = WHOLE.match(value.strip())
        if whole:
            return lookup(whole.group(1), ctx, step_ids)

        def sub(m):
            v = lookup(m.group(1), ctx, step_ids)
            if isinstance(v, str):
                return v
            if v is None:
                return ''
            if isinstance(v, (int, float, bool)):
                return json.dumps(v)
            return json.dumps(v, default=str)

        return REF.sub(sub, value)
    if isinstance(value, dict):
        return {resolve(k, ctx, step_ids): resolve(v, ctx, step_ids)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [resolve(v, ctx, step_ids) for v in value]
    return value


def env_view(allow=None):
    """Environment as a plain dict, with the obvious secrets held back.

    A graph is a document that gets saved, shared and printed in a run record.
    `${env.OPENAI_API_KEY}` in one is a leak with a UI, so the names that read
    like credentials are simply not visible here.
    """
    deny = ('SECRET', 'TOKEN', 'KEY', 'PASSWORD', 'PASSWD', 'SEED', 'MNEMONIC',
            'PRIVATE', 'CREDENTIAL', 'AUTH')
    out = {}
    for k, v in os.environ.items():
        if allow and k in allow:
            out[k] = v
        elif not any(d in k.upper() for d in deny):
            out[k] = v
    return out
