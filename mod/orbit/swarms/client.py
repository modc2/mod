#!/usr/bin/env python3
"""swarms client — the Swarms cloud API and the swarms.world marketplace.

Two upstreams, one class:

    https://api.swarms.world   run agents and multi-agent swarms. Needs a key.
    https://swarms.world/api   the marketplace catalog. Public, no key.

BYOK, always. Every completion spends the CALLER'S Swarms credits — this
module holds no house key and never bills anybody but the person who supplied
the key. Resolution order is: the key passed to this call, then the
`x-swarms-key` (or `authorization: Bearer`) header the API server read off the
request, then SWARMS_API_KEY in the environment, then the off-tree keystore at
~/.mod/swarms/key.json (0600). Nothing here ever returns a key.

Stdlib only — urllib and json. The module is a dependency-free drop-in on any
box with python3, which is the whole point of not reaching for `requests`.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('SWARMS_API_BASE', 'https://api.swarms.world').rstrip('/')
MARKET = os.environ.get('SWARMS_MARKET_BASE', 'https://swarms.world/api').rstrip('/')

DIR = os.path.expanduser(os.environ.get('SWARMS_DIR', '~/.mod/swarms'))
KEY_FILE = os.path.join(DIR, 'key.json')

TIMEOUT = float(os.environ.get('SWARMS_TIMEOUT', 300))
CACHE_TTL = float(os.environ.get('SWARMS_CACHE_TTL', 600))

# A swarm run bills per agent AND per token, so the thing that surprises people
# is agent count, not prompt length. Anything estimated above this needs
# confirm=true. The defaults below are the API's own published numbers
# (GET /v1/usage/costs returns the live ones, and price() prefers those).
SPEND_USD = float(os.environ.get('SWARMS_SPEND_USD', 0.50))

PRICING = {
    'agent_cost': 0.01,            # USD per agent, per swarm completion
    'input_per_1m': 6.50,          # USD per 1M input tokens
    'output_per_1m': 18.50,        # USD per 1M output tokens
}

# From SwarmSpec.swarm_type in the upstream OpenAPI schema. `auto` lets the API
# pick the architecture from the task.
SWARM_TYPES = (
    'AgentRearrange', 'MixtureOfAgents', 'SequentialWorkflow', 'ConcurrentWorkflow',
    'GroupChat', 'MultiAgentRouter', 'HierarchicalSwarm', 'auto', 'MajorityVoting',
    'CouncilAsAJudge', 'HeavySwarm', 'BatchedGridWorkflow', 'LLMCouncil',
    'DebateWithJudge', 'RoundRobin', 'PlannerWorkerSwarm',
)

# Fields of AgentSpec that this client passes straight through. Kept explicit so
# a typo in a caller's dict is a readable error here rather than a 422 from the
# far end with no hint about which agent it came from.
AGENT_FIELDS = (
    'agent_name', 'description', 'system_prompt', 'marketplace_prompt_id',
    'model_name', 'fallback_models', 'fallback_model_name', 'auto_generate_prompt',
    'max_tokens', 'temperature', 'role', 'max_loops', 'tools_list_dictionary',
    'selected_tools', 'mcp_url', 'mcp_urls', 'mcp_config', 'mcp_configs',
    'streaming_on', 'llm_args', 'top_p', 'dynamic_temperature_enabled',
    'tool_call_summary', 'reasoning_effort', 'thinking_tokens', 'reasoning_enabled',
    'publish_to_marketplace', 'use_cases', 'tags', 'capabilities', 'category',
    'is_free', 'price_usd', 'handoffs',
)

SWARM_FIELDS = (
    'name', 'description', 'agents', 'max_loops', 'swarm_type', 'rearrange_flow',
    'task', 'img', 'tasks', 'messages', 'stream', 'heavy_swarm_question_agent_model_name',
    'heavy_swarm_worker_model_name', 'heavy_swarm_variant', 'council_judge_model_name',
    'chairman_model', 'multi_agent_collab_prompt', 'heavy_swarm_max_loops',
    'list_all_agents', 'director_model_name', 'director_settings',
)

_cache = {}


class SwarmsError(Exception):
    """An upstream or argument failure with enough context to act on."""

    def __init__(self, message, status=400, detail=None, hint=None):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.hint = hint

    def dict(self):
        out = {'error': str(self)}
        if self.status:
            out['status'] = self.status
        if self.detail:
            out['detail'] = self.detail
        if self.hint:
            out['hint'] = self.hint
        return out


# ── the keystore ──

def _read_keystore():
    try:
        with open(KEY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def set_key(key=None, persist=True):
    """Store a key off-tree. Returns state, never the key itself."""
    if not key:
        raise SwarmsError('key is required', status=400)
    key = key.strip()
    if not persist:
        os.environ['SWARMS_API_KEY'] = key
        return {'stored': 'process env only', 'persisted': False, 'key': _mask(key)}
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    data = {**_read_keystore(), 'key': key, 'updated': int(time.time())}
    tmp = KEY_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, KEY_FILE)
    return {'stored': KEY_FILE, 'persisted': True, 'mode': '0600', 'key': _mask(key)}


def _mask(key):
    if not key:
        return None
    return f'{key[:6]}…{key[-4:]}' if len(key) > 14 else '…'


def _hint_for(code):
    """Turn an upstream status into the thing the caller should actually do.

    401, 402 and 429 all read as "it did not work" and have three different
    fixes, so the difference is worth spelling out at the point of failure.
    """
    return {
        401: 'the key was rejected — check it at https://swarms.world/platform/api-keys',
        402: 'out of credits — top up the Swarms account, then retry',
        422: 'the request shape was rejected: check model_name against swarms_models '
             'and swarm_type against swarms_architectures',
        429: 'rate limited — see swarms_account for the per-minute and per-day '
             'limits and how much of each is spent',
        500: 'the upstream failed, not the request — retry, then try a different model',
        503: 'the upstream is briefly unavailable — retry with backoff',
    }.get(code)


# ── the client ──

class Client:
    """One key, both upstreams. Cheap to construct — make one per request."""

    def __init__(self, key=None):
        self._explicit = (key or '').strip() or None

    @property
    def key(self):
        return (self._explicit
                or (os.environ.get('SWARMS_API_KEY') or '').strip()
                or (_read_keystore().get('key') or '').strip()
                or None)

    def key_state(self):
        """Where the key came from, without revealing it."""
        source = None
        if self._explicit:
            source = 'request'
        elif (os.environ.get('SWARMS_API_KEY') or '').strip():
            source = 'env'
        elif (_read_keystore().get('key') or '').strip():
            source = 'keystore'
        return {'key': _mask(self.key), 'source': source, 'keystore': KEY_FILE,
                'spend_guard_usd': SPEND_USD, 'upstream': BASE}

    # ── transport ──

    def _request(self, path, method='GET', body=None, params=None, base=None,
                 auth=True, timeout=None):
        url = (base or BASE) + path
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, '')}
            if clean:
                url += '?' + urllib.parse.urlencode(clean)
        headers = {'content-type': 'application/json',
                   'accept': 'application/json',
                   'user-agent': 'mod-swarms/1.0'}
        if auth:
            key = self.key
            if not key:
                raise SwarmsError(
                    'no Swarms API key', status=401,
                    hint='send x-swarms-key on the request, set SWARMS_API_KEY, or '
                         'call set_key to store one in ~/.mod/swarms/key.json. Get a '
                         'key at https://swarms.world/platform/api-keys')
            headers['x-api-key'] = key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw or b'{}')
            except Exception:
                payload = {'body': (raw or b'').decode('utf-8', 'replace')[:800]}
            err = payload.get('error') if isinstance(payload, dict) else None
            msg = (err or {}).get('message') if isinstance(err, dict) else None
            detail = (err or {}).get('detail') if isinstance(err, dict) else None
            raise SwarmsError(msg or f'{e.code} from {url}', status=e.code,
                              detail=detail or payload,
                              hint=_hint_for(e.code)) from None
        except urllib.error.URLError as e:
            raise SwarmsError(f'cannot reach {url}: {e.reason}', status=502) from None
        except TimeoutError:
            raise SwarmsError(f'{url} timed out after {timeout or TIMEOUT}s', status=504,
                              hint='swarm runs are slow — raise SWARMS_TIMEOUT') from None
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {'raw': raw.decode('utf-8', 'replace')}

    def _cached(self, key, fn):
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        value = fn()
        _cache[key] = (time.time(), value)
        return value

    # ── catalog ──

    def health(self):
        """Upstream liveness. The one call that needs no key."""
        started = time.time()
        out = self._request('/health', auth=False, timeout=20)
        return {'upstream': BASE, 'ok': True, 'latency_ms': int((time.time() - started) * 1000),
                'response': out}

    def models(self, q=None, refresh=False):
        """Every model the Swarms runtime will accept as `model_name`."""
        if refresh:
            _cache.pop('models', None)
        out = self._cached('models', lambda: self._request('/v1/models/available'))
        names = _as_list(out, ('models', 'data', 'available_models'))
        if q:
            needle = str(q).lower()
            names = [m for m in names if needle in json.dumps(m).lower()]
        return {'count': len(names), 'models': names}

    def swarm_types(self, refresh=False):
        """The architectures, with what each is good for and what tunes it."""
        if refresh:
            _cache.pop('swarm_types', None)
        try:
            out = self._cached('swarm_types', lambda: self._request('/v1/swarms/available'))
        except SwarmsError as e:
            if e.status != 401:
                raise
            # The names are fixed by the upstream schema, so a keyless caller
            # still gets something useful — just without the descriptions.
            return {'count': len(SWARM_TYPES), 'swarm_types': list(SWARM_TYPES),
                    'note': 'names only — send a key for descriptions and best_for'}
        types = _as_list(out, ('swarm_types', 'swarms', 'data'))
        return {'count': len(types), 'swarm_types': types}

    def tools(self):
        """Tools the hosted agents can call by name (`selected_tools`)."""
        return self._request('/v1/tools/available')

    def reasoning_types(self):
        return self._request('/v1/reasoning-agent/types')

    def agents_list(self):
        """Saved agent configurations on the caller's account."""
        return self._request('/v1/agents/list')

    # ── running things ──

    def agent(self, task=None, model_name=None, system_prompt=None, agent_name=None,
              history=None, img=None, imgs=None, tools_enabled=None, confirm=False,
              **agent_kw):
        """One agent, one task. POST /v1/agent/completions."""
        if not task and not history:
            raise SwarmsError('task is required', status=400)
        config = _agent_spec({'agent_name': agent_name or 'agent',
                              'model_name': model_name, 'system_prompt': system_prompt,
                              **agent_kw})
        guard = self._guard(1, config.get('max_tokens'), config.get('max_loops'), confirm)
        if guard:
            return guard
        body = {'agent_config': config, 'task': task}
        for k, v in (('history', history), ('img', img), ('imgs', imgs),
                     ('tools_enabled', tools_enabled)):
            if v is not None:
                body[k] = v
        return self._request('/v1/agent/completions', 'POST', body)

    def agent_batch(self, jobs, confirm=False):
        """Many agents, many tasks, in parallel. POST /v1/agent/batch/completions."""
        if not isinstance(jobs, list) or not jobs:
            raise SwarmsError('jobs must be a non-empty list of {agent_config, task}',
                              status=400)
        body = []
        for j in jobs:
            if not isinstance(j, dict):
                raise SwarmsError('each job must be an object', status=400)
            cfg = j.get('agent_config') or {k: v for k, v in j.items() if k != 'task'}
            body.append({'agent_config': _agent_spec(cfg), 'task': j.get('task')})
        guard = self._guard(len(body), None, None, confirm)
        if guard:
            return guard
        return self._request('/v1/agent/batch/completions', 'POST', body)

    def swarm(self, task=None, agents=None, swarm_type='auto', name=None,
              description=None, max_loops=1, confirm=False, **spec_kw):
        """A multi-agent swarm. POST /v1/swarm/completions.

        `agents` may be a list of AgentSpec dicts, or a list of plain strings —
        a string becomes an agent whose name and role are that string, which is
        the shortest path from an idea to a running swarm.
        """
        if not task and not spec_kw.get('tasks') and not spec_kw.get('messages'):
            raise SwarmsError('task is required', status=400)
        specs = _agent_list(agents)
        if swarm_type not in SWARM_TYPES:
            raise SwarmsError(f'unknown swarm_type: {swarm_type}', status=400,
                              hint=f'one of {", ".join(SWARM_TYPES)}')
        if not specs and swarm_type not in ('auto',):
            raise SwarmsError(f'{swarm_type} needs agents', status=400,
                              hint='pass agents=["researcher","critic"] or full '
                                   'AgentSpec objects, or use swarm_type="auto" to '
                                   'let the API build the roster')
        body = {'name': name or 'swarm', 'description': description,
                'swarm_type': swarm_type, 'task': task, 'max_loops': max_loops}
        if specs:
            body['agents'] = specs
        for k, v in spec_kw.items():
            if k not in SWARM_FIELDS:
                raise SwarmsError(f'unknown SwarmSpec field: {k}', status=400,
                                  hint=f'known fields: {", ".join(SWARM_FIELDS)}')
            body[k] = v
        body = {k: v for k, v in body.items() if v is not None}
        guard = self._guard(max(len(specs), 1), None, max_loops, confirm)
        if guard:
            return guard
        return self._request('/v1/swarm/completions', 'POST', body)

    def swarm_batch(self, swarms, confirm=False):
        """Several swarms in one request. POST /v1/swarm/batch/completions."""
        if not isinstance(swarms, list) or not swarms:
            raise SwarmsError('swarms must be a non-empty list of SwarmSpec objects',
                              status=400)
        body = []
        for s in swarms:
            spec = dict(s or {})
            if spec.get('agents'):
                spec['agents'] = _agent_list(spec['agents'])
            body.append(spec)
        total = sum(len(s.get('agents') or []) or 1 for s in body)
        guard = self._guard(total, None, None, confirm)
        if guard:
            return guard
        return self._request('/v1/swarm/batch/completions', 'POST', body)

    def auto_build(self, task, model_name=None, confirm=False):
        """Turn a task into a roster. POST /v1/auto-agent-builder/completions.

        This is the call to make when you do not know what agents you need: it
        returns the AgentSpec list, which you can inspect and edit before
        spending anything on running it.
        """
        if not task:
            raise SwarmsError('task is required', status=400)
        guard = self._guard(1, None, None, confirm)
        if guard:
            return guard
        body = {'task': task}
        if model_name:
            body['model_name'] = model_name
        return self._request('/v1/auto-agent-builder/completions', 'POST', body)

    def reasoning(self, task, agent_name=None, model_name=None, swarm_type=None,
                  max_loops=None, num_samples=None, system_prompt=None,
                  output_type=None, confirm=False):
        """A reasoning agent. POST /v1/reasoning-agent/completions."""
        if not task:
            raise SwarmsError('task is required', status=400)
        guard = self._guard(1, None, max_loops, confirm)
        if guard:
            return guard
        body = {k: v for k, v in {
            'agent_name': agent_name or 'reasoning-agent', 'task': task,
            'model_name': model_name, 'swarm_type': swarm_type, 'max_loops': max_loops,
            'num_samples': num_samples, 'system_prompt': system_prompt,
            'output_type': output_type}.items() if v is not None}
        return self._request('/v1/reasoning-agent/completions', 'POST', body)

    def graph_workflow(self, body, confirm=False):
        """A DAG of agents. POST /v1/graph-workflow/completions."""
        if not isinstance(body, dict):
            raise SwarmsError('graph workflow body must be an object with agents and edges',
                              status=400)
        spec = dict(body)
        if spec.get('agents'):
            spec['agents'] = _agent_list(spec['agents'])
        guard = self._guard(len(spec.get('agents') or []) or 1, None, None, confirm)
        if guard:
            return guard
        return self._request('/v1/graph-workflow/completions', 'POST', spec)

    def batched_grid(self, body, confirm=False):
        """POST /v1/batched-grid-workflow/completions — agents × tasks."""
        if not isinstance(body, dict):
            raise SwarmsError('batched grid body must be an object', status=400)
        spec = dict(body)
        if spec.get('agents'):
            spec['agents'] = _agent_list(spec['agents'])
        n = len(spec.get('agents') or []) or 1
        guard = self._guard(n * max(len(spec.get('tasks') or []), 1), None, None, confirm)
        if guard:
            return guard
        return self._request('/v1/batched-grid-workflow/completions', 'POST', spec)

    def chat(self, messages=None, model=None, prompt=None, system=None, confirm=False,
             **kw):
        """OpenAI-shaped chat. POST /v1/chat/completions.

        The upstream routes this into the same agent runtime as /v1/agent/completions,
        so it is a compatibility shim rather than a second engine: point any
        OpenAI client at it and the agent runs.
        """
        msgs = messages
        if not msgs:
            if not prompt:
                raise SwarmsError('messages or prompt is required', status=400)
            msgs = ([{'role': 'system', 'content': system}] if system else []) + \
                   [{'role': 'user', 'content': prompt}]
        guard = self._guard(1, kw.get('max_tokens'), None, confirm)
        if guard:
            return guard
        body = {'model': model or 'gpt-4o-mini', 'messages': msgs, **kw}
        return self._request('/v1/chat/completions', 'POST', body)

    # ── the money ──

    def credits(self):
        """What is left on the caller's account."""
        return self._request('/v1/account/credits')

    def pricing(self, refresh=False):
        """Live upstream pricing, with the local estimator's constants beside it."""
        if refresh:
            _cache.pop('pricing', None)
        try:
            live = self._cached('pricing', lambda: self._request('/v1/usage/costs'))
        except SwarmsError as e:
            if e.status != 401:
                raise
            live = None
        return {'live': live, 'estimator': dict(PRICING), 'spend_guard_usd': SPEND_USD,
                'note': 'the estimator prices a run BEFORE it happens and is an '
                        'upper bound from max_tokens, not a quote — the receipt is '
                        'the usage block on the completion'}

    def rate_limits(self):
        return self._request('/v1/rate/limits')

    def logs(self, limit=None):
        """Past API requests on this account."""
        return self._request('/v1/account/logs', params={'limit': limit})

    def metrics(self):
        return self._request('/v1/account/metrics/summary')

    def cost(self, agents=1, input_tokens=2000, output_tokens=2000, loops=1):
        """Price a run before making it, from the API's published rates.

        Deliberately an UPPER bound: it assumes every agent generates its full
        output budget on every loop, which almost never happens. Use it to
        decide whether a shape is affordable, not to predict the invoice.
        """
        agents = max(int(agents or 1), 1)
        loops = max(int(loops or 1), 1)
        rates = self._live_rates()
        calls = agents * loops
        agent_usd = calls * rates['agent_cost']
        in_usd = calls * (float(input_tokens or 0) / 1e6) * rates['input_per_1m']
        out_usd = calls * (float(output_tokens or 0) / 1e6) * rates['output_per_1m']
        total = agent_usd + in_usd + out_usd
        return {'agents': agents, 'loops': loops, 'agent_calls': calls,
                'input_tokens': input_tokens, 'output_tokens': output_tokens,
                'rates': rates,
                'usd': {'agents': round(agent_usd, 6), 'input': round(in_usd, 6),
                        'output': round(out_usd, 6), 'total': round(total, 6)},
                'over_guard': total > SPEND_USD, 'guard_usd': SPEND_USD,
                'basis': 'upper bound — assumes every agent fills its output budget '
                         'on every loop'}

    def _live_rates(self):
        """Upstream rates when a key is present, published defaults otherwise."""
        try:
            live = self._cached('pricing', lambda: self._request('/v1/usage/costs'))
        except SwarmsError:
            return dict(PRICING)
        up = (live or {}).get('usage_pricing') or {}
        return {
            'agent_cost': _num(up.get('swarm_completions_agent_cost'),
                               PRICING['agent_cost']),
            'input_per_1m': _num(up.get('swarm_completions_input_cost_per_1m'),
                                 PRICING['input_per_1m']),
            'output_per_1m': _num(up.get('swarm_completions_output_cost_per_1m'),
                                  PRICING['output_per_1m']),
        }

    def _guard(self, agents, max_tokens, loops, confirm):
        """Return a needs_confirm block instead of spending past the guard.

        An agent that mis-reads a swarm_type can turn one task into fifty agent
        calls. The guard is the difference between that costing a cent and it
        costing whatever the account has.
        """
        if confirm or SPEND_USD <= 0:
            return None
        out_tokens = int(max_tokens or 2000)
        quote = self.cost(agents=agents, input_tokens=2000, output_tokens=out_tokens,
                          loops=loops or 1)
        if not quote['over_guard']:
            return None
        return {'needs_confirm': True, 'estimate': quote,
                'reason': f"estimated ${quote['usd']['total']:.4f} is above the "
                          f'${SPEND_USD:.2f} spend guard',
                'how': 'call again with confirm=true, or raise SWARMS_SPEND_USD'}

    # ── the marketplace (public, no key) ──

    def market(self, kind='agents', q=None, limit=50):
        """swarms.world listings: agents, prompts or tools.

        Public data — this runs without a key, which is why the console can show
        the marketplace to somebody who has not signed up yet.
        """
        paths = {'agents': '/get-agents', 'prompts': '/get-prompts', 'tools': '/get-tools'}
        if kind not in paths:
            raise SwarmsError(f'unknown kind: {kind}', status=400,
                              hint='agents, prompts or tools')
        out = self._cached(f'market:{kind}',
                           lambda: self._request(paths[kind], base=MARKET, auth=False,
                                                 timeout=60))
        rows = out.get('data') if isinstance(out, dict) else out
        rows = rows if isinstance(rows, list) else []
        if q:
            needle = str(q).lower()
            rows = [r for r in rows if needle in json.dumps(r, default=str).lower()]
        total = len(rows)
        rows = rows[:int(limit or 50)]
        return {'kind': kind, 'count': len(rows), 'total': total,
                'source': MARKET + paths[kind], 'items': rows}

    def raw(self, path, method='GET', body=None, params=None, market=False):
        """Any upstream route, with the caller's key attached.

        The escape hatch exists because this file will go stale before the API
        does — a new endpoint is reachable the day it ships.
        """
        if not path.startswith('/'):
            path = '/' + path
        return self._request(path, method=method.upper(), body=body, params=params,
                             base=MARKET if market else None, auth=not market)


