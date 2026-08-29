#!/usr/bin/env python3
"""agent mcp — this module's own API, spoken as Model Context Protocol.

Interlaced, not bolted on: every tool below is a thin call into the same
handler the REST route calls, and through it into `Mod.forward()`. Nothing is
reimplemented here, which is the whole point — the permission gate, the credit
meter, the task registry and the write sandbox are the module's, so an MCP
client is exactly as privileged as an HTTP client holding the same token, and
neither can drift away from the other when a rule changes.

    agent_run          run the agent loop
    agent_task         watch a run that outlived the call
    agent_agents       the personas, and what each is built from
    agent_build        write a new one
    agent_parts        the live agent box: model, memory, toolbox, prompt
    agent_tools        the whole registry — shipped, custom, and the fleet
    agent_toolbox      the bundles, and snapping one on
    agent_tool_run     call one tool directly, without a model in the loop
    agent_recall       facts, scored against a query
    agent_retrieve     every memory layer at once, ranked
    agent_remember     write a fact future runs will find
    agent_memory       the layers themselves: state, episodes, dialogue
    agent_library      prompts, tool documents, memory notes, agents
    agent_discover     scan GitHub / npm / the MCP registry for tools
    agent_install      keep one
    agent_arena        the board: agents, models, tasks, matches
    agent_arena_run    play a match
    agent_modules      the fleet's audit surface
    agent_vault        the caller's own key-value vaults
    agent_whoami       who this token is, and what it may spend

Two transports, one dispatch:

    POST /mcp                          streamable HTTP, mounted by src/api/api.py
    python3 src/mcp.py                 stdio, newline-delimited JSON-RPC 2.0

Connect a client:

    claude mcp add --transport http agent https://modc2.com/api/agent/mcp \\
        --header "Authorization: Bearer $MOD_TOKEN"
    claude mcp add agent -- python3 /root/mod/mod/orbit/agent/src/mcp.py

Auth is the module's, too. Reads are open to anyone; running the loop, writing
a fact or calling a shell tool wants a signed protocol-auth token, carried
either as an `Authorization: Bearer …` header on the HTTP transport or as a
`key` argument on any single tool. A tool that is refused says so in its own
result rather than failing the call, so the model can read the reason.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))          # …/agent/src
MODULE_ROOT = os.path.abspath(os.path.join(HERE, '..'))    # …/orbit/agent
MOD_ROOT = os.path.abspath(os.path.join(MODULE_ROOT, '..', '..', '..'))
for p in (MODULE_ROOT, MOD_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    with open(os.path.join(MODULE_ROOT, 'config.json')) as f:
        VERSION = json.load(f).get('version', '0.0.0')
except Exception:
    VERSION = '0.0.0'

SERVER_INFO = {'name': 'agent', 'title': 'Agent', 'version': VERSION}

# The version we speak. A client asking for an older one we still understand is
# answered in that version; anything unrecognised gets ours, which the spec
# defines as "negotiate down or disconnect".
PROTOCOL_VERSION = '2025-06-18'
SUPPORTED_PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')

CAPABILITIES = {
    'tools': {'listChanged': False},
    'resources': {'listChanged': False, 'subscribe': False},
    'prompts': {'listChanged': False},
}

INSTRUCTIONS = (
    'An autonomous coding agent you can drive from here. agent_run is the point '
    'of the server: it runs the loop — a persona, a model, a toolbox and a memory '
    'module — and returns the trace of what it actually did. A run can outlive a '
    'single MCP call, so agent_run takes `wait` and `timeout` and hands back a '
    'task_id when it needs longer; agent_task then follows it to the end. '
    'Before running, agent_agents says which personas exist and agent_parts says '
    'what the live one is built from; agent_tools is the registry those tools come '
    'from and agent_tool_run calls one directly when no model is needed. '
    'agent_recall / agent_retrieve read the memory the agent thinks with, and '
    'agent_remember writes to it. Reads are open; running, writing and calling a '
    'shell tool need a signed token — pass it as `key` or as an Authorization: '
    'Bearer header, and check it with agent_whoami. Runs by a non-owner are billed '
    "against that address's credits, so free=true (a zero-cost model) is the "
    'polite default when you are only trying something out.'
)


# ── the module underneath ────────────────────────────────────────────
#
# The API process imports this file; this file must therefore never import it
# at module scope. It is looked up in sys.modules first for a stronger reason
# than circularity: uvicorn loads it as top-level `api`, and importing
# `src.api.api` here would build a SECOND module object with its own Mod and
# its own task registry — runs started over MCP would then be invisible to the
# console. One process, one API module, one registry.

def _api():
    """The already-loaded API module, whatever name it was loaded under."""
    for name in ('api', 'src.api.api'):
        m = sys.modules.get(name)
        if m is not None and hasattr(m, 'get_mod'):
            return m
    from src.api import api as a       # noqa: E402 — stdio: nobody loaded it yet
    return a


_STANDALONE_MOD = None


def _mod():
    """The Mod singleton — the API's if there is one, else our own."""
    global _STANDALONE_MOD
    try:
        return _api().get_mod()
    except Exception:
        if _STANDALONE_MOD is None:          # stdio without fastapi installed
            from src.mod import Mod
            _STANDALONE_MOD = Mod()
        return _STANDALONE_MOD


def _fwd(action: str, key=None, **kw):
    """One forward() call, with the module's own permission gate in front."""
    return _mod().forward(action, key=key, **kw)


