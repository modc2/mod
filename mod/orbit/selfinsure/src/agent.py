#!/usr/bin/env python3
"""selfinsure agent — ask it anything about mutual insurance, and have it draft
(and, when you say so, open) your own pool. Fleet agent contract, stdlib only.

    GET  /agents             {"agents": [...]} — the roster orbit/build probes
    GET  /agents/{id}        one agent in full
    POST /run                {"query": "..."} → one answer, JSON
    POST /run/stream         the same run as SSE: model_start, token,
                             tool_start, step, done|error
    GET  /.well-known/agent.json   the agent/1.0 card

TWO BRAINS, ONE DOOR
    Every tool call goes through mcp.call_tool() — the function REST and MCP
    already use — so the agent can never see a different ledger or skip a
    check a person would hit.

    rules  (default, always on)  no model, no network, no key, no cost. Knows
           what every term means (guide.TOPICS), turns "a pool for 20 couriers,
           $8 a month, up to $600" into pool terms with a could-it-pay check
           (guide.draft), and reads the live pools, stats, claim queue, the
           health template and the demo contract.
    llm    (optional, local first)  any OpenAI-compatible /chat/completions
           endpoint with tool calling — a local ollama or llama-server first of
           all. SELFINSURE_AGENT_LLM=http://127.0.0.1:11434/v1 (+ _MODEL, and
           _KEY or ~/.mod/selfinsure/agent.key). It answers questions outside
           insurance too. If it fails the run falls back to rules and says so;
           it never silently goes somewhere else.

CREATING IS EXPLICIT
    "create a pool for …" returns a DRAFT. Nothing is written until the same
    request says confirm / now / go ahead (or the app's Create button is
    pressed). The owner key comes back once, in that answer.
"""

import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import guide as G                                               # noqa: E402
import mcp as M                                                 # noqa: E402
from pool import SelfInsureError                                # noqa: E402

LLM_URL = os.environ.get('SELFINSURE_AGENT_LLM', '').rstrip('/')
LLM_MODEL = os.environ.get('SELFINSURE_AGENT_MODEL', 'qwen2.5:7b')
LLM_TIMEOUT = float(os.environ.get('SELFINSURE_AGENT_TIMEOUT', 120))
KEY_FILE = os.path.join(os.path.expanduser(
    os.environ.get('SELFINSURE_HOME', '~/.mod/selfinsure')), 'agent.key')
MAX_STEPS = 10

AGENTS = {
    'selfinsure': {
        'name': 'selfinsure guide',
        'description': 'Explains mutual insurance in plain words, reads the live '
                       'pools, and drafts your own pool from one sentence — opening '
                       'it only when you confirm.',
        'writes': True,
    },
    'selfinsure-reader': {
        'name': 'selfinsure reader',
        'description': 'The same guide, read-only: explains, reads and drafts, '
                       'never opens a pool or moves money, whoever asks.',
        'writes': False,
    },
}
DEFAULT_AGENT = 'selfinsure'

# What a person sees as starting points. Ordered: meaning first, then doing.
EXAMPLES = [
    'What does selfinsure mean?',
    'How do I create my own pool?',
    'Draft a pool for 20 couriers covering bike theft, $8 a month, up to $600',
    'What is the operator fee?',
    'What happens if a pool runs out of money?',
    'Who decides claims?',
    'Show me the pools',
    'Show the US health template',
]

# Tools that change the ledger or a chain. Everything else only reads.
WRITE_TOOLS = {'si_create_pool', 'si_set_terms', 'si_join', 'si_premium', 'si_donate',
               'si_register_agent', 'si_admit_agent', 'si_claim_file', 'si_vote',
               'si_withdraw_claim', 'si_distribute', 'si_withdraw_fees', 'si_deploy'}


def _err(e):
    if isinstance(e, SelfInsureError):
        return e.dict().get('error') or str(e)
    return f'{type(e).__name__}: {e}'


def _call(tool, args, can_write):
    if tool in WRITE_TOOLS and not can_write:
        raise SelfInsureError(f'{tool} changes the ledger and this agent is read-only '
                              '— ask the selfinsure agent, or use the app.', status=403)
    if tool == 'si_ask':
        raise SelfInsureError('si_ask is this agent; ask directly', status=400)
    return M.call_tool(tool, args)


