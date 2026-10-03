#!/usr/bin/env python3
"""swarms mcp — eighteen tools over the Swarms protocol, both halves of it.

Swarms is two things wearing one name, and an agent needs both:

    the runtime   api.swarms.world — sixteen multi-agent architectures that
                  take a task and a roster and return work
    the token     $swarms on Solana — what the agent economy is priced in

So the tools are ordered the way the decision actually goes. Start at
`swarms_architectures` to see which of the sixteen fits the shape of the
problem, `swarms_build` if you do not yet know what agents you need,
`swarms_cost` to price the run before making it, then `swarms_run`. The chain
tools answer the other question — `swarms_token` for what $swarms is worth,
`swarms_quote` for what a position in it would cost.

Self-contained: JSON-RPC 2.0 hand-rolled on the stdlib, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50690 # Streamable HTTP — POST /mcp

The module's API server mounts `handle()` at /mcp, so the tools, the REST
routes and the console can never drift apart.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

import chain                                            # noqa: E402
from chain import ChainError                            # noqa: E402
from client import SPEND_USD, SWARM_TYPES, Client, SwarmsError  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Swarms is a multi-agent runtime and a Solana token, and this server covers '
    'both. RUNTIME: sixteen orchestration architectures — SequentialWorkflow, '
    'ConcurrentWorkflow, HierarchicalSwarm, MixtureOfAgents, MajorityVoting, '
    'GroupChat, DebateWithJudge, HeavySwarm and the rest — reachable through '
    'swarms_run. Call swarms_architectures first: picking the architecture IS the '
    'design decision, and the wrong one costs the same as the right one. If you '
    'do not know what agents the task needs, swarms_build turns the task into a '
    'roster you can inspect before spending anything on it. swarms_cost prices a '
    "run up front. Every completion spends the CALLER'S Swarms credits — this "
    'module holds no house key; set one with swarms_set_key or the x-swarms-key '
    f'header. Runs estimated above ${SPEND_USD:.2f} return needs_confirm instead '
    'of running; call again with confirm=true. TOKEN: swarms_token, swarms_price, '
    'swarms_holders, swarms_balance and swarms_quote read $swarms on Solana '
    '(mint 74SBV4zDXxTRgv1pEMoECskKBkZHc2yGPnc7GYVepump). These are READS ONLY — '
    'swarms_quote prices a swap and returns the route, but this module holds no '
    'keypair and cannot sign or submit a Solana transaction.'
)


def _client(a):
    """Per-call keys never leave this process and are never echoed back."""
    if not isinstance(a, dict):
        return Client()
    return Client(key=a.pop('key', None))


# ── runtime tools ──

def _t_run(a):
    c = _client(a)
    return c.swarm(**a)


def _t_agent(a):
    return _client(a).agent(**a)


def _t_batch(a):
    c = _client(a)
    return c.agent_batch(a.get('jobs') or [], confirm=bool(a.get('confirm')))


def _t_build(a):
    c = _client(a)
    return c.auto_build(a['task'], model_name=a.get('model_name'),
                        confirm=bool(a.get('confirm')))


def _t_reasoning(a):
    return _client(a).reasoning(**a)


def _t_architectures(a):
    return _client(a).swarm_types(refresh=bool(a.get('refresh')))


def _t_models(a):
    c = _client(a)
    return c.models(q=a.get('q'), refresh=bool(a.get('refresh')))


def _t_tools(a):
    return _client(a).tools()


def _t_cost(a):
    c = _client(a)
    return c.cost(agents=a.get('agents', 1), input_tokens=a.get('input_tokens', 2000),
                  output_tokens=a.get('output_tokens', 2000), loops=a.get('loops', 1))


def _t_account(a):
    """Everything the caller needs when a call fails with 401, 402 or 429."""
    c = _client(a)
    out = {'key': c.key_state()}
    for name, fn in (('credits', c.credits), ('rate_limits', c.rate_limits),
                     ('pricing', c.pricing)):
        try:
            out[name] = fn()
        except SwarmsError as e:
            out[name] = e.dict()
    return out


def _t_market(a):
    c = _client(a)
    return c.market(kind=a.get('kind', 'agents'), q=a.get('q'), limit=a.get('limit', 25))


def _t_set_key(a):
    import client
    return client.set_key(key=a.get('key'), persist=a.get('persist', True))


def _t_raw(a):
    c = _client(a)
    return c.raw(a['path'], method=a.get('method') or 'GET', body=a.get('body'),
                 params=a.get('params'), market=bool(a.get('market')))


# ── chain tools ──

def _t_token(a):
    return chain.token(a.get('mint'))


def _t_price(a):
    mint = a.get('mint')
    out = {'price': chain.price(mint)}
    try:
        out['market'] = chain.pools(mint, limit=a.get('limit', 8))
    except ChainError as e:
        out['market'] = e.dict()
    return out


def _t_holders(a):
    return chain.holders(a.get('mint'), limit=a.get('limit', 20))


def _t_balance(a):
    return chain.balance(a['owner'], a.get('mint'))


def _t_quote(a):
    return chain.quote(side=a.get('side', 'buy'), amount=a.get('amount', 1),
                       mint=a.get('mint'), slippage_bps=a.get('slippage_bps', 100),
                       pay_with=a.get('pay_with', 'SOL'))


# ── schema helpers ──

def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _num(desc):
    return {'type': 'number', 'description': desc}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_AGENTS = {
    'type': ['array', 'string'],
    'items': {'type': ['object', 'string']},
    'description': 'the roster. Either a list of plain names — ["researcher", '
                   '"analyst", "critic"] — which become agents whose role is that '
                   'name, or full AgentSpec objects with agent_name, system_prompt, '
                   'model_name, temperature, max_loops, mcp_url, tools and so on. '
                   'Use swarms_build if you do not know the roster yet',
}

_CONFIRM = _bool(f'spend past the ${SPEND_USD:.2f} guard — required when a call '
                 'returns needs_confirm')

TOOLS = {
    'swarms_architectures': {
        'description': 'The sixteen multi-agent architectures, each with what it is '
                       'best for and which SwarmSpec fields tune it. CALL THIS FIRST '
                       'when designing a swarm: SequentialWorkflow, '
                       'ConcurrentWorkflow, HierarchicalSwarm, MixtureOfAgents, '
                       'MajorityVoting, GroupChat, DebateWithJudge, CouncilAsAJudge, '
                       'LLMCouncil, HeavySwarm, RoundRobin, AgentRearrange, '
                       'MultiAgentRouter, PlannerWorkerSwarm, BatchedGridWorkflow and '
                       'auto all cost about the same to run and produce very '
                       'different work. Names come back without a key; descriptions '
                       'need one.',
        'inputSchema': {'type': 'object', 'properties': {
            'refresh': _bool('bypass the 10-minute cache')}},
        'handler': _t_architectures,
    },
    'swarms_run': {
        'description': "Run a multi-agent swarm. SPENDS THE CALLER'S Swarms credits. "
                       'Give a `task` and a roster of `agents`, and pick a '
                       '`swarm_type` from swarms_architectures — or leave it "auto" '
                       'and let the API choose the architecture and build the roster '
                       'from the task alone. Returns each agent\'s output plus the '
                       'usage block, which is the real receipt. Billing is per agent '
                       'AND per token, so agent count matters as much as prompt '
                       'length.',
        'inputSchema': {'type': 'object', 'properties': {
            'task': _str('what the swarm should accomplish — the one required field'),
            'agents': _AGENTS,
            'swarm_type': _str('the architecture; "auto" lets the API pick',
                               enum=list(SWARM_TYPES)),
            'name': _str('a name for the swarm'),
            'description': _str("the swarm's objective, given to the agents as context"),
            'max_loops': _num('how many times the swarm may iterate (default 1, max 50) '
                              '— this multiplies the bill'),
            'rearrange_flow': _str('for AgentRearrange: the flow, e.g. "a -> b, c"'),
            'tasks': {'type': 'array', 'items': {'type': 'string'},
                      'description': 'for BatchedGridWorkflow: several tasks at once'},
            'messages': {'type': 'array', 'items': {'type': 'object'},
                         'description': 'conversation history to seed the swarm with'},
            'heavy_swarm_variant': _str('for HeavySwarm: how hard it thinks',
                                        enum=['default', 'medium', 'heavy']),
            'council_judge_model_name': _str('for CouncilAsAJudge: the judge model'),
            'chairman_model': _str('for LLMCouncil: the model that synthesizes'),
            'director_model_name': _str('for HierarchicalSwarm: the director model'),
            'multi_agent_collab_prompt': _bool('inject the collaboration prompt so '
                                               'agents coordinate rather than answer '
                                               'in parallel'),
            'list_all_agents': _bool('let each agent see the others and their roles'),
            'confirm': _CONFIRM,
        }, 'required': ['task']},
        'handler': _t_run,
    },
    'swarms_agent': {
        'description': "Run ONE agent on one task. SPENDS THE CALLER'S credits. The "
                       'cheap path when the job does not need a committee — one '
                       'model, one system prompt, optional tools and MCP servers of '
                       'its own. Prefer this over swarms_run for anything a single '
                       'competent agent can finish.',
        'inputSchema': {'type': 'object', 'properties': {
            'task': _str('what the agent should do'),
            'agent_name': _str('a name for the agent'),
            'system_prompt': _str("the agent's instructions"),
            'model_name': _str('model id — ids come from swarms_models'),
            'description': _str("the agent's purpose"),
            'temperature': _num('0-2; lower is more deterministic'),
            'max_tokens': _num('cap on generated tokens — also what the guard prices'),
            'max_loops': _num('how many times the agent may iterate'),
            'role': _str("the agent's role"),
            'auto_generate_prompt': _bool('let the API write the system prompt'),
            'reasoning_enabled': _bool('let the agent reason before answering'),
            'reasoning_effort': _str('how hard it reasons, e.g. low / medium / high'),
            'mcp_url': _str('an MCP server the agent may call tools on'),
            'tools_enabled': {'type': 'array', 'items': {'type': 'string'},
                              'description': 'hosted tool names from swarms_tools'},
            'history': {'type': ['object', 'array'],
                        'description': 'prior turns to carry into this one'},
            'confirm': _CONFIRM,
        }, 'required': ['task']},
        'handler': _t_agent,
    },
    'swarms_build': {
        'description': 'Turn a task into a ROSTER. Given a goal in plain language, '
                       'the auto-builder returns the AgentSpec list it would run — '
                       'names, roles, system prompts, models. This is the call to '
                       'make when you do not know what agents the problem needs: it '
                       'is one cheap agent call, and you can read and edit the roster '
                       'before paying to run it through swarms_run.',
        'inputSchema': {'type': 'object', 'properties': {
            'task': _str('the goal, in plain language'),
            'model_name': _str('model for the builder itself'),
            'confirm': _CONFIRM,
        }, 'required': ['task']},
        'handler': _t_build,
    },
    'swarms_reasoning': {
        'description': "Run a reasoning agent — self-consistency, reflection and the "
                       'other reasoning topologies the runtime offers, rather than a '
                       'plain completion. Use when the answer needs to be checked by '
                       "the thing that produced it. SPENDS THE CALLER'S credits.",
        'inputSchema': {'type': 'object', 'properties': {
            'task': _str('the problem to reason about'),
            'agent_name': _str('a name for the agent'),
            'model_name': _str('model id'),
            'swarm_type': _str('the reasoning topology — names from the upstream '
                               '/v1/reasoning-agent/types'),
            'system_prompt': _str('instructions'),
            'max_loops': _num('reasoning iterations'),
            'num_samples': _num('samples to draw before agreeing with itself'),
            'output_type': _str('output format'),
            'confirm': _CONFIRM,
        }, 'required': ['task']},
        'handler': _t_reasoning,
    },
    'swarms_batch': {
        'description': 'Run many independent agent jobs in parallel in one request. '
                       'Each job is {agent_config, task}. Use for fan-out over a list '
                       '— one agent per document, per ticker, per candidate — where '
                       'the jobs do not need to see each other. For jobs that DO need '
                       'to coordinate, use swarms_run with a swarm_type instead.',
        'inputSchema': {'type': 'object', 'properties': {
            'jobs': {'type': 'array', 'items': {'type': 'object'},
                     'description': 'list of {agent_config: {…AgentSpec}, task: "…"}'},
            'confirm': _CONFIRM,
        }, 'required': ['jobs']},
        'handler': _t_batch,
    },
    'swarms_models': {
        'description': 'Every model the Swarms runtime accepts as `model_name`. Ids '
                       'from here are the only ones valid in swarms_run, swarms_agent '
                       'and swarms_reasoning — a model the runtime does not host is a '
                       '422, not a fallback.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('filter by substring'),
            'refresh': _bool('bypass the 10-minute cache'),
        }},
        'handler': _t_models,
    },
    'swarms_tools': {
        'description': 'The hosted tools an agent can be given by name in '
                       '`tools_enabled` / `selected_tools` — search, scrape and the '
                       'rest. These run on the Swarms side, so they need no MCP '
                       'server of your own and may bill separately from tokens.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_tools,
    },
    'swarms_cost': {
        'description': 'Price a run before making it, from the API\'s own published '
                       'rates (per agent, plus input and output tokens per million). '
                       'Deliberately an UPPER bound — it assumes every agent fills '
                       'its whole output budget on every loop. Use it to decide '
                       'whether a shape is affordable; the receipt is the usage block '
                       'on the completion. This is the tool that shows why agent '
                       'count and max_loops multiply.',
        'inputSchema': {'type': 'object', 'properties': {
            'agents': _num('how many agents in the swarm (default 1)'),
            'loops': _num('max_loops (default 1) — multiplies everything'),
            'input_tokens': _num('input tokens per agent call (default 2000)'),
            'output_tokens': _num('output tokens per agent call (default 2000)'),
        }},
        'handler': _t_cost,
    },
    'swarms_account': {
        'description': "The caller's account in one call: which key resolved and from "
                       'where (never the key itself), credit balance, rate limits and '
                       'usage against them, and live pricing. Call this when a request '
                       'fails with 401, 402 or 429 — it distinguishes "no key" from '
                       '"no credit" from "too fast", which are three different fixes.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_account,
    },
    'swarms_market': {
        'description': 'The swarms.world marketplace: published agents, prompts and '
                       'tools with their descriptions, tags and prices. PUBLIC — this '
                       'is the one runtime tool that needs no key. A prompt id from '
                       'here can be passed as `marketplace_prompt_id` on an AgentSpec '
                       "to use somebody else's system prompt.",
        'inputSchema': {'type': 'object', 'properties': {
            'kind': _str('what to list', enum=['agents', 'prompts', 'tools']),
            'q': _str('free-text filter over the listing'),
            'limit': _num('rows to return (default 25)'),
        }},
        'handler': _t_market,
    },
    'swarms_token': {
        'description': 'The $swarms token on Solana in one card: mint identity and '
                       'metadata, on-chain supply, USD price, fully diluted value, '
                       'and every venue trading it with its liquidity and 24h volume. '
                       'Mint 74SBV4zDXxTRgv1pEMoECskKBkZHc2yGPnc7GYVepump, 6 '
                       'decimals. Pass another mint to check whether a token claiming '
                       'to be $swarms actually is — the answer is the mint, not the '
                       'ticker. READ ONLY.',
        'inputSchema': {'type': 'object', 'properties': {
            'mint': _str('SPL mint address (defaults to $swarms)')}},
        'handler': _t_token,
    },
    'swarms_price': {
        'description': 'Spot price and where it comes from: the Jupiter-aggregated '
                       'USD price with 24h change, plus each pool trading the token '
                       'ranked by liquidity. Thin total liquidity is the number that '
                       'matters before sizing anything — read it here, not from the '
                       'market cap.',
        'inputSchema': {'type': 'object', 'properties': {
            'mint': _str('SPL mint address (defaults to $swarms)'),
            'limit': _num('how many pools to return (default 8)'),
        }},
        'handler': _t_price,
    },
    'swarms_holders': {
        'description': 'The largest token accounts and what share of supply each '
                       'holds. These are token ACCOUNTS, not people — a liquidity '
                       "pool and an exchange's hot wallet each appear as one large "
                       'holder — so read it as a concentration signal, not a rich '
                       'list. Needs an RPC with headroom; the public one rate-limits '
                       'this method.',
        'inputSchema': {'type': 'object', 'properties': {
            'mint': _str('SPL mint address (defaults to $swarms)'),
            'limit': _num('how many accounts (default 20)'),
        }},
        'handler': _t_holders,
    },
    'swarms_balance': {
        'description': 'What one Solana wallet holds: SOL, its $swarms balance across '
                       'every token account it owns, and what that is worth in USD at '
                       'the current price. READ ONLY — an address is all this needs '
                       'and all it will ever accept.',
        'inputSchema': {'type': 'object', 'properties': {
            'owner': _str('the wallet address (base58)'),
            'mint': _str('SPL mint to check (defaults to $swarms)'),
        }, 'required': ['owner']},
        'handler': _t_balance,
    },
    'swarms_quote': {
        'description': 'Price a swap into or out of $swarms through Jupiter: how much '
                       'you would receive, the worst case after slippage, the price '
                       'impact and the venues the route crosses. THIS IS A QUOTE, NOT '
                       'A TRADE — this module holds no keypair and cannot sign or '
                       'submit a Solana transaction. Price impact on a thin pair is '
                       'the real cost of size; this is where you see it.',
        'inputSchema': {'type': 'object', 'properties': {
            'side': _str('buy spends pay_with to get the token; sell is the reverse',
                         enum=['buy', 'sell']),
            'amount': _num('how much to spend, in the input asset'),
            'pay_with': _str('the other side of the pair', enum=['SOL', 'USDC']),
            'slippage_bps': _num('slippage tolerance in basis points (default 100 = 1%)'),
            'mint': _str('SPL mint to trade (defaults to $swarms)'),
        }},
        'handler': _t_quote,
    },
    'swarms_set_key': {
        'description': "Store the caller's Swarms API key in the off-tree keystore "
                       '(~/.mod/swarms/key.json, 0600) so later calls need no key '
                       'argument. Keys are never returned by any tool — every '
                       'response masks them. Get a key at '
                       'https://swarms.world/platform/api-keys.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('the Swarms API key'),
            'persist': _bool('write to disk (default true); false sets it for this '
                             'process only'),
        }, 'required': ['key']},
        'handler': _t_set_key,
    },
    'swarms_raw': {
        'description': 'Escape hatch: call any Swarms API route directly with the '
                       "caller's key attached. For anything not normalized above — "
                       'new or beta endpoints, or fields the summaries drop. Set '
                       'market=true to hit the public swarms.world marketplace API '
                       'instead of the runtime.',
        'inputSchema': {'type': 'object', 'properties': {
            'path': _str('path on the upstream, e.g. /v1/account/logs'),
            'method': _str('GET (default), POST, PUT, DELETE'),
            'body': {'type': 'object', 'description': 'JSON body'},
            'params': {'type': 'object', 'description': 'query parameters'},
            'market': _bool('call swarms.world/api instead of api.swarms.world'),
        }, 'required': ['path']},
        'handler': _t_raw,
    },
}


# ── JSON-RPC 2.0 ──

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool. Raises SwarmsError/ChainError with a readable message."""
    tool = TOOLS.get(name)
    if not tool:
        raise ValueError(f'unknown tool: {name} — have {", ".join(TOOLS)}')
    return tool['handler'](dict(args or {}))


def _call(id_, params):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = call_tool(name, args)
    except (SwarmsError, ChainError) as e:
        # A tool failure is a *successful* JSON-RPC response carrying isError, per
        # the MCP spec, so the model reads the hint and retries instead of dying.
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), indent=2)}],
                             'structuredContent': e.dict(), 'isError': True})
    except KeyError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name}: missing argument {e}'}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name}: bad arguments — {e}'}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name} failed: {type(e).__name__}: {e}'}],
                             'isError': True})
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'swarms', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def tool_list():
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


# ── transports ──

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import server as api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50690)))
    else:
        serve_stdio()
