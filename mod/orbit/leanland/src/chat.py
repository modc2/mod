"""
The reading half: talking about papers, and turning what a paper says into a
definition the library will accept.

Two entry points and they are deliberately different in kind.

`discuss` is a conversation. It is grounded in `lit/` — the notes you have
actually written about a paper — and in the signatures already in the library,
so the answer knows what you have and what you have decided about it. Nothing
it says is written anywhere.

`elaborate` is a compiler front-end that happens to be a language model. The
model proposes surface syntax; the parser, the typechecker and the paper's own
numbers decide whether it is a definition. When they reject it, the *error text
goes back to the model* and it tries again — up to `tries` times — and if it
never passes, nothing is written and you get the last error. That asymmetry is
the whole safety story of this module: an LLM is allowed to draft mathematics,
never to be the reason mathematics is trusted.
"""
from __future__ import annotations

import json
import re

from . import ir, lean, lower

FENCE = re.compile(r'```(?:lean)?\s*\n(.*?)```', re.S)

PROVIDERS = ('model.openrouter', 'dev', 'agent')
DEFAULT_MODEL = 'anthropic/claude-sonnet-4.5'
OPENROUTER = 'https://openrouter.ai/api/v1/chat/completions'
KEY_STORE = '~/.mod/model/openrouter/apikeys.json'


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

def ask(message: str, system: str = '', model: str = None, history=None,
        provider: str = None) -> str:
    """One completion, over whichever fleet module can reach a model.

    No key of its own: leanland borrows the box's provider the way every other
    module does, so there is one place to put a key and one place to pay.
    """
    import mod as m
    model = model or DEFAULT_MODEL
    tried = {}
    for name in ([provider] if provider else PROVIDERS):
        try:
            mod = m.mod(name)()
        except Exception as e:
            tried[name] = f'not available: {e}'
            continue
        try:
            if name == 'model.openrouter':
                reply = mod.forward(message, system_prompt=system, model=model,
                                    history=history or [], stream=False)
            elif name == 'dev':
                reply = mod.ask(f'{system}\n\n{message}' if system else message, model=model)
            else:
                reply = mod.forward(f'{system}\n\n{message}' if system else message,
                                    model=model)
            text = _text(reply)
            if text:
                return text
            # A fleet module that answers a prompt with its own info dict has not
            # answered. Treat it as a miss rather than handing a dict downstream.
            tried[name] = f'returned no text ({type(reply).__name__})'
        except Exception as e:
            tried[name] = str(e)[:300]
    try:
        return direct(message, system=system, model=model, history=history)
    except Exception as e:
        tried['openrouter (direct)'] = str(e)[:300]
    raise RuntimeError('no LLM provider answered: ' + json.dumps(tried, indent=2))


def _text(reply) -> str | None:
    """Pull the answer out of whatever a provider handed back, or None."""
    if isinstance(reply, str):
        return reply.strip() or None
    if isinstance(reply, dict):
        for k in ('answer', 'content', 'text', 'output', 'response', 'message'):
            v = reply.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def key() -> str:
    """The box's OpenRouter key. Borrowed from wherever the fleet already keeps
    it — leanland does not want a key of its own to lose track of."""
    import os
    k = os.environ.get('OPENROUTER_API_KEY')
    if k:
        return k
    path = os.path.expanduser(KEY_STORE)
    if os.path.exists(path):
        with open(path) as f:
            keys = json.load(f)
        keys = list(keys.values()) if isinstance(keys, dict) else list(keys)
        if keys:
            return keys[0] if isinstance(keys[0], str) else keys[0].get('key', '')
    raise RuntimeError(f'no OpenRouter key in $OPENROUTER_API_KEY or {KEY_STORE}')


def direct(message: str, system: str = '', model: str = None, history=None,
           timeout: int = 300) -> str:
    """Last resort: call OpenRouter ourselves.

    The fleet's own model wrapper is the first choice — it meters and bills. This
    exists because a broken wrapper somewhere else should not make the reading
    side of this module unusable.
    """
    import requests
    msgs = []
    if system:
        msgs.append({'role': 'system', 'content': system})
    for h in (history or []):
        role = h.get('role', 'user')
        msgs.append({'role': 'assistant' if role in ('assistant', 'them') else 'user',
                     'content': h.get('content', '')})
    msgs.append({'role': 'user', 'content': message})
    r = requests.post(OPENROUTER, timeout=timeout,
                      headers={'Authorization': f'Bearer {key()}',
                               'Content-Type': 'application/json'},
                      json={'model': model or DEFAULT_MODEL, 'messages': msgs})
    if r.status_code != 200:
        raise RuntimeError(f'openrouter {r.status_code}: {r.text[:300]}')
    return r.json()['choices'][0]['message']['content']


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