# A connection that carried no token at all is an anonymous stranger, not the
# server — the same rule the REST API states in `signed_in`. It matters here
# because forward() reads a key of None as "the process itself", which is right
# for a CLI call and wrong for anything that arrived over the network. A stdio
# server IS a local process someone started on the host, so it keeps the CLI
# reading; the HTTP transport does not.
LOCAL = False


def _signed_in(key) -> bool:
    return bool(key and str(key).strip())


def _clean(d):
    """Agent configs carry a class object that will not serialise."""
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if k != 'cls'}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


def _trim(v, limit=600):
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= limit else s[:limit] + f'… (+{len(s) - limit} chars)'


# ── running the loop ─────────────────────────────────────────────────

def _run_model(a: dict, mod) -> Optional[str]:
    """The model a run should use when the caller named none.

    RunRequest carries a hard default, so passing it through blindly would
    override the model an agent was *built* with — the console always names one,
    an MCP client usually will not.
    """
    if a.get('model'):
        return a['model']
    name = a.get('agent')
    if name:
        try:
            cfg = mod.agents.get(name) or {}
            if cfg.get('model'):
                return cfg['model']
        except Exception:
            pass
    try:
        return mod.DEFAULT_MODELS.get(a.get('provider') or 'openrouter')
    except Exception:
        return None


def _brief(step) -> dict:
    """One step, small enough that a 25-step trace is still readable."""
    if not isinstance(step, dict):
        return {'step': _trim(step, 300)}
    out = {'tool': step.get('tool')}
    params = step.get('params')
    if isinstance(params, dict) and params:
        out['params'] = {k: _trim(v, 200) for k, v in params.items()}
    if step.get('result') is not None:
        out['result'] = _trim(step['result'])
    if step.get('error'):
        out['error'] = _trim(step['error'], 300)
    return out


def _task_row(api, task_id: str) -> dict:
    try:
        return api.get_task(task_id)
    except Exception:
        return {}


def _t_run(a: dict, key):
    api = _api()
    mod = _mod()
    query = (a.get('query') or '').strip()
    if not query:
        return {'error': 'query is required — say what the agent should do'}

    wait = a.get('wait', True)
    timeout = max(5, min(int(a.get('timeout') or 120), 900))
    full = bool(a.get('full'))

    req = api.RunRequest(
        query=query,
        key=key,
        agent_type=a.get('agent'),
        model=_run_model(a, mod) or 'anthropic/claude-opus-5',
        provider=a.get('provider'),
        steps=int(a.get('steps') or 10),
        tools=a.get('tools'),
        toolbox=a.get('toolbox'),
        prompt=a.get('prompt'),
        memory=a.get('memory'),
        memory_ids=a.get('memory_ids'),
        tool_ids=a.get('tool_ids'),
        temperature=float(a.get('temperature') or 0.0),
        free=bool(a.get('free')),
        session=a.get('session'),
    )

    before = set(api.TASKS)
    box: Dict[str, Any] = {}

    def _go():
        try:
            box['result'] = api.run_agent(req)
        except Exception as e:                       # pragma: no cover - defensive
            box['error'] = f'{type(e).__name__}: {e}'

    th = threading.Thread(target=_go, daemon=True, name='mcp-run')
    th.start()

    # The task id is minted inside run_agent, so it is read back off the
    # registry rather than handed to us — which is also how a run that outruns
    # this call stays followable with agent_task.
    task_id = None
    deadline = time.time() + 3
    while task_id is None and time.time() < deadline:
        new = [t for t in api.TASKS if t not in before]
        if new:
            task_id = new[-1]
            break
        if not th.is_alive():
            break
        time.sleep(0.05)

    th.join(timeout if wait else 0.5)

    if th.is_alive():
        row = _task_row(api, task_id) if task_id else {}
        return {'status': 'running', 'task_id': task_id, 'query': query,
                'agent': row.get('agent_type'), 'model': row.get('model'),
                'steps': row.get('steps', 0), 'trace': row.get('trace', []),
                'waited_seconds': timeout if wait else 0,
                'hint': 'the run is still going — poll agent_task with this '
                        'task_id, or raise `timeout` next time'}

    if box.get('error'):
        return {'status': 'error', 'task_id': task_id, 'error': box['error']}

    out = box.get('result') or {}
    if out.get('error'):
        return {'status': 'error', 'task_id': out.get('task_id') or task_id,
                'error': out['error'], 'code': out.get('code')}

    row = _task_row(api, out.get('task_id') or task_id or '')
    steps = out.get('result') if isinstance(out.get('result'), list) else []
    if out.get('chain'):
        steps = [s for r in (out.get('results') or []) for s in (r.get('result') or [])]
    return {
        'status': row.get('status', 'done'),
        'task_id': out.get('task_id'),
        'query': query,
        'agent': out.get('agent_type') or row.get('agent_type'),
        'model': row.get('model'),
        'summary': row.get('summary'),
        'step_count': len(steps),
        'trace': steps if full else [_brief(s) for s in steps],
        'charged': out.get('charged'),
    }


def _t_task(a: dict, key):
    api = _api()
    tid = a.get('id')
    if not tid:
        return api.list_tasks(limit=int(a.get('limit') or 25))
    row = api.get_task(tid)
    if row.get('error'):
        return row
    if not a.get('full'):
        row = {**row, 'trace': (row.get('trace') or [])[-30:]}
    return row


