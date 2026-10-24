"""
bt.agent — the console's chat: a Bittensor analyst wired into this module's
own tools, speaking the fleet's agent protocol (``agent/1.0``).

The agent's entire toolbox is this module's MCP server (:mod:`bt.mcp_server`),
started with ``--strict-mcp-config`` so nothing else is reachable: it can read
every market, account, validator and tracked trader, and it cannot sign
anything — the six on-chain writes are denied by name.

Surface (all under ``/api``)::

    GET  /agent/card    who it is, what it can do          (also /.well-known/agent.json)
    GET  /agent/status  auth, model, tool count
    GET  /agent/tools   the toolbox, grouped
    GET  /agent/chats   conversations   ·  /agent/chats/{id} one, with messages
    POST /agent/chat    {message, chat, context} -> SSE run
    POST /agent/ask     the same turn, run to completion, one JSON reply
    POST /agent/stop    {chat} -> kill the run in flight

A run streams these events::

    start      model, chat, session, tools
    status     the CLI's own phase ("requesting", "compacting", …)
    text_delta token-by-token answer text
    text       a whole text block (when partial streaming is off)
    tool       name, args, id — a tool call started
    tool_done  id, ok, ms, preview — and what it returned
    view       an action for the console to apply (see bt_view)
    done       answer, session, turns, ms, cost_usd
    error      why the run stopped

Conversations are multi-turn: every chat carries a Claude session id, which
this module re-reads before each turn and rewrites after, so a chat can be
picked up days later. The transcript the console renders lives in
:mod:`bt.chats`, not in the CLI.

Auth resolves in order: ANTHROPIC_API_KEY env → ~/.mod/bt/anthropic.key →
Claude CLI OAuth (~/.claude/.credentials.json) while it is unexpired → the
session the build console's credential keeper publishes
(~/.mod/build/private/claude_host.json). If none is usable, status()/chat()
say so and carry ``login`` — the page that fixes it — and a run that still
dies on auth is retried once on the keeper's session before giving up.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Dict, Generator, List, Optional, Tuple

from . import chats, tools

# ------------------------------------------------------------- parameters

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLAUDE_BIN = os.environ.get('BT_AGENT_BIN', 'claude')
MODEL = os.environ.get('BT_AGENT_MODEL', 'sonnet')
MAX_TURNS = int(os.environ.get('BT_AGENT_MAX_TURNS', '14'))
TIMEOUT_SEC = int(os.environ.get('BT_AGENT_TIMEOUT', '300'))
# token-by-token streaming; off falls back to whole text blocks
STREAM_PARTIAL = os.environ.get('BT_AGENT_STREAM', '1') != '0'
PREVIEW_CHARS = 220

KEY_FILE = os.path.expanduser('~/.mod/bt/anthropic.key')
OAUTH_FILE = os.path.expanduser('~/.claude/.credentials.json')
HOST_CRED_FILE = os.path.expanduser(
    os.environ.get('BT_HOST_CRED', '~/.mod/build/private/claude_host.json'))
LOGIN_URL = os.environ.get('BT_LOGIN_URL', '/build/auth')
# a credential this close to expiry is treated as already gone
EXPIRY_MARGIN = 120

# The agent gets every read-only tool; on-chain writes are explicitly denied
# so a question can never sign anything.
ALLOWED_TOOLS: List[str] = [
    f'mcp__bittensor__{t.name}' for t in tools.TOOLS if not t.mutates]
DISALLOWED_TOOLS: List[str] = [
    f'mcp__bittensor__{t.name}' for t in tools.TOOLS if t.mutates]

# The CLI's own built-ins are not part of this agent's job. They are already
# outside --allowedTools (so they would prompt, and a -p run denies), but
# naming them is cheaper than trusting that. ToolSearch survives: when the CLI
# defers tool schemas, that is how it reaches the bittensor tools at all.
BUILTIN_DENY: List[str] = [
    'Bash', 'Edit', 'Write', 'NotebookEdit', 'Read', 'Glob', 'Grep', 'Task',
    'WebFetch', 'WebSearch', 'Skill', 'SendMessage', 'KillShell',
]

MCP_CONFIG = {'mcpServers': {'bittensor': {
    'command': sys.executable, 'args': ['-m', 'bt.mcp_server'], 'cwd': ROOT}}}

SYSTEM_PROMPT = (
    "You are the bt explorer's network guide — a Bittensor analyst sitting "
    "beside the live console the person is looking at.\n\n"
    "Answer from tool results, never from memory: prices, market caps, "
    "validators and trader books all move. Prefer the instant local index "
    "(bt_screener, bt_stats, bt_history, bt_traders, bt_trader_board, "
    "bt_trader) over full-chain scans like bt_scan, which take ~40s.\n\n"
    "For \"why is it moving\" or \"what is new\" questions, read bt_news "
    "(per subnet, or netuid 0 for Bittensor-wide) and bt_news_buzz — a local "
    "index scraped from GitHub, the subnet's blog, Google News, Reddit, Hacker "
    "News and crypto outlets. Cite the headline and publisher, and say a "
    "story is only a mention when focus is 0.\n\n"
    "Drive the console, don't describe it. When an answer is about a subnet, "
    "a trader or an account the console can show, call bt_view so it opens on "
    "their screen — then say in a sentence or two what they are now looking "
    "at. Never say 'you can check the Markets tab'; open it.\n\n"
    "Lead with the numbers. Name subnets as \"Name (#netuid)\", quote TAO with "
    "the tau sign, and keep answers short — a few sentences or a small table, "
    "not an essay. Say when a figure comes from the local index and how stale "
    "it is if that changes what it means (bt_sync knows). Tracked traders are "
    "only the coldkeys someone added here, and their trades are inferred from "
    "position changes between snapshots, not read from extrinsics — say so "
    "rather than implying a complete tape.\n\n"
    "You are read-only: trading, transfers and wallet creation are denied. If "
    "asked to trade, say what you would do and point at the Trade tab, where "
    "the person signs it themselves. Never invent a number; if a tool cannot "
    "answer, say what is missing."
)

STARTERS: List[str] = [
    'Which subnet pumped hardest in the last 24h, and what do they do?',
    'Summarize the alpha market right now in three sentences.',
    'Show me subnet 64 and tell me whether the last week was real volume.',
    'Rank the tracked traders by trading skill over 7 days, not by deposits.',
    'Who are the top validators on the biggest subnet by market cap?',
    'How stale is the local index right now?',
]


# ------------------------------------------------------------------- auth

def _oauth_expires_at() -> Optional[float]:
    """Root's CLI login expiry (epoch secs); 0 = no login, None = unknown."""
    try:
        o = json.load(open(OAUTH_FILE)).get('claudeAiOauth') or {}
    except (OSError, ValueError):
        return 0
    if not o.get('accessToken'):
        return 0
    ms = o.get('expiresAt')
    return ms / 1000 if isinstance(ms, (int, float)) and ms > 0 else None


