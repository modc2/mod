#!/usr/bin/env python3
"""postquant agent — the chain as something you can ASK, on the fleet's agent
contract, stdlib only.

    GET  /agents             {"agents": [...]} — the shape orbit/build probes
                             before it mounts a module as an agent backend
    GET  /agents/{id}        one agent in full
    POST /run                one run, JSON answer
    POST /run/stream         SSE: model_start, token, tool_start, step, done|error
    GET  /.well-known/agent.json   the agent/1.0 card

The event names are orbit/agent's on purpose: build's job renderer already
draws them, so a postquant run in that console looks like every other job.

THREE BRAINS, ONE DOOR
    Every tool call an agent makes goes through mcp.call_tool() — the same
    function REST and MCP call — so the agent can never see a different chain
    or skip a check a person would hit.

    rules  (default, always on)  a plain-English command parser. No model, no
           network, no key, no cost: "set hello to world for 2 days", "send 5
           to pq…", "quote avatar", "balance", "block 12". Understands several
           commands joined by "then" / ";" / new lines. It is what makes the
           agent work on a box with nothing else installed.
    chat   (on when the liquidai module is)  conversation, with the rules
           brain as its hands. Commands in the message are parsed and
           executed by rules first — deterministic, gated, free — then the
           LFM resident in liquidai's server runtime answers in plain
           language, grounded in those results and a live chain context, and
           its tokens stream as they are generated. liquidai's /v1 face has
           no tool calling, and a 1.2B model should not be trusted to invent
           transactions anyway — so the model never picks tools or amounts;
           it only ever talks about what the rules brain actually did. If
           liquidai is down the answer degrades to the rules summary, out
           loud. See lfm.py for how it authenticates without a wallet.
    llm    (optional)  any OpenAI-compatible /chat/completions endpoint with
           tool calling — a local ollama or llama-server first of all. Set
           POSTQUANT_AGENT_LLM=http://127.0.0.1:11434/v1 (+ _MODEL, optional
           _KEY or ~/.mod/postquant/agent.key). If the endpoint fails the run
           falls back to rules and says so; it never silently goes elsewhere.

WRITES FOLLOW THE REST GATE
    A write spends PQ. The HTTP layer decides `can_write` with exactly the
    rule the write routes use (server.secret bearer, or open when there is no
    secret). A run that may not write still answers: writes that can be priced
    are re-run as dry_run quotes, the rest are refused with the reason. The
    `pq-reader` agent never writes, whoever asks.
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

import lfm                                                      # noqa: E402
import mcp as mcpsrv                                            # noqa: E402
from state import StateError                                    # noqa: E402

LLM_URL = os.environ.get('POSTQUANT_AGENT_LLM', '').rstrip('/')
LLM_MODEL = os.environ.get('POSTQUANT_AGENT_MODEL', 'qwen2.5:7b')
LLM_TIMEOUT = float(os.environ.get('POSTQUANT_AGENT_TIMEOUT', 120))
KEY_FILE = os.path.join(os.path.expanduser(
    os.environ.get('POSTQUANT_DATA_DIR', '~/.mod/postquant')), 'agent.key')
MAX_STEPS = 12

AGENTS = {
    'pq': {
        'name': 'postquant operator',
        'description': 'Reads the chain and, when the caller may write, acts '
                       'on it: wallets, faucet, set/quote/fund keys, '
                       'transfers, the key market, mining, verification.',
        'writes': True,
    },
    'pq-reader': {
        'name': 'postquant reader',
        'description': 'Answers questions about the chain and prices writes '
                       'as dry runs. Never signs anything, whoever asks.',
        'writes': False,
    },
}
DEFAULT_AGENT = 'pq'

# pq_wallet list/show read the keystore; every other action changes it.
_WALLET_READS = ('list', 'ls', 'show', 'get')

EXAMPLES = [
    'status',
    'wallets',
    'create wallet alice with SLH-DSA-SHAKE-128f',
    'faucet 100 to alice',
    'quote greeting = hello world for 2 days',
    'set greeting to hello world for 2 days',
    'get greeting',
    'send 5 to bob from alice',
    'keys starting with gree',
    'list greeting for 20',
    'market',
    'verify',
]


def _is_write(tool, args):
    if tool == 'pq_wallet':
        return (args.get('action') or 'list').lower() not in _WALLET_READS
    return tool in mcpsrv.WRITE_TOOLS


def _can_dry_run(tool):
    props = mcpsrv.TOOLS.get(tool, {}).get('inputSchema', {}).get('properties', {})
    return 'dry_run' in props


# ── the rules brain ───────────────────────────────────────────────

_NUM = r'(\d+(?:\.\d+)?)'
_ADDR = re.compile(r'^pq[0-9a-f]{40}$')
_HASH = re.compile(r'^(?:0x)?[0-9a-f]{64}$')


def _clean(tok):
    return (tok or '').strip().strip('"\'`').strip()


def _who(tok):
    """A name or an address → the arg the tools take for it."""
    tok = _clean(tok)
    if not tok:
        return {}
    return {'address': tok} if _ADDR.match(tok.lower()) else {'wallet': tok}


def _lease(text):
    """'for 2 days' / 'for 3 hours' → args, and the text without it."""
    m = re.search(r'\s+for\s+' + _NUM + r'\s*(d|day|days|h|hr|hrs|hour|hours)\b',
                  text, re.I)
    if not m:
        return {}, text
    unit = 'days' if m.group(2).lower().startswith('d') else 'hours'
    return {unit: m.group(1)}, (text[:m.start()] + text[m.end():]).strip()


def _from(text):
    m = re.search(r'\s+(?:from|as|using wallet|with wallet)\s+(\S+)\s*$', text, re.I)
    if not m:
        return {}, text
    return {'wallet': _clean(m.group(1))}, text[:m.start()].strip()


def _kv(rest):
    """'k to v' | 'k = v' | 'k as v' | 'k v…' → (key, value or None)."""
    m = re.match(r'^(\S+)\s*(?:=|:=|\bto\b|\bas\b)\s*(.+)$', rest, re.I | re.S)
    if m:
        return _clean(m.group(1)), _clean(m.group(2))
    parts = rest.split(None, 1)
    if not parts:
        return None, None
    return _clean(parts[0]), (_clean(parts[1]) if len(parts) > 1 else None)


def _value_args(value, raw=False):
    """Text stored as its hash (what the chain is priced for) unless the
    caller asked for raw bytes."""
    if value is None:
        return {}
    if raw:
        return {'value': value, 'value_kind': 'raw'}
    return {'data': value}


def _p_set(m, text):
    lease, text = _lease(text)
    who, text = _from(text)
    body = re.sub(r'^\s*(?:set|write|store|put|save)\s+(?:key\s+)?', '', text, flags=re.I)
    raw = bool(re.search(r'\braw\b', body, re.I))
    body = re.sub(r'\s*\(?\braw\b\)?\s*$', '', body, flags=re.I)
    key, value = _kv(body)
    if not key:
        return None
    return 'pq_set', {'key': key, **_value_args(value, raw), **lease, **who}


def _p_quote(m, text):
    lease, text = _lease(text)
    who, text = _from(text)
    body = re.sub(r'^\s*(?:quote|price|how much (?:does it cost |would it cost |is it )?'
                  r'(?:to )?(?:store|set|write|put)?)\s*(?:key\s+)?', '', text, flags=re.I)
    body = body.rstrip('?').strip()
    key, value = _kv(body)
    if not key:
        return None
    return 'pq_quote', {'key': key, **_value_args(value), **lease, **who}


def _p_transfer(m, text):
    who, _ = _from(text)
    return 'pq_transfer', {'amount': m.group(1), 'to': _clean(m.group(2)), **who}


def _p_faucet(m, text):
    amt = re.search(_NUM, text)
    to = re.search(r'\b(?:to|for)\s+(\S+)', text, re.I)
    args = {'amount': amt.group(1)} if amt else {}
    if to:
        args.update(_who(to.group(1)))
    return 'pq_faucet', args


def _p_fund(m, text):
    lease, text = _lease(text)
    who, text = _from(text)
    args = {'key': _clean(m.group(1)), **lease, **who}
    dep = re.search(r'\bwith\s+' + _NUM, text, re.I)
    if dep:
        args['deposit'] = dep.group(1)
    return 'pq_fund', args


def _p_wallet_create(m, text):
    args = {'action': 'create', 'name': _clean(m.group(1))}
    s = re.search(r'\b(?:with|using|scheme)\s+([A-Za-z0-9\-]+)', text, re.I)
    if s:
        args['scheme'] = _scheme(s.group(1))
    return 'pq_wallet', args


def _scheme(tok):
    """'slh-dsa-shake-128f' / 'mldsa65' → the registry's exact name."""
    squash = lambda x: re.sub(r'[^a-z0-9]', '', x.lower())
    for name in mcpsrv.ALGOS.names():
        if squash(name) == squash(tok):
            return name
    return tok