def _money(v, unit='USD'):
    if v is None:
        return '—'
    try:
        v = float(v)
    except (TypeError, ValueError):
        return f'{v} {unit}'
    return f'{v:,.2f}'.rstrip('0').rstrip('.') + f' {unit}'


# ── what the rules brain says about each tool's result ───────────

def _say_pools(out):
    rows = out.get('pools') or []
    if not rows:
        return ('There are no pools on this node yet. You can open the first one — '
                'say "draft a pool for …" or use the Create page.')
    lines = [f'{out.get("count", len(rows))} pool(s):']
    for r in rows[:10]:
        t = r.get('terms') or {}
        lines.append(f'- {r["name"]} ({r["id"]}): {_money(t.get("premium"), r["unit"])} '
                     f'per {t.get("period_days", 30):g} days, up to '
                     f'{_money(t.get("coverage"), r["unit"])} a claim, '
                     f'{r.get("members", 0)} member(s), fee {t.get("fee_pct", 0):g}% — '
                     f'{(r.get("solvency") or {}).get("verdict", "")}')
    return '\n'.join(lines)


def _say_pool(out):
    t, m, u = out.get('terms') or {}, out.get('money') or {}, out.get('unit', 'USD')
    return (f'{out["name"]} ({out["id"]}) — {out.get("about") or "no description"}\n'
            f'Costs {_money(t.get("premium"), u)} every {t.get("period_days", 30):g} days; '
            f'pays up to {_money(t.get("coverage"), u)} per claim after a '
            f'{_money(t.get("deductible"), u)} deductible; {t.get("waiting_days", 0):g}-day wait.\n'
            f'{t.get("quorum")} vote(s) decide a claim, {float(t.get("threshold") or 0) * 100:.0f}% '
            f'must accept. Operator fee {t.get("fee_pct", 0):g}%.\n'
            f'{out.get("members", 0)} member(s); holds {_money(m.get("balance"), u)}; '
            f'{_money(m.get("paid_in_claims"), u)} paid in claims; '
            f'{_money(m.get("returned_to_members"), u)} returned as surplus.\n'
            f'Solvency: {(out.get("solvency") or {}).get("verdict", "unknown")}.')


def _say_stats(out):
    if not out.get('pools'):
        return 'No pools on this node yet — nothing has been paid in or out.'
    return (f'{out["pools"]} pool(s), {out["members"]} member(s), {out["claims"]} '
            f'claim(s) ({out["open_claims"]} open). Premiums in: {out["premiums_in"]:,.2f}; '
            f'paid in claims: {out["paid_in_claims"]:,.2f}; returned to members: '
            f'{out["returned_to_members"]:,.2f}; operator fees: {out["operator_fees"]:,.2f} '
            f'({out.get("operator_share", 0) * 100:.1f}% of premium).')


def _say_quote(out):
    u = out['unit']
    return (f'In {out["name"]}: a loss of {_money(out["claim_of"], u)} would pay '
            f'{_money(out["would_pay"], u)} (after the {_money(out["deductible"], u)} '
            f'deductible and the per-claim cap). Joining costs {_money(out["premium"], u)} '
            f'every {out["period_days"]:g} days. '
            f'{"The pool could pay that today." if out["funded_today"] else "The pool does NOT hold enough to pay that today — it would be queued as owed."} '
            f'Decided by {out["decided_by"]}.')


def _say_queue(out):
    rows = out.get('claims') or []
    if not rows:
        return 'No claims are waiting on a decision.'
    return '\n'.join([f'{len(rows)} claim(s) waiting:'] +
                     [f'- {c.get("id")}: {c.get("title") or ""} — {c.get("amount")} '
                      f'{c.get("unit", "")}, {c.get("state")}' for c in rows[:10]])


def _say_preset(out):
    h = out.get('terms_human') or {}
    return (f'{out.get("title")}: {out.get("about")}\n'
            f'Premium {h.get("premium"):,} per {h.get("period_days")} days, up to '
            f'{h.get("coverage"):,} a claim, {h.get("deductible"):,} deductible, '
            f'{h.get("annual_cap"):,} a year per member, {h.get("waiting_days")}-day wait, '
            f'{h.get("reserve_floor"):,} reserve, operator fee {h.get("fee_bps", 0) / 100:g}%.')


