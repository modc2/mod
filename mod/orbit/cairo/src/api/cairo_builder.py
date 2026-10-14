"""cairo_builder.py — the builder agent: make and edit contracts over the agent contract.

    POST /cairo/run/stream                 {agent: "builder", query, ...}
    POST /cairo/agents/builder/run[/stream] {query, ...}     (same thing)

Body (all optional but query):
    query     what to make, or what to change ("a lottery where ...",
              "add a pause switch")
    name      the contract. Exists -> EDIT it. Missing -> MAKE it.
              Default: context.contract when the query reads like an edit,
              else a name parsed from the query ("called foo", "make a foo").
    template  starter for a new contract (else guessed from the query,
              falling back to counter)
    fork      copy this existing contract instead of a starter
    new       true = always make a new contract, never edit context.contract
    vibe      false = only scaffold/fork (+ test), no agent edit
    model     passed to orbit/claudecode

The edit itself is mod.vibe(): orbit/claudecode (:51260) rewrites the Scarb
project, then build + snforge test. It runs as a normal 'vibe' job in the
server's job table (so the console's TEST tab, /cairo/api/jobs and MCP
cairo_jobs all see it and it survives the SSE client going away); this
module streams that job's steps out as agent-protocol step events and ends
with {type: done, answer, actions: [{open: name}], contract, job}.

Owner-only: it writes files on this box and bills the host Claude account.
"""
import re
import time

AGENT = {
    'id': 'builder',
    'name': 'cairo builder',
    'description': 'Makes and edits Cairo contracts. Name a new one (from a starter or a fork of '
                   'another) or point it at an existing one and say what to change; orbit/claudecode '
                   'writes the Cairo + tests, then it builds and runs the snforge tests. '
                   'Owner-only: writes files and bills the host Claude account.',
    'writes': True,
    'access': 'owner',
    'run': '/cairo/agents/builder/run',
    'stream': '/cairo/agents/builder/run/stream',
    'params': {'query': 'what to make or change (required)',
               'name': 'contract to make or edit (default: context.contract / parsed from query)',
               'template': 'starter for a new contract (see /cairo/api/templates)',
               'fork': 'existing contract to copy for a new one',
               'new': 'true = make a new contract even if one is open',
               'vibe': 'false = just scaffold/fork and test',
               'model': 'model for orbit/claudecode'},
    'examples': ['make a tip jar called tips where anyone can send and the owner withdraws',
                 'add a pause switch the owner can flip',
                 'fork usd as eurc and make it a euro coin'],
}

STOP = {'a', 'an', 'the', 'my', 'new', 'own', 'simple', 'basic', 'little', 'smart', 'contract',
        'cairo', 'starknet', 'me', 'us', 'please', 'that', 'which', 'where', 'with', 'for', 'to',
        'of', 'and', 'or', 'can', 'lets', 'let', 'so', 'it', 'one', 'some', 'called', 'named'}
MAKE = r'\b(make|create|build|write|scaffold|start|new|spin up|code up|launch)\b'


def _slug(words):
    s = '_'.join(w for w in words if w)
    return re.sub(r'[^a-z0-9_]', '', s.lower())[:32].strip('_')


def _name_from(q):
    """A contract name the person gave, or one made from what they asked for."""
    m = re.search(r'\b(?:called|named|name it|as)\s+[`"\']?([A-Za-z][\w-]{1,40})', q)
    if m and m.group(1).lower() not in STOP:
        return _slug([m.group(1).replace('-', '_')])
    m = re.search(MAKE + r'\s+(?:(?:me|us|a|an|the|my|new|another|own|simple|basic|little|smart|cairo|starknet)\s+)*'
                  r'([a-z][a-z0-9_-]*)(?:\s+([a-z][a-z0-9_-]*))?', q.lower())
    if m:
        ws = [w for w in (m.group(2), m.group(3)) if w and w not in STOP]
        if ws and ws[-1] in ('contract', 'app'):
            ws = ws[:-1]
        if ws:
            return _slug(ws[:2])
    return None


def _guess_template(q, templates):
    ql = q.lower()
    rules = [('vault_stable', r'\b(dai|collateral|vault|borrow|cdp|liquidat)'),
             ('stablecoin', r'\b(stable|usdc|dollar|usd|euro|backed)'),
             ('merkle_allowlist', r'\b(merkle|allowlist|whitelist|airdrop)'),
             ('commit_reveal', r'\b(commit|reveal|sealed|secret|hidden|vote|voting|lottery|auction)'),
             ('token', r'\b(token|erc20|coin|points|balance|transfer|tip|pay)')]
    known = {t['id'] for t in templates}
    for tid in known:
        if re.search(rf'\b{re.escape(tid)}\b', ql):
            return tid
    for tid, rx in rules:
        if tid in known and re.search(rx, ql):
            return tid
    return 'counter'


def _unique(mod, pkg, names):
    if pkg not in names:
        return pkg
    i = 2
    while f'{pkg}_{i}' in names:
        i += 1
    return f'{pkg}_{i}'