def _p_account(m, text):
    return 'pq_account', _who(m.group(1) or '')


def _p_keys(m, text):
    p = re.search(r'\b(?:prefix|starting with|under|beginning with|like)\s+(\S+)', text, re.I)
    return 'pq_keys', ({'prefix': _clean(p.group(1))} if p else {})


def _p_block(m, text):
    n = re.search(r'\d+', text)
    return 'pq_block', ({'block': n.group(0)} if n else {})


def _p_history(m, text):
    t = re.search(r'\b(?:of|for)\s+(\S+)', text, re.I)
    if not t:
        return 'pq_history', {}
    tok = _clean(t.group(1))
    if _ADDR.match(tok.lower()):
        return 'pq_history', {'address': tok}
    return 'pq_history', {'key': tok}


def _k(tool, **extra):
    """A parser for '<verb> <key>' commands."""
    return lambda m, text: (tool, {'key': _clean(m.group(1)), **extra,
                                   **_from(text)[0]})


# ── the whitepaper, one topic at a time ───────────────────────────
# "why no curves?" deserves an answer, not "I did not understand".
# Same facts as the console's whitepaper tab; the corner helper there
# leans on these. Order matters: the first alias pattern that matches
# the clause wins, so the specific schemes come before 'curves'.
GLOSSARY = {
    'mldsa': (r'\bml[- ]?dsa\b|\bdilithium\b|\blattices?\b',
        'ML-DSA (FIPS 204) is the lattice signature family — sets 44, 65 and '
        '87, picked per wallet. An ML-DSA-44 signature is 2420 bytes, and the '
        'chain bills witness gas for exactly those bytes.'),
    'slhdsa': (r'\bslh[- ]?dsa\b|\bsphincs\b|hash[- ]based',
        'SLH-DSA-SHAKE (FIPS 205) is the hash-based signature family — it '
        'rests on nothing but SHAKE256, so it stands even if lattices fall. '
        'The price of that caution is size: a 128f signature is 17088 bytes, '
        'billed as witness gas like any other.'),
    'curves': (r'curves?\b|quantum|shor|grover|ed25519|secp256k1|ecdsa',
        'Elliptic curves are exactly what Shor\'s algorithm takes apart, so '
        'nothing in this chain\'s trust path uses one. Signatures are ML-DSA '
        '(lattices) or SLH-DSA (hashes only), key exchange is ML-KEM, and '
        'every commitment is SHA3-256 — Grover only halves a hash\'s '
        'exponent, which 256 bits absorbs. Classical key types can still '
        'register: the gate prices them by measured break cost instead of '
        'banning them (ask for "complexity" for how).'),
    'complexity': (r'complexity|gate\b|2\^|min[_ ]?bits|entropy probe',
        'The complexity gate prices key types instead of banning them: any '
        'scheme witnesses if its best-known classical attack clears the '
        '2^100 floor and, for plugins, an entropy probe can\'t refute the '
        'keyspace. quantum_safe=false on the catalog means a quantum '
        'computer breaks it in polynomial time — printed, not hidden.'),
    'wasm': (r'\bwasm\b|verifier|key ?types?\b|plugin',
        'A key type here is a compiled wasm verifier plus the SHA3-256 of '
        'its bytes, pinned in a manifest. Every witness is checked inside '
        'that exact blob — wrong hash, no run; no run, no witness. A new '
        'key type is one plugin file plus one blob, not a fork.'),
    'rent': (r'\brent\b|escrow|expir|lease\b',
        'Every byte in the store pays rent per byte-hour out of a prepaid '
        'escrow. When the escrow runs dry the entry expires and anyone may '
        'sweep it for the bond the writer put up — that bounty is what makes '
        'expiry real. Delete the entry yourself and the bond comes back '
        'whole. Extend a lease with fund.'),
    'witness': (r'witness|signature size|sig bytes',
        'Witness gas bills each transaction for the actual bytes of its '
        'signature and public key — 2420 for ML-DSA-44, 17088 for '
        'SLH-DSA-128f — so every key type pays its honest wire price '
        'instead of being quietly subsidised by a flat fee.'),
    'gas': (r'\bgas\b|write cost|cost of a write|base ?fee|1559|fee market',
        'Write gas is one-time, per byte entering the state: a key byte '
        'costs 4x a value byte, and a kind=hash value is cheaper than a raw '
        'blob. It settles at a base fee that floats EIP-1559-style against '
        'a target of 4096 bytes of state growth per block (±12.5%); the '
        'base fee is burned. Use quote to price a write before signing.'),
    'sweep': (r'sweep|bond\b',
        'Every write posts a small bond. When an entry\'s rent escrow runs '
        'dry it expires, and the first sweeper collects that bond for '
        'clearing the dead entry — garbage collection as a market.'),
    'market': (r'buy a key|sell a key|key market|listings?\b|namespace',
        'Keys can be listed and bought. The buyer takes the entry with its '
        'remaining lease and bond, so a well-funded key is worth more than '
        'an empty one. Say "market" to see what is for sale now.'),
    'consensus': (r'consensus|proposer|authority|replay|verify|light client|merkle',
        'Consensus is a single proposer — authority, not agreement, and the '
        'whitepaper says so out loud. What keeps it honest: the ordered log '
        'is the truth, execution is deterministic, and "verify" replays '
        'every block from genesis, re-checking every hash, root and witness. '
        'A Merkle proof (prove <key>) ties any entry to the state root.'),
    'address': (r'address(es)?\b',
        'An address is pq + the first 20 bytes of domain-separated SHA3-256 '
        'over the key type and public key — the address commits to the '
        'algorithm, so the same key bytes under two schemes are two '
        'different addresses and a witness can never cross key types.'),
    'units': (r'\bpq\b|\bnq\b|units?\b|denominat',
        '1 PQ = 1e9 nq. In every API an amount given as a string ("1.5") is '
        'PQ, the display unit; a bare integer is nq, the base unit. Nothing '
        'is ever guessed from magnitude.'),
    'postquant': (r'postquant|this chain|whitepaper|what can i own',
        'postquant is a post-quantum L1 whose entire state machine is a '
        'market in key/value space. One thing to own — a key in the store — '
        'and three separate prices for holding it: write gas per byte, '
        'witness gas per byte of signature, rent per byte-hour. No elliptic '
        'curve anywhere in the trust path.'),
}