def host_session() -> Optional[str]:
    """The owner session build publishes when root's login has lapsed."""
    try:
        v = json.load(open(HOST_CRED_FILE))
    except (OSError, ValueError):
        return None
    tok = v.get('token') if v.get('source') == 'owner-session' else None
    if tok and (v.get('expires_at') or 0) > time.time() + EXPIRY_MARGIN:
        return tok
    return None


def ensure_auth() -> Tuple[bool, Optional[str], Optional[str], Dict[str, str]]:
    """(ready, method, hint, extra_env) — creates KEY_FILE if nothing exists."""
    env_key = os.environ.get('ANTHROPIC_API_KEY', '')
    # an inherited sk-ant-oat… here is a leaked Claude Code session token, not
    # a key — it expires, and the CLI rejects it as one; never trust it
    if env_key and not env_key.startswith('sk-ant-oat'):
        return True, 'api-key-env', None, {}
    try:
        key = open(KEY_FILE).read().strip()
    except OSError:
        key = ''
    if key:
        return True, 'api-key-file', None, {'ANTHROPIC_API_KEY': key}
    exp = _oauth_expires_at()
    if exp is None or (exp and exp > time.time() + EXPIRY_MARGIN):
        return True, 'claude-cli', None, {}
    tok = host_session()
    if tok:
        return True, 'build-session', None, {'CLAUDE_CODE_OAUTH_TOKEN': tok}
    if not os.path.exists(KEY_FILE):
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        with open(KEY_FILE, 'w'):
            pass
        os.chmod(KEY_FILE, 0o600)
    why = ('The Claude login on this host has expired' if exp
           else 'No Claude login on this host')
    return False, None, (
        f'{why} — sign in with Claude at {LOGIN_URL} (or paste an API key '
        f'into {KEY_FILE}).'), {}