# ── what the agent is made of ────────────────────────────────────────

def _t_agents(a: dict, key):
    mod = _mod()
    name = a.get('name')
    if name:
        try:
            return _clean(_fwd('agent', key, name=name))
        except KeyError:
            return {'error': f'no agent named {name!r}', 'available': mod.agents.ls()}
    out = _clean(_fwd('agents', key))
    out['default'] = mod.default_agent(key)
    return out


def _t_build(a: dict, key):
    api = _api()
    name = (a.get('name') or '').strip()
    if not name:
        return {'error': 'name is required'}
    exists = name in _mod().agents.ls()
    if exists and not a.get('update'):
        return {'error': f'{name!r} already exists — pass update=true to change it',
                'agent': _clean(_mod().agents.get(name))}
    if exists:
        return _clean(api.update_agent(name, api.AgentUpdateRequest(
            description=a.get('description'), goal=a.get('prompt') or a.get('goal'),
            icon=a.get('icon'), tools=a.get('tools'), model=a.get('model'),
            memory=a.get('memory'), harness=a.get('harness'), key=key)))
    return _clean(api.create_agent(api.AgentCreateRequest(
        name=name, description=a.get('description') or '',
        goal=a.get('prompt') or a.get('goal') or '', icon=a.get('icon') or '>_',
        tools=a.get('tools'), model=a.get('model'), memory=a.get('memory'),
        harness=a.get('harness'), key=key)))


def _t_parts(a: dict, key):
    return _clean(_fwd('parts', key))


def _t_tools(a: dict, key):
    api = _api()
    if a.get('installed'):
        return _fwd('installed_tools', key)
    out = api.list_tools(mods=bool(a.get('fleet')), q=a.get('q') or '',
                         limit=int(a.get('limit') or 40))
    tools = [{k: v for k, v in t.items() if k != 'params'} if a.get('brief') else t
             for t in out.get('tools', [])]
    return {**out, 'tools': tools}


def _t_toolbox(a: dict, key):
    op = (a.get('op') or 'list').lower()
    name = a.get('name') or ''
    if op == 'list':
        return _fwd('toolboxes', key)
    if op == 'get':
        return _fwd('toolbox', key, name=name)
    if op == 'snap':
        return _fwd('snap', key, name=name)
    if op == 'unsnap':
        return _fwd('unsnap', key, name=name or None)
    if op == 'select':
        return _fwd('select', key, tools=a.get('tools'))
    if op == 'save':
        return _fwd('toolbox_add', key, name=name, tools=a.get('tools') or [],
                    description=a.get('description') or '')
    if op == 'remove':
        return _fwd('toolbox_rm', key, name=name)
    return {'error': f'unknown op {op!r}',
            'options': ['list', 'get', 'snap', 'unsnap', 'select', 'save', 'remove']}


def _t_tool_run(a: dict, key):
    """One tool, called straight — the console's 'try it', over MCP.

    Routed through the API handler on purpose: that is where the write sandbox
    lives, so a non-owner calling `write` here lands in their own directory
    exactly as they would over HTTP.
    """
    api = _api()
    name = (a.get('name') or '').strip()
    if not name:
        return {'error': 'name is required — agent_tools lists them'}
    return api.run_tool(name, api.ToolRunRequest(params=a.get('params') or {}, key=key))


# ── memory ───────────────────────────────────────────────────────────

def _t_recall(a: dict, key):
    return _fwd('recall', key, query=a.get('query') or a.get('q') or '',
                k=int(a.get('k') or 5))


def _t_retrieve(a: dict, key):
    return _fwd('retrieve', key, query=a.get('query') or a.get('q') or '',
                k=int(a.get('k') or 5), layers=a.get('layers'),
                session=a.get('session'), min_score=a.get('min_score'))


def _t_remember(a: dict, key):
    return _fwd('remember', key, name=a.get('name') or '',
                content=a.get('content') or '', tags=a.get('tags'))


def _t_memory(a: dict, key):
    op = (a.get('op') or 'state').lower()
    if op == 'state':
        return _fwd('memory_state', key)
    if op == 'episodes':
        return _fwd('episodes', key, n=int(a.get('n') or 50), session=a.get('session'))
    if op == 'exchanges':
        return _fwd('exchanges', key, n=int(a.get('n') or 20), session=a.get('session'))
    if op == 'facts':
        return _fwd('facts', key)
    if op == 'forget':
        return _fwd('forget', key, id=a.get('id') or '')
    if op == 'modules':
        return _fwd('memories', key, name=a.get('name'))
    if op == 'notes':
        return _fwd('memory', key)
    return {'error': f'unknown op {op!r}',
            'options': ['state', 'episodes', 'exchanges', 'facts', 'forget',
                        'modules', 'notes']}


# ── library, aggregator ──────────────────────────────────────────────

def _t_library(a: dict, key):
    return _fwd('library', key, q=a.get('q'), kind=a.get('kind'), tag=a.get('tag'))


def _t_discover(a: dict, key):
    if a.get('id'):
        return _fwd('discover_detail', key, id=a['id'])
    return _fwd('discover', key, q=a.get('q') or '', sources=a.get('sources'),
                limit=int(a.get('limit') or 20), kind=a.get('kind'),
                fresh=bool(a.get('fresh')))


def _t_install(a: dict, key):
    return _fwd('tool_install', key, id=a.get('id') or '', path=a.get('path'))


