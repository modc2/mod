"""starknet/api/guide.py — ask Starknet what it is, on the fleet's agent contract.

    GET  /agents                  {"agents": [...]} — what orbit/build probes
                                  before it mounts a module as an agent backend
    GET  /agents/<id>             one agent in full
    POST /run       {query}       one run, JSON answer
    POST /run/stream {query}      SSE: step ... done | error
    GET  /.well-known/agent.json  the agent/1.0 card

Events are orbit/agent's ({type: step, step: {tool, params, result}} then
{type: done, result: [steps]}), so a run drawn by the build console or
orbit/agent looks like any other job there.

TWO BRAINS
    facts   (default, open to everyone) no model, no key, no cost. Routes the
            question to the topics it touches — what Starknet is, stablecoins,
            the STRK20 privacy pool, fees and STRK, accounts, Cairo, this
            module — reads the live numbers each needs from the chain, and
            answers in plain English with them. Every read is a step in the
            trace, so you can see where a number came from.
    claude  (owner or loopback only) the same live readings handed to Claude
            Code through orbit/claudecode's POST /run/stream, as context in
            its system prompt. Claude answers anything, follows up through
            this API with curl, and its steps stream straight through. It
            runs with its CLI permissions off and spends the host's Claude
            account, so it gets the studio's write gate: a mod-protocol token
            from an owner wallet, or a call made on this box. Anyone else
            asking for it is answered by facts, and told so.

    STARKNET_AGENT_CLAUDE   orbit/claudecode base url
                            (default http://127.0.0.1:51260; empty disables)
"""

import json
import os
import queue
import re
import threading
import time
import urllib.request

import chain
import apps
import stables
import strk20

CLAUDE = os.environ.get('STARKNET_AGENT_CLAUDE', 'http://127.0.0.1:51260').rstrip('/')
CLAUDE_TIMEOUT = int(os.environ.get('STARKNET_AGENT_TIMEOUT', 600))
PORT = int(os.environ.get('PORT', 51020))

AGENTS = {
    'guide': {
        'name': 'starknet guide',
        'description': 'Explains the Starknet protocol in plain English — how '
                       'the rollup works, its stablecoins, the STRK20 privacy '
                       'pool, fees, accounts and Cairo — with live numbers '
                       'read from the chain. Read-only.',
        'writes': False,
    },
}
DEFAULT_AGENT = 'guide'

EXAMPLES = [
    'What is Starknet and how does it work?',
    'How do stablecoins work on Starknet?',
    'Is USDC on its peg right now?',
    'How does the STRK20 privacy pool hide payments?',
    'What do I pay fees in?',
    'How could I launch my own stablecoin?',
    'What Starknet mods run on this node?',
]


def _fmt_usd(v):
    if v is None:
        return '?'
    for div, s in ((1e9, 'B'), (1e6, 'M'), (1e3, 'k')):
        if abs(v) >= div:
            return f'${v / div:,.2f}{s}'
    return f'${v:,.0f}'


# ── live readings ────────────────────────────────────────────────────────────
# Each returns (data, one-line summary). The data rides in the step; the
# summary is what a reader sees in the trace.

def read_network(net):
    st = chain.status(network=net)
    bl = chain.blocks(12, network=net)['blocks']
    ts = [b['timestamp'] for b in bl if b.get('timestamp')]
    bt = (max(ts) - min(ts)) / (len(ts) - 1) if len(ts) > 1 else None
    txs = sum(b.get('transaction_count') or 0 for b in bl)
    data = {'chain': st['chain'], 'block_number': st['block_number'],
            'spec_version': st['spec_version'], 'block_time_s': bt,
            'tx_last_12_blocks': txs,
            'starknet_version': next((b.get('starknet_version') for b in bl
                                      if b.get('starknet_version')), None)}
    return data, (f"{st['chain']} at block #{st['block_number']:,}, "
                  f"{bt:.1f}s blocks, {txs} txs in the last 12" if bt else
                  f"{st['chain']} at block #{st['block_number']:,}")


def read_stables(net):
    s = stables.stables(network=net)
    return s, (f"{len(s['tokens'])} stablecoins, {_fmt_usd(s['total_supply_usd'])} "
               f"on chain")


def read_pool(net):
    p = strk20.state(network=net)
    hold = {k: v for k, v in (p.get('public_holdings') or {}).items()
            if isinstance(v, (int, float))}
    return p, ('pool ' + ('paused' if p.get('paused') else 'live') + ', holds ' +
               ', '.join(f'{v:,.2f} {k.upper()}' for k, v in hold.items()))


