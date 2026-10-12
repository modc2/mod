#!/usr/bin/env python3
"""postquant ↔ orbit/agent — the agent brain, stdlib only.

The chat brain is a voice: rules parse and act, the LFM only talks. This is
the other way round. orbit/agent (the fleet's agent protocol: GET /agents,
POST /run/stream) runs a real plan→tool→observe loop with liquidai's LFM as
its model, and the tools it may pick are this chain's own MCP tools, which
orbit/agent already mounts as mcp.postquant.<tool>. The model decides what
to call; postquant decides what it is allowed to call.

    ready()     orbit/agent and liquidai both answering
    tools()     the mcp.postquant.* names a run gets — reads only unless the
                caller may write, and a handful picked by the message (a
                1.2B model on CPU reads every schema it is handed, each step)
    stream()    POST /run/stream, yield orbit/agent's events as dicts

HOST STANDING
    orbit/agent sandboxes every caller that is not its owner, and a sandboxed
    run may not touch fleet tools. A token signed by this box's own key is
    the host (orbit/agent's is_host), so that is what goes in `key`. It is
    minted in a child python with cwd=/tmp: the protocol package is `mod`,
    and inside this process `mod` is postquant's own mod.py. Nothing is
    stored; if minting fails, the run goes out unsigned and orbit/agent
    refuses the tools out loud.

    POSTQUANT_FLEET_AGENT        base url (default http://127.0.0.1:50117,
                                 empty disables the agent brain)
    POSTQUANT_FLEET_PROVIDER     default liquidai
    POSTQUANT_FLEET_MODEL        default = the chat brain's model, i.e. the
                                 one already resident in liquidai (asking for
                                 another evicts it — liquidai keeps one)
    POSTQUANT_FLEET_STEPS        loop budget (default 5)
    POSTQUANT_FLEET_TOKEN        a token to use instead of minting one
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

import lfm

URL = os.environ.get('POSTQUANT_FLEET_AGENT', 'http://127.0.0.1:50117').rstrip('/')
PROVIDER = os.environ.get('POSTQUANT_FLEET_PROVIDER', 'liquidai')
MODEL = os.environ.get('POSTQUANT_FLEET_MODEL', lfm.MODEL)
STEPS = int(os.environ.get('POSTQUANT_FLEET_STEPS', 5))
TIMEOUT = float(os.environ.get('POSTQUANT_FLEET_TIMEOUT', 300))
PREFIX = 'mcp.postquant.'
MAX_TOOLS = 6

_lock = threading.Lock()
_token = {'value': None, 'exp': 0.0}
_health = {'at': 0.0, 'out': None}

_MINT = ("import mod as m\n"
         "print(m.mod('auth')().token({'mod': 'postquant', 'as': 'host'}))\n")


def enabled():
    return bool(URL)


def host_token():
    env = os.environ.get('POSTQUANT_FLEET_TOKEN')
    if env:
        return env.strip()
    now = time.time()
    with _lock:
        if _token['value'] and _token['exp'] > now:
            return _token['value']
    try:
        out = subprocess.run([sys.executable, '-c', _MINT], cwd='/tmp',
                             capture_output=True, text=True, timeout=60)
        lines = out.stdout.strip().splitlines()
        tok = lines[-1].strip() if out.returncode == 0 and lines else None
    except (OSError, subprocess.SubprocessError):
        tok = None
    # orbit/agent verifies with a 1h max age; re-mint well inside it
    with _lock:
        _token.update(value=tok, exp=now + (3000 if tok else 60))
    return tok


def health(max_age=20):
    now = time.time()
    with _lock:
        if now - _health['at'] < max_age:
            return _health['out']
    out = None
    if URL:
        try:
            with urllib.request.urlopen(URL + '/health', timeout=2) as r:
                out = json.loads(r.read())
        except Exception:                               # noqa: BLE001
            out = None
    with _lock:
        _health.update(at=now, out=out)
    return out


def ready():
    return enabled() and bool(health()) and lfm.ready()


# what a word in the message points at — the tool list a small model sees
_ROUTES = [
    (r'algo|scheme|key ?type|dsa|slh|signature|quantum|wasm|plugin', ('pq_algos', 'pq_complexity')),
    (r'private|hidden|cloak|anonym|seal|reveal', ('pq_private',)),
    (r'key ?type market|keymarket|marketplace|\bprims?\b|primitive', ('pq_keymarket', 'pq_prims')),
    (r'market|sale|listing|for sale|buy|price|cost|fee|burn', ('pq_market', 'pq_quote')),
    (r'quote|how much|afford|\brent\b|\bbytes?\b', ('pq_quote',)),
    (r'\bget\b|value of|stored (at|under)', ('pq_get', 'pq_keys')),
    (r'\bkeys\b(?! ?types?)|prefix|starting with', ('pq_keys',)),
    (r'block|height|tip|latest', ('pq_block',)),
    (r'\btx\b|transaction|hash', ('pq_tx', 'pq_mempool')),
    (r'history|activity|recent', ('pq_history',)),
    (r'mempool|pending', ('pq_mempool',)),
    (r'account|balance|nonce|pq[0-9a-f]{6}', ('pq_account', 'pq_wallet')),
    (r'wallet|create|new key', ('pq_wallet',)),
    (r'verify|replay|valid', ('pq_verify',)),
    (r'proof|prove|inclusion', ('pq_prove', 'pq_check')),
    (r'\bset\b|store|write|put', ('pq_set', 'pq_quote')),
    (r'delete|remove|\bdel\b', ('pq_del',)),
    (r'fund|top ?up|escrow', ('pq_fund',)),
    (r'sweep|expired', ('pq_sweep',)),
    (r'\blist\b|sell', ('pq_list',)),
    (r'send|transfer|pay', ('pq_transfer', 'pq_account')),
    (r'faucet|drip|free pq', ('pq_faucet',)),
    (r'\bmine\b|produce', ('pq_mine',)),
]


def tools(query, allowed):
    """mcp.postquant.* names for this run: pq_head always, then what the
    message points at, in order, capped. `allowed` is the gate's answer."""
    low = (query or '').lower()
    picked = ['pq_head']
    for rx, names in _ROUTES:
        if re.search(rx, low):
            picked += [n for n in names if n not in picked]
    return [PREFIX + n for n in picked if n in allowed][:MAX_TOOLS]


