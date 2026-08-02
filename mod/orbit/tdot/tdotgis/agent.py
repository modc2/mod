"""
tdot.agent — the map's chat interface: ask for what you want to see.

A Claude agent whose entire toolbox is this module's own MCP server
(:mod:`tdotgis.mcp_server`). Ask "where are the tall buildings being proposed
in Scarborough" and it queries the development pipeline, answers with the
numbers, and turns the layer on — on the map you are already looking at.

POST /agent/chat streams the run as SSE events:

    {type: start|text|tool|tool_done|map|done|error, ...}

The ``map`` event is the point of the whole thing. Tools marked ``drives_map``
return a ``__map__`` block; this module lifts it out of the tool result and
forwards it, so the browser applies the change live rather than the agent
merely describing it.

Conversations are continued with the ``session`` returned by ``done`` — the
Claude CLI keeps the transcript, so this module holds no per-user state.

Auth resolves in order: ANTHROPIC_API_KEY env → ~/.mod/tdot/anthropic.key →
Claude CLI OAuth (~/.claude/.credentials.json). If none exist, the key file is
created empty (0600) and status()/chat() say where to paste a key.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Dict, Generator, List, Optional, Tuple

from . import tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLAUDE_BIN = os.environ.get('TDOT_AGENT_BIN', 'claude')
MODEL = os.environ.get('TDOT_AGENT_MODEL', 'sonnet')
MAX_TURNS = int(os.environ.get('TDOT_AGENT_MAX_TURNS', '14'))
TIMEOUT_SEC = int(os.environ.get('TDOT_AGENT_TIMEOUT', '300'))

KEY_FILE = os.path.expanduser('~/.mod/tdot/anthropic.key')
OAUTH_FILE = os.path.expanduser('~/.claude/.credentials.json')

ALLOWED_TOOLS: List[str] = [f'mcp__tdot__{t.name}' for t in tools.TOOLS]

MCP_CONFIG = {'mcpServers': {'tdot': {
    'command': sys.executable, 'args': ['-m', 'tdotgis.mcp_server'], 'cwd': ROOT}}}

SYSTEM_PROMPT = (
    "You are the Toronto Atlas guide, sitting beside a live open-data map of "
    "the city. The people asking are residents, journalists, planners and "
    "councillors — some know the datasets cold, most do not.\n\n"
    "Work the map, don't narrate it. When someone asks to see something, call "
    "tdot_show_layers / tdot_fly_to / tdot_set_crime_view so it actually "
    "appears; then say in one or two sentences what they are now looking at. "
    "Never say 'you could turn on X' — turn it on.\n\n"
    "Answer with numbers from tdot_layer_query, never from memory: how many "
    "units are in the pipeline, which ward has the most short-term rentals, "
    "how a neighbourhood's crime moved. Name the dataset behind any figure so "
    "it can be checked.\n\n"
    "If no layer covers the question, search the city's portal with "
    "tdot_search_open_data and add what you find with tdot_add_open_data — "
    "adding a dataset is a normal thing to do here, not an escalation. If a "
    "dataset genuinely cannot be mapped, say plainly why.\n\n"
    "For housing questions start at tdot_housing_data — it is the full list of "
    "what the city publishes, including the datasets with no geography and the "
    "ones that are not open at all. Sale prices are not open data; do not "
    "imply otherwise.\n\n"
    "tdot_score_model predicts a building's inspection score from open data. "
    "It explains about a third of the variance, so quote its typical error "
    "alongside any prediction and treat the outliers as a shortlist to check, "
    "never as a verdict on a landlord.\n\n"
    "Everything here is public open data with real limits: police locations "
    "are offset to intersections, short-term rental addresses are withheld, "
    "RentSafeTO scores end in 2023. Say so when it changes what a number "
    "means. Be brief, be concrete, and never invent a figure.")


# ─────────────────────────────────────────────────────────────────────── auth

def ensure_auth() -> Tuple[bool, Optional[str], Optional[str], Dict[str, str]]:
    """(ready, method, hint, extra_env) — creates KEY_FILE if nothing exists."""
    if os.environ.get('ANTHROPIC_API_KEY'):
        return True, 'api-key-env', None, {}
    try:
        key = open(KEY_FILE).read().strip()
    except OSError:
        key = ''
    if key:
        return True, 'api-key-file', None, {'ANTHROPIC_API_KEY': key}
    if os.path.exists(OAUTH_FILE):
        return True, 'claude-cli', None, {}
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    if not os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'w'):
            pass
        os.chmod(KEY_FILE, 0o600)
    return False, None, (
        f'No Anthropic auth configured — paste an API key into {KEY_FILE} '
        f'(created, 0600) or run `claude login` on this host.'), {}


def status() -> Dict:
    ready, method, hint, _ = ensure_auth()
    return {'ready': ready, 'method': method, 'hint': hint, 'model': MODEL,
            'max_turns': MAX_TURNS, 'tools': len(ALLOWED_TOOLS)}


def card() -> Dict:
    """The agent's card: who it is, what it can do, how to talk to it."""
    return {
        'protocol': 'agent/1.0',
        'name': 'toronto-atlas-guide',
        'title': 'Toronto Atlas guide',
        'description': ('Ask for what you want to see on a live open-data map '
                        'of Toronto. Answers with figures from the city\'s own '
                        'datasets, drives the map to show them, and can add any '
                        'dataset the portal publishes.'),
        'audience': ['residents', 'journalists', 'planners', 'city officials'],
        'endpoints': {'chat': '/agent/chat', 'card': '/agent/card',
                      'tools': '/agent/tools', 'status': '/agent/status'},
        'streaming': 'text/event-stream',
        'events': ['start', 'text', 'tool', 'tool_done', 'map', 'done', 'error'],
        'skills': [{'group': g['group'],
                    'tools': [t['name'] for t in g['tools']]}
                   for g in tools.docs()],
        'tool_count': len(tools.TOOLS),
        'data': 'City of Toronto Open Data, Toronto Police Service, TTC — all public',
        'writes': ('local only: a saved dataset spec and the disk cache. '
                   'The agent cannot change city data or sign anything.'),
        **status(),
    }


