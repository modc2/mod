"""compute chat — ASK the desk, and watch it draw on the map.

The answerer is the local Claude CLI in print mode, sandboxed to this module's
own MCP tools over stdio — and only the read tier of them: the MCP subprocess
is started with COMPUTE_MCP_READONLY=1, so a typed message can search, quote
and map every market but can never rent, stop, exec or touch a node. The CLI's
allow/deny lists say the same thing a second time.

One of those tools is a *display* tool: `compute_show_map` returns a validated
`directive` the console draws — points on the landmask, a camera move, a
caption. `stream()` spots its tool_results in the CLI's stream-json output and
forwards them as `display` events, so only tool-validated JSON ever reaches
the page. Same shape as orbit/nyc's ASK agent.

Owner-only at the API layer: every turn runs an agent on this box with the
operator's Claude credentials.
"""

import json
import os
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))

CHAT_MODEL = os.environ.get('COMPUTE_CHAT_MODEL', 'claude-sonnet-5')
CHAT_TIMEOUT = float(os.environ.get('COMPUTE_CHAT_TIMEOUT', 180))

SYSTEM = (
    'You are the COMPUTE desk agent, inside a console that reads every GPU '
    'rental market at once (Targon, Lium, Akash, Vast, Clore, Nosana, Aleph, '
    'Cathedral, Prime, Polaris, Hyperbolic, RunPod, Fluence, Shadeform). '
    'Answer from the tools, never from memory — prices move hourly. Be short '
    'and concrete: lead with the number, name the market, give offer ids as '
    'provider:ref. Plain text only — no markdown tables or headers; short '
    'paragraphs and simple "-" lists render best in the chat panel. '
    'YOU ALSO DRIVE THE USER\'S MAP. A world map of the offers sits beside '
    'this chat, and each message starts with what it shows right now. '
    'Whenever the question touches place, price or availability, show it: '
    'call compute_show_map with the filters under discussion, and a `focus` '
    'to fly the camera ("Germany", "Des Moines", "europe"; "world" pulls '
    'back out). Requests like "only the US", "just H100s under $2", "zoom '
    'into Europe", "show everything again" are map edits — apply them with '
    'compute_show_map against the current state and confirm in one line. '
    'Markets that publish no location show as "nowhere" — say so, never '
    'guess them onto the map. You cannot rent, stop or touch nodes from this '
    'chat; point the user at the MARKET and NODES tabs for that.')

# Belt and braces with COMPUTE_MCP_READONLY: the CLI itself only allows the
# read tools, and denies the harness's own filesystem/shell surface.
ALLOWED = ','.join('mcp__compute__' + t for t in (
    'compute_providers', 'compute_search', 'compute_map', 'compute_show_map',
    'compute_offer', 'compute_quote', 'compute_mods', 'compute_oracle'))
DENIED = ('Bash,Edit,Write,NotebookEdit,Read,Glob,Grep,WebFetch,WebSearch,'
          'Task,TodoWrite')

DISPLAY_TOOLS = {'mcp__compute__compute_show_map'}


def _mcp_config():
    """The MCP config the CLI points at; per-user state, so off-tree."""
    cfg_dir = os.path.expanduser('~/.mod/compute')
    os.makedirs(cfg_dir, exist_ok=True)
    cfg = os.path.join(cfg_dir, 'chat.mcp.json')
    with open(cfg, 'w') as f:
        json.dump({'mcpServers': {'compute': {
            'command': sys.executable or 'python3',
            'args': [os.path.join(HERE, 'mcp.py')],
            'cwd': HERE,
            'env': {'COMPUTE_MCP_READONLY': '1'},
        }}}, f, indent=2)
    return cfg


def health():
    cli = shutil.which('claude')
    return {'available': bool(cli), 'cli': cli, 'model': CHAT_MODEL,
            'display_tools': sorted(t.split('__')[-1] for t in DISPLAY_TOOLS)}


def _tool_result_json(block):
    """The JSON a display tool returned, out of a stream-json tool_result."""
    if block.get('is_error'):
        return None
    content = block.get('content')
    texts = [content] if isinstance(content, str) else [
        c.get('text', '') for c in (content or []) if isinstance(c, dict)]
    for t in texts:
        try:
            out = json.loads(t)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(out, dict) and isinstance(out.get('directive'), dict):
            return out['directive']
    return None


def stream(message, session_id=None, map_state=None):
    """One turn with the desk agent. Yields event dicts:

        session {id}            pass back as session_id to continue
        text    {text}          a block of the answer
        tool    {name, input}   the agent consulting a market tool
        display {directive}     draw this — validated by compute_show_map
        done    {ms, session_id}
        error   {error}
    """
    message = str(message or '').strip()
    if not message:
        yield {'type': 'error', 'error': 'empty message'}
        return
    if len(message) > 4000:
        yield {'type': 'error', 'error': 'message too long (4000 chars)'}
        return
    if not shutil.which('claude'):
        yield {'type': 'error',
               'error': 'claude CLI not installed on this host'}
        return

    cmd = ['claude', '-p', '--output-format', 'stream-json', '--verbose',
           '--model', CHAT_MODEL,
           '--strict-mcp-config', '--mcp-config', _mcp_config(),
           '--allowedTools', ALLOWED,
           '--disallowedTools', DENIED,
           '--append-system-prompt', SYSTEM,
           '--max-turns', '25']
    if session_id:
        cmd += ['--resume', str(session_id)]
    if map_state:
        state = json.dumps(map_state, default=str)[:2000]
        message = f"[The user's map right now: {state}]\n\n{message}"

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=HERE)
    # a hung agent must not pin the connection open forever
    watchdog = threading.Timer(CHAT_TIMEOUT, proc.kill)
    watchdog.start()
    display_ids = set()
    try:
        proc.stdin.write(message)
        proc.stdin.close()
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get('type')
            if t == 'system' and ev.get('subtype') == 'init':
                yield {'type': 'session', 'id': ev.get('session_id')}
            elif t == 'assistant':
                for block in (ev.get('message') or {}).get('content', []):
                    if block.get('type') == 'text' and block.get('text'):
                        yield {'type': 'text', 'text': block['text']}
                    elif block.get('type') == 'tool_use':
                        # only surface real market tools — the CLI also emits
                        # harness plumbing nobody needs to see
                        name = str(block.get('name', ''))
                        if name in DISPLAY_TOOLS:
                            display_ids.add(block.get('id'))
                        if name.startswith('mcp__compute__'):
                            yield {'type': 'tool',
                                   'name': name.replace('mcp__compute__', ''),
                                   'input': block.get('input') or {}}
            elif t == 'user':
                for block in (ev.get('message') or {}).get('content', []) or []:
                    if not isinstance(block, dict) or \
                            block.get('type') != 'tool_result':
                        continue
                    if block.get('tool_use_id') in display_ids:
                        d = _tool_result_json(block)
                        if d:
                            yield {'type': 'display', 'directive': d}
            elif t == 'result':
                if ev.get('subtype') != 'success':
                    yield {'type': 'error',
                           'error': ev.get('result') or ev.get('subtype')}
                else:
                    yield {'type': 'done', 'ms': ev.get('duration_ms'),
                           'session_id': ev.get('session_id')}
        rc = proc.wait(timeout=10)
        if rc != 0:
            tail = (proc.stderr.read() or '')[-400:]
            yield {'type': 'error', 'error': f'agent exited {rc}: {tail}'}
    except GeneratorExit:
        # client went away mid-answer — don't leave the agent running
        proc.kill()
        raise
    except Exception as e:
        yield {'type': 'error', 'error': f'{type(e).__name__}: {e}'}
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