def child_env(extra: Dict[str, str]) -> Dict[str, str]:
    """A clean env for the CLI. When this server was started from inside a
    Claude Code session it inherits that session's wiring — CHILD_SESSION,
    SESSION_ID, a messaging socket to a parent long gone, sometimes its token
    — and a CLI that believes it is someone's child leaves token refresh to
    that parent, so root's login silently lapses. Strip all of it."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith('CLAUDE_CODE_') or k in ('CLAUDECODE', 'CLAUDE_PID'))}
    if env.get('ANTHROPIC_API_KEY', '').startswith('sk-ant-oat'):
        env.pop('ANTHROPIC_API_KEY')
    env.update(extra)
    return env


AUTH_FAIL = ('failed to authenticate', 'oauth session expired',
             'invalid api key', 'authentication_error', 'please run /login',
             'oauth token has expired', 'invalid bearer token')


def is_auth_failure(text: str) -> bool:
    t = (text or '').lower()
    return any(m in t for m in AUTH_FAIL)


def status() -> Dict:
    ready, method, hint, _ = ensure_auth()
    return {'ready': ready, 'method': method, 'hint': hint,
            'login': None if ready else LOGIN_URL,
            'model': MODEL, 'max_turns': MAX_TURNS,
            'token_streaming': STREAM_PARTIAL,
            'tools': len(ALLOWED_TOOLS), 'denied': len(DISALLOWED_TOOLS),
            'running': running(), 'starters': STARTERS}


# ------------------------------------------------------------------- card

def card() -> Dict:
    """The agent card: who this agent is and how to talk to it."""
    return {
        'protocol': 'agent/1.0',
        'name': 'bt-network-guide',
        'title': 'bt network guide',
        'description': (
            'Ask the Bittensor network anything. Answers from live chain '
            'reads and a local open index of every subnet and tracked '
            'trader, and drives the bt console to show what it is talking '
            'about. Read-only: it sees everything, it signs nothing.'),
        'audience': ['traders', 'subnet owners', 'validators', 'researchers'],
        'endpoints': {
            'card': '/api/agent/card',
            'status': '/api/agent/status',
            'tools': '/api/agent/tools',
            'chat': '/api/agent/chat',
            'ask': '/api/agent/ask',
            'stop': '/api/agent/stop',
            'chats': '/api/agent/chats',
            'mcp': '/mcp',
        },
        'streaming': 'text/event-stream',
        'events': ['start', 'status', 'text_delta', 'text', 'tool',
                   'tool_done', 'view', 'done', 'error'],
        'conversation': {
            'multi_turn': True,
            'param': 'chat',
            'store': 'server-side (bt.chats), readable at /api/agent/chats',
        },
        'skills': [{'group': g['group'],
                    'tools': [t['name'] for t in g['tools']]}
                   for g in tools.docs()],
        'tool_count': len(ALLOWED_TOOLS),
        'denied_tools': [n.replace('mcp__bittensor__', '')
                         for n in DISALLOWED_TOOLS],
        'drives_ui': {'event': 'view', 'tool': 'bt_view',
                      'views': list(tools.VIEWS)},
        'data': ('Bittensor finney chain plus this module\'s local index '
                 '(subnet snapshots every 5 min, tracked coldkeys every 15)'),
        'writes': 'none — every on-chain tool is denied to the agent',
        'starters': STARTERS,
        **status(),
    }


# ------------------------------------------------------------------- runs

_runs: Dict[str, subprocess.Popen] = {}   # chat id -> live process
_runs_lock = threading.Lock()


def running() -> List[str]:
    """Chat ids with a run in flight."""
    with _runs_lock:
        return [c for c, p in _runs.items() if p.poll() is None]


def stop(chat_id: str) -> Dict:
    """Kill the run in flight for one chat (the console's stop button)."""
    with _runs_lock:
        proc = _runs.get(chat_id)
    if proc is None or proc.poll() is not None:
        return {'ok': False, 'chat': chat_id, 'note': 'nothing running'}
    proc.kill()
    return {'ok': True, 'chat': chat_id}


def build_cmd(question: str, session: Optional[str] = None) -> List[str]:
    cmd = [
        CLAUDE_BIN, '-p', question,
        '--output-format', 'stream-json', '--verbose',
        '--model', MODEL,
        '--max-turns', str(MAX_TURNS),
        '--strict-mcp-config', '--mcp-config', json.dumps(MCP_CONFIG),
        '--allowedTools', ','.join(ALLOWED_TOOLS),
        '--disallowedTools', ','.join(DISALLOWED_TOOLS + BUILTIN_DENY),
        '--append-system-prompt', SYSTEM_PROMPT,
    ]
    if STREAM_PARTIAL:
        cmd.append('--include-partial-messages')
    if session:
        cmd += ['--resume', session]
    return cmd


def context_line(context: Optional[Dict]) -> str:
    """What the person is looking at, stated to the agent in one line."""
    if not isinstance(context, dict):
        return ''
    bits = []
    view = context.get('view')
    if view:
        bits.append(f'on the {view} tab')
    if context.get('netuid') is not None:
        bits.append(f'subnet {context["netuid"]} open')
    if context.get('address'):
        bits.append(f'address {context["address"]} open')
    return f'[console: {", ".join(bits)}]\n' if bits else ''


def _view_action(text: str) -> Optional[Dict]:
    """Lift a ``__view__`` block out of a tool result, if there is one."""
    if '__view__' not in (text or ''):
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    action = payload.get('__view__') if isinstance(payload, dict) else None
    return action if isinstance(action, dict) else None


def _result_text(body) -> str:
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        return ''.join(p.get('text', '') for p in body if isinstance(p, dict))
    return ''


def _preview(text: str) -> str:
    """A one-line gist of a tool result, for the chip in the transcript."""
    text = ' '.join(str(text or '').split())
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            keys = [k for k in val if not k.startswith('__')]
            for k in ('rows', 'traders', 'subnets', 'neurons', 'flows'):
                if isinstance(val.get(k), list):
                    return f'{len(val[k])} {k}'
            text = ', '.join(
                f'{k}={val[k]}' for k in keys[:3]
                if isinstance(val[k], (str, int, float, bool)))
        elif isinstance(val, list):
            return f'{len(val)} rows'
    except (TypeError, ValueError):
        pass
    return text[:PREVIEW_CHARS] + ('…' if len(text) > PREVIEW_CHARS else '')


class _Run:
    """One turn: translates the CLI's stream into console events."""

    def __init__(self):
        self.streamed = False        # partial text arrived, ignore whole blocks
        self.block = None            # which content block the deltas belong to
        self.pending: Dict[str, Dict] = {}   # tool_use id -> {name, args, t0}
        self.tools: List[Dict] = []          # what this turn played
        self.views: List[Dict] = []
        self.text: List[str] = []
        self.session: Optional[str] = None
        self.model: Optional[str] = None
        self.turns = 0
        self.cost = 0.0
        self.ms = 0

    def events(self, msg: Dict) -> Generator[Dict, None, None]:
        t = msg.get('type')
        if t == 'stream_event':
            yield from self._partial(msg.get('event') or {})
        elif t == 'system':
            if msg.get('subtype') == 'init':
                self.session = msg.get('session_id') or self.session
                self.model = msg.get('model') or self.model
                yield {'type': 'start', 'model': self.model,
                       'session': self.session,
                       'tools': sum(1 for x in msg.get('tools', [])
                                    if str(x).startswith('mcp__bittensor__'))}
            elif msg.get('subtype') == 'status' and msg.get('status'):
                yield {'type': 'status', 'status': msg['status']}
        elif t == 'assistant':
            for c in msg.get('message', {}).get('content', []):
                kind = c.get('type')
                if kind == 'text' and not self.streamed and c.get('text', '').strip() \
                        and not (not self.text and is_auth_failure(c['text'])):
                    # a second answer block after a tool call is a new
                    # paragraph, not a continuation of the last sentence
                    text = ('\n\n' if self.text else '') + c['text']
                    self.text.append(text)
                    yield {'type': 'text', 'text': text}
                elif kind == 'tool_use':
                    raw = str(c.get('name', ''))
                    name = raw.replace('mcp__bittensor__', '')
                    builtin = not raw.startswith('mcp__bittensor__')
                    tid = str(c.get('id') or f'{name}-{len(self.tools)}')
                    args = c.get('input') or {}
                    self.pending[tid] = {'name': name, 'args': args,
                                         'builtin': builtin, 't0': time.time()}
                    yield {'type': 'tool', 'id': tid, 'name': name,
                           'args': args, 'builtin': builtin}
        elif t == 'user':
            content = msg.get('message', {}).get('content')
            for c in content if isinstance(content, list) else []:
                if not isinstance(c, dict) or c.get('type') != 'tool_result':
                    continue
                tid = str(c.get('tool_use_id') or '')
                started = self.pending.pop(tid, None) or \
                    (self.pending.popitem()[1] if self.pending else None)
                text = _result_text(c.get('content'))
                ok = not c.get('is_error')
                ms = int((time.time() - started['t0']) * 1000) if started else None
                name = started['name'] if started else ''
                builtin = bool(started and started.get('builtin'))
                self.tools.append({'name': name,
                                   'args': started['args'] if started else {},
                                   'ok': ok, 'ms': ms, 'builtin': builtin})
                yield {'type': 'tool_done', 'id': tid, 'name': name,
                       'error': not ok, 'ms': ms, 'builtin': builtin,
                       'preview': _preview(text)}
                action = _view_action(text)
                if action:
                    self.views.append(action)
                    yield {'type': 'view', 'action': action}
        elif t == 'result':
            self.session = msg.get('session_id') or self.session
            self.turns = msg.get('num_turns') or 0
            self.cost = float(msg.get('total_cost_usd') or 0.0)
            self.ms = msg.get('duration_ms') or 0
            answer = msg.get('result') or ''.join(self.text)
            if is_auth_failure(answer) and not self.tools:
                # the CLI reports a dead login as a one-line "answer" — it is
                # an error, and one the caller can act on
                yield {'type': 'error', 'error': answer, 'auth': True,
                       'login': LOGIN_URL}
                return
            if msg.get('is_error') and not answer:
                yield {'type': 'error', 'error': msg.get('subtype') or 'run failed'}
                return
            yield {'type': 'done', 'answer': answer, 'session': self.session,
                   'turns': self.turns, 'ms': self.ms, 'cost_usd': self.cost}

    def _partial(self, ev: Dict) -> Generator[Dict, None, None]:
        if ev.get('type') == 'message_start':
            # block indexes restart at 0 in every message, so a fresh message
            # is a fresh block even when the number is the same one
            self.block = 'new-message'
            return
        if ev.get('type') != 'content_block_delta':
            return
        delta = ev.get('delta') or {}
        if delta.get('type') == 'text_delta' and delta.get('text'):
            text = delta['text']
            index = ev.get('index')
            if self.text and index != self.block:
                text = '\n\n' + text      # a new block starts a new paragraph
            self.block = index
            self.streamed = True
            self.text.append(text)
            yield {'type': 'text_delta', 'delta': text}

    @property
    def answer(self) -> str:
        return ''.join(self.text).strip()


def chat(message: str, chat_id: Optional[str] = None,
         context: Optional[Dict] = None) -> Generator[Dict, None, None]:
    """Run one turn of a conversation, yielding events as they happen.

    ``chat_id`` continues an existing conversation (a new one is opened and
    named after the first message when it is omitted); the id rides on every
    event so the console can attach a reload to the right thread.
    """
    message = str(message or '').strip()
    if not message:
        yield {'type': 'error', 'error': 'ask something'}
        return
    ready, _, hint, extra = ensure_auth()
    if not ready:
        yield {'type': 'error', 'error': hint, 'auth': True, 'login': LOGIN_URL}
        return

    if chat_id and chats.exists(chat_id):
        session = chats.session_of(chat_id)
    else:
        chat_id = chats.create(message, model=MODEL)
        session = None
    chats.append(chat_id, 'user', message,
                 meta={'context': context} if context else None)
    yield {'type': 'chat', 'chat': chat_id, 'session': session}

    prompt = context_line(context) + message
    run = _Run()
    try:
        # Two attempts at most: the second only when the first died on auth
        # and the build keeper has a session the first did not use.
        for attempt in (0, 1):
            run = _Run()
            retry = None
            for ev in _spawn(prompt, session, extra, chat_id, run):
                if ev.get('auth') and attempt == 0:
                    tok = host_session()
                    if tok and extra.get('CLAUDE_CODE_OAUTH_TOKEN') != tok:
                        retry = {'CLAUDE_CODE_OAUTH_TOKEN': tok}
                        continue
                if ev.get('auth'):
                    ev['error'] = (f'{ev["error"]} — sign in with Claude at '
                                   f'{LOGIN_URL} and ask again.')
                yield ev
            if not retry:
                break
            extra = retry
            yield {'type': 'status', 'chat': chat_id,
                   'status': 'host login expired — retrying on the build session'}
    finally:
        if run.answer or run.tools:
            chats.append(chat_id, 'assistant', run.answer, tools=run.tools,
                         meta={'views': run.views, 'turns': run.turns,
                               'ms': run.ms, 'cost_usd': run.cost,
                               'model': run.model or MODEL})
        chats.finish_turn(chat_id, session=run.session, model=run.model,
                          turns=run.turns, cost_usd=run.cost)


def _spawn(prompt: str, session: Optional[str], extra: Dict[str, str],
           chat_id: str, run: '_Run') -> Generator[Dict, None, None]:
    """One CLI process for one attempt at a turn, streamed as events."""
    try:
        proc = subprocess.Popen(build_cmd(prompt, session), cwd=ROOT,
                                env=child_env(extra),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
    except FileNotFoundError:
        yield {'type': 'error', 'chat': chat_id,
               'error': f'{CLAUDE_BIN} CLI not found on this host'}
        return

    with _runs_lock:
        _runs[chat_id] = proc
    watchdog = threading.Timer(TIMEOUT_SEC, proc.kill)
    watchdog.start()
    finished = False
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            for ev in run.events(msg):
                finished = finished or ev['type'] in ('done', 'error')
                ev['chat'] = chat_id
                yield ev
        proc.wait(timeout=10)
        if not finished:
            err = (proc.stderr.read() or '')[-400:].strip()
            stopped = proc.returncode in (-9, -15, 137)
            ev = {'type': 'error', 'chat': chat_id,
                  'error': 'stopped' if stopped else
                  (err or f'agent exited early (code {proc.returncode})'),
                  'stopped': stopped}
            if not stopped and is_auth_failure(err):
                ev.update(auth=True, login=LOGIN_URL)
            yield ev
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
        with _runs_lock:
            if _runs.get(chat_id) is proc:
                _runs.pop(chat_id, None)


def ask(question: str, chat_id: Optional[str] = None,
        context: Optional[Dict] = None) -> Dict:
    """Run a turn to completion and return the answer — the non-streaming shape."""
    answer, used, views, error = '', [], [], None
    session = None
    for ev in chat(question, chat_id=chat_id, context=context):
        kind = ev['type']
        chat_id = ev.get('chat') or chat_id
        if kind == 'tool':
            used.append(ev['name'])
        elif kind == 'view':
            views.append(ev['action'])
        elif kind == 'done':
            answer = ev.get('answer') or answer
            session = ev.get('session')
        elif kind == 'error':
            error = ev['error']
    out = {'answer': answer, 'chat': chat_id, 'session': session,
           'tools_used': used, 'views': views}
    if error:
        out['error'] = error
    return out