def _say_onchain(out):
    p = out.get('provider') or {}
    return (f'{out.get("name")} at {out.get("address")} ({out.get("network")}): '
            f'operator fee now {p.get("fee_pct_now")}% (cap {p.get("fee_cap_bps", 1000) / 100:g}%), '
            f'provider\'s share of all premium so far {p.get("profit_share_of_premium")}, '
            f'{(out.get("counts") or {}).get("members", 0)} member(s). '
            f'{(out.get("solvency") or {}).get("verdict", "")}')


def _say_draft(out):
    a, c = out['create_args'], out['check']
    u = a.get('unit', 'USD')
    lines = [f'Here is a draft — nothing has been created yet.',
             f'Name: {a["name"]}',
             f'Rule claims are judged against: {a.get("about")}',
             f'Premium: {_money(a.get("premium"), u)} every {a.get("period_days", 30):g} days',
             f'Pays up to: {_money(a.get("coverage"), u)} per claim'
             + (f', {_money(a.get("annual_cap"), u)} per member per year' if a.get('annual_cap') else ''),
             f'Deductible: {_money(a.get("deductible"), u)}  ·  Waiting period: '
             f'{a.get("waiting_days", 0):g} days',
             f'Claims decided by {a.get("quorum")} vote(s), '
             f'{float(a.get("threshold") or 0.5) * 100:.0f}% must accept '
             f'({a.get("agent_policy", "open")} adjudicators)',
             f'Operator fee: {a.get("fee_bps", 0) / 100:g}%',
             '', f'Could it pay? {c["headline"]}']
    lines += [f'- {n}' for n in c['notes']]
    if out.get('assumed'):
        lines.append(f'(I filled in {", ".join(out["assumed"])} from '
                     f'{"the " + out["template"] + " starter" if out.get("template") else "sensible defaults"}'
                     ' — change anything.)')
    lines.append('To open it: press Create on this draft, or ask again ending with '
                 '"confirm".')
    return '\n'.join(lines)


SAY = {'si_pools': _say_pools, 'si_pool': _say_pool, 'si_stats': _say_stats,
       'si_quote': _say_quote, 'si_queue': _say_queue, 'si_preset': _say_preset,
       'si_onchain': _say_onchain, 'si_draft': _say_draft}


def summarize(tool, out):
    fn = SAY.get(tool)
    if fn:
        try:
            return fn(out)
        except Exception:                               # noqa: BLE001
            pass
    if tool == 'si_create_pool':
        return (f'Created "{out["name"]}" — pool id {out["id"]}.\n'
                f'OWNER KEY: {out["owner_key"]}\n'
                'Save it now. It is shown only this once and nobody can recover it. '
                'Share the pool id with people who should join.')
    return json.dumps(out, default=str)[:1500]


# ── the rules brain ──────────────────────────────────────────────

_POOL_ID = re.compile(r'\b([a-z0-9][a-z0-9-]{1,60}-[0-9a-f]{4})\b')
_CONFIRM = re.compile(r'\b(confirm(?:ed)?|go ahead|do it now|create it now|for real|'
                      r'yes,? create)\b', re.I)
_MAKE = re.compile(r'\b(create|make|start|open|set ?up|launch|draft|build|design|want|'
                   r'need|plan)\b', re.I)
_THING = re.compile(r'\b(pool|mutual|fund|cover|coverage|insurance|insure|group)\b', re.I)
_HOWTO = re.compile(r'^\s*(how|what|where|why|can|could|should|is|do|does)\b', re.I)


def demo_pool():
    try:
        with open(os.path.join(os.path.dirname(HERE), 'config.json')) as f:
            c = json.load(f)
        return c['contracts']['local']['contracts']['TravisCountyHealthMutual']['address']
    except Exception:                                   # noqa: BLE001
        return None


