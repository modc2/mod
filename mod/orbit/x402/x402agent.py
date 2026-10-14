"""x402 agent — say what you need, an agent goes through the index for you.

55k paid endpoints is past what a search box and a sort order can sort out.
"I need to check whether a token is a rug, cheap, on Base" is three filters
and a judgement call, and that judgement is this file's whole job.

`stream()` runs the **Claude agent** (the host's `claude` CLI, headless)
against **this module's own MCP server** — the live one at
http://127.0.0.1:<port>/mcp, so it searches the warm semantic index rather than
loading a second copy — with only the read tools allowed:

    claude --print --tools "" --strict-mcp-config
           --allowedTools mcp__x402__x402_find,…   (no call, quote, probe, vouch)

It cannot pay, probe or call anything; it can only look. Every answer ends in
a PICKS line of service URLs, which `picks()` resolves back into index rows
so the console shows them as the same tiles the SERVICES grid does — a URL
the agent made up resolves to nothing and is dropped, not shown.

    POST /x402/api/ask {"question": "...", "session"?: "..."}   → SSE
    GET  /x402/api/ask                                          → the card

A conversation continues with `session` (the Claude session id each answer
carries). Anonymous callers get RATE_PER_HOUR questions an hour per IP; a call
from this box itself has no limit. Host credential handling follows bt's
agent.py: a clean child env, and the build keeper's session when root's own
login has lapsed.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

CLAUDE = os.environ.get('X402_CLAUDE_BIN') or shutil.which('claude')
MODEL = os.environ.get('X402_AGENT_MODEL', 'sonnet')
MODELS = ('sonnet', 'opus', 'haiku')
TIMEOUT = int(os.environ.get('X402_AGENT_TIMEOUT', 240))
MAX_CONCURRENT = int(os.environ.get('X402_AGENT_CONCURRENCY', 2))
RATE_PER_HOUR = int(os.environ.get('X402_AGENT_RATE', 12))
HOST_CRED = os.path.expanduser('~/.mod/build/private/claude_host.json')
LOGIN_URL = '/build/auth'

# Read-only, every one. x402_call / quote / probe / vouch / sync are absent:
# finding a service is this agent's job, paying for one is the user's.
TOOLS = ('x402_find', 'x402_search', 'x402_mcp', 'x402_service',
         'x402_providers', 'x402_stats', 'x402_facilitators')

SYSTEM = """\
You are the finder for X402 — a local index of every x402 (HTTP 402
pay-per-request) API that exists: ~55k endpoints from every facilitator that
publishes a list. The person asking wants to find a paid API (or a paid MCP
server) that does a job. Find it for them.

HOW
* Only from the x402_* tools. Never recommend a service you did not see in a
  tool result in this session — names and prices change, and an invented URL
  is worse than no answer.
* Start with x402_find (ranked by meaning) using the job in plain words. Try
  2-3 phrasings if the first is thin. Use x402_mcp when they want an MCP
  server or tools for an agent. Use x402_search for exact words, a network
  (base, solana, ...) or a price cap. x402_service opens one to check its
  offers, input example and how much it is actually used.
* Prefer services people really call (calls_30d), with a known price, on the
  network the user named. Say plainly when everything found is thin.
* Be short. A few bullets: name or host, what it does, price per call, network,
  why it fits. No preamble.

ALWAYS end with exactly one line, nothing after it:
PICKS: <url>, <url>, ...
listing the service URLs you recommend, best first, at most 8, exactly as the
tools returned them. If nothing fits, write `PICKS:` with nothing after it.
"""

_SEM = threading.BoundedSemaphore(MAX_CONCURRENT)
_HITS, _HITS_LOCK = {}, threading.Lock()
_PICKS = re.compile(r'^\s*\**PICKS:?\**:?\s*(.*)$', re.I | re.M)
_URL = re.compile(r'https?://[^\s,<>()\[\]`"\']+')


def available():
    if not CLAUDE:
        return {'ok': False, 'why': 'the claude CLI is not installed on this host'}
    return {'ok': True, 'bin': CLAUDE}


def card(port):
    av = available()
    return {'protocol': 'agent/1.0', 'name': 'x402-finder',
            'description': 'Describe the job; an agent searches the x402 index '
                           'with its read-only tools and hands back the services '
                           'that fit.',
            'available': av['ok'], 'why_not': av.get('why'),
            'model': MODEL, 'models': list(MODELS), 'tools': list(TOOLS),
            'mcp': f'http://127.0.0.1:{port}/mcp',
            'limits': {'per_hour_per_ip': RATE_PER_HOUR, 'concurrent': MAX_CONCURRENT,
                       'timeout_s': TIMEOUT},
            'ask': 'POST /x402/api/ask {question, session?, model?} -> text/event-stream'}


def _rate(ip):
    now = time.time()
    with _HITS_LOCK:
        hits = [t for t in _HITS.get(ip or '?', []) if now - t < 3600]
        if len(hits) >= RATE_PER_HOUR:
            _HITS[ip or '?'] = hits
            return False
        _HITS[ip or '?'] = hits + [now]
        return True


def _oauth_expires_at():
    try:
        d = json.load(open(os.path.expanduser('~/.claude/.credentials.json')))
        ms = (d.get('claudeAiOauth') or {}).get('expiresAt')
        return ms / 1000 if ms else 0
    except (OSError, ValueError):
        return None


def _env():
    """A clean env for the CLI: a server started from inside a Claude Code
    session inherits that session's wiring, and a CLI that thinks it is a
    child leaves token refresh to a parent that is long gone."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith('CLAUDE_CODE_') or k in ('CLAUDECODE', 'CLAUDE_PID'))}
    if env.get('ANTHROPIC_API_KEY', '').startswith('sk-ant-oat'):
        env.pop('ANTHROPIC_API_KEY')
    exp = _oauth_expires_at()
    if exp is not None and exp and exp < time.time() + 120 and 'ANTHROPIC_API_KEY' not in env:
        try:
            tok = json.load(open(HOST_CRED)).get('token')
        except (OSError, ValueError):
            tok = None
        if tok:
            env['CLAUDE_CODE_OAUTH_TOKEN'] = tok
    env['CLAUDE_CODE_ENTRYPOINT'] = 'x402-finder'
    return env