# ──────────────────────────────────────────────────────────────────────── run

def build_cmd(question: str, session: Optional[str] = None) -> List[str]:
    cmd = [CLAUDE_BIN, '-p', question,
           '--output-format', 'stream-json', '--verbose',
           '--model', MODEL,
           '--max-turns', str(MAX_TURNS),
           '--strict-mcp-config', '--mcp-config', json.dumps(MCP_CONFIG),
           '--allowedTools', ','.join(ALLOWED_TOOLS),
           '--append-system-prompt', SYSTEM_PROMPT]
    if session:
        cmd += ['--resume', session]
    return cmd


def _map_action(text: str) -> Optional[dict]:
    """Lift a ``__map__`` block out of a tool result, if there is one."""
    if '__map__' not in text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    action = payload.get('__map__') if isinstance(payload, dict) else None
    return action if isinstance(action, dict) else None


def _events(msg: Dict) -> Generator[Dict, None, None]:
    """Translate one claude stream-json message into chat events."""
    t = msg.get('type')
    if t == 'system' and msg.get('subtype') == 'init':
        yield {'type': 'start', 'model': msg.get('model'),
               'session': msg.get('session_id'),
               'tools': sum(1 for x in msg.get('tools', [])
                            if str(x).startswith('mcp__tdot__'))}
    elif t == 'assistant':
        for c in msg.get('message', {}).get('content', []):
            if c.get('type') == 'text' and c.get('text', '').strip():
                yield {'type': 'text', 'text': c['text']}
            elif c.get('type') == 'tool_use':
                yield {'type': 'tool',
                       'name': str(c.get('name', '')).replace('mcp__tdot__', ''),
                       'args': c.get('input', {})}
    elif t == 'user':
        content = msg.get('message', {}).get('content')
        for c in content if isinstance(content, list) else []:
            if not isinstance(c, dict) or c.get('type') != 'tool_result':
                continue
            body = c.get('content')
            text = body if isinstance(body, str) else ''.join(
                p.get('text', '') for p in body if isinstance(p, dict)) \
                if isinstance(body, list) else ''
            yield {'type': 'tool_done', 'error': bool(c.get('is_error'))}
            action = _map_action(text)
            if action:
                yield {'type': 'map', 'action': action}
    elif t == 'result':
        yield {'type': 'done', 'answer': msg.get('result') or '',
               'session': msg.get('session_id'),
               'turns': msg.get('num_turns'), 'ms': msg.get('duration_ms'),
               'cost_usd': msg.get('total_cost_usd')}


def chat(question: str, session: Optional[str] = None) -> Generator[Dict, None, None]:
    """Run one turn of the conversation, yielding events as they happen."""
    question = str(question or '').strip()
    if not question:
        yield {'type': 'error', 'error': 'ask something'}
        return
    ready, _, hint, extra = ensure_auth()
    if not ready:
        yield {'type': 'error', 'error': hint}
        return
    env = {**os.environ, **extra}
    # keep the child from thinking it's nested inside a Claude Code session
    env.pop('CLAUDECODE', None)
    env.pop('CLAUDE_CODE_ENTRYPOINT', None)
    try:
        proc = subprocess.Popen(build_cmd(question, session), cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
    except FileNotFoundError:
        yield {'type': 'error', 'error': f'{CLAUDE_BIN} CLI not found on this host'}
        return

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
            for ev in _events(msg):
                finished = finished or ev['type'] == 'done'
                yield ev
        proc.wait(timeout=10)
        if not finished:
            err = (proc.stderr.read() or '')[-400:].strip()
            yield {'type': 'error',
                   'error': err or f'agent exited early (code {proc.returncode})'}
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()


def ask(question: str, session: Optional[str] = None) -> Dict:
    """Run a turn to completion and return the answer — the CLI's shape."""
    answer, actions, used, error = '', [], [], None
    for ev in chat(question, session=session):
        if ev['type'] == 'text' and not answer:
            pass
        elif ev['type'] == 'tool':
            used.append(ev['name'])
        elif ev['type'] == 'map':
            actions.append(ev['action'])
        elif ev['type'] == 'done':
            answer = ev.get('answer') or answer
            session = ev.get('session') or session
        elif ev['type'] == 'error':
            error = ev['error']
    out = {'answer': answer, 'tools_used': used, 'map_actions': actions,
           'session': session}
    if error:
        out['error'] = error
    return out
