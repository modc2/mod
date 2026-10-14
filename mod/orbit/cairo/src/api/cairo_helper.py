"""cairo_helper.py — the cairo helper, on the fleet's agent contract.

    GET  /cairo/agents                {"agents": [...]}   (what orbit/build probes)
    GET  /cairo/agents/<id>           one agent
    GET  /cairo/.well-known/agent.json  the agent/1.0 card
    POST /cairo/run         {query, brain?, context?}   one run, JSON answer
    POST /cairo/run/stream  {query, brain?, context?}   SSE: step ... done | error

Events are orbit/agent's ({type: step, step: {tool, params, result}} then
{type: done, result: [steps], answer, actions}), so a run looks the same in
the console's corner orb, the build console, or orbit/agent.

`context` is what the console knows: {contract, view, lesson, account}. The
helper answers about THAT contract when the question doesn't name one.

TWO BRAINS
    helper  (default, open, free) no model. Routes the question to lessons,
            glossary words and the open contract, and reads every live fact
            through the cairo MCP tools (cairo_mcp.py) in-process — each read is a
            step named mcp:<tool>, so you see where every number came from.
    claude  (owner or loopback only) orbit/claudecode's POST /run/stream with
            the helper's findings as context and the cairo MCP server's URL
            in its system prompt; it calls the tools itself with curl and its
            steps stream straight through. It runs with CLI permissions off
            and bills the host Claude account, so it gets the write gate.
            Anyone else asking for it is answered by helper, and told so.

    CAIRO_AGENT_URL   orbit/claudecode base (default http://127.0.0.1:51260)
"""
import json
import os
import re
import urllib.error
import urllib.request

import cairo_mcp as mcp

CLAUDE = os.environ.get('CAIRO_AGENT_URL', 'http://127.0.0.1:51260').rstrip('/')
TIMEOUT = int(os.environ.get('CAIRO_HELPER_TIMEOUT', 420))

AGENTS = {
    'helper': {
        'id': 'helper',
        'name': 'cairo helper',
        'description': 'Explains stablecoins, smart contracts and Cairo in plain English, '
                       'and looks at your contracts on the practice chain: what a button does, '
                       'whether a loan is safe, why a test failed. Uses the cairo MCP server.',
        'writes': False,
        'brains': ['helper', 'claude'],
        'mcp': '/cairo/mcp',
    },
}
EXAMPLES = [
    'What is a stablecoin, like I\'m 10?',
    'How do I make my own stablecoin?',
    'What\'s the difference between USDC and DAI?',
    'What does liquidation mean?',
    'What can this contract do?',
    'Why did my test fail?',
]


def card(base='/cairo'):
    return {'name': 'cairo helper', 'version': '1.0', 'protocol': 'agent/1.0',
            'description': AGENTS['helper']['description'],
            'endpoints': {'agents': f'{base}/agents', 'run': f'{base}/run',
                          'stream': f'{base}/run/stream', 'mcp': f'{base}/mcp'},
            'brains': {'helper': 'open, free, no model',
                       'claude': 'owner-only, via orbit/claudecode'},
            'examples': EXAMPLES}


# ---- helpers ----------------------------------------------------------------