def read_mods(net):
    f = apps.apps()
    return f, f"{f['up']}/{f['count']} Starknet apps live: " + ', '.join(
        m['name'] + ('' if m.get('up') else ' (down)') for m in f['apps'])


READS = {'mods': read_mods, 'network': read_network, 'stables': read_stables, 'pool': read_pool}


# ── the facts brain ──────────────────────────────────────────────────────────

TOPICS = [
    ('mods', r'\bapps?\b|\bmods?\b|modules?|starktime|zquorum|\bzuma\b|zkwasm|workbench|on this node|family'),
    ('stables', r'stable|usdc|usdt|tether|\bdai\b|peg|dollar|\bcash\b|opus|circle'),
    ('privacy', r'privacy|private|strk20|pool|note|nullif|anonym|shield'),
    ('fees', r'\bfee|gas|\bstrk\b|token|pay for|cost'),
    ('accounts', r'account|wallet|abstraction|signer|passkey|session key'),
    ('cairo', r'cairo|contract|smart contract|deploy|scarb|build|launch|issue|my own|create'),
    ('module', r'\bapi\b|\bmcp\b|agent|this module|this app|endpoint'),
    ('protocol', r'starknet|rollup|stark|proof|prover|l1|ethereum|sequencer|how does|what is|work|secur|finality|block'),
]

NEEDS = {'mods': ['mods'], 'protocol': ['network'], 'stables': ['stables'], 'privacy': ['pool'],
         'fees': ['network'], 'accounts': [], 'cairo': [], 'module': []}


def topics_of(query):
    q = (query or '').lower()
    hit = [t for t, rx in TOPICS if re.search(rx, q)]
    # "launch my own stablecoin" is a stablecoin question first
    if 'stables' in hit and 'cairo' in hit:
        hit.remove('cairo')
        hit.append('issue')
    # "which starknet mods..." is about the mods, not the rollup
    if 'mods' in hit and 'protocol' in hit:
        hit.remove('protocol')
    return hit[:3] or ['protocol']


def _peg_line(t):
    if t.get('price') is None:
        return f"{t['symbol']}: no oracle price"
    d = t['deviation_bps']
    state = 'on peg' if abs(d) < 25 else 'slightly off peg' if abs(d) < 100 else 'OFF PEG'
    return f"{t['symbol']} ${t['price']:.4f} ({d:+.1f} bps, {state})"


