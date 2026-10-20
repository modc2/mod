"""agents — any agent on the agent protocol, as a target.

The agent protocol (orbit/agent docs/agent.md, the contract orbit/build
mounts) is two routes on whatever mod speaks it:

    GET  /agents        → {"agents": [name | {name, ...}, ...]}
    POST /run/stream    {query, agent_type, prompt, ...}
                        → SSE `data: {"type": "step"|"done"|"error", ...}`

A mod speaks it when its config.json declares an `agent_contract` (claudecode,
hermes, judge, feedback, zkapi …). orbit/agent predates that key, so it is
listed here by hand. Nothing else is hard-coded: a mod that ships a contract
tomorrow is a target tomorrow, with no change to this file.

The target string is

    agent:<provider>/<agent_type>   one agent off one provider's roster
    agent:<provider>                that provider's default agent
    agent:<agent_type>              an agent on orbit/agent (the big registry)

The defense's system prompt goes in as `prompt` — the protocol's system-prompt
override — so a defense replaces the agent's persona exactly as it replaces a
model's default prompt. With no system prompt the agent runs as itself.

A run's answer is the protocol's rule: the finish step's params.summary, else
the last response step. A `{"tool": "error"}` step is the model call failing,
which is a ModelError here, never a reply to score.
"""

import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ORBIT = os.path.dirname(os.path.dirname(HERE))

# Tool-step budget per exchange. The target is being asked one question; a
# long tool loop is cost, not signal.
STEPS = int(os.environ.get('RVB_AGENT_STEPS', 3))
# What the target is offered. `think` only: an attack is scored on what the
# agent *says*, and a full toolbox costs thousands of prompt tokens a turn
# (the arena measured 6 min/turn against 30s). RVB_AGENT_TOOLS=all lifts it.
TOOLS = os.environ.get('RVB_AGENT_TOOLS', 'think')