def language_reference() -> str:
    """The grammar, generated from the primitive table so it cannot go stale.

    A model writing for a language it has never seen needs the whole language in
    front of it — and this one is small enough that it fits."""
    prims = ', '.join(sorted(ir.PRIMS))
    return f"""\
LEANLAND DEFINITION LANGUAGE (a small Lean-flavoured fragment — NOT full Lean)

  /-- one-line description -/
  @[source <citation-key>, eq <equation number>]
  def <name> (<arg> : <Type>) ... : <Type> :=
    let <name> := <expr>          -- optional, only at the head of the body
    <expr>
  #example <name> <literal args> = <value>
  #example <name> <literal args> ≈ <value> tol 1e-6

Types: {', '.join(ir.TYPES)}. Nat < Int < Real widen automatically.
  A vector literal [1, 2, 3] is always `Vec Real`.

Expressions:
  arithmetic  + - * / ^ %      comparison  = ≠ < ≤ > ≥      logic  ∧ ∨ ¬
  if <cond> then <a> else <b>
  sum i in <lo>..<hi>, <body>            -- inclusive on both ends
  v[i]                                   -- index; NO space before the bracket
  f x y                                  -- application is juxtaposition
  a constant is a def with no parameters: `def pi : Real := 3.14159...`

Primitives (call them like functions, e.g. `sqrt x`, `max a b`):
  {prims}

Rules that are enforced, not suggestions:
  * No recursion, no loops other than `sum`, no state, no I/O. Pure expressions.
  * `let` is allowed only at the head of a body, never inside `sum` or a branch.
    Need an intermediate inside a loop? Give it its own `def` — naming it is the
    point of a library.
  * `div` and `pow` always produce Real. There is no integer division.
  * Every def should carry `@[source <key>, eq <n>]` pointing at a lit/ entry.
    A definition nobody can trace back to a paper is flagged.
  * Mark a def `@[convention]` instead when it is a definition or a convention
    rather than a result from a paper.
  * Every def should carry at least one `#example` whose expected value is a
    number the SOURCE states — not a number you computed. If the paper gives no
    number, say so rather than inventing one.
  * Special functions (erf, gamma, ...) are NOT primitives. Write the cited
    approximation as an ordinary def, as lib/special.lean does for norm_cdf.
"""


def library_context(defs: dict, papers: dict, full: bool = False) -> str:
    lines = ['LIBRARY (already defined — call these by name rather than restating them):']
    for d in lower.order(defs):
        src = d.source.get('key', 'convention' if d.source.get('convention') else '—')
        lines.append(f'  {d.signature()}    [{src}]  {d.doc.splitlines()[0] if d.doc else ""}')
    if not defs:
        lines.append('  (empty)')
    lines.append('')
    lines.append('LITERATURE (lit/<key>.md — cite by key):')
    for k, p in papers.items():
        lines.append(f'  {k}: {p.cite()}' + (f"  tags: {', '.join(p.get('tags', []))}"
                                             if p.get('tags') else ''))
    if not papers:
        lines.append('  (empty)')
    if full:
        for k, p in papers.items():
            lines.append(f'\n--- lit/{k}.md ---\n{p.get("notes", "")}')
    return '\n'.join(lines)


DISCUSS_SYSTEM = """\
You are the reading partner for a small, deliberately lean computational library.

You are talking to someone who reads papers in order to *compute* with them.
Be concrete about what a result actually says, what it assumes, and what has to
be true for it to be worth implementing. When a paper's assumptions are false in
practice, say which and what it costs — that judgement is the thing being
collected here, not the formula.

You have their literature notes and the definitions already in the library.
Prefer their own notes over your recollection of the paper, and say plainly when
you are recalling rather than reading. If something they want is already in the
library, point at the definition instead of restating the formula.

When the conversation reaches something worth keeping, say so and name it: which
definition, over which parameters, citing which equation. Do not write the
definition unless asked — `elaborate` does that, and it has a compiler behind it.

Be brief. No preamble, no summary of what you are about to say.
"""

ELABORATE_SYSTEM = """\
You turn a result from the literature into ONE definition (or a short chain of
definitions) in the leanland definition language.

Reply with a single fenced ```lean block and nothing else — no explanation
before or after. The block must contain the doc comment, the @[source ...]
attribute, the def, and its #example lines.

Your output is parsed, typechecked and run before it is filed. If any of those
fail you will be shown the exact error and asked again, so prefer the smallest
definition that is clearly right over a general one that might not typecheck.
"""


# ---------------------------------------------------------------------------
# discuss
# ---------------------------------------------------------------------------

def discuss(library, message: str, paper: str = None, history=None,
            model: str = None, full: bool = False) -> dict:
    defs, _ = library.load()
    papers = library.lit.all()
    ctx = library_context(defs, papers, full=full)
    if paper:
        p = library.lit.get(paper)
        ctx += f'\n\nTHE PAPER IN FRONT OF US — lit/{paper}.md\n{p.cite()}\n\n{p["notes"]}'
    answer = ask(message, system=DISCUSS_SYSTEM + '\n\n' + ctx, model=model,
                 history=history)
    return {'answer': answer, 'paper': paper, 'defs': len(defs), 'papers': len(papers)}