def _p_explain(m, text):
    for name, (rx, _ans) in GLOSSARY.items():
        if re.search(rx, text, re.I):
            return ('explain', {'topic': name})
    return None    # not a paper topic — fall through to the other rules


# Order matters: the more specific verb wins. Each pattern sees one clause.
RULES = [
    (r'^(?:help|\?|what can you do|commands)\b', lambda m, t: ('help', {})),
    (r'^(?:quote|price)\b|^how much\b', _p_quote),
    # questions about the protocol answer from the glossary; a clause that
    # looks like a question but names no paper topic falls through (None)
    (r'^(?:explain|why|what|how|is|are|does|do|tell me about|who)\b', _p_explain),
    (r'^(?:set|write|store|put|save)\s+\S+', _p_set),
    (r'^(?:send|transfer|pay|give)\s+' + _NUM + r'\s*(?:pq)?\s+to\s+(\S+)', _p_transfer),
    (r'^(?:faucet|drip|airdrop)\b|^fund me\b|^give me\b', _p_faucet),
    (r'^(?:create|new|make|add)\s+(?:a\s+)?wallet\s+(?:called\s+|named\s+)?(\S+)', _p_wallet_create),
    (r'^(?:use|switch to)\s+wallet\s+(\S+)', lambda m, t: ('pq_wallet', {'action': 'use', 'name': _clean(m.group(1))})),
    (r'^(?:show|get)\s+wallet\s+(\S+)', lambda m, t: ('pq_wallet', {'action': 'show', 'name': _clean(m.group(1))})),
    (r'^(?:my\s+|list\s+|show\s+)?wallets?\b', lambda m, t: ('pq_wallet', {'action': 'list'})),
    (r'^fund\s+(\S+)', _p_fund),
    (r'^(?:delete|del|remove|drop)\s+(?:key\s+)?(\S+)', _k('pq_del')),
    (r'^sweep\s+(?:key\s+)?(\S+)', _k('pq_sweep')),
    (r'^(?:list|sell|offer)\s+(\S+)\s+(?:for|at)\s+' + _NUM,
     lambda m, t: ('pq_list', {'key': _clean(m.group(1)), 'price': m.group(2), **_from(t)[0]})),
    (r'^buy\s+(\S+)(?:\s+(?:for|max|up to)\s+' + _NUM + r')?',
     lambda m, t: ('pq_buy', {'key': _clean(m.group(1)),
                              **({'max_price': m.group(2)} if m.group(2) else {}),
                              **_from(t)[0]})),
    (r'^prove\s+(?:key\s+)?(\S+)', _k('pq_prove')),
    (r'^check\s+(\S+)\s*(?:=|against|matches|is)\s*(.+)$',
     lambda m, t: ('pq_check', {'key': _clean(m.group(1)), 'data': _clean(m.group(2))})),
    (r'^(?:market|listings|for sale|what(?:\'s| is) for sale)\b', lambda m, t: ('pq_market', {})),
    (r'^(?:algos|algorithms|key ?types|schemes)\b', lambda m, t: ('pq_algos', {})),
    (r'^(?:mempool|pending)\b', lambda m, t: ('pq_mempool', {})),
    (r'^(?:mine|produce a block|make a block)\b',
     lambda m, t: ('pq_mine', {'force': bool(re.search(r'\b(?:force|empty)\b', t, re.I))})),
    (r'^(?:verify|audit|replay)\b',
     lambda m, t: ('pq_verify', {'signatures': bool(re.search(r'\b(?:sig|signature|full|witness)', t, re.I))})),
    (r'^(?:balance|account)(?:\s+(?:of|for))?(?:\s+(\S+))?', _p_account),
    (r'^(?:keys|list keys|show keys|what keys|all keys)\b', _p_keys),
    (r'^(?:latest\s+)?block\b', _p_block),
    (r'^(?:tx|transaction)\s+(\S+)', lambda m, t: ('pq_tx', {'hash': _clean(m.group(1))})),
    (r'^history\b', _p_history),
    (r'^(?:head|tip|status|height|chain|info)\b', lambda m, t: ('pq_head', {})),
    (r'^(?:get|read|show|lookup|look up|what is|what\'s)\s+(?:key\s+)?(\S+?)\??$', _k('pq_get')),
]
RULES = [(re.compile(p, re.I), fn) for p, fn in RULES]