# ── helpers ──

def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_list(out, keys):
    """Upstream list responses are inconsistently wrapped; unwrap all of them."""
    if isinstance(out, list):
        return out
    if isinstance(out, dict):
        for k in keys:
            v = out.get(k)
            if isinstance(v, list):
                return v
        for v in out.values():
            if isinstance(v, list):
                return v
    return []


def _agent_spec(cfg):
    """Validate one AgentSpec locally so bad fields fail here, with a hint."""
    if isinstance(cfg, str):
        return {'agent_name': cfg, 'description': cfg, 'role': cfg}
    if not isinstance(cfg, dict):
        raise SwarmsError('an agent must be a name or an AgentSpec object', status=400)
    spec = {k: v for k, v in cfg.items() if v is not None}
    unknown = [k for k in spec if k not in AGENT_FIELDS]
    if unknown:
        raise SwarmsError(f'unknown AgentSpec field(s): {", ".join(sorted(unknown))}',
                          status=400,
                          hint=f'known fields: {", ".join(AGENT_FIELDS)}')
    spec.setdefault('agent_name', 'agent')
    return spec


def _agent_list(agents):
    if not agents:
        return []
    if isinstance(agents, str):
        agents = [a.strip() for a in agents.split(',') if a.strip()]
    if not isinstance(agents, list):
        raise SwarmsError('agents must be a list', status=400)
    return [_agent_spec(a) for a in agents]