def _cmd(question, port, model, session):
    cfg = {'mcpServers': {'x402': {'type': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}}}
    cmd = [CLAUDE, '--print', '--model', model, '--tools', '',
           '--mcp-config', json.dumps(cfg), '--strict-mcp-config',
           '--allowedTools', ','.join('mcp__x402__' + t for t in TOOLS),
           '--append-system-prompt', SYSTEM,
           '--output-format', 'stream-json', '--verbose']
    if session:
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', str(session)):
            raise ValueError('that is not a session id')
        cmd += ['--resume', str(session)]
    return cmd + ['-p', question]


def split_picks(text):
    """(answer without the PICKS line, [urls]) — urls de-duplicated, in order."""
    m = None
    for m in _PICKS.finditer(text or ''):
        pass
    if not m:
        return (text or '').strip(), []
    urls = list(dict.fromkeys(u.rstrip('.;:') for u in _URL.findall(m.group(1))))
    return (text[:m.start()] + text[m.end():]).strip(), urls


def picks(urls, lookup, limit=8):
    """URLs → index rows. A URL that is not in the index was invented: drop it."""
    out = []
    for u in urls:
        try:
            s = lookup(u)
        except Exception:                                    # noqa: BLE001
            continue
        s = {k: v for k, v in s.items() if k not in ('raw', 'offers')}
        if s['url'] not in [p['url'] for p in out]:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def stream(question, port, lookup, session=None, model=None, ip=None, owner=False):
    """Yield events: start, tool, text, then one done (or one error)."""
    question = (question or '').strip()
    if not question:
        yield {'type': 'error', 'error': 'say what you are looking for'}
        return
    if len(question) > 2000:
        yield {'type': 'error', 'error': 'keep it under 2000 characters'}
        return
    av = available()
    if not av['ok']:
        yield {'type': 'error', 'error': av['why']}
        return
    model = str(model or MODEL).lower()
    if model not in MODELS:
        yield {'type': 'error', 'error': 'model must be one of ' + ', '.join(MODELS)}
        return
    if not owner and not _rate(ip):
        yield {'type': 'error', 'error': f'{RATE_PER_HOUR} questions an hour per address — '
               'the agent runs on the host account. The same tools are open at /x402/mcp.'}
        return
    if not _SEM.acquire(blocking=False):
        yield {'type': 'error', 'error': f'{MAX_CONCURRENT} agents are already searching; '
               'try again in a moment'}
        return
    try:
        yield from _run(question, port, lookup, model, session)
    finally:
        _SEM.release()


def _run(question, port, lookup, model, session):
    t0 = time.time()
    try:
        cmd = _cmd(question, port, model, session)
    except ValueError as e:
        yield {'type': 'error', 'error': str(e)}
        return
    yield {'type': 'start', 'model': model}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                                cwd='/tmp', env=_env())
    except OSError as e:
        yield {'type': 'error', 'error': f'could not start the agent: {e}'}
        return
    calls, text, result = 0, [], None
    watchdog = threading.Timer(TIMEOUT, proc.kill)
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in proc.stdout:
            if not line.startswith('{'):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get('type') == 'assistant':
                for c in (ev.get('message') or {}).get('content') or []:
                    if c.get('type') == 'tool_use':
                        calls += 1
                        args = {k: v for k, v in (c.get('input') or {}).items()
                                if isinstance(v, (str, int, float, bool))}
                        yield {'type': 'tool', 'tool': str(c.get('name', '')).replace(
                            'mcp__x402__', ''), 'input': args}
                    elif c.get('type') == 'text' and (c.get('text') or '').strip():
                        text.append(c['text'])
                        yield {'type': 'text', 'text': split_picks(c['text'])[0]}
            elif ev.get('type') == 'result':
                result = ev
        proc.wait(timeout=15)
        err = (proc.stderr.read() or '')[-1000:]
    except Exception as e:                                   # noqa: BLE001
        proc.kill()
        yield {'type': 'error', 'error': f'{type(e).__name__}: {e}'}
        return
    finally:
        watchdog.cancel()
    if result is None:
        killed = proc.returncode in (-9, 137)
        yield {'type': 'error', 'error': f'the agent ran past {TIMEOUT}s and was stopped'
               if killed else 'the agent stopped without answering'
               + (': ' + err.strip().splitlines()[-1] if err.strip() else '')}
        return
    raw = (result.get('result') or '\n'.join(text)).strip()
    if result.get('is_error'):
        msg = raw or result.get('error') or 'the agent errored'
        auth = 'authenticat' in msg.lower() or 'login' in msg.lower()
        yield {'type': 'error', 'error': msg[:400], 'login': LOGIN_URL if auth else None}
        return
    answer, urls = split_picks(raw)
    yield {'type': 'done', 'answer': answer, 'picks': picks(urls, lookup),
           'session': result.get('session_id'), 'tool_calls': calls,
           'grounded': calls > 0, 'cost_usd': result.get('total_cost_usd'),
           'elapsed_ms': int((time.time() - t0) * 1000)}