def plan(query, can_write=True):
    """Query → list of (tool, args) or ('say', text). Pure: no calls."""
    q = (query or '').strip()
    low = q.lower()
    if not q or re.fullmatch(r'(help|\?|hi|hello|hey|start|menu|what can you do\??|'
                             r'commands)', low):
        return [('say', help_text())]

    said = G.parse_terms(q)
    details = {k for k in said if k not in ('members', 'name', 'unit')}
    wants_pool = _MAKE.search(q) and _THING.search(q)
    if wants_pool and (details or (G._pick_template(q) and not _HOWTO.match(q))) \
            or (details and len(details) >= 2):
        steps = [('si_draft', {'text': q})]
        if _CONFIRM.search(q):
            steps.append(('create_from_draft', {}))
        return steps
    if wants_pool and _HOWTO.match(q):
        return [('topic', {'id': 'create'}), ('say', _templates_text())]

    m = _POOL_ID.search(low)
    if m:
        pid = m.group(1)
        amt = re.search(r'\b(?:quote|claim of|loss of|for)\s*\$?(\d[\d,]*(?:\.\d+)?)', low)
        if 'quote' in low or amt:
            return [('si_quote', {'pool': pid, **({'amount': amt.group(1).replace(',', '')}
                                                  if amt else {})})]
        return [('si_pool', {'pool': pid, 'full': False})]
    if re.search(r'\b(demo|live)\b.*\b(pool|contract|chain)\b|\bon[- ]?chain pool\b', low):
        addr = demo_pool()
        if addr:
            return [('si_onchain', {'address': addr})]
    if re.search(r'\b(list|show|see|which|any|all|browse)\b.*\bpools?\b|^pools?$', low):
        return [('si_pools', {'limit': 20})]
    if re.search(r'\b(stats|statistics|totals|numbers|how much (?:has|is))\b', low):
        return [('si_stats', {})]
    if re.search(r'\b(queue|pending claims|open claims|claims waiting)\b', low):
        return [('si_queue', {'state': 'open', 'limit': 20})]
    if re.search(r'\b(show|see|give|terms|numbers)\b.*\b(health|template|preset)\b', low):
        kind = 'parametric' if 'parametric' in low else 'mutual' if 'plain' in low else 'health'
        return [('si_preset', {'preset': kind})]

    e = G.explain(q)
    if e['topic']:
        return [('topic', {'id': e['topic']['id'], 'related': e['related']})]
    return [('say', _unknown_text(q))]


def help_text():
    return ('I am the selfinsure guide. I run on this node with no outside service. '
            'I can explain what anything here means, read the live pools, and draft '
            'your own pool from one sentence. Try:\n' +
            '\n'.join(f'- {e}' for e in EXAMPLES))


def _templates_text():
    return ('Starters you can begin from (say "draft a pool for <one of these>"): ' +
            ', '.join(t['title'] for t in G.TEMPLATES if t['id'] != 'custom') + '.')


def _unknown_text(q):
    tail = ('' if LLM_URL else ' For questions outside insurance, the node owner can '
            'connect a local model (SELFINSURE_AGENT_LLM).')
    return (f'I am not sure how to answer "{q[:80]}" yet.' + tail + '\n\n' + help_text())


def _topic_text(tid, related=()):
    t = G.topic(tid)
    out = f'{t["title"]}\n{t["text"]}'
    rel = [G.topic(r)['title'] for r in related if G.topic(r)]
    if rel:
        out += '\n\nRelated: ' + ', '.join(rel) + '.'
    return out


def _run_rules(query, can_write, emit):
    emit({'type': 'model_start', 'step': 0, 'model': 'rules', 'brain': 'rules'})
    lines, steps, extra = [], [], {'links': [], 'topics': []}
    last_draft = None
    for i, (tool, args) in enumerate(plan(query, can_write)):
        if tool == 'say':
            lines.append(args)
            continue
        if tool == 'topic':
            lines.append(_topic_text(args['id'], args.get('related', ())))
            extra['topics'].append(args['id'])
            link = (G.topic(args['id']) or {}).get('link')
            if link:
                extra['links'].append(link)
            continue
        if tool == 'create_from_draft':
            if not last_draft:
                continue
            if not can_write:
                lines.append('This agent is read-only, so I did not create it.')
                continue
            tool, args = 'si_create_pool', dict(last_draft['create_args'])
        emit({'type': 'tool_start', 'tool': tool, 'params': args, 'i': i})
        step = {'tool': tool, 'params': args}
        t0 = time.time()
        try:
            out = _call(tool, dict(args), can_write)
            step['result'] = out
            if tool == 'si_draft':
                last_draft = out
                extra['draft'] = out
                extra['links'].append('/create')
            if tool == 'si_create_pool':
                extra['created'] = {'id': out['id'], 'name': out['name'],
                                    'owner_key': out['owner_key']}
                extra.pop('draft', None)
            lines.append(summarize(tool, out))
        except Exception as e:                          # noqa: BLE001
            step['error'] = _err(e)
            if tool.startswith('si_onchain'):
                lines.append('I could not read the on-chain demo pool — the local '
                             'chain is not running it right now (it is a test chain '
                             'that resets). Everything else here works without it. '
                             f'Detail: {step["error"][:160]}')
            else:
                lines.append(f'{tool} failed: {step["error"]}')
        step['ms'] = int((time.time() - t0) * 1000)
        steps.append(step)
        emit({'type': 'step', 'step': step})
    answer = '\n\n'.join(x for x in lines if x)
    emit({'type': 'token', 'text': answer})
    return answer, steps, extra


