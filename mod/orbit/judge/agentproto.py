"""agentproto — every judge is an agent, spoken over the agent protocol.

The agent protocol (orbit/agent docs/agent.md, the contract orbit/build
mounts) is two routes:

    GET  /agents        → {"agents": [names...]}
    POST /run/stream    {query, agent_type, prompt, model, steps, ...}
                        → SSE `data: {"type": "step"|"done"|"error", ...}`
    POST /run           the same, blocking → {"result": [steps], "task_id"}

A run's answer is the finish step's params.summary, else the last response
step. This file speaks it both ways:

  consumer — an agent judge names a `provider` (any mod that declares an
      `agent_contract` in its config.json: orbit/agent, claudecode, hermes,
      zkapi …, or this module itself) and an `agent_type` off that
      provider's roster. The input is posed as a run and the answer is
      parsed as {"score": 0-100, "reason": "..."}.
  provider — this module serves the contract too, so judges are agents the
      rest of the fleet can mount and call:
        scorer       one model turn that scores the query 0-100 (the LFM
                     weights on this box via liquidai — fast, keyless)
        <panel>      every panel is an agent: each judge's vote streams as
                     a step and the finish summary is the verdict.

The default judge is `judge`/`scorer` — the fast local agent, reached
in-process (same code the HTTP route runs, no socket to itself).
"""

import json
import os
import time
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ORBIT = os.path.dirname(HERE)

SELF = 'judge'
SCORER = 'scorer'
# orbit/agent predates the agent_contract config key; its API is :50117.
LEGACY = {'agent': {'url': os.environ.get('JUDGE_AGENT_URL', 'http://localhost:50117/run').rsplit('/run', 1)[0],
                    'list': '/agents', 'run': '/run/stream'}}

SCORER_SYSTEM = ('You are a judge. Score the INPUT from 0 (worst) to 100 (best) '
                 'against the goal and criteria you are given. Reply with ONLY a '
                 'JSON object: {"score": <number>, "reason": "<one line>"}')


# ── who speaks the protocol ─────────────────────────────────────


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


def provider(name):
    """{name, url, list, run} for one agent-protocol mod, or None."""
    name = (name or SELF).strip()
    if name in LEGACY:
        return dict(LEGACY[name], name=name)
    cfg = _cfg(name)
    contract = cfg.get('agent_contract')
    if not contract or not cfg.get('port'):
        return None
    return {'name': name, 'url': f'http://localhost:{cfg["port"]}',
            'list': _path(contract.get('list') or contract.get('roster'), '/agents'),
            'run': _path(contract.get('run'), '/run/stream')}


_providers = {'t': 0.0, 'v': None}


def providers():
    """Every agent-protocol provider on this box — this module first."""
    if _providers['v'] is not None and time.time() - _providers['t'] < 60:
        return _providers['v']
    out = [provider(SELF)] if provider(SELF) else []
    out += [provider(n) for n in LEGACY]
    try:
        names = sorted(os.listdir(ORBIT))
    except Exception:
        names = []
    for n in names:
        if n == SELF or n in LEGACY:
            continue
        p = provider(n)
        if p:
            out.append(p)
    _providers.update(t=time.time(), v=out)
    return out


_rosters = {}


def roster(name, panels=None):
    """One provider's agent names (60s cache; this module answers locally)."""
    name = name or SELF
    if name == SELF and panels is not None:
        return {'provider': SELF, **agents(panels)}
    hit = _rosters.get(name)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    p = provider(name)
    if not p:
        return {'provider': name, 'agents': [],
                'error': f'{name!r} does not speak the agent protocol'}
    try:
        with urllib.request.urlopen(p['url'] + p['list'], timeout=5) as r:
            data = json.loads(r.read().decode())
        names = data.get('agents', []) if isinstance(data, dict) else data
        names = [a if isinstance(a, str) else a.get('name', '') for a in names]
        out = {'provider': name, 'agents': [a for a in names if a]}
    except Exception as e:
        return {'provider': name, 'agents': [], 'error': f'{name} unreachable: {e}'}
    _rosters[name] = (time.time(), out)
    return out


# ── consumer: pose a judge's input as a run ─────────────────────


