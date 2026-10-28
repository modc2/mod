"""
agentproto — the nyc module's ONLY dependency on the agent protocol.

ASK NYC can be answered by any agent the agent module (orbit/agent) hosts,
not just the Claude CLI. Everything nyc needs from that module goes through
this file, over its public HTTP surface — no import of its code, so either
side can be rebuilt, moved to a peer, or swapped for another implementation
of the same protocol:

    GET  /agents            roster()      who can answer
    POST /agents            register()    file the nyc-atlas agent
    PUT  /agents/{name}     fit()         make an agent able to reach the data
    POST /agents/vibe       create()      a plain description in, an agent out
    POST /run/stream        run()         ask one of them, translated to chat

How an agent reaches the data: the protocol's built-in `fetch` tool, POSTed
at this module's own `POST /tools/<name>` (the same registry the MCP server
serves). `fetch` is open to a sandboxed caller where fleet tools are not, so
this works for an address that is not the agent module's owner.

Identity: calls are signed as a mod-protocol token from a local key —
NYC_AGENT_KEY names it, default the box's own key, the same identity the
arena seats agents with. Agents created here are owned by that address.

Spend: runs go out with `free=true` unless NYC_AGENT_FREE=0, so a public map
page never quietly spends a hosted provider's credit — the agent module runs
them on local weights (liquidai) first.
"""

import base64
import json
import os
import re
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

import requests

ATLAS = 'nyc-atlas'
# Every agent this module filed carries this line in its system prompt; it is
# how the roster tells an NYC agent apart without any state of our own.
MARKER = 'NYC TOOL PROTOCOL'
CORE_TOOLS = ['fetch', 'think', 'finish']
DISPLAY = ('nyc_map', 'nyc_infographic')
TIMEOUT = int(os.environ.get('NYC_AGENT_TIMEOUT', '300'))
STEPS = int(os.environ.get('NYC_AGENT_STEPS', '12'))
_TOOL_URL = re.compile(r'/tools/(nyc_[a-z_]+)')


# ── where things are ─────────────────────────────────────────────────────

def agent_url() -> str:
    """The agent module's API. Env wins, then its config.json, then the port."""
    env = os.environ.get('NYC_AGENT_URL')
    if env:
        return env.rstrip('/')
    try:
        import mod as m
        url = ((m.config('agent') or {}).get('urls') or {}).get('api')
        if url:
            return url.rstrip('/')
    except Exception:
        pass
    return 'http://localhost:50117'


def tools_url() -> str:
    """Where an agent POSTs a tool call — this module's own API."""
    return os.environ.get('NYC_TOOLS_URL', 'http://localhost:50310').rstrip('/') + '/tools'


def free() -> bool:
    return os.environ.get('NYC_AGENT_FREE', '1') not in ('0', 'false', 'no')


# ── identity ─────────────────────────────────────────────────────────────

def token(data: str = 'nyc') -> str:
    """
    A mod-protocol token: base64url JSON {data, time, key, signature}, the
    signature EIP-191 over compact {"data","time"}. `data` a string and `time`
    whole seconds, the shape every verifier in the fleet accepts.
    """
    import mod as m
    from eth_account import Account
    from eth_account.messages import encode_defunct
    name = os.environ.get('NYC_AGENT_KEY') or None
    k = m.key(name) if name else m.key()
    pk = k.private_key
    pk = pk.hex() if hasattr(pk, 'hex') else str(pk)
    t = str(int(time.time()))
    msg = json.dumps({'data': data, 'time': t}, separators=(',', ':'))
    sig = Account.sign_message(encode_defunct(text=msg), private_key=pk).signature.hex()
    tok = {'data': data, 'time': t, 'key': k.address,
           'signature': sig if sig.startswith('0x') else '0x' + sig}
    return base64.urlsafe_b64encode(
        json.dumps(tok, separators=(',', ':')).encode()).decode().rstrip('=')


def _call(method: str, path: str, body: Optional[dict] = None,
          timeout: int = 30) -> dict:
    """One JSON call. The agent module reports most failures as 200 + error."""
    url = agent_url() + path
    try:
        if method == 'GET':
            r = requests.get(url, params=body or {}, timeout=timeout)
        else:
            r = requests.request(method, url, json=body or {}, timeout=timeout)
    except requests.RequestException as e:
        raise ConnectionError(f'agent module unreachable at {agent_url()}: {e}')
    try:
        out = r.json()
    except ValueError:
        raise RuntimeError(f'agent {path} -> {r.status_code}: {r.text[:200]}')
    if isinstance(out, dict) and out.get('error'):
        raise RuntimeError(str(out['error']))
    if r.status_code >= 400:
        raise RuntimeError(f'agent {path} -> {r.status_code}: {str(out)[:200]}')
    return out


