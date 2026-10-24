"""tutor.py — the NEAR tutor and builder, on the fleet's agent contract.

    GET  /near/agents                  {"agents": [...]}   (what orbit/build probes)
    GET  /near/agents/<id>             one agent
    GET  /near/.well-known/agent.json  the agent/1.0 card
    POST /near/run          {query, agent?, brain?, context?}   one run, JSON answer
    POST /near/run/stream   {query, agent?, brain?, context?}   SSE: step ... done | error
    POST /near/agents/<id>/run[/stream]                          same, agent fixed

Events are orbit/agent's ({type: step, step: {tool, params, result}} then
{type: done, result: [steps], answer, actions}), so a run looks the same in
the workshop's corner orb, the build console, or orbit/agent.

`context` is what the page knows: {project, lesson, account, network, view}.

TWO AGENTS
    tutor    open, free, no model. Routes a question to the lessons and the
             glossary, reads the open project (methods, walkthrough, last
             build/test), reads any contract on chain through the near MCP
             tools, and for anything zero-knowledge pulls the cairo mod's own
             lessons over its MCP server. Every read is a step named
             mcp:<tool> so you see where each fact came from. brain=claude
             (token holders / loopback only) hands the same question plus
             the tutor's findings to orbit/claudecode with the near MCP URL.
    builder  token holders / loopback only (it writes files and bills the
             host Claude account). "make a tip jar called tips" scaffolds a
             crate from a starter and runs a vibe job (orbit/claudecode edits
             it, then cargo build + test). Point it at an open project and
             say what to change, and it edits that. Anything zero-knowledge
             ("zk", "cairo", "anonymous", "private") is forwarded whole to
             the cairo mod's builder; its steps stream back here and the
             answer links to the contract over there.

    NEAR_CAIRO_URL    orbit/cairo base (default http://127.0.0.1:51291)
    NEAR_AGENT_URL    orbit/claudecode base (default http://127.0.0.1:51260)
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

import lessons as L
import studio

CAIRO = os.environ.get('NEAR_CAIRO_URL', 'http://127.0.0.1:51291').rstrip('/')
CLAUDE = studio.AGENT_URL
TIMEOUT = int(os.environ.get('NEAR_TUTOR_TIMEOUT', 420))

AGENTS = {
    'tutor': {
        'id': 'tutor',
        'name': 'near tutor',
        'description': 'Teaches NEAR smart contracts from zero: what an account/contract is, the four '
                       'parts of a contract, keys, tokens, build-test-ship, and the zero-knowledge '
                       'ladder (commitments, merkle proofs, then Cairo via the cairo mod). Reads your '
                       'projects, their last build/test, and any contract on chain through the near '
                       'MCP server. Free, no model; brain=claude for token holders.',
        'writes': False,
        'access': 'open',
        'brains': ['tutor', 'claude'],
        'mcp': '/near/mcp',
        'run': '/near/agents/tutor/run',
        'stream': '/near/agents/tutor/run/stream',
    },
    'builder': {
        'id': 'builder',
        'name': 'near builder',
        'description': 'Makes and edits NEAR contracts in Rust (near-sdk 5). Name a new one from a '
                       'starter (counter, token, commit_reveal, merkle_allowlist) or point it at an '
                       'open project and say what to change; orbit/claudecode writes the Rust and '
                       'tests, then it builds the .wasm and runs cargo test. Asks for zero knowledge '
                       '("zk", "cairo", "anonymous") are forwarded to the cairo mod\'s builder. '
                       'Token holders / loopback only: writes files and bills the host Claude account.',
        'writes': True,
        'access': 'token',
        'run': '/near/agents/builder/run',
        'stream': '/near/agents/builder/run/stream',
        'params': {'query': 'what to make or change (required)',
                   'name': 'project to make or edit (default: context.project / parsed from query)',
                   'template': 'starter for a new project (see /near/templates)',
                   'new': 'true = make a new project even if one is open',
                   'vibe': 'false = just scaffold and test, no agent edit',
                   'zk': 'true = send to the cairo builder regardless of wording',
                   'model': 'model for orbit/claudecode'},
        'examples': ['make a tip jar called tips where anyone can send NEAR and the owner withdraws',
                     'add a pause switch only the owner can flip',
                     'make a zk vote where members stay anonymous'],
    },
}
EXAMPLES = [
    'What is a smart contract on NEAR, like I\'m 10?',
    'How do I make my own token?',
    'What does "one yocto" mean?',
    'What can wrap.near do?',
    'What is zero knowledge, and can I do it on NEAR?',
    'Why did my test fail?',
]


def card(base='/near'):
    return {'name': 'near tutor', 'version': '1.0', 'protocol': 'agent/1.0',
            'description': AGENTS['tutor']['description'],
            'endpoints': {'agents': f'{base}/agents', 'run': f'{base}/run',
                          'stream': f'{base}/run/stream', 'mcp': '/mcp'},
            'brains': {'tutor': 'open, free, no model',
                       'claude': 'token holders / loopback, via orbit/claudecode'},
            'agents': {a: f'{base}/agents/{a}/run/stream' for a in AGENTS},
            'examples': EXAMPLES}


# ── plumbing ─────────────────────────────────────────────────────

class Run:
    """One question. tool() calls near MCP tools in-process and records each
    as a step; cairo() does the same against the cairo mod's MCP over HTTP."""

    def __init__(self, emit):
        self.emit, self.steps = emit, []

    def step(self, tool, params, result):
        st = {'tool': tool, 'params': params, 'result': result}
        self.steps.append(st)
        self.emit({'type': 'step', 'step': st})

    @staticmethod
    def _brief(r):
        if isinstance(r, dict):
            return {k: (v if len(json.dumps(v, default=str)) < 300 else '…')
                    for k, v in list(r.items())[:8]}
        return r

    def tool(self, _tool, **args):
        import mcp
        try:
            r = mcp.call_tool(_tool, args, local=True)
        except Exception as e:  # noqa: BLE001
            r = {'error': str(e)}
        self.step(f'mcp:{_tool}', args, self._brief(r))
        return r

    def cairo(self, _tool, **args):
        r = cairo_mcp(_tool, args)
        self.step(f'cairo:{_tool}', args, self._brief(r))
        return r