def explain(topic, r):
    n, s, p = r.get('network'), r.get('stables'), r.get('pool')
    if topic == 'mods':
        f = r.get('mods')
        if not f:
            return ''
        rows = '\n'.join(
            f"- **{m['title']}** (`starknet.{m['name']}`, {'live' if m.get('up') else 'down'}) — {m.get('pitch', '')}"
            + (' Now: ' + ', '.join(f'{k} {v}' for k, v in m['facts']) + '.' if m.get('facts') else '')
            for m in f['apps'])
        how = '\n'.join(f'{i}. {x}' for i, x in enumerate(f['how']['steps'], 1))
        return ("**The apps on Starknet on this node.** Every app is a submod of starknet "
                "at `mods/<name>` — some are pages of this app, some are sibling mods with "
                f"their own app ({f['up']} of {f['count']} live right now):\n\n" + rows +
                "\n\n**Make your own app.**\n" + how +
                "\n\nThe APPS tab shows them all with live numbers, and has a + New app card.")
    if topic == 'protocol':
        live = (f" Right now it is at block #{n['block_number']:,}, producing a block "
                f"about every {n['block_time_s']:.1f} seconds ({n['tx_last_12_blocks']} "
                f"transactions in the last 12 blocks).") if n and n.get('block_time_s') else ''
        return ("**What Starknet is.** Starknet is a layer-2 network on top of Ethereum — "
                "a *validity rollup*. Transactions run on Starknet, which is fast and cheap, "
                "and the network then proves to Ethereum that it ran them correctly.\n\n"
                "**How it works, step by step.**\n"
                "1. You sign a transaction with your Starknet account and send it in.\n"
                "2. The *sequencer* orders transactions into blocks and executes them. You get "
                "a soft confirmation in seconds.\n"
                "3. A *prover* (SHARP / Stwo) turns many blocks of execution into one STARK "
                "proof — a short piece of math that is only possible to produce if every step "
                "was computed correctly.\n"
                "4. That proof and the state changes are posted to a contract on Ethereum, "
                "which checks the proof. Once it verifies, the blocks are final with "
                "Ethereum's security — nobody has to trust the sequencer about what happened.\n\n"
                "STARKs need no trusted setup and rest on hash functions only, which is "
                "also why they are considered safe against quantum computers. Programs are "
                "written in **Cairo**, a language built so that its execution is provable." + live)
    if topic == 'stables':
        if not s:
            return ''
        rows = '\n'.join(f"- **{t['symbol']}** — {t['name']}, {t['issuer']}. "
                         f"{_fmt_usd(t['supply'])} supply. {_peg_line(t)}."
                         for t in s['tokens'])
        bm = s['by_model']
        return ("**Stablecoins on Starknet.** A stablecoin is an ordinary token contract "
                "(ERC-20) whose price stays near $1 because something *outside* the "
                "contract stands behind it. On Starknet there are three kinds:\n\n"
                f"- **Native fiat-backed** ({_fmt_usd(bm.get('fiat'))}): {stables.MODELS['fiat']}\n"
                f"- **Bridged** ({_fmt_usd(bm.get('bridged'))}): {stables.MODELS['bridged']}\n"
                f"- **Crypto-backed CDP** ({_fmt_usd(bm.get('cdp'))}): {stables.MODELS['cdp']}\n\n"
                f"Live right now ({_fmt_usd(s['total_supply_usd'])} in total; prices are the "
                "Pragma oracle's on-chain median):\n" + rows + "\n\n"
                "To *have* stablecoins you hold one of these tokens in a Starknet account "
                "(bridge USDC in via CCTP or StarkGate, buy one on a DEX like Ekubo or AVNU, "
                "or borrow CASH from an Opus vault). Fees can be paid in STRK while you hold "
                "only dollars.")
    if topic == 'issue':
        return ("**Launching your own stablecoin.** The token is the easy part — a Cairo "
                "ERC-20 with a `mint` that only a minter can call (the BUILD tab's *token* "
                "template is a start). What makes it *stable* is the rule behind `mint`:\n"
                "1. **Reserve-backed**: you hold $1 off-chain for every token and mint/burn "
                "on deposits and redemptions. Simple, but you are a regulated issuer and "
                "users trust you.\n"
                "2. **Over-collateralised (CDP)**: users lock e.g. 150% of the value in STRK or "
                "ETH and mint against it; a price oracle (Pragma) watches the ratio and anyone "
                "can liquidate a vault that falls below it. This is how Opus CASH and DAI work.\n"
                "3. **Wrapped**: hold USDC in the contract and issue a 1:1 claim on it, adding "
                "yield or rules on top.\n"
                "Either way you need: an oracle, a redemption path, liquidation or reserve "
                "audits, and an upgrade/pause policy — that last part is where most "
                "stablecoins actually fail.")
    if topic == 'privacy':
        live = ''
        if p:
            hold = ', '.join(f'{v:,.2f} {k.upper()}' for k, v in (p.get('public_holdings') or {}).items()
                             if isinstance(v, (int, float)))
            live = (f"\n\nLive: version {p.get('version')}, "
                    f"{'paused' if p.get('paused') else 'accepting deposits'}, holding {hold}. "
                    f"A proof stays valid for {p.get('proof_validity_blocks')} blocks.")
        return ("**The STRK20 privacy pool.** You deposit tokens into one shared contract "
                "and receive *notes* — encrypted IOUs only you can read. Spending a note "
                "publishes a *nullifier* (a fingerprint that marks it used, without saying "
                "which note it was) plus a zero-knowledge proof that you owned a valid, "
                "unspent note. So the chain can check nobody double-spends, yet it cannot "
                "link who paid whom or how much inside the pool. Only the edges are "
                "public: deposits going in, withdrawals coming out, and *helper* "
                "contracts the pool calls to swap or lend privately. Viewing keys stay with "
                "the user — this explorer never asks for one." + live)
    if topic == 'fees':
        return ("**Fees and STRK.** Every transaction pays a fee in **STRK**, Starknet's "
                "native token (ETH is still accepted on older transaction versions). The "
                "fee covers the L2 execution plus your share of posting data and the proof "
                "to Ethereum — that shared proof is why a transfer costs fractions of a "
                "cent. STRK is also staked by validators and used for governance. With "
                "*paymasters* an app can pay your fee, or let you pay it in USDC.")
    if topic == 'accounts':
        return ("**Accounts.** On Starknet every wallet is a smart contract — *native "
                "account abstraction*. That is why one account can use a passkey or a "
                "hardware key instead of a seed phrase, batch several calls into one "
                "transaction, give a game a time-limited *session key*, or have its fees "
                "paid by someone else. Wallets like Ready (Argent) and Braavos deploy "
                "your account contract the first time you transact.")
    if topic == 'cairo':
        return ("**Cairo and contracts.** Contracts are written in Cairo, a Rust-like "
                "language that compiles to provable code. Each contract's ABI is stored "
                "on chain with its class, which is why this explorer can read any "
                "contract without a verified-source step. The BUILD tab writes, builds "
                "and tests Cairo with scarb and snforge.")
    if topic == 'module':
        return ("**This module.** A read-only Starknet explorer with one API: JSON over "
                "HTTP at /starknet/_api/*, 30+ MCP tools at POST /mcp, and this agent on "
                "the fleet agent contract (GET /agents, POST /run/stream). It holds no "
                "keys and never signs.")
    return ''