# ── the nyc tool protocol, as prompt ─────────────────────────────────────

def guide() -> str:
    """
    How any agent reaches NYC data, generated from the live tool registry so
    a tool added there is usable by every agent without touching this text.
    """
    from nycgis import tools as T
    lines = []
    for t in T.list_tools():
        props = t['inputSchema'].get('properties') or {}
        req = set(t['inputSchema'].get('required') or [])
        args = ', '.join(f'{p}*' if p in req else p for p in list(props)[:9])
        lines.append(f'- {t["name"]}({args}): {t["description"].split(". ")[0][:110]}')
    url = tools_url()
    return (
        f'{MARKER}: every NYC number comes from a tool call. Call one with the '
        f'fetch tool, method POST, url {url}/<tool name>, json_body = the '
        f'arguments object (* = required). Example: fetch with '
        f'{{"url": "{url}/nyc_housing", "method": "POST", "json_body": '
        f'{{"metric": "median_price", "geography": "borough"}}}}. The reply is '
        f'{{"ok": true, "result": ...}}; on a mistake it says what was wrong — '
        f'fix the arguments and call again. Never state a figure you did not '
        f'fetch.\n'
        f'TOOLS:\n' + '\n'.join(lines) + '\n'
        f'The user is looking at a map. To change it call nyc_map (layers, '
        f'metric, geography, highlight, only, focus, overlay, reset, caption); '
        f'to show a stats card call nyc_infographic (title*, stats, bars, '
        f'sources). Finish with a short plain-text answer: the figure first, '
        f'the place, and the dataset it came from.')


ATLAS_GOAL = (
    'You are NYC Atlas, a data analyst for New York City built on public open '
    'data, for city staff and residents alike. Answer questions about NYC from '
    'the tools below and never guess a number you could look up. Housing: '
    'nyc_housing / nyc_prices / nyc_trend / nyc_sales. Transit, parks, flood '
    'zones, crashes: nyc_layers then nyc_layer. Anything else (311, crime, '
    'schools, budgets, permits): nyc_find_datasets, then nyc_dataset, then '
    'nyc_query. Work in small steps: one fetch, read the result, then the '
    'next. Questions about places should also move the map with nyc_map.')


def spec() -> dict:
    """The nyc-atlas agent, as the agent protocol files it."""
    return {'name': ATLAS, 'icon': '◈',
            'description': 'NYC open-data analyst - answers from the nyc tools and drives the map',
            'goal': ATLAS_GOAL + '\n\n' + guide(), 'tools': CORE_TOOLS}


# ── roster ───────────────────────────────────────────────────────────────

def _brief(name: str, s: dict, me: str) -> dict:
    goal = s.get('goal') or ''
    return {'name': name, 'label': s.get('name') or name,
            'icon': s.get('icon') or '>_',
            'description': (s.get('description') or '')[:160],
            'model': s.get('model'), 'owner': s.get('owner'),
            'tools': s.get('tools'),
            'nyc': MARKER in goal,
            'mine': bool(me) and (s.get('owner') or '').lower() == me}


def roster(q: str = '') -> dict:
    """
    Every agent the protocol hosts that this module can run: harness agents
    (a CLI on the host) are owner-only there, so they are left out rather
    than offered and refused. NYC agents first.
    """
    out = _call('GET', '/agents', {'key': token()})
    me = whoami().get('address', '')
    schemas = out.get('schemas') or {}
    agents = [_brief(n, s, me) for n, s in schemas.items()
              if isinstance(s, dict) and not s.get('harness')]
    q = (q or '').strip().lower()
    if q:
        agents = [a for a in agents
                  if q in a['name'] or q in a['description'].lower()]
    agents.sort(key=lambda a: (not a['nyc'], a['name'] != ATLAS, a['name']))
    return {'agents': agents, 'total': len(agents), 'url': agent_url(),
            'address': me, 'free': free()}


_ME: Dict[str, Any] = {}


def whoami() -> dict:
    """Which address this module is to the agent module (cached 10 min)."""
    if _ME.get('t', 0) > time.time() - 600:
        return _ME['v']
    try:
        v = _call('GET', '/whoami', {'key': token()}, timeout=10)
    except Exception:
        v = {}
    _ME.update(t=time.time(), v=v)
    return v


# ── making agents ────────────────────────────────────────────────────────