def stream(query, tool_names, prompt, model=None, steps=None):
    """POST /run/stream; yield orbit/agent's SSE events. Raises before the
    stream opens if orbit/agent is not there."""
    body = {'query': query, 'provider': PROVIDER, 'model': model or MODEL,
            'tools': tool_names, 'steps': steps or STEPS, 'prompt': prompt,
            'memory': 'ephemeral'}
    tok = host_token()
    if tok:
        body['key'] = tok
    req = urllib.request.Request(URL + '/run/stream', json.dumps(body).encode(),
                                 {'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        for raw in r:
            line = raw.strip()
            if not line.startswith(b'data: '):
                continue
            try:
                yield json.loads(line[6:])
            except json.JSONDecodeError:
                continue


def local_tool(name):
    """mcp.postquant.pq_head → pq_head; anything else as-is."""
    return name[len(PREFIX):] if isinstance(name, str) and name.startswith(PREFIX) else name


def unwrap(result):
    """orbit/agent wraps an MCP result as {server, tool, success, result}."""
    if isinstance(result, dict) and 'server' in result and 'result' in result:
        return result['result']
    return result


_TAIL = re.compile(r'\s*finish\s*\(.*$', re.S)
_STEP = re.compile(r'<STEP>.*?</STEP>', re.S)


def clean(text):
    """The final words without the loop's anchors: small models echo
    <STEP>{…}</STEP> blocks and a trailing finish(summary: …) into prose."""
    text = _STEP.sub('', text or '')
    text = _TAIL.sub('', text)
    return re.sub(r'^\s*Answer what you asked:\s*', '', text.strip(), flags=re.I)


if __name__ == '__main__':
    print('ready:', ready(), '·', URL, '·', PROVIDER, MODEL)
    print('host token:', 'yes' if host_token() else 'NO')