_SPLIT = re.compile(r'\s*(?:;|\n|\band then\b|\bthen\b)\s*', re.I)


def parse(query):
    """Text → [(tool, args, clause)]. A clause nothing understands comes back
    as ('unknown', {}, clause) so the answer can say which part it skipped."""
    plan = []
    for clause in _SPLIT.split(query or ''):
        clause = clause.strip().rstrip('.!')
        if not clause:
            continue
        low = re.sub(r'^(?:please|pls|can you|could you|i want to|let\'s|lets)\s+', '',
                     clause, flags=re.I)
        dry = bool(re.search(r'\b(?:dry[ -]?run|preview|simulate)\b', low, re.I))
        low = re.sub(r'\s*\(?\b(?:dry[ -]?run|preview|simulate)\b\)?\s*', ' ', low,
                     flags=re.I).strip()
        # a bare address or tx hash means "look it up"
        if _ADDR.match(low.lower()):
            plan.append(('pq_account', {'address': low}, clause))
            continue
        if _HASH.match(low.lower()):
            plan.append(('pq_tx', {'hash': low}, clause))
            continue
        for rx, fn in RULES:
            m = rx.search(low)
            if m:
                step = fn(m, low)
                if step:
                    tool, args = step
                    if dry and _can_dry_run(tool):
                        args['dry_run'] = True
                    plan.append((tool, args, clause))
                    break
        else:
            plan.append(('unknown', {}, clause))
    return plan