def register(update: bool = True) -> dict:
    """
    File nyc-atlas if the protocol does not have it; refresh its prompt when
    the tool registry has moved on (and the agent is ours to edit).
    """
    want = spec()
    try:
        have = _call('GET', f'/agents/{ATLAS}')
    except RuntimeError:
        have = {}
    if not have.get('goal'):
        made = _call('POST', '/agents', {**want, 'key': token()})
        return {'registered': ATLAS, 'created': True, 'owner': made.get('owner')}
    if update and have.get('goal') != want['goal']:
        try:
            _call('PUT', f'/agents/{ATLAS}',
                  {'goal': want['goal'], 'tools': want['tools'],
                   'description': want['description'], 'key': token()})
            return {'registered': ATLAS, 'updated': True}
        except RuntimeError as e:
            return {'registered': ATLAS, 'stale': str(e)}
    return {'registered': ATLAS}


def fit(name: str) -> dict:
    """
    Make an agent we own able to reach NYC data: the core tools on its
    loadout and the tool protocol in its prompt. Idempotent.
    """
    have = _call('GET', f'/agents/{name}')
    goal = have.get('goal') or ''
    tools = have.get('tools')
    patch: Dict[str, Any] = {}
    if MARKER not in goal:
        patch['goal'] = (goal.rstrip() + '\n\n' + guide()).strip()
    if tools is not None and not set(CORE_TOOLS) <= set(tools):
        patch['tools'] = list(dict.fromkeys(list(tools) + CORE_TOOLS))
    if patch:
        _call('PUT', f'/agents/{name}', {**patch, 'key': token()})
    return {'name': name, 'fitted': sorted(patch)}


def _made_name(out: dict) -> Optional[str]:
    """The saved agent's id. The vibe reply's `draft.name` is the slug; the
    `agent` it echoes carries the display label under `name`, not the id."""
    draft = out.get('draft') if isinstance(out.get('draft'), dict) else {}
    if draft.get('name'):
        return str(draft['name'])
    agent = out.get('agent') if isinstance(out.get('agent'), dict) else {}
    label = agent.get('name')
    return str(label).lower().replace(' ', '-') if label else None


def create(description: str, name: Optional[str] = None) -> dict:
    """
    One prompt in, one NYC agent out, by the protocol's own route: the
    vibe-builder drafts it (`POST /agents/vibe save=true`), then fit() hands it
    the NYC tool protocol. If drafting fails, the description itself becomes
    the persona, filed through `POST /agents` — a prompt always makes an agent.
    """
    description = (description or '').strip()
    if not description:
        raise ValueError('describe the agent you want')
    if len(description) > 2000:
        raise ValueError('description too long (2000 chars)')
    brief = (f'{description}\n\nThis agent answers questions about New York '
             f'City from open data, so give it the tools fetch, think and finish.')
    via, err = 'vibe', None
    try:
        out = _call('POST', '/agents/vibe',
                    {'description': brief, 'name': name, 'save': True,
                     'free': free(), 'key': token()}, timeout=TIMEOUT)
        made = _made_name(out)
        if not made:
            raise RuntimeError('vibe returned no agent name')
    except RuntimeError as e:
        via, err = 'direct', str(e)
        made = _slug(name or description)
        _call('POST', '/agents', {
            'name': made, 'icon': '◇',
            'description': description.split('\n')[0][:140],
            'goal': f'You are {made.replace("-", " ")}. {description}\n\n'
                    f'{ATLAS_GOAL}\n\n{guide()}',
            'tools': CORE_TOOLS, 'key': token()})
    fit(made)
    return {'name': made, 'via': via, **({'vibe_error': err} if err else {})}


def _slug(text: str) -> str:
    words = [w for w in re.findall(r'[a-z0-9]+', text.lower())
             if w not in {'a', 'an', 'the', 'that', 'and', 'of', 'for', 'to',
                          'agent', 'about', 'in', 'on', 'with', 'who', 'me'}]
    base = '-'.join(words[:3]) or 'nyc'
    return f'{base}-{uuid.uuid4().hex[:4]}'


# ── running one ──────────────────────────────────────────────────────────

def _tool_of(tool: str, params: dict) -> Optional[str]:
    """`nyc_housing` out of a fetch aimed at /tools/nyc_housing."""
    if tool != 'fetch' or not isinstance(params, dict):
        return None
    hit = _TOOL_URL.search(str(params.get('url') or ''))
    return hit.group(1) if hit else None


def _args_of(params: dict) -> dict:
    body = params.get('json_body') or params.get('body') or {}
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = {}
    return body if isinstance(body, dict) else {}