def target(j):
    """(provider, agent_type) for a judge spec. A spec that names an
    agent_type but no provider predates providers: it meant orbit/agent."""
    prov = j.get('provider')
    atype = j.get('agent_type')
    if not prov:
        prov = 'agent' if (atype and atype != SCORER) or j.get('url') else SELF
    if not atype:
        atype = SCORER if prov == SELF else 'default'
    return prov, atype


def run(prov, body, url=None, timeout=300, panels=None):
    """Run one agent over the protocol; return its answer text."""
    if prov == SELF and not url:
        return answer_of(run_local(panels, body)['result'])
    if not url:
        p = provider(prov)
        if not p:
            raise ValueError(f'{prov!r} does not speak the agent protocol '
                             '(no agent_contract in its config.json)')
        url = p['url'] + p['run']
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {'Content-Type': 'application/json',
                                  'Accept': 'text/event-stream, application/json'})
    with urllib.request.urlopen(req, timeout=float(timeout)) as r:
        if 'event-stream' in (r.headers.get('Content-Type') or ''):
            result = _read_sse(r)
        else:
            data = json.loads(r.read().decode())
            if isinstance(data, dict) and data.get('error'):
                raise ValueError(f'agent run failed: {data["error"]}')
            result = data.get('result') if isinstance(data, dict) else data
    answer = answer_of(result)
    if not answer:
        raise ValueError('agent run produced no answer')
    return answer


def _read_sse(r):
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
            raise ValueError(f'agent run failed: {ev.get("error")}')
    return steps


def answer_of(result):
    """A run's answer, by the protocol's rule: the finish step's
    params.summary, else the last response step."""
    if isinstance(result, str):
        return result
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
    return summary or response


# ── provider: this module's own agents ──────────────────────────


def agents(panels):
    """GET /agents — the scorer plus one agent per panel."""
    names = [SCORER]
    if panels is not None:
        names += [p['name'] for p in panels.list() if p['name'] != SCORER]
    return {'agents': names, 'default': SCORER}


def run_local(panels, body, emit=None):
    """POST /run[/stream] for this module. `emit(event)` gets each event as
    it happens; the return value is the `done` event."""
    emit = emit or (lambda ev: None)
    query = str(body.get('query') or '').strip()
    if not query:
        raise ValueError('query is required')
    atype = body.get('agent_type') or SCORER
    task_id = uuid.uuid4().hex[:12]
    steps = []

    def step(s):
        steps.append(s)
        emit({'type': 'step', 'step': s})

    if atype == SCORER:
        emit({'type': 'model_start', 'step': 0, 'model': body.get('model') or 'local'})
        text = scorer(body, query)
        step({'tool': 'finish', 'params': {'summary': text}})
    else:
        if panels is None or 'error' in panels.get(atype):
            raise ValueError(f'no agent {atype!r} here — GET /agents lists them')
        v = panels.judge(atype, query, on_vote=lambda s: step(
            {'tool': 'vote', 'params': {'judge': s['name'], 'kind': s['kind']},
             'result': {'score': s['score'], 'reason': s['reason']}}))
        if 'error' in v:
            raise ValueError(v['error'])
        summary = {'score': v['average'], 'approved': v['approved'],
                   'reason': f'{"PASS" if v["approved"] else "FAIL"} — {v["reason"]}',
                   'verdict': v['id']}
        step({'tool': 'finish', 'params': {'summary': json.dumps(summary)}})
    done = {'type': 'done', 'result': steps, 'task_id': task_id, 'charged': None}
    emit(done)
    return done


def scorer(body, query):
    """The scorer agent: one model turn. `prompt` (the protocol's system
    override) carries the judge's goal/criteria; `model`, `url`, `runtime`
    pick where it runs (local LFM by default)."""
    import panel  # late: panel imports market imports us
    system = SCORER_SYSTEM
    if body.get('prompt'):
        system += '\n\n' + str(body['prompt'])
    user = f'INPUT:\n{query}'
    spec = {k: body[k] for k in ('model', 'runtime', 'api_key_env', 'timeout')
            if body.get(k)}
    url = body.get('url') or panel.DEFAULT_LLM_URL
    if url:
        return panel._chat_http(spec, url, system, user)
    return panel._chat_local(spec, system, user)