def cairo_mcp(name, args=None, timeout=20):
    """One tools/call on the cairo mod's MCP server. {'error': ...} if the
    cairo mod is down, so the tutor can say so instead of failing."""
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                       'params': {'name': name, 'arguments': args or {}}}).encode()
    req = urllib.request.Request(CAIRO + '/cairo/mcp', data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {'error': f'cairo mod unreachable at {CAIRO}: {e}', 'cairo_down': True}
    res = out.get('result') or {}
    if res.get('structuredContent') is not None:
        return res['structuredContent']
    try:
        return json.loads((res.get('content') or [{}])[0].get('text') or '{}')
    except ValueError:
        return {'error': out.get('error') or 'bad cairo answer'}


def cairo_alive():
    try:
        with urllib.request.urlopen(CAIRO + '/cairo/mcp', timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _words(q):
    return set(re.findall(r"[a-z0-9_']+", q.lower()))


ZK = r'\b(zk|zero[- ]knowledge|cairo|starknet|stark|snark|anonymous|anonymity|nullifier|ring sig\w*|poseidon|prover|proof of membership)\b'
MAKE = r'\b(make|create|build|write|scaffold|start|new|spin up|code up|launch)\b'
STOP = {'a', 'an', 'the', 'my', 'new', 'own', 'simple', 'basic', 'little', 'smart', 'contract',
        'near', 'rust', 'me', 'us', 'please', 'that', 'which', 'where', 'with', 'for', 'to',
        'of', 'and', 'or', 'can', 'lets', 'let', 'so', 'it', 'one', 'some', 'called', 'named',
        'zk', 'cairo', 'version', 'in'}


# ── the tutor brain ──────────────────────────────────────────────

def _lesson_scores(q):
    ql = q.lower()
    scored = []
    for l in L.LESSONS:
        s = sum(2 if len(t) > 5 else 1 for t in l['topics'] if re.search(rf'\b{re.escape(t)}', ql))
        if s:
            scored.append((s, l))
    scored.sort(key=lambda x: -x[0])
    return [x for _, x in scored]


def _first_say(lesson, n=2):
    out = []
    for sec in lesson.get('sections', []):
        if sec.get('say'):
            out.append(sec['say'].split('\n\n')[0])
        if len(out) >= n:
            break
    return '\n\n'.join(out)


def _defs(ql):
    out = []
    for term, d in L.GLOSSARY.items():
        keys = [k.strip() for k in term.split('/')]
        if any(re.search(rf'\b{re.escape(k)}\b', ql) for k in keys if len(k) > 1):
            out.append((term, d))
    return out


def _kind_word(k):
    return {'view': 'check', 'change': 'do', 'init': 'set up'}.get(k, k)


def _project_answer(run, name, ql, ctx):
    p = run.tool('near_project', name=name)
    if p.get('error'):
        return [f'I couldn\'t open **{name}**: {p["error"]}'], []
    parts, acts = [], [{'label': f'Open {name}', 'open': name}]
    m = p.get('meta') or {}
    head = f'**{name}**' + (f' (from the {m["template"]} starter)' if m.get('template') else '')
    if m.get('desc'):
        head += f': {m["desc"]}'
    parts.append(head)
    ms = p.get('methods') or []
    does = [x['name'] for x in ms if x['kind'] == 'change']
    checks = [x['name'] for x in ms if x['kind'] == 'view']
    if does:
        parts.append('Things it can **do** (signed calls): ' + ', '.join(f'`{x}`' for x in does) + '.')
    if checks:
        parts.append('Things you can **check** (free views): ' + ', '.join(f'`{x}`' for x in checks) + '.')
    st = p.get('state')
    if st == 'unbuilt':
        parts.append('It isn\'t built yet: press **Build**, then **Test**.')
    elif st == 'stale':
        parts.append('The source changed since the last build: press **Build** again before shipping.')
    deps = m.get('deployed') or []
    if deps:
        d = deps[-1]
        parts.append(f'Last shipped to **{d.get("account")}** on {d.get("network")}. '
                     f'Open the CALL tab with that account to use it.')
        acts.append({'label': f'Look up {d.get("account")}', 'lookup': d.get('account')})
    elif st == 'built':
        parts.append('Built and tested? Press **Ship** and it lands on your testnet account.')
    if re.search(r'\b(how|use|start|try|steps|walk)\b', ql) and p.get('steps'):
        parts.append('How to use it:\n\n' + '\n'.join(f'{i + 1}. {s}' for i, s in enumerate(p['steps'])))
    if re.search(r'\b(explain|line|why|mean|walkthrough|code)\b', ql) and p.get('walkthrough'):
        notes = [w for w in p['walkthrough'] if w.get('line')][:4]
        if notes:
            parts.append('Reading the code:\n\n' + '\n'.join(
                f'- line {w["line"]} `{w["find"]}`: {w["say"].split(". ")[0]}.' for w in notes))
            parts.append('The CODE view has every note pinned to its line.')
    if m.get('cairo_twin'):
        acts.append({'label': f'Cairo twin: {m["cairo_twin"]}', 'url': '/cairo/#new'})
    return parts, acts


def _failure_answer(run, name):
    jobs = run.tool('near_jobs', name=name or None).get('jobs', [])
    mine = [j for j in jobs if j.get('fn') in ('build', 'verify', 'check', 'vibe')]
    if not mine:
        return [('I don\'t see a recent build or test' + (f' of **{name}**' if name else '') +
                 '. Press **Build** or **Test** and ask me again.')], []
    j = run.tool('near_job', id=mine[0]['id'])
    r = j.get('result') or {}
    acts = [{'label': f'Open {j.get("name")}', 'open': j.get('name')}] if j.get('name') else []
    if j.get('state') in ('queued', 'running'):
        return [f'The {j["fn"]} of **{j.get("name")}** is still running. Give it a few seconds '
                '(the first build on a cold box takes about half a minute).'], acts
    build = r.get('build') if 'build' in r else (r if j['fn'] == 'build' else None)
    test = r.get('test') if 'test' in r else (r if j['fn'] == 'verify' else None)
    if build and not build.get('ok'):
        d = build.get('diagnostics') or []
        lines = [f'- `{x.get("file")}:{x.get("line")}` {x.get("message")}' for x in d[:5] if x.get('line')] \
            or [f'- {x.get("message")}' for x in d[:5]]
        return [f'**{j.get("name")}** didn\'t compile:\n\n' + '\n'.join(lines) +
                '\n\nClick a red line in the workshop to jump there. Rust\'s messages are long but '
                'the first "error:" is the one to fix; the rest usually follow from it. A missing `;`, '
                'a `&self` method trying to write, or a type mismatch (`U128` vs `u128`) are the usual suspects.'], acts
    bad = [t for t in (test or {}).get('tests') or [] if t.get('status') != 'pass']
    if bad:
        lines = [f'- `{t["name"].split("::")[-1]}`: {t.get("reason") or "failed"}' for t in bad[:6]]
        return [f'In the last test run of **{j.get("name")}**, {len(bad)} test(s) failed:\n\n' + '\n'.join(lines) +
                '\n\nA failing test means the contract did something the test said it shouldn\'t (or refused '
                'something it should allow). The text after the colon is the contract\'s own "no" from a '
                '`require!`, or the assertion that didn\'t hold. Find that message in the code to see which rule tripped.'], acts
    if j.get('error') or r.get('error'):
        return [f'The last {j["fn"]} of **{j.get("name")}** stopped with: `{str(j.get("error") or r.get("error"))[:300]}`'], acts
    return [f'Good news: the last {j["fn"]} of **{j.get("name")}** passed. Nothing is failing right now.'], acts


def _chain_contract_answer(run, acct, net):
    c = run.tool('near_contract', account_id=acct, network=net)
    if c.get('error'):
        return [f'**{acct}** on {net}: {c["error"]}'], []
    ms = c.get('methods') or c.get('fns') or []
    names = [m if isinstance(m, str) else m.get('name') for m in ms]
    if not names:
        return [f'**{acct}** has no code on {net}; it\'s a plain account.'], [{'label': f'Look up {acct}', 'lookup': acct}]
    std = []
    if 'ft_transfer' in names:
        std.append('a NEP-141 fungible token')
    if 'nft_transfer' in names:
        std.append('a NEP-171 NFT collection')
    if 'storage_deposit' in names:
        std.append('NEP-145 storage deposits')
    views = [n for n in names if n.startswith(('get_', 'ft_metadata', 'ft_balance', 'ft_total', 'nft_metadata',
                                               'nft_token', 'is_', 'has_', 'view', 'list', 'storage_balance'))]
    parts = [f'**{acct}** is a contract on {net} with {len(names)} methods' +
             (f', and it speaks {" and ".join(std)}' if std else '') + '.']
    if views:
        parts.append('Likely free views: ' + ', '.join(f'`{v}`' for v in views[:10]) + '.')
    others = [n for n in names if n not in views][:12]
    if others:
        parts.append('Other methods: ' + ', '.join(f'`{v}`' for v in others) + '.')
    parts.append('NEAR keeps no ABI on chain, so these names come from the WASM export table; the '
                 'CALL tab\'s VIEW mode tells views from changes by trying (a change refuses).')
    return parts, [{'label': f'Look up {acct}', 'lookup': acct}]


def _zk_answer(run, ql):
    parts, acts = [], []
    parts.append('Zero knowledge means proving something is true without revealing anything else. It has rungs, '
                 'and the first two are a single hash each; you can build and ship them here on NEAR today:\n\n'
                 '1. **Commitment** (sha256 of value + salt + your account): prove you decided earlier. The `commit_reveal` starter.\n'
                 '2. **Merkle membership** (one 32-byte root for a whole list): prove you are on the list. The `merkle_allowlist` starter.\n'
                 '3. **Nullifier**: claim once without revealing which member you are. Needs a real proof.\n'
                 '4. **SNARK verifier**: NEAR has alt_bn128 host functions, so a Groth16 verifier contract is possible, but it is a project, not a starter.\n'
                 '5. **Proof-native chain**: Cairo on Starknet. That is the cairo mod on this box.')
    acts += [{'label': 'Lesson: the ZK ladder', 'lesson': 'zk-ladder'},
             {'label': 'Make a sealed vote (rung 1)', 'new': 'commit_reveal'},
             {'label': 'Make a merkle allowlist (rung 2)', 'new': 'merkle_allowlist'}]
    idx = run.cairo('cairo_tutorials')
    if idx.get('cairo_down'):
        parts.append('The cairo mod isn\'t running right now, so I can\'t show its lessons; its workbench normally lives at /cairo/.')
    else:
        ls = idx.get('lessons') or []
        pick = [l for l in ls if any(w in (l.get('title', '') + l.get('id', '')).lower()
                                     for w in ('contract', 'cairo', 'commit', 'merkle', 'zk', 'test'))][:3] or ls[:3]
        if pick:
            parts.append('For rungs 3 to 5 the cairo mod has its own school and a practice chain (no money). Lessons there:\n\n' +
                         '\n'.join(f'- **{l["title"]}**' + (f': {l["summary"]}' if l.get('summary') else '') for l in pick))
        ctr = run.cairo('cairo_contracts')
        names = [c['name'] for c in ctr.get('contracts', [])] if isinstance(ctr, dict) else []
        zk_ones = [n for n in names if any(k in n for k in ('zuma', 'zq', 'commit', 'merkle', 'airdrop', 'subnet'))][:6]
        if zk_ones:
            parts.append('Cairo contracts already on this box you can read: ' + ', '.join(f'`cairo.{n}`' for n in zk_ones) + '.')
        acts.append({'label': 'Open the cairo workbench', 'url': '/cairo/#learn'})
    if re.search(MAKE, ql):
        parts.append('If you want me to **build** the ZK version, use the Build pill and say so ("make a zk vote called ..."): '
                     'the builder hands it to the cairo mod and streams the result back here.')
    return parts, acts[:5]


def tutor_brain(run, query, ctx):
    q = query.strip()
    ql = q.lower()
    parts, actions = [], []
    net = ctx.get('network') or 'testnet'
    projects = run.tool('near_projects')
    names = [p['name'] for p in projects.get('projects', [])]
    named = [n for n in names if re.search(rf'\b{re.escape(n)}\b', ql)]
    target = (named or [ctx.get('project')] or [None])[0]
    if target not in names:
        target = None
    lessons = _lesson_scores(q)
    defs = _defs(ql)
    asks_def = bool(re.search(r'\b(what|whats|what\'s|explain|mean|means|meaning|define|is a|is an)\b', ql))
    accts = [a for a in re.findall(r'\b([a-z0-9][a-z0-9._-]{1,62}\.(?:near|testnet))\b', ql)]

    # 1. zero knowledge, in any wording
    if re.search(ZK, ql) or re.search(r'\bprivate\b.*\b(vote|member|list|contract)', ql):
        parts, actions = _zk_answer(run, ql)
        lessons = []

    # 2. how do I make X
    elif re.search(MAKE, ql) and re.search(r'token|coin|stable|nft|contract|dapp|app|vote|auction|lottery|jar|counter|list', ql):
        tpl = 'token' if re.search(r'token|coin|stable|point', ql) else \
            'commit_reveal' if re.search(r'vote|auction|lottery|seal|secret|guess', ql) else \
            'merkle_allowlist' if re.search(r'allow|white|airdrop|list|member', ql) else 'counter'
        parts.append('You can make it right here, in a few minutes, and ship it to testnet for free. Two ways:\n\n'
                     '- **Start from a starter**: the BUILD tab scaffolds a working Rust contract with tests and a line-by-line walkthrough. '
                     'Change what you like, Build, Test, Ship.\n'
                     '- **Describe it**: use the Build pill on this orb ("make a tip jar called tips where anyone can send and the owner withdraws"). '
                     'The builder scaffolds the closest starter, has orbit/claudecode shape it to your ask, then builds and tests it.')
        actions += [{'label': f'Start from the {tpl} starter', 'new': tpl},
                    {'label': 'Lesson: build, test, ship', 'lesson': 'build-test-ship'}]
        if tpl == 'token':
            actions.append({'label': 'Lesson: make your own token', 'lesson': 'make-your-own-token'})
        lessons = []

    # 3. failing builds / tests
    elif re.search(r'\b(fail|failed|failing|error|broken|red|panic|wrong|compile|won\'t build|doesn\'t build)\b', ql):
        parts, actions = _failure_answer(run, target)
        lessons = [l for l in lessons if l['id'] == 'test-like-a-pro']

    # 4. a contract on chain, by name
    elif accts and not named:
        parts, actions = _chain_contract_answer(run, accts[0], 'mainnet' if accts[0].endswith('.near') else 'testnet')
        lessons = lessons[:1] if asks_def else []

    # 5. which projects are here
    elif re.search(r'\b(projects|mine|what have i|i made|my contracts)\b', ql) and not named:
        rows = projects.get('projects', [])
        if rows:
            parts.append(f'There are {len(rows)} projects in the workshop:\n\n' + '\n'.join(
                f'- **{p["name"]}**' + (f' ({p["template"]})' if p.get('template') else '') +
                f' · {p.get("state")}' + (' · shipped' if p.get('deployed') else '') for p in rows))
            actions += [{'label': f'Open {p["name"]}', 'open': p['name']} for p in rows[:3]]
        else:
            parts.append('No projects yet. Make one from a starter in BUILD, or ask the Build pill for one.')
            actions.append({'label': 'Start with a counter', 'new': 'counter'})

    # 6. a project here (named, or the one that's open)
    elif target and (named or (
            re.search(r'\b(this|it|project|my|do|does|can|button|buttons|use|ship|deploy|built|methods?|code|explain|line|walkthrough|read)\b', ql)
            and not (asks_def and (defs or lessons) and not re.search(r'\b(this|it|my|code)\b', ql)))):
        parts, actions = _project_answer(run, target, ql, ctx)

    # 7. ship / deploy questions
    elif re.search(r'\b(ship|deploy|testnet|mainnet|upgrade|redeploy)\b', ql):
        parts.append('**Ship** hands the built .wasm to this module\'s keystore (the same one the WALLET tab manages). '
                     'On testnet it just goes; on mainnet it refuses unless you confirm. The starter\'s constructor '
                     '(`new` + example args) is batched into the same transaction the first time; a redeploy to the same '
                     'account upgrades the code and keeps the data, and skips the init.')
        actions.append({'label': 'Lesson: build, test, ship', 'lesson': 'build-test-ship'})
        lessons = []

    if defs and (asks_def or not parts):
        for term, d in defs[:3]:
            parts.append(f'**{term}**: {d}.')

    if lessons and len(parts) < 3:
        top = lessons[0]
        if not parts or asks_def:
            parts.append(_first_say(top))
        actions.append({'label': f'Lesson: {top["title"]}', 'lesson': top['id']})
        if top.get('try'):
            actions.append({'label': 'Try it', 'new': top['try']['template']})
        for l in lessons[1:2]:
            actions.append({'label': f'Lesson: {l["title"]}', 'lesson': l['id']})

    if not parts:
        parts.append('I\'m the tutor for this workshop. I can explain **NEAR smart contracts** from zero, help you '
                     '**make your own** (a counter, a token, a sealed vote, an allowlist), read any contract on chain, '
                     'tell you why a build or test failed, and walk you up the **zero-knowledge ladder** into the '
                     'cairo mod. Try one of these:')
        actions += [{'label': x, 'ask': x} for x in EXAMPLES[:4]]
        actions.append({'label': 'Start the lessons', 'lesson': 'an-account-is-a-contract'})
    seen, uniq = set(), []
    for a in actions:
        k = json.dumps(a, sort_keys=True)
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return '\n\n'.join(p for p in parts if p), uniq[:5]


# ── the claude brain ─────────────────────────────────────────────

def _claude_goal(ctx, mcp_url):
    return (
        'You are the friendly tutor inside "near", a NEAR smart-contract workshop for beginners with a '
        'zero-knowledge track that continues in the "cairo" mod. The person asking is probably NOT a '
        'programmer: plain English, short paragraphs, everyday analogies (vending machines, arcade tokens, '
        'sealed envelopes), define jargon in passing, no walls of code.\n\n'
        f'You have the near MCP server at {mcp_url} (JSON-RPC 2.0 over HTTP POST). Use it with curl, e.g.\n'
        f'  curl -s {mcp_url} -H "Content-Type: application/json" -d \'{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}\'\n'
        f'  curl -s {mcp_url} -H "Content-Type: application/json" -d \'{{"jsonrpc":"2.0","id":2,"method":"tools/call",'
        '"params":{"name":"near_project","arguments":{"name":"<project>"}}}}\'\n'
        'Key tools: near_lessons/near_lesson (the school), near_projects/near_project/near_read (the person\'s '
        'Rust projects, with a walkthrough pinned to lines), near_jobs/near_job (last build/test results), '
        'near_contract/near_view/near_account (any contract on chain), near_zk (the cairo mod\'s lessons and '
        'contracts), near_merkle/near_seal (helpers for the ZK starters). Do NOT write files or deploy unless '
        'asked; that is the builder\'s job. Never claim anything about real money without saying it is not advice.\n\n'
        f'What the page says the person is looking at: {json.dumps(ctx)}\n'
        'End with the answer itself (your final message is shown to the person).')


def claude_brain(run, query, ctx, mcp_url):
    hint, acts = tutor_brain(Run(lambda e: None), query, ctx)
    body = json.dumps({'query': f'{query}\n\n(What the built-in tutor found, for context:\n{hint[:3000]})',
                       'goal': _claude_goal(ctx, mcp_url), 'path': studio.ROOT,
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
                run.step(st.get('tool') or 'step',
                         {k: str(v)[:240] for k, v in p.items()
                          if k in ('command', 'file_path', 'path', 'pattern', 'summary', 'text')},
                         str(st.get('result') or '')[:300] or None)
            elif ev.get('type') == 'error':
                err = ev.get('error') or 'agent error'
    if err and not final:
        return f'The smart brain hit an error: {err}', acts
    return final or hint, _actions_from_text(final) or acts


def _actions_from_text(text):
    acts, tl = [], (text or '').lower()
    for l in L.LESSONS:
        if l['id'] in tl or l['title'].lower() in tl:
            acts.append({'label': f'Lesson: {l["title"]}', 'lesson': l['id']})
    for tid in ('commit_reveal', 'merkle_allowlist', 'token', 'counter'):
        if tid in tl:
            acts.append({'label': f'Start from {tid}', 'new': tid})
    return acts[:4]


# ── the builder ──────────────────────────────────────────────────

def _slugify(words):
    return re.sub(r'[^a-z0-9_]', '', '_'.join(w for w in words if w).lower())[:32].strip('_')


def _name_from(q):
    m = re.search(r'\b(?:called|named|name it|as)\s+[`"\']?([A-Za-z][\w-]{1,40})', q)
    if m and m.group(1).lower() not in STOP:
        return _slugify([m.group(1).replace('-', '_')])
    m = re.search(MAKE + r'\s+(?:(?:me|us|a|an|the|my|new|another|own|simple|basic|little|smart|near|rust|zk|cairo)\s+)*'
                  r'([a-z][a-z0-9_-]*)(?:\s+([a-z][a-z0-9_-]*))?', q.lower())
    if m:
        ws = [w for w in (m.group(2), m.group(3)) if w and w not in STOP]
        if ws and ws[-1] in ('contract', 'app', 'project'):
            ws = ws[:-1]
        if ws:
            return _slugify(ws[:2])
    return None


def _guess_template(q, ids):
    ql = q.lower()
    for tid in ids:
        if re.search(rf'\b{re.escape(tid)}\b', ql):
            return tid
    rules = [('merkle_allowlist', r'\b(merkle|allowlist|whitelist|airdrop|member)'),
             ('commit_reveal', r'\b(commit|reveal|sealed|seal|secret|hidden|vote|voting|lottery|auction|guess)'),
             ('token', r'\b(token|nep-?141|ft|coin|points?|balance|transfer|tip|pay|stable|money|jar|treasury|withdraw)')]
    for tid, rx in rules:
        if tid in ids and re.search(rx, ql):
            return tid
    return 'counter'


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
        s += ':\n\n' + '\n'.join(f'- `{x["name"].split("::")[-1]}`: {x.get("reason") or "failed"}' for x in bad)
    return s


def cairo_builder(run, body, ctx):
    """Forward a ZK ask to the cairo mod's builder (loopback = owner there),
    relaying its steps. Returns (answer, actions, extra)."""
    if not cairo_alive():
        return ('That needs the cairo mod, and it isn\'t running right now. Start it (`m cairo/serve`) '
                'or build the hash-only rung here instead.', [{'label': 'Make a sealed vote here', 'new': 'commit_reveal'}],
                {'mode': 'cairo', 'error': 'cairo down'})
    fwd = {k: v for k, v in body.items() if k in ('query', 'name', 'template', 'fork', 'new', 'vibe', 'model')}
    fwd['context'] = {'contract': ctx.get('cairo_contract')} if ctx.get('cairo_contract') else {}
    run.step('cairo:builder', {'query': fwd.get('query', '')[:200]}, {'forwarded': True})
    req = urllib.request.Request(CAIRO + '/cairo/agents/builder/run/stream', data=json.dumps(fwd).encode(),
                                 method='POST', headers={'Content-Type': 'application/json'})
    try:
        resp = urllib.request.urlopen(req, timeout=1800)
    except (urllib.error.URLError, OSError) as e:
        return f'The cairo builder didn\'t answer: {e}', [], {'mode': 'cairo', 'error': str(e)}
    done, err = None, None
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
                run.step('cairo:' + str(st.get('tool') or 'step'), st.get('params') or {}, st.get('result'))
            elif ev.get('type') == 'done':
                done = ev
            elif ev.get('type') == 'error':
                err = ev.get('error') or 'cairo builder error'
    if err and not done:
        return f'The cairo builder stopped: {err}', [{'label': 'Open the cairo workbench', 'url': '/cairo/'}], \
            {'mode': 'cairo', 'error': err}
    name = (done or {}).get('contract')
    acts = [{'label': f'Open cairo.{name}', 'url': f'/cairo/#studio/{name}'}] if name else \
        [{'label': 'Open the cairo workbench', 'url': '/cairo/'}]
    ans = (done or {}).get('answer') or 'Done on the cairo side.'
    return ('Built on the **cairo** mod (Cairo + Starknet, the proof-native rung):\n\n' + ans, acts,
            {'mode': 'cairo', 'contract': name, 'job': (done or {}).get('job')})


def builder_run(run, body, ctx, start_job, get_job):
    q = str(body.get('query') or body.get('prompt') or '').strip()
    ql = q.lower()
    if body.get('zk') or re.search(ZK, ql):
        return cairo_builder(run, body, ctx)
    names = [p['name'] for p in run.tool('near_projects').get('projects', [])]
    ids = [t['id'] for t in studio.templates()['templates']]
    is_make = bool(body.get('new') or re.search(r'\b(called|named)\b', ql)
                   or re.search(MAKE + r'\s+(?:me\s+)?(?:a|an|my|another|new)\b', ql))
    want = body.get('name')
    if not want and not is_make:
        want = ctx.get('project')
    if not want:
        want = _name_from(q)
    if not want:
        return ('Tell me what to call it, e.g. "make a tip jar **called tips**", or open a project first '
                'and ask for a change.', [], {})
    try:
        pkg = studio.slug(want)
    except ValueError as e:
        return f'"{want}" can\'t be a project name: {e}', [], {}
    made = None
    if pkg in names and not body.get('new'):
        mode = 'edit'
    else:
        mode = 'make'
        if pkg in names:
            i = 2
            while f'{pkg}_{i}' in names:
                i += 1
            pkg = f'{pkg}_{i}'
        tid = body.get('template') or _guess_template(q, ids)
        res = run.tool('near_new', name=pkg, template=tid, desc=body.get('desc') or q[:120] or None)
        if res.get('error'):
            return f'Couldn\'t make **{pkg}**: {res["error"]}', [], {'project': pkg}
        made = f'the **{tid}** starter'
    acts = [{'label': f'Open {pkg}', 'open': pkg}]
    if body.get('vibe') is False or not q:
        job = start_job('check', {'name': pkg})
        run.step('job', {'fn': 'check', 'name': pkg}, {'job': job['id']})
        j = _wait(get_job, job['id'])
        r = j.get('result') or {}
        what = f'Made **{pkg}** from {made}' if made else f'**{pkg}** is unchanged'
        b = r.get('build') or {}
        line = _test_line(r.get('test')) if b.get('ok') else 'it does not compile yet'
        return f'{what}; {line}.', acts, {'project': pkg, 'mode': mode, 'job': job['id']}

    prompt = q
    if mode == 'make':
        prompt = (f'This crate ({pkg}) was just scaffolded from {made.replace("**", "")}. Turn it into what '
                  f'the person asked for, rename things to fit, keep the tests meaningful (cover the new rules, '
                  f'not the old starter), and keep .studio.json "init" in sync with the constructor.\n\n'
                  f'The ask: {q}')
    kw = {'name': pkg, 'prompt': prompt}
    if body.get('model'):
        kw['model'] = body['model']
    job = start_job('vibe', kw)
    run.step('job', {'fn': 'vibe', 'name': pkg}, {'job': job['id'], 'mode': mode})
    seen = 0
    while True:
        time.sleep(1.5)
        j = get_job(job['id'])
        for st in (j.get('steps') or [])[seen:]:
            run.step(st.get('tool') or 'step', {'what': st.get('what')}, {'error': True} if st.get('error') else None)
        seen = len(j.get('steps') or [])
        if j.get('state') not in ('queued', 'running'):
            break
    res = j.get('result') or {}
    extra = {'project': pkg, 'mode': mode, 'job': job['id']}
    if j.get('error') or (not res.get('ok') and res.get('result') and not res.get('build')):
        return f'The agent stopped on **{pkg}**: {str(j.get("error") or res.get("result"))[-400:]}', acts, extra
    head = f'Made **{pkg}** from {made} and shaped it to your ask.' if mode == 'make' else f'Edited **{pkg}**.'
    out = [head]
    b = res.get('build') or {}
    if b and not b.get('ok'):
        d = (b.get('diagnostics') or [])[:4]
        out.append('It does **not** compile yet:\n\n' + '\n'.join(
            f'- `{x.get("file")}:{x.get("line")}` {x.get("message")}' for x in d))
    elif 'test' in res:
        out.append(_test_line(res['test']).capitalize() + '.')
        if res['test'].get('ok'):
            out.append('Press **Ship** to put it on your testnet account.')
    summary = (res.get('result') or '').strip()
    if summary:
        out.append(summary if len(summary) <= 2500 else summary[:2500].rsplit('\n', 1)[0] + '\n\n…')
    return '\n\n'.join(out), acts, extra


def _wait(get_job, id_, every=1.0):
    while True:
        j = get_job(id_)
        if j.get('state') not in ('queued', 'running'):
            return j
        time.sleep(every)


# ── entry ────────────────────────────────────────────────────────

def run(body, who, emit, start_job, get_job, agent=None, mcp_url='http://127.0.0.1:50910/mcp'):
    """Answer one request. Returns the done event (also emitted).
    who = {'writable': bool, 'why': str} from the server's gate."""
    query = str(body.get('query') or body.get('prompt') or '').strip()
    ctx = body.get('context') if isinstance(body.get('context'), dict) else {}
    ctx = {k: ctx.get(k) for k in ('project', 'lesson', 'account', 'network', 'view', 'cairo_contract')
           if ctx.get(k)}
    agent = agent or body.get('agent') or ('builder' if body.get('brain') == 'builder' else 'tutor')
    want = body.get('brain') or 'tutor'
    r = Run(emit)
    if agent not in AGENTS:
        ev = {'type': 'error', 'error': f'no agent {agent!r}', 'agents': list(AGENTS)}
        emit(ev)
        return ev
    if not query and not (agent == 'builder' and body.get('vibe') is False):
        ev = {'type': 'error', 'error': 'empty query'}
        emit(ev)
        return ev
    if agent == 'builder':
        if not who.get('writable'):
            ev = {'type': 'error', 'owner_only': True,
                  'error': f"the builder needs the write token: {who.get('why') or 'not a token holder'}"}
            emit(ev)
            return ev
        answer, actions, extra = builder_run(r, body, ctx, start_job, get_job)
        ev = {'type': 'done', 'result': r.steps, 'answer': answer, 'actions': actions,
              'agent': 'builder', 'brain': 'claude', **extra}
        emit(ev)
        return ev
    brain, note = 'tutor', None
    if want == 'claude':
        if who.get('writable'):
            brain = 'claude'
        else:
            note = 'The smart (Claude) brain needs the write token, so the built-in tutor answered.'
    out = None
    if brain == 'claude':
        out = claude_brain(r, query, ctx, mcp_url)
        if out is None:
            brain, note = 'tutor', 'Claude was unreachable, so the built-in tutor answered.'
    if out is None:
        out = tutor_brain(r, query, ctx)
    answer, actions = out
    ev = {'type': 'done', 'result': r.steps, 'answer': answer, 'actions': actions,
          'agent': 'tutor', 'brain': brain, **({'note': note} if note else {})}
    emit(ev)
    return ev