# ── summaries: a person reads the answer, not the JSON ────────────

def _pq(v):
    """{'nq': .., 'pq': '1.5'} → '1.5 PQ'; anything else as-is."""
    if isinstance(v, dict) and 'pq' in v:
        return f"{v['pq'].rstrip('0').rstrip('.') or '0'} PQ"
    return '?' if v is None else str(v)


def _cost(q):
    """A quote (or a dry run's quote) → 'X PQ (write Y + deposit Z)'."""
    total = q.get('total')
    if total is None and isinstance(q.get('write_cost'), dict):
        total = {'pq': f"{(q['write_cost']['nq'] + (q.get('deposit') or {}).get('nq', 0)) / 1e9:.9f}"}
    parts = [f"write {_pq(q['write_cost'])}"] if q.get('write_cost') else []
    if q.get('deposit'):
        parts.append(f"refundable deposit {_pq(q['deposit'])}")
    return _pq(total) + (f" ({' + '.join(parts)})" if parts else '')


def summarize(tool, args, out):
    """One line a person can read. Falls back to the first few fields."""
    if not isinstance(out, dict):
        return json.dumps(out, default=str)[:300]
    try:
        if out.get('dry_run'):
            return (f"dry run {out.get('kind', tool[3:])} from {out.get('from')} "
                    f"would cost {_cost(out.get('quote') or {})} — nothing was signed")
        if tool == 'pq_head':
            return (f"height {out.get('height')} on {out.get('chain_id')}, base fee "
                    f"{_pq(out.get('base_fee'))}/gas, supply {_pq(out.get('supply'))}, "
                    f"state root {str(out.get('state_root', ''))[:16]}…")
        if tool == 'pq_wallet' and 'wallets' in out:
            ws = out['wallets']
            if not ws:
                return 'no wallets yet — try "create wallet alice"'
            return 'wallets: ' + '; '.join(
                f"{w['name']} {w['address']} ({w.get('scheme')}) {_pq(w.get('balance'))}"
                for w in ws)
        if tool == 'pq_wallet' and out.get('address'):
            return f"wallet {out.get('name')} = {out['address']} ({out.get('scheme')})"
        if tool == 'pq_quote':
            return (f"storing {out.get('key')} costs {_cost(out)}, lease "
                    f"{(out.get('rent') or {}).get('seconds', '?')}s, "
                    f"{(out.get('witness') or {}).get('scheme', '')} witness")
        if tool == 'pq_get':
            return (f"{out.get('key', args.get('key'))} = {out.get('value')} "
                    f"(owner {out.get('owner')}, expires in {out.get('expires_in', out.get('expires'))}s)")
        if tool == 'pq_account':
            return f"{out.get('address')} balance {_pq(out.get('balance'))}, nonce {out.get('nonce')}"
        if tool == 'pq_keys':
            ks = out.get('keys') or out.get('entries') or []
            names = [k.get('key') if isinstance(k, dict) else k for k in ks]
            return f"{len(names)} keys: " + ', '.join(map(str, names[:20])) + \
                ('…' if len(names) > 20 else '')
        if tool == 'pq_market':
            return (f"base fee {_pq(out.get('base_fee'))}/gas, {out.get('keys')} keys "
                    f"({out.get('state_bytes')} bytes), {len(out.get('listings') or [])} "
                    f"listed, {len(out.get('expiring_soon') or [])} expiring soon, burned "
                    f"{_pq(out.get('burned'))}")
        if tool == 'pq_verify':
            return f"verify: {'OK' if out.get('ok') else 'FAILED'} — " + \
                json.dumps({k: v for k, v in out.items() if not isinstance(v, (list, dict))},
                           default=str)[:300]
        if out.get('receipt') or out.get('tx'):
            r = out.get('receipt') or {}
            block = out.get('block')
            return (f"{tool[3:]} {r.get('status', out.get('status', 'submitted'))}"
                    f" — tx {str(out.get('tx', out.get('hash', '')))[:16]}…"
                    + (f", block {block.get('height')}" if isinstance(block, dict) else '')
                    + (f", fee {_pq(r.get('fee'))}" if r.get('fee') else ''))
    except Exception:                                   # noqa: BLE001
        pass
    small = {k: v for k, v in out.items() if not isinstance(v, (list, dict))}
    return json.dumps(small or out, default=str)[:400]


def help_text():
    return ('I can drive this chain for you. Try: ' +
            ' · '.join(f'"{e}"' for e in EXAMPLES) +
            '. Chain several with "then". Add "dry run" to price a write '
            'without signing it.')


# ── running one tool, gated ───────────────────────────────────────

def _call(tool, args, can_write):
    """Run one tool through the shared door. Returns (result, note)."""
    if tool not in mcpsrv.TOOLS:
        raise StateError(f'no tool {tool!r}', code='no_tool', status=404)
    if _is_write(tool, args) and not can_write and not args.get('dry_run'):
        if _can_dry_run(tool):
            args = {**args, 'dry_run': True}
            return (mcpsrv.call_tool(tool, args),
                    'this caller may not write here, so it ran as a dry run — '
                    'nothing was signed')
        raise StateError(f'{tool} spends PQ or changes the keystore and this '
                         'caller may not write — send the bearer from '
                         '~/.mod/postquant/server.secret',
                         code='write_denied', status=401)
    return mcpsrv.call_tool(tool, args), None