# ── arena ────────────────────────────────────────────────────────────

def _t_arena(a: dict, key):
    op = (a.get('op') or 'board').lower()
    if op == 'board':
        return _fwd('arena', key)
    if op == 'status':
        return _fwd('arena_status', key)
    if op == 'tasks':
        return _fwd('arena_tasks', key)
    if op == 'matches':
        return _fwd('arena_matches', key, limit=int(a.get('limit') or 25),
                    agent=a.get('agent'), task=a.get('task'))
    if op == 'agent':
        return _fwd('arena_card', key, agent=a.get('agent') or '')
    if op == 'models':
        return _fwd('arena_models', key)
    if op == 'model':
        return _fwd('arena_model', key, model=a.get('model') or '')
    if op == 'task_board':
        return _fwd('arena_task_board', key)
    if op == 'openarena':
        return _fwd('openarena', key)
    return {'error': f'unknown op {op!r}',
            'options': ['board', 'status', 'tasks', 'matches', 'agent', 'models',
                        'model', 'task_board', 'openarena']}


def _t_arena_run(a: dict, key):
    return _fwd('arena_run', key, agent=a.get('agent'), task=a.get('task'),
                model=a.get('model'), steps=a.get('steps'),
                free=a.get('free', True), reason='mcp')


# ── the fleet's audit surface ────────────────────────────────────────

def _t_modules(a: dict, key):
    op = (a.get('op') or 'list').lower()
    if op == 'list':
        return _fwd('modules', key, q=a.get('q') or '')
    if op == 'tree':
        return _fwd('module_tree', key, name=a.get('name') or '')
    if op == 'file':
        return _fwd('module_file', key, name=a.get('name') or '',
                    path=a.get('path') or '')
    return {'error': f'unknown op {op!r}', 'options': ['list', 'tree', 'file']}


# ── the caller's own things ──────────────────────────────────────────

def _t_vault(a: dict, key):
    op = (a.get('op') or 'list').lower()
    name = a.get('name') or ''
    if op == 'list':
        return _fwd('vaults', key)
    if op == 'get':
        return _fwd('vaults_get', key, name=name, reveal=bool(a.get('reveal')))
    if op == 'create':
        return _fwd('vaults_add', key, name=name)
    if op == 'set':
        return _fwd('vaults_set', key, name=name, entry=a.get('entry') or '',
                    value=a.get('value') or '', private=bool(a.get('private', True)))
    if op == 'remove':
        return _fwd('vaults_rm', key, name=name)
    if op == 'remove_entry':
        return _fwd('vaults_key_rm', key, name=name, entry=a.get('entry') or '')
    if op == 'public':
        return _fwd('vaults_public', key, address=a.get('address') or '', name=name)
    return {'error': f'unknown op {op!r}',
            'options': ['list', 'get', 'create', 'set', 'remove', 'remove_entry',
                        'public']}


def _t_whoami(a: dict, key):
    api = _api()
    who = api.whoami(key=key)
    try:
        who['credits'] = _fwd('credits', key)
    except Exception:
        pass
    return who


# ── schemas ──────────────────────────────────────────────────────────

def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _num(desc):
    return {'type': 'number', 'description': desc}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


def _list(desc):
    return {'type': 'array', 'items': {'type': 'string'}, 'description': desc}


_KEY = _str('signed protocol-auth token, when this call needs one and the '
            'transport carries no Authorization header')