# ── the llm brain (optional, local first) ────────────────────────

def llm_key():
    k = os.environ.get('SELFINSURE_AGENT_LLM_KEY')
    if k:
        return k.strip()
    try:
        with open(KEY_FILE) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def llm_ready():
    return bool(LLM_URL)


def _llm_tools(can_write):
    return [{'type': 'function', 'function': {
        'name': n, 'description': t['description'], 'parameters': t['inputSchema']}}
        for n, t in M.TOOLS.items()
        if n != 'si_ask' and (can_write or n not in WRITE_TOOLS)]


def _chat(messages, tools, model):
    body = json.dumps({'model': model, 'messages': messages, 'tools': tools,
                       'temperature': 0.2}).encode()
    headers = {'content-type': 'application/json'}
    key = llm_key()
    if key:
        headers['authorization'] = f'Bearer {key}'
    req = urllib.request.Request(LLM_URL + '/chat/completions', body, headers)
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
        return json.loads(r.read())['choices'][0]['message']


def _run_llm(query, can_write, emit, model=None, history=None):
    model = model or LLM_MODEL
    system = (M.INSTRUCTIONS + '\n\nYou are the selfinsure guide. Help with anything '
              'the user asks, in plain language an average person understands. For '
              'insurance terms, si_explain gives the house explanation. To design a '
              'pool, call si_draft with the user\'s words, show the terms and the '
              'could-it-pay check, and call si_create_pool ONLY after the user '
              'explicitly confirms. When a pool is created, repeat the owner key and '
              'tell them it is shown once.' +
              ('' if can_write else ' You are read-only: never call tools that write.'))
    messages = [{'role': 'system', 'content': system}, *(history or [])[-12:],
                {'role': 'user', 'content': query}]
    tools = _llm_tools(can_write)
    steps, extra = [], {'links': [], 'topics': []}
    for i in range(MAX_STEPS):
        emit({'type': 'model_start', 'step': i, 'model': model, 'brain': 'llm'})
        msg = _chat(messages, tools, model)
        calls = msg.get('tool_calls') or []
        if not calls:
            answer = (msg.get('content') or '').strip()
            emit({'type': 'token', 'text': answer})
            return answer, steps, extra
        messages.append({'role': 'assistant', 'content': msg.get('content') or '',
                         'tool_calls': calls})
        for c in calls:
            fn = c.get('function') or {}
            name = fn.get('name')
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except json.JSONDecodeError:
                args = {}
            emit({'type': 'tool_start', 'tool': name, 'params': args})
            step = {'tool': name, 'params': args}
            try:
                out = _call(name, dict(args), can_write)
                step['result'] = out
                if name == 'si_draft':
                    extra['draft'] = out
                if name == 'si_create_pool':
                    extra['created'] = {'id': out['id'], 'name': out['name'],
                                        'owner_key': out['owner_key']}
                content = json.dumps(out, default=str)
            except Exception as e:                      # noqa: BLE001
                step['error'] = _err(e)
                content = json.dumps({'error': step['error']})
            steps.append(step)
            emit({'type': 'step', 'step': step})
            messages.append({'role': 'tool', 'tool_call_id': c.get('id', name),
                             'name': name, 'content': content[:8000]})
    answer = 'Stopped after the step budget — the tool results above are real.'
    emit({'type': 'token', 'text': answer})
    return answer, steps, extra


# ── the contract ─────────────────────────────────────────────────

def brains():
    local = LLM_URL.startswith(('http://127.', 'http://localhost', 'http://[::1]')) \
        if LLM_URL else None
    return {'rules': {'ready': True, 'local': True, 'cost': 'free'},
            'llm': {'ready': llm_ready(), 'local': local, 'url': LLM_URL or None,
                    'model': LLM_MODEL if LLM_URL else None,
                    'how': 'set SELFINSURE_AGENT_LLM to an OpenAI-compatible base url, '
                           'e.g. http://127.0.0.1:11434/v1 (ollama), then restart'}}