def _err(e):
    if isinstance(e, StateError):
        return e.dict().get('error') or str(e)
    return f'{type(e).__name__}: {e}'


def _exec_step(tool, args, can_write, emit, i=0, n=1):
    """Run one parsed step through the shared gate, emit its events, and
    return (step_record, one_line_summary) — the rules brain's loop body,
    shared with the chat brain so a fallback can never re-sign anything."""
    emit({'type': 'tool_start', 'tool': tool, 'params': args, 'i': i, 'n': n})
    step = {'tool': tool, 'params': args}
    t0 = time.time()
    try:
        out, note = _call(tool, dict(args), can_write)
        step['result'] = out
        line = summarize(tool, args, out)
        if note:
            step['note'] = note
            line += f' ({note})'
    except Exception as e:                              # noqa: BLE001
        step['error'] = _err(e)
        line = f'{tool[3:]} failed: {step["error"]}'
    step['ms'] = int((time.time() - t0) * 1000)
    emit({'type': 'step', 'step': step})
    return step, line


def _run_rules(query, can_write, emit):
    emit({'type': 'model_start', 'step': 0, 'model': 'rules', 'brain': 'rules'})
    plan = parse(query)
    lines, steps = [], []
    if not plan:
        plan = [('help', {}, query)]
    for i, (tool, args, clause) in enumerate(plan):
        if tool == 'help':
            lines.append(help_text())
            continue
        if tool == 'unknown':
            lines.append(f'I did not understand "{clause}". ' + help_text())
            continue
        if tool == 'explain':
            lines.append(GLOSSARY[args['topic']][1])
            continue
        step, line = _exec_step(tool, args, can_write, emit, i, len(plan))
        lines.append(line)
        steps.append(step)
    answer = '\n'.join(lines)
    emit({'type': 'token', 'text': answer})
    return answer, steps


# ── the chat brain (liquidai's LFM talks, rules act) ──────────────

CHAT_PERSONA = (
    "You are the console voice of postquant, a post-quantum L1 whose whole "
    "state machine is a key/value market. The one thing to own is a key in "
    "the store, and the protocol is its market: write gas per byte (a key "
    "byte costs 4x a value byte), witness gas per signature byte (ML-DSA-44 "
    "2420 bytes, SLH-DSA 17088 — hash-based honesty is billed as-is), and "
    "rent per byte-hour from a prepaid escrow; expired entries are sweepable "
    "for their bond. Signatures are ML-DSA or SLH-DSA, every commitment is "
    "SHA3-256, no elliptic curves. Units: 1 PQ = 1e9 nq.\n"
    "Answer in 2-4 plain short sentences unless asked for more. Never "
    "invent numbers, hashes or balances.")

# What the model may claim depends on whether anything ran: a 1.2B model
# told flatly "you cannot run anything" will deny a transaction that is
# already in a block, so the two situations get two different footers.
_CHAT_DID = (
    "\nThe console has ALREADY EXECUTED the user's request before you were "
    "asked to speak. These operations ran and their results are ground "
    "truth — confirm what happened, quoting the real numbers; do not say "
    "you cannot do it, because it is done:\n")
_CHAT_DIDNT = (
    "\nNo operation was executed for this message, and you cannot run any "
    "yourself. If the user wants an action, give them the exact command to "
    "send, like \"set greeting to hello for 2 days\", \"faucet 100 to "
    "alice\", \"quote avatar = <data> for 1 day\", \"send 5 to bob\", "
    "\"market\", \"verify\". Commands chain with \"then\"; \"dry run\" "
    "prices a write without signing.")


def _ctx_lines(query, already_ran=()):
    """A few grounding lines of live chain state, picked by what the message
    is about — reads only, each already a summarize() one-liner. Tools the
    message just executed are skipped: their results are in the prompt."""
    low = (query or '').lower()
    want = [('pq_head', {}, True),
            ('pq_market', {}, bool(re.search(
                r'market|sale|listing|price|fee|rent|burn|cost', low))),
            ('pq_algos', {}, bool(re.search(
                r'algo|scheme|key ?type|quantum|dsa|slh|signature|wasm', low))),
            ('pq_wallet', {'action': 'list'}, bool(re.search(
                r'wallet|balance|account|faucet|afford', low)))]
    lines = []
    for tool, args, wanted in want:
        if not wanted or tool in already_ran:
            continue
        try:
            lines.append(summarize(tool, args, mcpsrv.call_tool(tool, args)))
        except Exception:                               # noqa: BLE001
            continue
    return lines