TOOLS: Dict[str, dict] = {
    'agent_run': {
        'auth': True,
        'description': 'Run the agent loop and return what it did, step by step. '
                       'Every other tool here describes or changes the thing this one '
                       'runs. A run is a persona (`agent`), a model, a toolbox and a '
                       'memory module; name only what you want to differ from the '
                       "agent's own build. Runs take minutes, so `wait`/`timeout` "
                       'bound the call: when the budget runs out you get the task_id '
                       'and the trace so far, and agent_task follows it to the end. '
                       'Needs a token; a non-owner run is billed against that '
                       "address's credits unless free=true picks a zero-cost model.",
        'inputSchema': {'type': 'object', 'properties': {
            'query': _str('what the agent should do — the whole instruction'),
            'agent': _str('persona to run as, from agent_agents (default: the '
                          "module's default agent). A harness agent hands the run "
                          'to Claude Code or Codex instead of this loop'),
            'model': _str('provider model id, e.g. anthropic/claude-opus-5 — '
                          "omit to use the agent's own model"),
            'provider': _str('openrouter | venice | liquidai | … (default openrouter)'),
            'steps': _num('step budget for the run (default 10)'),
            'tools': _list('exact tool names this run may call — omit for the '
                           "agent's toolbox"),
            'toolbox': _str('snap one bundle on for this run: core, explore, code, '
                            'verify, vcs, web, memory, meta, or a saved one'),
            'prompt': _str("system prompt for this run, overriding the agent's"),
            'memory': _str('memory module: default (persists) | ephemeral (retrieves '
                           'during the run, written nowhere)'),
            'memory_ids': _list('library memory notes to inject as context'),
            'tool_ids': _list('installed tool documents to inject as context'),
            'session': _str('conversation id — makes the run a remembered exchange '
                            'that later runs can recall'),
            'free': _bool('run on a zero-cost model: never billed, never gated on '
                          'a credit balance'),
            'temperature': _num('sampling temperature (default 0)'),
            'wait': _bool('block for the run (default true); false returns the '
                          'task_id straight away'),
            'timeout': _num('seconds to wait before handing back a task_id '
                            '(default 120, max 900)'),
            'full': _bool('return every step whole instead of trimmed'),
            'key': _KEY,
        }, 'required': ['query']},
        'handler': _t_run,
    },
    'agent_task': {
        'description': 'A run in the server-side registry: its status, step count, '
                       'and the trail of tools it has called. With no id, the recent '
                       'runs across every client, running first. Runs keep going '
                       'server-side after the call that started them returns, so this '
                       'is how a long agent_run is followed to the end.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('task id from agent_run'),
            'limit': _num('how many rows when listing (default 25)'),
            'full': _bool('the whole trace instead of the last 30 steps'),
        }},
        'handler': _t_task,
    },
    'agent_agents': {
        'description': 'The personas a run can be made as, each with its owner, '
                       'model, toolbox, memory module and system prompt — plus which '
                       'one an unnamed run lands on. With `name`, that one in full. '
                       'An agent with a harness does not run on this loop at all: it '
                       'hands the whole run to a coding CLI or another console.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('one agent instead of the list'), 'key': _KEY}},
        'handler': _t_agents,
    },
    'agent_build': {
        'auth': True,
        'description': 'Write a new agent, or change one you wrote. An agent is a '
                       'name, an icon, a system prompt (`prompt`), a model, a toolbox '
                       'and a memory module — every part optional, and an empty '
                       'toolbox means every tool rather than none. Signed in only: '
                       'the agent is filed under the address that made it, and only '
                       'that address (or the host) can change it afterwards.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('agent name, lowercase-kebab'),
            'description': _str('one line: what it is for'),
            'prompt': _str('its system prompt — the thing that makes it a persona'),
            'icon': _str('a short glyph shown beside it in the console'),
            'model': _str('the model it is built with'),
            'tools': _list('the exact tools it may use (omit = all of them)'),
            'memory': _str('default | ephemeral'),
            'harness': _str('hand its runs to a CLI instead: claude | codex | '
                            'claudemod | buildmod (host only)'),
            'update': _bool('true to change an agent that already exists'),
            'key': _KEY,
        }, 'required': ['name']},
        'handler': _t_build,
    },
    'agent_parts': {
        'description': 'The live agent box: the model it will use, the memory module '
                       'it thinks with, the toolbox snapped onto it, the tools that '
                       'leaves it holding, and the prompt underneath. Read this '
                       'before agent_run when you want to know what a bare run will '
                       'actually be.',
        'inputSchema': {'type': 'object', 'properties': {'key': _KEY}},
        'handler': _t_parts,
    },
    'agent_tools': {
        'description': 'The whole tool surface in one list, with three kinds in it: '
                       'the tools shipped in this module, custom shell tools added on '
                       'the host, and — with fleet=true — every mod-protocol module '
                       'on the box, callable as mod.<name>. The fleet is potential, '
                       'not loaded: hundreds of modules would drown a prompt, so it '
                       'is searched server-side with `q` and reaches a run only once '
                       'switched on. `active` says what the model gets right now.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('filter by name/description'),
            'fleet': _bool('include the fleet — needs a q to be useful'),
            'installed': _bool('instead: the tool DOCUMENTS installed from the '
                               'aggregator (text, never executable)'),
            'brief': _bool('drop parameter schemas from the listing'),
            'limit': _num('how many (default 40)'),
        }},
        'handler': _t_tools,
    },
    'agent_toolbox': {
        'auth': {'snap', 'unsnap', 'select', 'save', 'remove'},
        'description': 'Tool bundles: list them, snap one onto the live agent, refine '
                       'that into an exact list, or save what you landed on as a named '
                       'box to snap back later. Snapping changes what every later run '
                       'on this module gets, so it is admin — a single run is better '
                       "narrowed with agent_run's own `toolbox`/`tools`.",
        'inputSchema': {'type': 'object', 'properties': {
            'op': _str('what to do', enum=['list', 'get', 'snap', 'unsnap', 'select',
                                           'save', 'remove']),
            'name': _str('the box, for get / snap / unsnap / save / remove'),
            'tools': _list('tool names, for save — or for select, which pins the '
                           'loadout to exactly this list (empty = back to the boxes)'),
            'description': _str('one line, when saving a box'),
            'key': _KEY,
        }},
        'handler': _t_toolbox,
    },
    'agent_tool_run': {
        'description': "Call one of the agent's tools directly, with no model in the "
                       'loop — read a file, grep a tree, run a test, call a fleet '
                       'module. A shipped tool is open to anyone (writes land inside '
                       "the caller's own sandbox); a custom shell tool or a fleet "
                       'module runs code on the host and is admin. Params are the '
                       "tool's own, from agent_tools.",
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('tool name, e.g. read, grep, bash, mod.chain'),
            'params': {'type': 'object', 'description': "the tool's arguments"},
            'key': _KEY,
        }, 'required': ['name']},
        'handler': _t_tool_run,
    },
    'agent_recall': {
        'description': 'Facts the agent has stored, scored against a query — the same '
                       'call its own `recall` tool makes mid-run. For the wider read '
                       'across every layer at once, use agent_retrieve.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': _str('what to look for'),
            'k': _num('how many hits (default 5)'),
        }, 'required': ['query']},
        'handler': _t_recall,
    },
    'agent_retrieve': {
        'description': 'One ranking engine over every memory layer at once: working, '
                       'episodic (steps already taken), dialogue (what was said) and '
                       'semantic (facts). Scores are comparable across layers, which '
                       'is the point — "have I done this" and "do I know this" come '
                       'back on the same scale. Dialogue hits are scoped to the '
                       'caller, so a token gets its own turns and nobody else\'s.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': _str('what to look for'),
            'k': _num('hits per layer (default 5)'),
            'layers': _list('restrict to some layers: working, episodic, dialogue, '
                            'semantic'),
            'session': _str('a conversation to scope dialogue hits to'),
            'min_score': _num('drop hits below this score'),
            'key': _KEY,
        }, 'required': ['query']},
        'handler': _t_retrieve,
    },
    'agent_remember': {
        'auth': True,
        'description': 'Store a durable fact in the semantic layer — something later '
                       'runs should find with agent_recall without being told again. '
                       'Admin: it writes to the memory every run on this module reads.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('short title for the fact'),
            'content': _str('the fact itself'),
            'tags': _list('tags to file it under'),
            'key': _KEY,
        }, 'required': ['name', 'content']},
        'handler': _t_remember,
    },
    'agent_memory': {
        'auth': {'forget'},
        'description': 'The memory subsystem itself rather than a search over it: '
                       "layer state and sizes, the step trail, the caller's own "
                       'conversation turns, the stored facts, the memory modules an '
                       'agent can be built with, and the library notes you can attach '
                       'to a run.',
        'inputSchema': {'type': 'object', 'properties': {
            'op': _str('which read', enum=['state', 'episodes', 'exchanges', 'facts',
                                           'forget', 'modules', 'notes']),
            'n': _num('how many rows, for episodes / exchanges'),
            'session': _str('scope to one conversation'),
            'id': _str('fact id, for forget'),
            'name': _str('one memory module, for modules'),
            'key': _KEY,
        }},
        'handler': _t_memory,
    },
    'agent_library': {
        'description': 'The unified market: prompts, installed tool documents, memory '
                       'notes and shareable agents in one index, each with its owner '
                       'and its localfs CID. Filter by kind and tag, or search the '
                       'lot. The ids that come back are what agent_run takes as '
                       'memory_ids / tool_ids.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('free-text search'),
            'kind': _str('narrow to one kind', enum=['prompt', 'tool', 'memory', 'agent']),
            'tag': _str('narrow to one tag'),
        }},
        'handler': _t_library,
    },
    'agent_discover': {
        'description': 'Scan the internet for tools an agent could use: GitHub repos '
                       'and skill topics, the anthropics/skills catalog, npm, the MCP '
                       'registry, Glama and curated awesome-lists, merged across '
                       'platforms. Results are DOCUMENTS, not executable code — '
                       'installing one attaches it to a run as context. Pass `id` for '
                       'the full record of a single result.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('what the tool should do, e.g. "pdf extraction"'),
            'sources': _list('restrict to some sources: github, skills, npm, mcp, '
                             'glama, awesome'),
            'kind': _str('narrow the result kind, e.g. skill | mcp | package'),
            'limit': _num('how many results (default 20)'),
            'fresh': _bool('bypass the cache'),
            'id': _str('instead: the full record for one result id'),
            'key': _KEY,
        }},
        'handler': _t_discover,
    },
    'agent_install': {
        'auth': True,
        'description': 'Keep a scanned result: installs it into the library as a tool '
                       'document, which agent_run can then attach with tool_ids. '
                       'Nothing executable is fetched or run — a SKILL.md is '
                       'instructions, and handing it to the model is how it is used. '
                       'Signed in: the document is filed under your address.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('result id from agent_discover, e.g. gh:owner/repo:skills/pdf'),
            'path': _str('explicit SKILL.md path inside the repo, when the scan found '
                         'several'),
            'key': _KEY,
        }, 'required': ['id']},
        'handler': _t_install,
    },
    'agent_arena': {
        'description': 'The board: every agent playing the same tasks in the same '
                       'seeded scratch directory under the same step budget, scored '
                       'by deterministic checks (0.7 correctness, 0.2 reliability, '
                       '0.1 unspent budget) and rated pairwise with Elo. Read it four '
                       'ways: by agent (board / agent), by model (models / model), by '
                       'task (tasks / task_board), or as the match log (matches).',
        'inputSchema': {'type': 'object', 'properties': {
            'op': _str('which read', enum=['board', 'status', 'tasks', 'matches',
                                           'agent', 'models', 'model', 'task_board',
                                           'openarena']),
            'agent': _str('agent name, for agent / matches'),
            'model': _str('model id, for model'),
            'task': _str('task key, for matches'),
            'limit': _num('how many matches (default 25)'),
        }},
        'handler': _t_arena,
    },
    'agent_arena_run': {
        'auth': True,
        'description': 'Play a match now: one agent on one task, or the whole field '
                       'when neither is named. Admin — a round spends real steps on '
                       "the host's provider key, which is why free defaults to true "
                       'here. A match the provider failed is replayed and then voided '
                       'rather than scored as a loss.',
        'inputSchema': {'type': 'object', 'properties': {
            'agent': _str('one agent, or omit for the field'),
            'task': _str('one task, or omit for the rotation'),
            'model': _str('model to play on'),
            'steps': _num('step budget for the match'),
            'free': _bool('use a zero-cost model (default true here)'),
            'key': _KEY,
        }},
        'handler': _t_arena_run,
    },
    'agent_modules': {
        'description': 'The fleet as an audit surface: every module on the host with '
                       'its visibility, the file tree of a public one, and the source '
                       'of a single file. Public means auditable — anyone may read '
                       'it, signed in or not — while secrets, build output and '
                       'anything outside a module directory are withheld from this '
                       'surface regardless of who asks. A private module is still '
                       'listed by name, since that it exists is not the secret.',
        'inputSchema': {'type': 'object', 'properties': {
            'op': _str('list | tree | file', enum=['list', 'tree', 'file']),
            'name': _str('module name, for tree / file'),
            'path': _str('file path within the module, for file'),
            'q': _str('filter the list'),
        }},
        'handler': _t_modules,
    },
    'agent_vault': {
        'auth': True,
        'description': "The caller's own key-value vaults, persisted through the "
                       'store module: public entries anyone can read and private ones '
                       'sealed with AES-GCM, which is where an API key belongs. '
                       'Self-scoped to the verified address behind your token — there '
                       'is no way to read another address\'s private entries, and '
                       'reveal=true unseals only your own.',
        'inputSchema': {'type': 'object', 'properties': {
            'op': _str('what to do', enum=['list', 'get', 'create', 'set', 'remove',
                                           'remove_entry', 'public']),
            'name': _str('vault name'),
            'entry': _str('entry key, for set / remove_entry'),
            'value': _str('entry value, for set'),
            'private': _bool('seal the value (default true)'),
            'reveal': _bool('unseal private values, for get'),
            'address': _str('whose public entries to read, for public'),
            'key': _KEY,
        }},
        'handler': _t_vault,
    },
    'agent_whoami': {
        'description': 'What this connection is: whether the token resolves, the '
                       'address behind it, whether that address is the host, and the '
                       'credit balance a billed run would draw on. Call it first when '
                       'a tool answers with a permission error — an unsigned '
                       'connection is an anonymous stranger here, not the server.',
        'inputSchema': {'type': 'object', 'properties': {'key': _KEY}},
        'handler': _t_whoami,
    },
}


