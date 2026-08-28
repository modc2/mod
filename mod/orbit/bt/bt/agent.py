"""
bt.agent — "ask the network": a Claude agent that answers questions by
playing this module's own MCP server (bt.mcp_server) as its toolbox.

POST /api/ask streams its run as SSE events:
    {type: start|text|tool|tool_done|done|error, ...}

Auth resolves in order: ANTHROPIC_API_KEY env → ~/.mod/bt/anthropic.key →
Claude CLI OAuth (~/.claude/.credentials.json). If none exist, the key file
is created empty (0600) and status()/ask() say where to paste a key.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Dict, Generator, List, Optional, Tuple

from . import tools

# ------------------------------------------------------------- parameters

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLAUDE_BIN = os.environ.get('BT_AGENT_BIN', 'claude')
MODEL = os.environ.get('BT_AGENT_MODEL', 'sonnet')
MAX_TURNS = int(os.environ.get('BT_AGENT_MAX_TURNS', '12'))
TIMEOUT_SEC = int(os.environ.get('BT_AGENT_TIMEOUT', '240'))

KEY_FILE = os.path.expanduser('~/.mod/bt/anthropic.key')
OAUTH_FILE = os.path.expanduser('~/.claude/.credentials.json')

# The agent gets every read-only tool; on-chain writes are explicitly denied
# so a question can never sign anything.
ALLOWED_TOOLS: List[str] = [
    f'mcp__bittensor__{t.name}' for t in tools.TOOLS if not t.mutates]
DISALLOWED_TOOLS: List[str] = [
    f'mcp__bittensor__{t.name}' for t in tools.TOOLS if t.mutates]

MCP_CONFIG = {'mcpServers': {'bittensor': {
    'command': sys.executable, 'args': ['-m', 'bt.mcp_server'], 'cwd': ROOT}}}

SYSTEM_PROMPT = (
    'You are the bt explorer\'s network guide — a Bittensor analyst wired '
    'into live chain tools. Answer from tool results, never from memory. '
    'Prefer bt_screener / bt_stats / bt_history / bt_traders (instant, local '
    'index) over full-chain scans. Lead with the numbers, keep it short, and '
    'name subnets as "Name (#netuid)". You are read-only: trading and '
    'transfer tools are denied — if asked to trade, point at the Trade tab.')


# ------------------------------------------------------------------- auth

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
    return {'ready': ready, 'method': method, 'hint': hint,
            'model': MODEL, 'max_turns': MAX_TURNS,
            'tools': len(ALLOWED_TOOLS), 'denied': len(DISALLOWED_TOOLS)}


# -------------------------------------------------------------------- run

def build_cmd(question: str) -> List[str]:
    return [
        CLAUDE_BIN, '-p', question,
        '--output-format', 'stream-json', '--verbose',
        '--model', MODEL,
        '--max-turns', str(MAX_TURNS),
        '--strict-mcp-config', '--mcp-config', json.dumps(MCP_CONFIG),
        '--allowedTools', ','.join(ALLOWED_TOOLS),
        '--disallowedTools', ','.join(DISALLOWED_TOOLS),
        '--append-system-prompt', SYSTEM_PROMPT,
    ]


def _events(msg: Dict) -> Generator[Dict, None, None]:
    """Translate one claude stream-json message into console events."""
    t = msg.get('type')
    if t == 'system' and msg.get('subtype') == 'init':
        yield {'type': 'start', 'model': msg.get('model'),
               'tools': sum(1 for x in msg.get('tools', [])
                            if str(x).startswith('mcp__bittensor__'))}
    elif t == 'assistant':
        for c in msg.get('message', {}).get('content', []):
            if c.get('type') == 'text' and c.get('text', '').strip():
                yield {'type': 'text', 'text': c['text']}
            elif c.get('type') == 'tool_use':
                yield {'type': 'tool',
                       'name': str(c.get('name', '')).replace('mcp__bittensor__', ''),
                       'args': c.get('input', {})}
    elif t == 'user':
        content = msg.get('message', {}).get('content')
        for c in content if isinstance(content, list) else []:
            if isinstance(c, dict) and c.get('type') == 'tool_result':
                yield {'type': 'tool_done', 'error': bool(c.get('is_error'))}
    elif t == 'result':
        yield {'type': 'done', 'answer': msg.get('result') or '',
               'turns': msg.get('num_turns'), 'ms': msg.get('duration_ms'),
               'cost_usd': msg.get('total_cost_usd')}


def ask(question: str) -> Generator[Dict, None, None]:
    ready, _, hint, extra = ensure_auth()
    if not ready:
        yield {'type': 'error', 'error': hint}
        return
    env = {**os.environ, **extra}
    # keep the child from thinking it's nested inside a Claude Code session
    env.pop('CLAUDECODE', None)
    env.pop('CLAUDE_CODE_ENTRYPOINT', None)
    try:
        proc = subprocess.Popen(build_cmd(question), cwd=ROOT, env=env,
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