def _run_chat(query, can_write, emit, history=None):
    """Execute what rules understands, then let the LFM answer. Returns
    (answer, steps, fallback_note) — the fallback never re-runs a step."""
    plan = [(t, a, c) for t, a, c in parse(query)
            if t not in ('help', 'unknown', 'explain')]
    # Glossary grounding scans the WHOLE message, not just the clauses the
    # parser ceded to 'explain' — "status, and why is SLH-DSA so expensive?"
    # matches a command rule first, but the model still needs the witness
    # note to answer the second half without inventing FIPS numbers.
    gloss = [ans for _, (rx, ans) in GLOSSARY.items()
             if re.search(rx, query or '', re.I)][:3]
    steps, lines = [], []
    for i, (tool, args, clause) in enumerate(plan):
        step, line = _exec_step(tool, args, can_write, emit, i, len(plan))
        steps.append(step)
        lines.append(line)
    system = CHAT_PERSONA
    ctx = _ctx_lines(query, {s['tool'] for s in steps})
    if gloss:
        system += ('\nProtocol notes that answer this message (restate in '
                   'your own words, do not contradict):\n' +
                   '\n'.join('- ' + g for g in gloss))
    if ctx:
        system += '\nLive chain state:\n' + '\n'.join('- ' + c for c in ctx)
    # The DID/DIDN'T footer rides in a second system message AFTER the user
    # turn: a 1.2B model weighs the last thing it read, and with the results
    # only in the distant header it denied transactions already in a block.
    if lines:
        footer = _CHAT_DID + '\n'.join(f'- {s["tool"]}: {ln}'
                                       for s, ln in zip(steps, lines))
    else:
        footer = _CHAT_DIDNT
    if not can_write:
        system += ('\nThis caller may not write: anything priceable ran as a '
                   'dry run and nothing was signed.')
    messages = [{'role': 'system', 'content': system}]
    for h in (history or [])[-10:]:
        if isinstance(h, dict) and h.get('role') in ('user', 'assistant') \
                and h.get('content'):
            messages.append({'role': h['role'],
                             'content': str(h['content'])[:1500]})
    messages.append({'role': 'user', 'content': query})
    messages.append({'role': 'system', 'content': footer.strip()})
    emit({'type': 'model_start', 'step': 0, 'model': lfm.MODEL, 'brain': 'chat'})
    answer = ''
    try:
        for ev in lfm.stream(messages):
            kind = ev.get('type')
            if kind == 'token':
                answer += ev.get('text', '')
                emit({'type': 'token', 'text': ev.get('text', '')})
            elif kind == 'error':
                raise RuntimeError(ev.get('error') or 'generation failed')
        answer = answer.strip()
        if not answer:
            raise RuntimeError('the model answered nothing')
        return answer, steps, None
    except (urllib.error.URLError, OSError, RuntimeError, ValueError,
            KeyError, json.JSONDecodeError) as e:
        note = f'liquidai unavailable ({type(e).__name__}: {e}) — plain summary'
        emit({'type': 'status', 'text': note})
        answer = '\n'.join(lines + gloss) if lines or gloss else (
            'I could not reach the liquidai model for conversation, and the '
            'message is not a chain command I recognise. ' + help_text())
        emit({'type': 'token', 'text': answer})
        return answer, steps, note


# ── the llm brain (optional, local first) ─────────────────────────

def llm_key():
    k = os.environ.get('POSTQUANT_AGENT_LLM_KEY')
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
    out = []
    for name, t in mcpsrv.TOOLS.items():
        if not can_write and name in mcpsrv.WRITE_TOOLS and not _can_dry_run(name) \
                and name != 'pq_wallet':
            continue
        out.append({'type': 'function', 'function': {
            'name': name, 'description': t['description'],
            'parameters': t['inputSchema']}})
    return out


def _chat(messages, tools, model):
    body = json.dumps({'model': model, 'messages': messages, 'tools': tools,
                       'temperature': 0.1}).encode()
    headers = {'content-type': 'application/json'}
    key = llm_key()
    if key:
        headers['authorization'] = f'Bearer {key}'
    req = urllib.request.Request(LLM_URL + '/chat/completions', body, headers)
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
        return json.loads(r.read())['choices'][0]['message']


def _run_llm(query, can_write, emit, model=None, history=None, steps_max=MAX_STEPS):
    model = model or LLM_MODEL
    system = (mcpsrv.INSTRUCTIONS + '\n\nYou are the postquant agent. Use the '
              'tools to do what the user asks and answer briefly in plain '
              'language. Amounts: a string like "25" is PQ.' +
              ('' if can_write else ' This caller may NOT write: price writes '
               'with dry_run=true and say so.'))
    messages = [{'role': 'system', 'content': system}, *(history or []),
                {'role': 'user', 'content': query}]
    tools = _llm_tools(can_write)
    steps = []
    for i in range(steps_max):
        emit({'type': 'model_start', 'step': i, 'model': model, 'brain': 'llm'})
        msg = _chat(messages, tools, model)
        calls = msg.get('tool_calls') or []
        if not calls:
            answer = (msg.get('content') or '').strip()
            emit({'type': 'token', 'text': answer})
            return answer, steps
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
                out, note = _call(name, dict(args), can_write)
                step['result'] = out
                if note:
                    step['note'] = note
                content = json.dumps({'result': out, 'note': note}, default=str)
            except Exception as e:                      # noqa: BLE001
                step['error'] = _err(e)
                content = json.dumps({'error': step['error']})
            steps.append(step)
            emit({'type': 'step', 'step': step})
            messages.append({'role': 'tool', 'tool_call_id': c.get('id', name),
                             'name': name, 'content': content[:8000]})
    answer = 'stopped after the step budget — the tool results above are real.'
    emit({'type': 'token', 'text': answer})
    return answer, steps