def _directive(step: dict) -> Optional[dict]:
    """The validated directive a display tool returned, out of a fetch step."""
    res = step.get('result')
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except ValueError:
            return None
    if not isinstance(res, dict):
        return None
    body = res.get('json') if isinstance(res.get('json'), dict) else res
    inner = body.get('result') if isinstance(body.get('result'), dict) else body
    d = inner.get('directive') if isinstance(inner, dict) else None
    return d if isinstance(d, dict) else None


def answer_of(trace: List[dict]) -> str:
    """The finish step's summary, else the last response — never a tool output."""
    for s in reversed(trace or []):
        if isinstance(s, dict) and s.get('tool') == 'finish':
            p = s.get('params') or {}
            if p.get('summary'):
                return str(p['summary'])
    for s in reversed(trace or []):
        if isinstance(s, dict) and s.get('tool') == 'response':
            p = s.get('params') or {}
            text = p.get('text') or p.get('content') or s.get('result')
            if text:
                return str(text)
    return ''


def translate(events: Iterator[dict], session: str) -> Iterator[dict]:
    """
    /run/stream events -> the nyc chat events the page already renders
    (session, tool, display, text, done, error). Tool calls are named by the
    nyc tool they reached, not as `fetch`.
    """
    yield {'type': 'session', 'id': session}
    t0 = time.time()
    for ev in events:
        kind = ev.get('type')
        if kind == 'tool_start':
            tool, params = ev.get('tool'), ev.get('params') or {}
            nyc = _tool_of(tool, params)
            if nyc:
                yield {'type': 'tool', 'name': nyc, 'input': _args_of(params)}
            elif tool not in ('think', 'finish', 'response', None):
                yield {'type': 'tool', 'name': str(tool), 'input': params}
        elif kind == 'step':
            step = ev.get('step') or {}
            nyc = _tool_of(step.get('tool'), step.get('params') or {})
            if nyc in DISPLAY:
                d = _directive(step)
                if d:
                    yield {'type': 'display', 'directive': d}
        elif kind == 'done':
            trace = ev.get('result') or []
            text = answer_of(trace)
            if text:
                yield {'type': 'text', 'text': text}
            yield {'type': 'done', 'ms': int((time.time() - t0) * 1000),
                   'session_id': session}
            return
        elif kind == 'error':
            yield {'type': 'error', 'error': str(ev.get('error'))[:500]}
            return
    yield {'type': 'error', 'error': 'agent stream ended without an answer'}


def _sse(resp) -> Iterator[dict]:
    for line in resp.iter_lines(decode_unicode=True):
        if line and line.startswith('data: '):
            try:
                yield json.loads(line[6:])
            except ValueError:
                continue


def run(agent: str, message: str, session: Optional[str] = None,
        map_state: Optional[dict] = None, roster_entry: Optional[dict] = None
        ) -> Iterator[dict]:
    """
    Ask one agent, streaming nyc chat events. An agent that was not built
    here gets the tool protocol in its query, so ANY agent can answer — the
    NYC ones carry it in their own prompt already.
    """
    session = session or f'nyc-{uuid.uuid4().hex[:12]}'
    if roster_entry is None:
        try:
            have = _call('GET', f'/agents/{agent}', timeout=10)
            roster_entry = {'nyc': MARKER in (have.get('goal') or ''),
                            'tools': have.get('tools')}
        except Exception as e:
            yield {'type': 'session', 'id': session}
            yield {'type': 'error', 'error': f'no agent {agent!r}: {e}'}
            return
    query = message
    if map_state:
        query = (f'[The user\'s map right now: '
                 f'{json.dumps(map_state, default=str)[:3000]}]\n\n{query}')
    body: Dict[str, Any] = {'agent_type': agent, 'session': session,
                            'steps': STEPS, 'free': free(), 'key': token()}
    if not (roster_entry or {}).get('nyc'):
        query = f'{guide()}\n\nQUESTION: {query}'
        have = (roster_entry or {}).get('tools')
        if have is not None and not set(CORE_TOOLS) <= set(have):
            body['tools'] = list(dict.fromkeys(list(have) + CORE_TOOLS))
    body['query'] = query
    try:
        resp = requests.post(agent_url() + '/run/stream', json=body,
                             stream=True, timeout=(10, TIMEOUT))
    except requests.RequestException as e:
        yield {'type': 'session', 'id': session}
        yield {'type': 'error', 'error': f'agent module unreachable: {e}'}
        return
    try:
        if resp.status_code >= 400:
            yield {'type': 'session', 'id': session}
            yield {'type': 'error',
                   'error': f'agent run -> {resp.status_code}: {resp.text[:200]}'}
            return
        yield from translate(_sse(resp), session)
    finally:
        resp.close()