def _facts(query, net, emit):
    tops = topics_of(query)
    need = []
    for t in tops:
        for r in NEEDS.get('stables' if t == 'issue' else t, []):
            if r not in need:
                need.append(r)
    readings = {}
    for r in need:
        t0 = time.time()
        try:
            data, line = READS[r](net)
            readings[r] = data
            emit({'tool': f'read_{r}', 'params': {'network': net},
                  'result': line, 'ms': int((time.time() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001
            emit({'tool': f'read_{r}', 'params': {'network': net}, 'error': str(e)})
    parts = [explain(t, readings) for t in tops]
    answer = '\n\n'.join(x for x in parts if x)
    others = [e for e in EXAMPLES if not any(re.search(rx, e.lower())
                                             for t, rx in TOPICS if t in tops)][:3]
    if others:
        answer += '\n\nYou can also ask: ' + ' · '.join(f'"{e}"' for e in others)
    return answer, readings


# ── the claude brain ─────────────────────────────────────────────────────────

_ready = {'at': 0.0, 'ok': False}


def claude_ready():
    if not CLAUDE:
        return False
    if time.time() - _ready['at'] < 30:
        return _ready['ok']
    try:
        with urllib.request.urlopen(f'{CLAUDE}/agents', timeout=3) as r:
            ok = any(a.get('available') for a in json.load(r).get('agents', []))
    except Exception:  # noqa: BLE001
        ok = False
    _ready.update(at=time.time(), ok=ok)
    return ok


def _goal(net, readings):
    api = f'http://127.0.0.1:{PORT}'
    return (
        'You are the Starknet guide inside the starknet explorer module. Explain the '
        'Starknet protocol to a newcomer: plain English, short paragraphs, concrete '
        'numbers, no hype. Ground answers in the live readings below and say when a '
        'number is live. If you need more data, use curl against this module\'s '
        f'read-only API ({api}/<route>?network={net}; routes: /status /blocks /block '
        '/tx /account /balance /contract /read /events /stablecoins /strk20/pool '
        '/strk20/activity /strk20/docs?q=). Never modify files, never sign or send '
        'anything, never ask for keys. Never mention connectors, MCP servers or tools '
        'you did not use. End with the answer, not a summary of tools.\n\n'
        f'Live readings ({net}, {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}):\n'
        + json.dumps(readings, default=str)[:12000])


def _claude(query, net, model, history, emit):
    readings = {}
    for r in ('network', 'stables'):
        try:
            readings[r], line = READS[r](net)
            emit({'tool': f'read_{r}', 'params': {'network': net}, 'result': line})
        except Exception as e:  # noqa: BLE001
            emit({'tool': f'read_{r}', 'params': {'network': net}, 'error': str(e)})
    prompt = query
    if history:
        convo = '\n'.join(f"{m.get('role', 'user')}: {m.get('content', '')}"
                          for m in history[-8:] if isinstance(m, dict))
        prompt = f'Conversation so far:\n{convo}\n\nNow: {query}'
    body = json.dumps({'query': prompt, 'goal': _goal(net, readings),
                       'model': model, 'timeout': CLAUDE_TIMEOUT}).encode()
    req = urllib.request.Request(f'{CLAUDE}/run/stream', data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    answer, usage = None, None
    with urllib.request.urlopen(req, timeout=CLAUDE_TIMEOUT + 30) as r:
        for raw in r:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            ev = json.loads(line[5:])
            if ev.get('type') == 'step':
                st = ev['step']
                if st.get('tool') == 'finish':
                    answer = st.get('params', {}).get('summary')
                    usage = st.get('params', {}).get('usage')
                    continue
                if st.get('tool') == 'error':
                    raise RuntimeError(st.get('error') or 'claude failed')
                emit(st)
            elif ev.get('type') == 'error':
                raise RuntimeError(ev.get('error') or 'claude failed')
            elif ev.get('type') == 'done':
                usage = usage or ev.get('usage')
    if not answer:
        raise RuntimeError('claude returned no answer')
    return answer, usage


# ── the contract ─────────────────────────────────────────────────────────────

def brains(writable=False):
    return {'facts': True, 'claude': bool(writable and claude_ready()),
            'claude_installed': claude_ready()}


def agents():
    return {'agents': [{'id': k, **v, 'provider': 'starknet', 'local': True,
                        'free': True, 'examples': EXAMPLES} for k, v in AGENTS.items()],
            'default': DEFAULT_AGENT}


def agent(name=None):
    key = name or DEFAULT_AGENT
    if key not in AGENTS:
        raise KeyError(f'no agent {key!r} — have {list(AGENTS)}')
    return {'id': key, **AGENTS[key], 'examples': EXAMPLES,
            'brains': ['facts', 'claude'], 'topics': [t for t, _ in TOPICS]}


def card(base_url=''):
    return {'protocol': 'agent/1.0', 'name': 'starknet',
            'description': AGENTS[DEFAULT_AGENT]['description'],
            'agents': agents()['agents'],
            'endpoints': {'agents': f'{base_url}/agents', 'run': f'{base_url}/run',
                          'stream': f'{base_url}/run/stream', 'mcp': f'{base_url}/mcp'},
            'events': ['step', 'done', 'error'],
            'auth': 'open; the claude brain needs an owner token or a loopback call'}


def run_stream(query, agent=None, brain='auto', network=None, model=None,
               history=None, writable=False, **_ignored):
    """Yield the run's events; the last is done or error."""
    key = agent or DEFAULT_AGENT
    if key not in AGENTS:
        yield {'type': 'error', 'error': f'no agent {key!r} — have {list(AGENTS)}'}
        return
    if not str(query or '').strip():
        yield {'type': 'error', 'error': 'query is required'}
        return
    net = network or chain.DEFAULT_NETWORK
    can_claude = writable and claude_ready()
    use = 'claude' if (brain == 'claude' or brain == 'auto') and can_claude else 'facts'
    note = None
    if brain == 'claude' and not can_claude:
        note = ('claude is owner-only (sign in with the owner wallet)' if not writable
                else 'orbit/claudecode is not answering') + ' — answered from live facts instead'
    q, steps, box = queue.Queue(), [], {}
    t0 = time.time()

    def emit(step):
        steps.append(step)
        q.put({'type': 'step', 'step': step})

    def work():
        nonlocal use, note
        try:
            if note:
                emit({'tool': 'response', 'params': {}, 'result': note})
            if use == 'claude':
                try:
                    box['answer'], box['usage'] = _claude(query, net, model, history, emit)
                except Exception as e:  # noqa: BLE001
                    use, note = 'facts', f'claude failed ({e}) — answered from live facts instead'
                    emit({'tool': 'response', 'params': {}, 'result': note})
            if use == 'facts':
                box['answer'], _ = _facts(query, net, emit)
        except Exception as e:  # noqa: BLE001
            box['error'] = f'{type(e).__name__}: {e}'
        finally:
            q.put(None)

    threading.Thread(target=work, daemon=True).start()
    while True:
        try:
            ev = q.get(timeout=15)
        except queue.Empty:
            yield {'type': 'ping'}
            continue
        if ev is None:
            break
        yield ev
    if 'error' in box:
        yield {'type': 'error', 'error': box['error']}
        return
    fin = {'tool': 'finish', 'params': {'summary': box['answer'],
                                        **({'usage': box['usage']} if box.get('usage') else {})}}
    steps.append(fin)
    yield {'type': 'step', 'step': fin}
    yield {'type': 'done', 'result': steps, 'answer': box['answer'], 'brain': use,
           'note': note, 'agent': key, 'network': net,
           'ms': int((time.time() - t0) * 1000), 'charged': None}


def run(query, **kw):
    done = None
    for ev in run_stream(query, **kw):
        if ev['type'] in ('done', 'error'):
            done = ev
    if not done or done['type'] == 'error':
        raise RuntimeError((done or {}).get('error') or 'run produced no answer')
    return {k: v for k, v in done.items() if k != 'type'}