# ── the contract ──────────────────────────────────────────────────

def brains():
    return {'rules': {'ready': True, 'local': True, 'cost': 'free'},
            'chat': {'ready': lfm.ready(), 'local': True, 'cost': 'free',
                     'model': lfm.MODEL if lfm.enabled() else None,
                     'provider': 'liquidai', 'url': lfm.URL or None,
                     'how': 'conversation voiced by the liquidai module '
                            'next door (LFM resident in its server runtime); '
                            'commands in the message are still parsed and '
                            'executed by the rules brain — the model only '
                            'talks about what actually ran'},
            'llm': {'ready': llm_ready(), 'local': LLM_URL.startswith(
                ('http://127.', 'http://localhost', 'http://[::1]')) if LLM_URL else None,
                    'url': LLM_URL or None, 'model': LLM_MODEL if LLM_URL else None,
                    'how': 'set POSTQUANT_AGENT_LLM to an OpenAI-compatible '
                           'base url, e.g. http://127.0.0.1:11434/v1 (ollama)'}}


def agents():
    """GET /agents — THE compatibility contract orbit/build probes for."""
    return {'agents': [{'id': k, **v, 'provider': 'postquant', 'local': True,
                        'free': True, 'examples': EXAMPLES}
                       for k, v in AGENTS.items()],
            'default': DEFAULT_AGENT, 'brains': brains()}


def agent(name=None):
    key = name or DEFAULT_AGENT
    if key not in AGENTS:
        raise KeyError(f'no agent {key!r} — have {list(AGENTS)}')
    return {'id': key, **AGENTS[key], 'examples': EXAMPLES, 'brains': brains(),
            'tools': [n for n in mcpsrv.TOOLS
                      if AGENTS[key]['writes'] or n not in mcpsrv.WRITE_TOOLS
                      or _can_dry_run(n)]}


def card(base_url=''):
    """/.well-known/agent.json — agent/1.0."""
    return {'protocol': 'agent/1.0', 'name': 'postquant', 'version': mcpsrv.version(),
            'description': AGENTS[DEFAULT_AGENT]['description'],
            'agents': agents()['agents'],
            'endpoints': {'agents': f'{base_url}/agents', 'run': f'{base_url}/run',
                          'stream': f'{base_url}/run/stream', 'mcp': f'{base_url}/mcp'},
            'events': ['model_start', 'token', 'tool_start', 'step', 'done', 'error'],
            'auth': 'writes follow the REST gate: Authorization: Bearer '
                    '<~/.mod/postquant/server.secret> when that file exists'}


def run_stream(query, agent=None, can_write=False, brain='auto', model=None,
               history=None, **_ignored):
    """Yield the run's events. The last one is done or error."""
    key = agent or DEFAULT_AGENT
    if key not in AGENTS:
        yield {'type': 'error', 'error': f'no agent {key!r} — have {list(AGENTS)}'}
        return
    can_write = bool(can_write and AGENTS[key]['writes'])
    t0 = time.time()
    use = brain if brain in ('rules', 'llm', 'chat') else \
        ('llm' if llm_ready() else 'chat' if lfm.ready() else 'rules')
    fell_back = None
    # The brain runs on a thread and the generator drains its queue, so the
    # caller sees tool_start before a slow witness or a slow model finishes.
    q = queue.Queue()
    box = {}

    def work():
        nonlocal fell_back
        try:
            if use == 'chat':
                # chat does its own degrading: steps run exactly once up
                # front, and only the voice falls back to their summaries.
                answer, steps, fell_back = _run_chat(query, can_write,
                                                     q.put, history)
                box['out'] = (answer, steps)
                return
            if use == 'llm':
                if not llm_ready():
                    raise RuntimeError('no POSTQUANT_AGENT_LLM configured')
                try:
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

    threading.Thread(target=work, daemon=True, name='postquant-agent').start()
    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev
    if 'err' in box:
        yield {'type': 'error', 'error': box['err']}
        return
    answer, steps = box['out']
    finish = {'tool': 'finish', 'params': {'summary': answer}}
    yield {'type': 'step', 'step': finish}
    yield {'type': 'done', 'result': answer, 'steps': steps, 'agent': key,
           'brain': 'rules' if fell_back else use, 'fallback': fell_back,
           'can_write': can_write, 'free': True,
           'ms': int((time.time() - t0) * 1000)}


def run(query, **kw):
    """POST /run — the same run, collected."""
    done, trace = None, []
    for ev in run_stream(query, **kw):
        trace.append(ev)
        if ev['type'] in ('done', 'error'):
            done = ev
    if done is None or done['type'] == 'error':
        raise StateError((done or {}).get('error') or 'run produced no answer',
                         code='agent_error')
    return {k: v for k, v in done.items() if k != 'type'}


if __name__ == '__main__':
    q = ' '.join(sys.argv[1:]) or 'help'
    for ev in run_stream(q, can_write=True):
        if ev['type'] == 'step' and ev['step']['tool'] == 'finish':
            print(ev['step']['params']['summary'])
        elif ev['type'] == 'error':
            print('error:', ev['error'])