def tool_list() -> List[dict]:
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


# ── resources: the live module, readable as documents ────────────────

RESOURCES = [
    {'uri': 'agent://parts', 'name': 'the agent box',
     'description': 'model, memory module, toolbox, tools and prompt of the live agent',
     'mimeType': 'application/json'},
    {'uri': 'agent://tools', 'name': 'tool registry',
     'description': 'shipped and custom tools, with the current loadout marked',
     'mimeType': 'application/json'},
    {'uri': 'agent://agents', 'name': 'agent registry',
     'description': 'every persona a run can be made as',
     'mimeType': 'application/json'},
    {'uri': 'agent://arena/board', 'name': 'arena board',
     'description': 'the current standings, agents ranked by Elo',
     'mimeType': 'application/json'},
    {'uri': 'agent://docs/mcp', 'name': 'MCP notes',
     'description': 'how this server is wired to the API (docs/mcp.md)',
     'mimeType': 'text/markdown'},
    {'uri': 'agent://docs/uploads', 'name': 'upload format',
     'description': 'what a prompt / tool / memory / agent file may look like',
     'mimeType': 'text/markdown'},
    {'uri': 'agent://docs/arena', 'name': 'arena rules',
     'description': 'how a match is scored and rated',
     'mimeType': 'text/markdown'},
]