# ---------------------------------------------------------------------------
# elaborate:  paper -> proposal -> compiler -> library
# ---------------------------------------------------------------------------

def extract(text: str) -> str:
    """Pull the .lean block out of a model reply."""
    blocks = FENCE.findall(text or '')
    if blocks:
        return blocks[0].strip()
    return (text or '').strip()


def elaborate(library, want: str, paper: str = None, tries: int = 3,
              model: str = None, file: str = None, write: bool = True) -> dict:
    """Ask for a definition, and keep asking until the compiler accepts it.

    Returns the accepted source and what was written, or every attempt and the
    error that killed it. Nothing reaches lib/ unless it parsed, typechecked and
    reproduced whatever numbers its own #examples claim.
    """
    defs, _ = library.load()
    papers = library.lit.all()
    system = ELABORATE_SYSTEM + '\n\n' + language_reference() + '\n\n' \
        + library_context(defs, papers)
    prompt = f'Define: {want}'
    if paper:
        p = library.lit.get(paper)
        prompt += (f'\n\nFrom lit/{paper}.md — cite it as @[source {paper}, eq <n>].\n'
                   f'{p.cite()}\n\n{p["notes"]}')
    else:
        prompt += ('\n\nNo paper was named. Cite one of the keys above if the result '
                   'is theirs, or mark the def @[convention] if it is a definition '
                   'rather than a result.')

    attempts = []
    for n in range(1, max(1, int(tries)) + 1):
        reply = ask(prompt, system=system, model=model)
        source = extract(reply)
        result = _dry_run(library, source)
        attempts.append({'try': n, 'source': source, **result})
        if result['ok']:
            written = library.add(source, file=file or (paper or 'user') + '.lean') \
                if write else {'ok': True, 'written': False}
            return {'ok': written.get('ok', False), 'source': source, 'tries': n,
                    'defs': result['defs'], 'written': written, 'attempts': attempts}
        prompt = (f'That was rejected.\n\n```lean\n{source}\n```\n\n'
                  f'{result["stage"]} error: {result["error"]}\n\n'
                  f'Fix it and reply with the corrected ```lean block only.')
    return {'ok': False, 'tries': len(attempts), 'attempts': attempts,
            'error': attempts[-1]['error'] if attempts else 'no reply'}


def _dry_run(library, source: str) -> dict:
    """Parse + typecheck + run the examples, writing nothing."""
    defs, _ = library.load()
    try:
        new = lean.parse(source, '<proposal>')
    except SyntaxError as e:
        return {'ok': False, 'stage': 'parse', 'error': str(e), 'defs': []}
    if not new:
        return {'ok': False, 'stage': 'parse', 'error': 'no `def` in the reply', 'defs': []}
    env = dict(defs)
    for d in new:
        try:
            ir.check(d, env)
        except TypeError as e:
            return {'ok': False, 'stage': 'typecheck', 'error': str(e), 'defs': [d.name]}
        env[d.name] = d
    for d in new:
        if not d.examples:
            return {'ok': False, 'stage': 'examples', 'defs': [d.name],
                    'error': f"'{d.name}' states no #example — add one whose expected "
                             f'value the source itself gives'}
        r = library.verify_def(d, env)
        if not r['ok']:
            bad = [x for x in r['results'] if not x['ok']]
            return {'ok': False, 'stage': 'examples', 'defs': [d.name],
                    'error': f"'{d.name}' does not reproduce its own #example: "
                             + json.dumps(bad[:2])}
    return {'ok': True, 'stage': 'accepted', 'defs': [d.name for d in new], 'error': None}


# ---------------------------------------------------------------------------
# reading a paper into notes
# ---------------------------------------------------------------------------

READ_SYSTEM = """\
You are writing the reading note for a paper in a computational library.

Structure it exactly as:

## What it says
## What I actually want from it
## Caveats worth keeping

Be specific about equation numbers, and about which results are worth
implementing versus which are only there to justify them. In "Caveats", name the
assumptions that are false in practice and what breaks when they are. Terse.
Markdown, no preamble.
"""


def read(library, key: str, about: str = '', model: str = None) -> dict:
    """Draft (or extend) the reading note for a paper. Written to lit/<key>.md."""
    try:
        p = library.lit.get(key)
        head = f'{p.cite()}\n\nExisting notes:\n{p["notes"]}'
    except FileNotFoundError:
        return {'ok': False, 'error': f"no lit/{key}.md — add the paper first "
                                      f"(lit_add or lit_arxiv)"}
    note = ask(f'{head}\n\n{about or "Write the reading note."}',
               system=READ_SYSTEM, model=model)
    library.lit.note(key, note)
    return {'ok': True, 'key': key, 'note': note}