def _cfg(name):
    try:
        with open(os.path.join(ORBIT, name, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def _path(v, default):
    """A contract entry is a path ('/run/stream') or prose ('POST /run/stream → …')."""
    if not isinstance(v, str):
        return default
    for tok in v.split():
        if tok.startswith('/'):
            return tok
    return default


def _legacy_agent():
    url = os.environ.get('RVB_AGENT_URL') or \
        (_cfg('agent').get('urls') or {}).get('api') or 'http://localhost:50117'
    return {'name': 'agent', 'url': url.rstrip('/'), 'list': '/agents',
            'run': '/run/stream', 'default': 'default'}


def provider(name):
    """{name, url, list, run, default} for one agent-protocol mod, or None."""
    name = (name or 'agent').strip()
    if name == 'agent':
        return _legacy_agent()
    if not name.replace('-', '').replace('_', '').isalnum():
        return None
    cfg = _cfg(name)
    contract = cfg.get('agent_contract')
    if not isinstance(contract, dict) or not cfg.get('port'):
        return None
    return {'name': name, 'url': f'http://localhost:{cfg["port"]}',
            'list': _path(contract.get('list') or contract.get('roster'), '/agents'),
            'run': _path(contract.get('run'), '/run/stream'),
            'default': contract.get('default') or ''}


_providers = {'t': 0.0, 'v': None}


def providers():
    """Every agent-protocol provider on this box, orbit/agent first."""
    if _providers['v'] is not None and time.time() - _providers['t'] < 60:
        return _providers['v']
    out = [_legacy_agent()]
    try:
        names = sorted(os.listdir(ORBIT))
    except Exception:
        names = []
    for n in names:
        if n != 'agent':
            p = provider(n)
            if p:
                out.append(p)
    _providers.update(t=time.time(), v=out)
    return out


_rosters = {}


def roster(name):
    """One provider's agents as [{name, label?, description?}] (60s cache)."""
    name = name or 'agent'
    hit = _rosters.get(name)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    p = provider(name)
    if not p:
        return {'provider': name, 'agents': [],
                'error': f'{name!r} does not speak the agent protocol '
                         '(no agent_contract in its config.json)'}
    try:
        with urllib.request.urlopen(p['url'] + p['list'], timeout=5) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        return {'provider': name, 'agents': [], 'url': p['url'],
                'error': f'{name} unreachable: {type(e).__name__}: {e}'}
    items = data.get('agents', []) if isinstance(data, dict) else data
    agents = []
    for a in items or []:
        if isinstance(a, str):
            agents.append({'name': a})
        elif isinstance(a, dict) and (a.get('name') or a.get('id')):
            agents.append({'name': a.get('name') or a.get('id'),
                           **{k: a[k] for k in ('label', 'description', 'available')
                              if k in a}})
    out = {'provider': name, 'url': p['url'], 'agents': agents,
           'default': (data.get('default') if isinstance(data, dict) else None)
           or p['default'] or (agents[0]['name'] if agents else '')}
    _rosters[name] = (time.time(), out)
    return out


def resolve(name):
    """`<provider>/<agent_type>` | `<provider>` | `<agent_type>` → (provider, agent_type).

    agent_type may be '' — the provider then runs its own default."""
    name = (name or '').strip()
    if '/' in name:
        prov, _, atype = name.partition('/')
        return prov.strip() or 'agent', atype.strip()
    if not name:
        return 'agent', ''
    if name != 'agent' and provider(name):
        return name, ''
    if name == 'agent':
        return 'agent', ''
    return 'agent', name


def run(query, system=None, name='', timeout=120, error=Exception):
    """One exchange with an agent. Returns its answer text; raises `error`."""
    prov, atype = resolve(name)
    p = provider(prov)
    if not p:
        raise error(f'{prov!r} does not speak the agent protocol — '
                    'GET /agents lists the providers that do')
    body = {'query': query, 'steps': STEPS}
    if atype:
        body['agent_type'] = atype
    if system:
        body['prompt'] = system
    if TOOLS and TOOLS != 'all':
        body['tools'] = [t.strip() for t in TOOLS.split(',') if t.strip()]
    req = urllib.request.Request(
        p['url'] + p['run'], json.dumps(body).encode(),
        {'Content-Type': 'application/json',
         'Accept': 'text/event-stream, application/json'})
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as r:
            if 'event-stream' in (r.headers.get('Content-Type') or ''):
                result = _read_sse(r, error)
            else:
                data = json.loads(r.read().decode())
                if isinstance(data, dict) and data.get('error'):
                    raise error(f'{prov} run failed: {data["error"]}')
                result = data.get('result') if isinstance(data, dict) else data
    except urllib.error.HTTPError as e:
        raise error(f'{prov} returned {e.code}: '
                    f'{e.read().decode("utf-8", "replace")[:300]}')
    except error:
        raise
    except Exception as e:
        raise error(f'{prov} at {p["url"]}: {type(e).__name__}: {e}')
    failed = _model_error(result)
    if failed:
        raise error(f'{prov}/{atype or "default"}: {failed}')
    return answer_of(result)


def _read_sse(r, error):
    """Steps off a /run/stream response: `done` carries the whole trace;
    without one, the steps seen so far stand in."""
    steps = []
    for raw in r:
        line = raw.decode('utf-8', 'replace').strip()
        if not line.startswith('data:'):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except Exception:
            continue
        t = ev.get('type')
        if t == 'step' and isinstance(ev.get('step'), dict):
            steps.append(ev['step'])
        elif t == 'done':
            return ev.get('result') or steps
        elif t == 'error':
            raise error(f'agent run failed: {ev.get("error") or ev.get("message")}')
    return steps


def _model_error(result):
    """The model call itself failing ({"tool": "error"}), when nothing answered."""
    if not isinstance(result, list) or answer_of(result):
        return ''
    for s in result:
        if isinstance(s, dict) and s.get('tool') == 'error':
            return str(s.get('error') or s.get('result') or 'model call failed')[:300]
    return ''


def answer_of(result):
    """A run's answer: the finish step's params.summary, else the last response."""
    if isinstance(result, str):
        return result.strip()
    if not isinstance(result, list):
        return ''
    summary = response = ''
    for s in result:
        if not isinstance(s, dict):
            continue
        if s.get('tool') == 'finish':
            summary = (s.get('params') or {}).get('summary', '') or summary
        elif s.get('tool') == 'response' and s.get('result'):
            response = str(s['result'])
    if not isinstance(summary, str):
        summary = json.dumps(summary)
    return (summary or response).strip()