_DOCS = {'agent://docs/mcp': 'mcp.md', 'agent://docs/uploads': 'uploads.md',
         'agent://docs/arena': 'arena.md'}


def read_resource(uri: str, key=None) -> dict:
    """One resource, as an MCP contents entry."""
    if uri in _DOCS:
        path = os.path.join(MODULE_ROOT, 'docs', _DOCS[uri])
        try:
            with open(path) as f:
                return {'uri': uri, 'mimeType': 'text/markdown', 'text': f.read()}
        except OSError as e:
            raise ValueError(f'{uri}: {e}')
    live = {'agent://parts': lambda: _clean(_fwd('parts', key)),
            'agent://tools': lambda: _t_tools({'brief': True}, key),
            'agent://agents': lambda: _clean(_fwd('agents', key)),
            'agent://arena/board': lambda: _fwd('arena', key)}
    if uri not in live:
        raise ValueError(f'unknown resource: {uri}')
    return {'uri': uri, 'mimeType': 'application/json',
            'text': json.dumps(live[uri](), indent=2, default=str)}


# ── prompts: the module's own prompt library, as MCP prompts ─────────
#
# The library is already a shelf of system prompts with owners and CIDs, so
# re-listing it here costs nothing and means a client's slash-command menu is
# the same shelf the console shows.

def _slug(name: str) -> str:
    """A prompt name a client can type after a slash."""
    out = ''.join(c if c.isalnum() else '-' for c in str(name).lower())
    return '-'.join(x for x in out.split('-') if x)[:60]


def prompt_list(key=None) -> List[dict]:
    try:
        items = (_fwd('prompts', key) or {}).get('prompts') or []
    except Exception:
        return []
    out = []
    for p in items:
        slug = _slug(p.get('name') or p.get('id') or '')
        if not slug:
            continue
        out.append({'name': slug,
                    'title': p.get('name') or slug,
                    'description': (p.get('description') or '')[:300],
                    'arguments': [{'name': 'task', 'required': False,
                                   'description': 'what to do under this prompt'}]})
    return out