def agents():
    """GET /agents — the compatibility contract orbit/build probes for."""
    return {'agents': [{'id': k, **v, 'provider': 'selfinsure', 'local': True,
                        'free': True, 'examples': EXAMPLES} for k, v in AGENTS.items()],
            'default': DEFAULT_AGENT, 'brains': brains()}


def agent(name=None):
    key = name or DEFAULT_AGENT
    if key not in AGENTS:
        raise KeyError(f'no agent {key!r} — have {list(AGENTS)}')
    return {'id': key, **AGENTS[key], 'examples': EXAMPLES, 'brains': brains(),
            'tools': [n for n in M.TOOLS if AGENTS[key]['writes'] or n not in WRITE_TOOLS]}


def card(base_url=''):
    """/.well-known/agent.json — agent/1.0."""
    return {'protocol': 'agent/1.0', 'name': 'selfinsure', 'version': M.version(),
            'description': AGENTS[DEFAULT_AGENT]['description'],
            'agents': agents()['agents'],
            'endpoints': {'agents': f'{base_url}/agents', 'run': f'{base_url}/run',
                          'stream': f'{base_url}/run/stream', 'mcp': f'{base_url}/mcp'},
            'events': ['model_start', 'token', 'tool_start', 'step', 'done', 'error'],
            'auth': 'none — the same as the REST tools; pool writes still need the '
                    'owner/member keys they always needed'}


def run_stream(query, agent=None, can_write=True, brain='auto', model=None,
               history=None, **_ignored):
    """Yield the run's events. The last one is done or error."""
    key = agent or DEFAULT_AGENT
    if key not in AGENTS:
        yield {'type': 'error', 'error': f'no agent {key!r} — have {list(AGENTS)}'}
        return
    can_write = bool(can_write and AGENTS[key]['writes'])
    t0 = time.time()
    use = brain if brain in ('rules', 'llm') else ('llm' if llm_ready() else 'rules')
    fell_back = None
    q, box = queue.Queue(), {}

    def work():
        nonlocal fell_back
        try:
            if use == 'llm':
                try:
                    if not llm_ready():
                        raise RuntimeError('no SELFINSURE_AGENT_LLM configured')
                    box['out'] = _run_llm(query, can_write, q.put, model, history)
                    return
                except (urllib.error.URLError, OSError, KeyError, ValueError,
                        RuntimeError) as e:
                    fell_back = f'llm unavailable ({type(e).__name__}: {e}) — used rules'
                    q.put({'type': 'status', 'text': fell_back})
            box['out'] = _run_rules(query, can_write, q.put)
        except Exception as e:                          # noqa: BLE001
            box['err'] = _err(e)
        finally:
            q.put(None)

    threading.Thread(target=work, daemon=True, name='selfinsure-agent').start()
    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev
    if 'err' in box:
        yield {'type': 'error', 'error': box['err']}
        return
    answer, steps, extra = box['out']
    yield {'type': 'step', 'step': {'tool': 'finish', 'params': {'summary': answer}}}
    yield {'type': 'done', 'result': answer, 'steps': steps, 'agent': key,
           'brain': 'rules' if fell_back else use, 'fallback': fell_back,
           'can_write': can_write, 'free': True, **extra,
           'suggestions': _suggest(extra),
           'ms': int((time.time() - t0) * 1000)}


def _suggest(extra):
    """What to ask next, given what was just answered."""
    if extra.get('created'):
        return ['How do members join?', 'Who decides claims?', 'Show me the pools']
    if extra.get('draft'):
        return ['What is a waiting period?', 'What is the operator fee?',
                'What happens if a pool runs out of money?']
    seen = set(extra.get('topics') or [])
    pool = [e for e in EXAMPLES if not any(
        G.explain(e)['topic'] and G.explain(e)['topic']['id'] == s for s in seen)]
    return pool[:4]


def run(query, **kw):
    """POST /run — the same run, collected."""
    done = None
    for ev in run_stream(query, **kw):
        if ev['type'] in ('done', 'error'):
            done = ev
    if done is None or done['type'] == 'error':
        raise SelfInsureError((done or {}).get('error') or 'run produced no answer',
                              status=500)
    return {k: v for k, v in done.items() if k != 'type'}


if __name__ == '__main__':
    q = ' '.join(sys.argv[1:]) or 'help'
    print(run(q, can_write=False)['result'])