def _human(v, decimals=0):
    """1500000000000000000 (18) -> '1.5'; plain ints get separators."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v)
    if decimals:
        whole, frac = divmod(abs(n), 10 ** decimals)
        s = f'{whole:,}'
        if frac:
            s += ('.' + str(frac).rjust(decimals, '0')).rstrip('0')[:7]
        return ('-' if n < 0 else '') + s
    return f'{n:,}'


def _words(q):
    return set(re.findall(r"[a-z0-9_']+", q.lower()))


class Run:
    """One question. `tool()` calls MCP tools and records each as a step."""

    def __init__(self, mod, gate, jobs, emit):
        self.mod, self.gate, self.jobs, self.emit = mod, gate, jobs, emit
        self.steps = []

    def step(self, tool, params, result):
        st = {'tool': tool, 'params': params, 'result': result}
        self.steps.append(st)
        self.emit({'type': 'step', 'step': st})

    def tool(self, _tool, **args):
        name = _tool
        try:
            r = mcp.call_tool(self.mod, name, args, self.gate, self.jobs)
        except Exception as e:  # noqa: BLE001
            r = {'error': str(e)}
        brief = r
        if isinstance(r, dict):
            brief = {k: (v if len(json.dumps(v, default=str)) < 300 else '…')
                     for k, v in list(r.items())[:8]}
        self.step(f'mcp:{name}', args, brief)
        return r


# ---- the helper brain ---------------------------------------------------------

def _lesson_scores(run, q):
    idx = run.tool('cairo_tutorials')
    lessons = idx.get('lessons', [])
    ql = q.lower()
    scored = []
    import tutorials  # src/ is on sys.path (server.py)
    for l in lessons:
        full = tutorials.lesson(l['id']) or {}
        s = sum(2 if len(t) > 5 else 1 for t in full.get('topics', []) if t in ql)
        if s:
            scored.append((s, full))
    scored.sort(key=lambda x: -x[0])
    return [x for _, x in scored], idx.get('glossary', {})


def _first_say(lesson, n=2):
    out = []
    for sec in lesson.get('sections', []):
        if sec.get('say'):
            out.append(sec['say'].split('\n\n')[0])
        if len(out) >= n:
            break
    return '\n\n'.join(out)


def _contract_brief(app):
    ui = app.get('ui') or {}
    fns = ui.get('fns') or {}
    c = (app.get('abi') or [{}])[0] if app.get('abi') else {}
    reads, writes = [], []
    for f in c.get('functions') or []:
        name = f.get('name')
        meta = fns.get(name) or {}
        if meta.get('hide'):
            continue
        label = meta.get('label') or name.replace('_', ' ')
        (reads if f.get('mutability') == 'view' else writes).append(label)
    return ui, reads, writes


def helper_brain(run, query, ctx):
    q = query.strip()
    ql = q.lower()
    w = _words(q)
    parts, actions = [], []
    contracts = run.tool('cairo_contracts')
    names = [c['name'] for c in contracts.get('contracts', [])]
    named = [n for n in names if re.search(rf'\b{re.escape(n)}\b', ql)]
    target = (named or [ctx.get('contract')] or [None])[0]
    if target not in names:
        target = None
    lessons, glossary = _lesson_scores(run, q)

    # 1. "what is X" / a glossary word
    defs = []
    for term, d in glossary.items():
        keys = [k.strip() for k in term.split('/')]
        if any(re.search(rf'\b{re.escape(k)}', ql) for k in keys):
            defs.append((term, d))
    asks_def = bool(re.search(r'\b(what|whats|what\'s|explain|mean|means|meaning|define)\b', ql))

    # 2. how do I make a stablecoin?
    if re.search(r'\b(make|build|create|launch|start|deploy|issue|write)\b', ql) and \
            re.search(r'stable|coin|dollar|usd|token|money', ql):
        parts.append('You can make one right here, in about 5 minutes, with no code to write. There are two kinds to start from:\n\n'
                     '- **Cash-backed** (like USDC): a "bank" account can only make coins when it reports dollars to back them. Easiest.\n'
                     '- **Crypto-backed** (like DAI): people lock up crypto and borrow dollars against it. More moving parts, more fun.\n\n'
                     'Press one of the buttons below: it makes the contract with its tests and a simple app. Then press Test, '
                     'and open the APP tab to play with it on the practice chain (pretend money only).')
        actions += [{'label': 'Make a cash-backed dollar', 'new': 'stablecoin'},
                    {'label': 'Make a crypto-backed dollar', 'new': 'vault_stable'},
                    {'label': 'Read the lesson first', 'lesson': 'build-a-cash-dollar'}]
        lessons = []

    # 3. failing tests / errors
    elif re.search(r'\b(fail|failed|failing|error|broken|red|revert|panic|wrong)\b', ql):
        jobs = run.tool('cairo_jobs').get('jobs', [])
        mine = [j for j in jobs if j.get('fn') in ('test', 'build', 'vibe')
                and (not target or j.get('name') == target)]
        if not mine:
            parts.append('I don\'t see a recent build or test run' + (f' for **{target}**' if target else '') +
                         '. Press **Test** on the contract and ask me again.')
        else:
            j = mine[0]
            r = j.get('result') or {}
            r = r.get('test') or r.get('build') or r
            bad = [t for t in (r.get('tests') or []) if t.get('status') != 'pass']
            diags = r.get('diagnostics') or []
            if j.get('state') in ('queued', 'running'):
                parts.append(f'The {j["fn"]} of **{j.get("name")}** is still running. Give it a minute.')
            elif bad:
                lines = [f'- `{t.get("name", "").split("::")[-1]}`: {t.get("reason") or "failed"}' for t in bad[:6]]
                parts.append(f'In the last test run of **{j.get("name")}**, {len(bad)} test(s) failed:\n\n' + '\n'.join(lines) +
                             '\n\nA failing test means the contract did something the test said it shouldn\'t (or refused something '
                             'it should allow). The text after the colon is the contract\'s own "no" message from an `assert`. '
                             'Find that message in the CODE tab to see which rule tripped.')
            elif diags:
                lines = [f'- line {d.get("line")} of `{d.get("file")}`: {d.get("message")}' for d in diags[:5]]
                parts.append(f'The code of **{j.get("name")}** didn\'t compile:\n\n' + '\n'.join(lines) +
                             '\n\nClick an error in the console to jump to that line. Typos and a missing `;` are the usual suspects.')
            elif j.get('error') or r.get('error'):
                parts.append(f'The last {j["fn"]} of **{j.get("name")}** stopped with: `{(j.get("error") or r.get("error"))[:300]}`')
            else:
                parts.append(f'Good news: the last {j["fn"]} of **{j.get("name")}** passed. Nothing is failing right now.')
        lessons = [l for l in lessons if l['id'] == 'test-like-a-pro'] or lessons[:0]

    # 4. about a contract (named, or the one that's open)
    elif target and (named or (
            re.search(r'\b(this|it|contract|my|do|does|can|safe|loan|balance|how much|button|buttons|use)\b', ql)
            # "what does liquidation mean?" is about the word, not the open contract
            and not (asks_def and (defs or lessons) and not re.search(r'\b(this|it|my)\b', ql)))):
        app = run.tool('cairo_app', name=target)
        if app.get('error'):
            parts.append(f'I couldn\'t open **{target}**: {app["error"]}')
        else:
            ui, reads, writes = _contract_brief(app)
            dep = app.get('deployment') or {}
            head = f'**{ui.get("title") or target}**'
            if ui.get('intro'):
                head += f': {ui["intro"]}'
            elif app.get('description'):
                head += f': {app["description"]}'
            parts.append(head)
            if writes:
                parts.append('Things you can **do**: ' + ', '.join(writes) + '.')
            if reads:
                parts.append('Things you can **check**: ' + ', '.join(reads) + '.')
            if app.get('state') == 'unbuilt':
                parts.append('It isn\'t built yet: press **Build** (or Test) first.')
            elif not dep.get('live'):
                parts.append('It\'s not running on the practice chain yet. Open its **APP** tab and press **Start**.')
                actions.append({'label': f'Open {target}', 'open': target})
            else:
                parts += _live_reads(run, target, app, ctx, ql)
            if ui.get('steps') and re.search(r'\b(how|use|start|try|steps)\b', ql):
                parts.append('How to use it:\n\n' + '\n'.join(f'{i + 1}. {s}' for i, s in enumerate(ui['steps'])))

    # 5. which contracts are here
    elif re.search(r'\b(contracts|list|have|mine)\b', ql) and not lessons:
        rows = contracts.get('contracts', [])
        lines = [f'- **{c["name"]}**' + (f' ({c.get("template")})' if c.get('template') else '') +
                 (' · running' if (c.get('deployment') or {}).get('live') else '') for c in rows]
        parts.append(f'There are {len(rows)} contracts here:\n\n' + '\n'.join(lines))

    if defs and (asks_def or not parts):
        for term, d in defs[:3]:
            parts.append(f'**{term.capitalize()}**: {d}.')

    if lessons and len(parts) < 3:
        top = lessons[0]
        parts.append(_first_say(top))
        actions.append({'label': f'Lesson: {top["title"]}', 'lesson': top['id']})
        if top.get('try'):
            actions.append({'label': 'Make it', 'new': top['try']['template']})
        for l in lessons[1:2]:
            actions.append({'label': f'Lesson: {l["title"]}', 'lesson': l['id']})

    if not parts:
        parts.append('I\'m the helper for this workbench. I can explain **stablecoins** and **smart contracts** in plain words, '
                     'help you **make your own digital dollar**, tell you what a contract\'s buttons do, check if a loan is safe, '
                     'and explain why a test failed. Try one of these:')
        actions += [{'label': x, 'ask': x} for x in EXAMPLES[:4]]
        actions.append({'label': 'Start the lessons', 'lesson': 'what-is-a-stablecoin'})
    return '\n\n'.join(p for p in parts if p), actions[:5]


def _live_reads(run, name, app, ctx, ql):
    """Read every no-input / me-only view of a live contract, as the current
    account, and say the values in its app.json words."""
    ui = app.get('ui') or {}
    fns = ui.get('fns') or {}
    who = ctx.get('account') or 'alice'
    c = (app.get('abi') or [{}])[0]
    out = []
    for f in (c.get('functions') or [])[:16]:
        if not (f.get('mutability') == 'view'):
            continue
        meta = fns.get(f['name']) or {}
        if meta.get('hide'):
            continue
        ins = f.get('inputs') or []
        if len(ins) > 1:
            continue
        args = {}
        if ins:
            if not (meta.get('inputs') or {}).get(ins[0]['name'], {}).get('hide'):
                continue
            args = {ins[0]['name']: who}
        r = run.tool('cairo_call', name=name, fn=f['name'], args=args, account=who)
        if not r.get('ok', True) or r.get('error'):
            continue
        v = r.get('result', r.get('value'))
        if isinstance(v, list) and len(v) == 1:
            v = v[0]
        if isinstance(v, bool) or str(v).lower() in ('true', 'false'):
            b = v is True or str(v).lower() == 'true'
            val = meta.get('yes' if b else 'no') or ('Yes' if b else 'No')
        else:
            val = _human(v, meta.get('decimals') or 0)
            if meta.get('unit') == '$':
                val = '$' + val
            elif meta.get('unit') == '%':
                val += '%'
            elif meta.get('unit'):
                val += ' ' + meta['unit']
        out.append(f'- {meta.get("label") or f["name"].replace("_", " ")}: **{val}**')
    if not out:
        return []
    s = f'Right now, as **{who}**:\n\n' + '\n'.join(out)
    if re.search(r'safe|loan|liquidat|ratio', ql) and any('Safety' in o for o in out):
        s += ('\n\nThe safety level must stay at 150% or more. Under that, anyone can close the loan and take the locked ETH.')
    return [s]


# ---- the claude brain -------------------------------------------------------

def _claude_goal(ctx, mcp_url, mods_dir):
    return (
        'You are the friendly helper inside "cairo", a Cairo/Starknet contract workbench with a beginner school for '
        'stablecoins. The person asking is probably NOT a programmer: answer in plain English, short paragraphs, '
        'everyday analogies (arcade tokens, pawn shops, vending machines), define any jargon in passing, no walls of code.\n\n'
        f'You have the cairo MCP server at {mcp_url} (JSON-RPC 2.0 over HTTP POST). Use it with curl, e.g.\n'
        f'  curl -s {mcp_url} -H "Content-Type: application/json" -d \'{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}\'\n'
        f'  curl -s {mcp_url} -H "Content-Type: application/json" -d \'{{"jsonrpc":"2.0","id":2,"method":"tools/call",'
        '"params":{"name":"cairo_app","arguments":{"name":"<contract>"}}}\'\n'
        'Key tools: cairo_tutorials/cairo_lesson (the lessons), cairo_contracts, cairo_app (ABI + labels + deployment), '
        'cairo_read (code), cairo_call (use a contract on the practice chain as alice/bob/carol/dave; amounts are raw units, '
        '18 decimals for coins), cairo_events, cairo_jobs (last build/test results), cairo_new/cairo_write/cairo_test.\n\n'
        f'Contracts live in {mods_dir}/<name>/ (src/lib.cairo, tests/, app.json). Only change files if the person asks '
        'for a change; then edit, and run cairo_test via MCP to prove it still works. The practice chain is a local '
        'devnet: no real money. Never claim anything about real-world money without saying it is not advice.\n\n'
        f'What the console says the person is looking at: {json.dumps(ctx)}\n'
        'End with the answer itself (your final message is shown to the person).')


def claude_brain(run, query, ctx, base_url, root, mods_dir):
    # the helper's own findings go in as context, so Claude starts informed
    hint, acts = helper_brain(Run(run.mod, run.gate, run.jobs, lambda e: None), query, ctx)
    mcp_url = base_url.rstrip('/') + '/cairo/mcp'
    body = json.dumps({'query': f'{query}\n\n(What the built-in helper found, for context:\n{hint[:3000]})',
                       'goal': _claude_goal(ctx, mcp_url, mods_dir), 'path': root,
                       'timeout': TIMEOUT}).encode()
    req = urllib.request.Request(CLAUDE + '/run/stream', data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT + 60)
    except (urllib.error.URLError, OSError) as e:
        run.step('claude', {}, {'error': f'orbit/claudecode unreachable: {e}'})
        return None
    final, err = '', None
    with resp:
        for raw in resp:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            if ev.get('type') == 'step':
                st = ev.get('step') or {}
                p = st.get('params') or {}
                if st.get('tool') == 'finish':
                    final = p.get('summary') or final
                elif st.get('tool') in ('text', 'message', 'assistant') and p.get('text'):
                    final = p['text']
                run.step(st.get('tool') or 'step', {k: str(v)[:240] for k, v in p.items()
                                                    if k in ('command', 'file_path', 'path', 'pattern', 'summary', 'text')},
                         str(st.get('result') or '')[:300] or None)
            elif ev.get('type') == 'error':
                err = ev.get('error') or 'agent error'
    if err and not final:
        return f'The smart brain hit an error: {err}', acts
    return final or hint, _actions_from_text(final) or acts


def _actions_from_text(text):
    import tutorials
    acts = []
    tl = (text or '').lower()
    for l in tutorials.LESSONS:
        if l['id'] in tl or l['title'].lower() in tl:
            acts.append({'label': f'Lesson: {l["title"]}', 'lesson': l['id']})
    for tid, label in (('vault_stable', 'Make a crypto-backed dollar'), ('stablecoin', 'Make a cash-backed dollar')):
        if tid in tl:
            acts.append({'label': label, 'new': tid})
    return acts[:4]


# ---- entry ------------------------------------------------------------------

def run(mod, body, who, gate, jobs, emit, base_url, root, mods_dir):
    """Answer one question. Returns the done event (also emitted)."""
    query = str(body.get('query') or body.get('prompt') or '').strip()
    ctx = body.get('context') if isinstance(body.get('context'), dict) else {}
    ctx = {k: ctx.get(k) for k in ('contract', 'view', 'lesson', 'account') if ctx.get(k)}
    want = body.get('brain') or 'helper'
    r = Run(mod, gate, jobs, emit)
    if not query:
        ev = {'type': 'error', 'error': 'empty query'}
        emit(ev)
        return ev
    brain, note = 'helper', None
    if want == 'claude':
        if who.get('writable'):
            brain = 'claude'
        else:
            note = 'The smart (Claude) brain is owner-only, so the built-in helper answered.'
    out = None
    if brain == 'claude':
        out = claude_brain(r, query, ctx, base_url, root, mods_dir)
        if out is None:
            brain, note = 'helper', 'Claude was unreachable, so the built-in helper answered.'
    if out is None:
        out = helper_brain(r, query, ctx)
    answer, actions = out
    ev = {'type': 'done', 'result': r.steps, 'answer': answer, 'actions': actions,
          'brain': brain, **({'note': note} if note else {})}
    emit(ev)
    return ev