def prompt_get(name: str, args: dict, key=None) -> dict:
    items = (_fwd('prompts', key) or {}).get('prompts') or []
    hit = next((p for p in items
                if name in (str(p.get('name')), str(p.get('id')),
                            _slug(p.get('name') or ''))), None)
    if not hit:
        raise ValueError(f'unknown prompt: {name}')
    text = hit.get('text') or ''
    task = (args or {}).get('task')
    if task:
        text = f'{text}\n\n---\n{task}'
    return {'description': hit.get('description') or hit.get('name') or name,
            'messages': [{'role': 'user',
                          'content': {'type': 'text', 'text': text}}]}


# ── JSON-RPC 2.0 ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name: str, args: dict, key=None):
    """Run one tool. The per-call `key` argument beats the transport's."""
    tool = TOOLS.get(name)
    if not tool:
        raise ValueError(f'unknown tool: {name} — have {", ".join(TOOLS)}')
    args = dict(args or {})
    key = args.pop('key', None) or key
    need = tool.get('auth')
    if need and not LOCAL and not _signed_in(key):
        # `auth` is either the whole tool or the ops of it that write
        if need is True or str(args.get('op') or '').lower() in need:
            raise PermissionError(
                'this call needs a signed-in caller, and the connection '
                'carried no token')
    return tool['handler'](args, key)


def _tool_error(id_, text):
    """A refused or broken tool call is a *successful* JSON-RPC response
    carrying isError, per the spec — so the model reads the reason and adapts
    instead of the connection dying under it."""
    return _result(id_, {'content': [{'type': 'text', 'text': text}], 'isError': True})


def _call(id_, params, key):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = call_tool(name, args, key)
    except PermissionError as e:
        return _tool_error(id_, f'{name}: {e}\n\nSign in and pass the token as `key`, '
                                'or set an Authorization: Bearer header on this '
                                'connection. agent_whoami says what the current one is.')
    except (ValueError, KeyError) as e:
        return _tool_error(id_, f'{name}: {e}')
    except TypeError as e:
        return _tool_error(id_, f'{name}: bad arguments — {e}')
    except Exception as e:
        return _tool_error(id_, f'{name} failed: {type(e).__name__}: {e}')
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
        if result.get('error') or result.get('status') == 'error':
            # A handler that answers with an error field failed too — the API
            # returns errors in the body rather than raising, and a run that
            # died on its first step is not a result either. A client that
            # cannot see that would read a refusal as an answer.
            out['isError'] = True
    return _result(id_, out)


def negotiate(client_version: Optional[str]) -> str:
    return client_version if client_version in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION


def handle(body, key=None):
    """One JSON-RPC message in, one response out (None for notifications).

    `key` is the identity the transport recovered — a Bearer header over HTTP —
    and is used for every tool in the message that does not carry its own.
    """
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if not isinstance(params, dict):
        params = {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        return _result(id_, {
            'protocolVersion': negotiate(params.get('protocolVersion')),
            'capabilities': CAPABILITIES,
            'serverInfo': SERVER_INFO,
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params, key)
    if method == 'resources/list':
        return _result(id_, {'resources': RESOURCES})
    if method == 'resources/templates/list':
        return _result(id_, {'resourceTemplates': []})
    if method == 'resources/read':
        try:
            return _result(id_, {'contents': [read_resource(str(params.get('uri') or ''), key)]})
        except ValueError as e:
            return _error(id_, -32602, str(e))
        except Exception as e:
            return _error(id_, -32603, f'{type(e).__name__}: {e}')
    if method == 'prompts/list':
        return _result(id_, {'prompts': prompt_list(key)})
    if method == 'prompts/get':
        try:
            return _result(id_, prompt_get(str(params.get('name') or ''),
                                           params.get('arguments') or {}, key))
        except ValueError as e:
            return _error(id_, -32602, str(e))
    return _error(id_, -32601, f'method not found: {method}')


def info(base: str = None) -> dict:
    """What a client needs to connect — used by /status and the console."""
    base = base or f"http://localhost:{os.environ.get('PORT', 50117)}"
    return {
        'endpoint': f'{base}/mcp',
        'gateway': 'https://modc2.com/api/agent/mcp',
        'transport': 'Streamable HTTP (JSON-RPC 2.0)',
        'stdio': f'python3 {os.path.join(MODULE_ROOT, "src", "mcp.py")}',
        'protocol': PROTOCOL_VERSION,
        'auth': 'Authorization: Bearer <mod protocol-auth token> — optional; '
                'reads are open, runs and writes are not',
        'tools': len(TOOLS),
        'resources': len(RESOURCES),
        'connect': 'claude mcp add --transport http agent '
                   'https://modc2.com/api/agent/mcp',
        'names': list(TOOLS),
    }


# ── stdio transport ──────────────────────────────────────────────────

def serve_stdio(key=None):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body, key)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--tools' in argv or '--list' in argv:
        print(json.dumps({'tools': tool_list(), 'resources': RESOURCES,
                          'info': info()}, indent=2))
    else:
        # A stdio client is a local process — the host's own shell — so it reads
        # like a CLI call and may carry a token in the environment besides.
        LOCAL = True
        serve_stdio(os.environ.get('AGENT_KEY') or os.environ.get('MOD_TOKEN'))