def _test_line(t):
    tests = (t or {}).get('tests') or []
    if not tests:
        if (t or {}).get('error'):
            return f'tests did not run: {t["error"][:200]}'
        return 'tests: ' + ('passed' if (t or {}).get('ok') else 'failed')
    ok = sum(1 for x in tests if x.get('status') == 'pass')
    s = f'tests **{ok}/{len(tests)}** passed'
    bad = [x for x in tests if x.get('status') != 'pass'][:4]
    if bad:
        s += ':\n\n' + '\n'.join(f'- `{x.get("name", "").split("::")[-1]}`: {x.get("reason") or "failed"}'
                                 for x in bad)
    return s


def run(r, body, ctx, start_job, get_job):
    """r = cairo_helper.Run (tool() = gated MCP call recorded as a step).
    Returns (answer, actions, extra) for the done event."""
    q = str(body.get('query') or body.get('prompt') or '').strip()
    names = [c['name'] for c in r.tool('cairo_contracts').get('contracts', [])]
    templates = r.mod.templates()['templates']
    fork = body.get('fork')
    ql = q.lower()
    m = re.search(r'\bfork\s+([a-z]\w*)\b', ql)
    if m and m.group(1) in names and not fork:
        fork = m.group(1)
    # "make a lottery" / "called x" / a fork = a new contract; "make it count
    # by two" / "add a pause switch" = an edit of the open one
    is_make = bool(body.get('new') or fork or re.search(r'\b(called|named)\b', ql)
                   or re.search(MAKE + r'\s+(?:me\s+)?(?:a|an|my|another|new)\b', ql))
    want = body.get('name')
    if not want and not is_make:
        want = ctx.get('contract')
    if not want:
        want = _name_from(q)
    if not want:
        return ('Tell me what to call it, e.g. "make a tip jar **called tips**", or open a contract '
                'first and ask for a change.', [], {})
    try:
        pkg = r.mod._subdir(want)[0]
    except ValueError as e:
        return f'"{want}" can\'t be a contract name: {e}', [], {}

    made = None
    if pkg in names and not body.get('new'):
        mode = 'edit'
    else:
        mode = 'make'
        if pkg in names:
            pkg = _unique(r.mod, pkg, names)
        if fork:
            res = r.tool('cairo_fork', src=fork, name=pkg,
                         desc=body.get('desc') or q[:120] or None)
            made = f'a copy of **{fork}**'
        else:
            tid = body.get('template') or _guess_template(q, templates)
            res = r.tool('cairo_new', name=pkg, template=tid,
                         desc=body.get('desc') or q[:120] or None)
            made = f'the **{tid}** starter'
        if res.get('error'):
            return f'Couldn\'t make **{pkg}**: {res["error"]}', [], {'contract': pkg}

    if body.get('vibe') is False or not q:
        t = r.tool('cairo_test', name=pkg)
        what = f'Made **{pkg}** from {made}' if made else f'**{pkg}** is unchanged'
        return (f'{what}; ' + _test_line(t) + '.',
                [{'label': f'Open {pkg}', 'open': pkg}], {'contract': pkg, 'mode': mode})

    prompt = q
    if mode == 'make':
        prompt = (f'This contract ({pkg}) was just scaffolded from {made.replace("**", "")}. '
                  f'Turn it into what the person asked for, rename things to fit, keep the tests '
                  f'meaningful (cover the new rules, not the old starter), and update app.json '
                  f'labels/help if there is one.\n\nThe ask: {q}')
    kw = {'name': pkg, 'prompt': prompt}
    if body.get('model'):
        kw['model'] = body['model']
    no = r.gate('vibe', kw)
    if no:
        return no, [], {'contract': pkg}
    job = start_job('vibe', kw)
    r.step('job', {'fn': 'vibe', 'name': pkg}, {'job': job['id'], 'mode': mode})
    seen, j = 0, job
    while True:
        time.sleep(1.5)
        j = get_job(job['id'])
        for st in (j.get('steps') or [])[seen:]:
            r.step(st.get('tool') or 'step', {'what': st.get('what')},
                   {'error': True} if st.get('error') else None)
        seen = len(j.get('steps') or [])
        if j.get('state') not in ('queued', 'running'):
            break
    res = j.get('result') or {}
    acts = [{'label': f'Open {pkg}', 'open': pkg}]
    extra = {'contract': pkg, 'mode': mode, 'job': job['id']}
    if j.get('error') or res.get('error'):
        return f'The agent stopped on **{pkg}**: {(j.get("error") or res.get("error"))[:400]}', acts, extra
    head = (f'Made **{pkg}** from {made} and shaped it to your ask.' if mode == 'make'
            else f'Edited **{pkg}**.')
    if not res.get('ok'):
        return head + f' The agent reported a problem: {res.get("result", "")[-600:]}', acts, extra
    b = res.get('build') or {}
    out = [head]
    if b and not b.get('ok'):
        d = (b.get('diagnostics') or [])[:4]
        out.append('It does **not** compile yet:\n\n' + '\n'.join(
            f'- `{x.get("file")}:{x.get("line")}` {x.get("message")}' for x in d))
    elif 'test' in res:
        out.append(_test_line(res['test']).capitalize() + '.')
    summary = (res.get('result') or '').strip()
    if summary:
        out.append(summary if len(summary) <= 2500 else summary[:2500].rsplit('\n', 1)[0] + '\n\n…')
    return '\n\n'.join(out), acts, extra
