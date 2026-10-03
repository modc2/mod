#!/usr/bin/env python3
"""openrouter mcp — twelve tools that cover the whole OpenRouter API.

The catalog is 400+ models across 100+ providers, and the thing an agent
actually needs is not "call a model" — it is *choose* one and know what it will
cost. So the tools are ordered around that: `openrouter_models` to narrow by
capability and price, `openrouter_endpoints` to see which provider serves it
fastest and cheapest, `openrouter_cost` to price the call before making it,
`openrouter_chat` to make it, `openrouter_generation` to find out what it really
cost.

Self-contained: JSON-RPC 2.0 hand-rolled on the stdlib, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50600 # Streamable HTTP — POST /mcp

The module's API server mounts `handle()` at /mcp too, so the tools, the REST
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

from client import PROVIDER_PREFS, SORTS, SPEND_USD, Client, ORError  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'One key, every model: OpenRouter routes to 400+ models from 100+ providers '
    'behind an OpenAI-shaped API. Start with openrouter_models to narrow the '
    'catalog by capability and price (prices are quoted per MILLION tokens), '
    'openrouter_endpoints to see which provider serves a model cheapest/fastest '
    'and at what quantization, and openrouter_cost to price a call before making '
    'it. openrouter_chat runs the call and can carry a fallback model list plus '
    'full provider routing preferences; openrouter_generation reports what it '
    "really cost in the provider's own token accounting. Every call spends the "
    "CALLER'S OpenRouter credits — this module holds no house key; set one with "
    'openrouter_set_key or the x-openrouter-key header. Key provisioning '
    '(openrouter_provision) needs a separate, stronger provisioning key.'
)


def _client(a):
    """Per-call keys never leave this process and are never echoed back."""
    if not isinstance(a, dict):
        return Client()
    return Client(key=a.pop('key', None), provisioning_key=a.pop('provisioning_key', None))


# ── tools ──

def _t_models(a):
    c = _client(a)
    return c.search(**a)


def _t_model(a):
    return _client(a).model(a['id'], endpoints=a.get('endpoints', True))


def _t_endpoints(a):
    return _client(a).endpoints(a['id'])


def _t_providers(a):
    return _client(a).providers(q=a.get('q'))


def _t_chat(a):
    return _client(a).chat(**a)


def _t_complete(a):
    c = _client(a)
    return c.complete(a['model'], a['prompt'],
                      **{k: v for k, v in a.items() if k not in ('model', 'prompt')})


def _t_cost(a):
    c = _client(a)
    return c.cost(prompt_tokens=a.pop('prompt_tokens', 1000),
                  completion_tokens=a.pop('completion_tokens', 1000),
                  model=a.pop('model', None), limit=a.pop('limit', 15), **a)


def _t_generation(a):
    return _client(a).generation(a['id'])


def _t_key(a):
    return _client(a).key_info()


def _t_provision(a):
    c = _client(a)
    return c.provision(**a)


def _t_set_key(a):
    import client
    return client.set_key(key=a.get('key'), provisioning_key=a.get('provisioning_key'),
                          persist=a.get('persist', True))


def _t_raw(a):
    c = _client(a)
    return c.raw(a['path'], method=a.get('method') or 'GET', body=a.get('body'),
                 params=a.get('params'), provisioning=bool(a.get('provisioning')))


def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _num(desc):
    return {'type': 'number', 'description': desc}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_MODEL = _str('model id as author/slug, e.g. anthropic/claude-sonnet-4.5 — ids come '
              'from openrouter_models; "openrouter/auto" lets OpenRouter choose')

_FILTERS = {
    'q': _str('free text matched against id, name and description — all words must hit'),
    'modality': _str('substring of the modality string, e.g. "text->text", "image"'),
    'input': _str('required input modality', enum=['text', 'image', 'file', 'audio', 'video']),
    'output': _str('required output modality', enum=['text', 'image', 'audio']),
    'free': _bool('only models that are free to prompt AND to generate'),
    'tools': _bool('only models that support tool/function calling'),
    'reasoning': _bool('only models that expose reasoning tokens'),
    'structured': _bool('only models that support structured outputs / JSON schema'),
    'min_context': _num('minimum context window in tokens'),
    'max_prompt_usd_m': _num('price ceiling for input, USD per MILLION tokens'),
    'max_completion_usd_m': _num('price ceiling for output, USD per MILLION tokens'),
    'provider': _str('limit to one author/organisation prefix, e.g. "anthropic"'),
}

_PROVIDER_ARG = {
    'type': ['object', 'string'],
    'description': 'provider routing: an object with any of '
                   f'{", ".join(PROVIDER_PREFS)} — e.g. {{"order":["groq","cerebras"],'
                   '"allow_fallbacks":false}}, {"sort":"throughput"}, '
                   '{"only":["anthropic"]}, {"data_collection":"deny"}, '
                   '{"quantizations":["fp8"]} — or just a comma-separated list, '
                   'which means order',
}

TOOLS = {
    'openrouter_models': {
        'description': 'Search the OpenRouter catalog — 400+ models — by capability '
                       'and price. Prices come back per MILLION tokens '
                       '(prompt_usd_m / completion_usd_m), with context window, '
                       'modalities, tool support and whether the model is genuinely '
                       'free. This is the first call: it is how you pick a model id '
                       'for everything else.',
        'inputSchema': {'type': 'object', 'properties': {
            **_FILTERS,
            'sort': _str('cheapest prompt first by default', enum=list(SORTS)),
            'limit': _num('rows to return (default 40, max 500)'),
            'refresh': _bool('bypass the 10-minute catalog cache'),
        }},
        'handler': _t_models,
    },
    'openrouter_model': {
        'description': 'One model in full: catalog row, supported parameters, prices, '
                       'and every provider endpoint serving it. Use before committing '
                       'to a model — it shows the real context limit and max output, '
                       'which differ per provider.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _MODEL,
            'endpoints': _bool('include the provider endpoint list (default true)'),
        }, 'required': ['id']},
        'handler': _t_model,
    },
    'openrouter_endpoints': {
        'description': 'Every provider serving one model, cheapest first, with price, '
                       'context, quantization, 30-minute uptime, latency and '
                       'throughput. This is what makes provider routing a decision '
                       'rather than a guess — pick names from here for the chat '
                       "`provider.order` / `provider.only` fields.",
        'inputSchema': {'type': 'object', 'properties': {'id': _MODEL},
                        'required': ['id']},
        'handler': _t_endpoints,
    },
    'openrouter_providers': {
        'description': 'The provider catalog: name, slug, headquarters, datacenters, '
                       'privacy policy and terms. Use it to check jurisdiction or data '
                       'policy before routing to a provider.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('filter by name or slug')}},
        'handler': _t_providers,
    },
    'openrouter_chat': {
        'description': f'Run a chat completion. SPENDS THE CALLER\'S OpenRouter '
                       f'credits. Give `prompt` (plus optional `system`) or a full '
                       f'`messages` array. `models` is a fallback list — the first '
                       f'that can serve the request wins — and `provider` steers which '
                       f'upstream serves it. Returns the text, the provider that ran '
                       f'it, token usage and the actual USD cost. Worst-case '
                       f'estimates above ${SPEND_USD} return needs_confirm instead of '
                       f'running; call again with confirm=true.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL,
            'models': {'type': ['array', 'string'], 'items': {'type': 'string'},
                       'description': 'fallback model list, tried in order'},
            'prompt': _str('a single user turn — the shortcut for simple calls'),
            'system': _str('system prompt, used with `prompt`'),
            'messages': {'type': 'array', 'items': {'type': 'object'},
                         'description': 'full OpenAI-shape message array; overrides prompt'},
            'temperature': _num('0-2'),
            'max_tokens': _num('cap on generated tokens — also what the spend guard prices'),
            'top_p': _num('nucleus sampling'),
            'seed': _num('deterministic sampling where the provider supports it'),
            'stop': {'type': ['array', 'string'], 'items': {'type': 'string'},
                     'description': 'stop sequences'},
            'tools': {'type': 'array', 'items': {'type': 'object'},
                      'description': 'OpenAI-shape tool definitions'},
            'tool_choice': {'type': ['string', 'object'], 'description': 'auto | none | a tool'},
            'response_format': {'type': 'object',
                                'description': '{"type":"json_object"} or a json_schema block'},
            'reasoning': {'type': 'object',
                          'description': 'reasoning controls, e.g. {"effort":"high"} or '
                                         '{"max_tokens":2000} or {"exclude":true}'},
            'provider': _PROVIDER_ARG,
            'transforms': {'type': ['array', 'string'], 'items': {'type': 'string'},
                           'description': 'prompt transforms, e.g. ["middle-out"] to fit '
                                          'an over-long prompt into the context window'},
            'plugins': {'type': 'array', 'items': {'type': 'object'},
                        'description': 'OpenRouter plugins, e.g. [{"id":"web"}] for web search'},
            'confirm': _bool('yes, spend past the guard'),
        }},
        'handler': _t_chat,
    },
    'openrouter_complete': {
        'description': 'The legacy text-completion route (no chat template) for base '
                       'models. Same billing and same spend guard as openrouter_chat — '
                       'prefer that one unless you specifically need raw completion.',
        'inputSchema': {'type': 'object', 'properties': {
            'model': _MODEL, 'prompt': _str('raw prompt text'),
            'max_tokens': _num('cap on generated tokens'),
            'temperature': _num('0-2'),
            'stop': {'type': ['array', 'string'], 'items': {'type': 'string'},
                     'description': 'stop sequences'},
            'provider': _PROVIDER_ARG,
            'confirm': _bool('yes, spend past the guard'),
        }, 'required': ['model', 'prompt']},
        'handler': _t_complete,
    },
    'openrouter_cost': {
        'description': 'Price a call before making it. With `model` it is a quote; '
                       'without one it ranks the whole catalog (same filters as '
                       'openrouter_models) by what THIS call would cost — which is a '
                       'different order than prompt price alone, because output tokens '
                       'are usually the expensive half.',
        'inputSchema': {'type': 'object', 'properties': {
            'prompt_tokens': _num('input tokens to price (default 1000)'),
            'completion_tokens': _num('output tokens to price (default 1000)'),
            'model': {'type': ['array', 'string'], 'items': {'type': 'string'},
                      'description': 'quote these model(s) instead of ranking the catalog'},
            'limit': _num('how many ranked models to return (default 15)'),
            **_FILTERS,
        }},
        'handler': _t_cost,
    },
    'openrouter_generation': {
        'description': "What a finished generation really cost, in the provider's own "
                       'token accounting: native prompt/completion/reasoning tokens, '
                       'total cost, cache discount, latency and throughput. Pass the '
                       '`id` returned by openrouter_chat. Native counts differ from '
                       "the response's normalized usage block — this is the receipt.",
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('generation id from a completion response, e.g. gen-…')},
            'required': ['id']},
        'handler': _t_generation,
    },
    'openrouter_key': {
        'description': "The caller's key state: label, usage so far, spend limit, "
                       'remaining limit, free-tier flag, rate limit, and the credit '
                       'balance (purchased minus used). Call this when a request fails '
                       'with 402 or 429.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_key,
    },
    'openrouter_provision': {
        'description': 'Manage inference keys: list, get, create, update (rename, cap '
                       'the spend limit, disable) and delete. Requires a separate '
                       'PROVISIONING key — an inference key cannot do this, by design. '
                       'A created key\'s secret is returned exactly once and is never '
                       'stored by this module.',
        'inputSchema': {'type': 'object', 'properties': {
            'action': _str('what to do', enum=['list', 'get', 'create', 'update', 'delete']),
            'hash': _str('the key hash, for get / update / delete'),
            'name': _str('label for the key, for create / update'),
            'limit': _num('spend limit in USD for this key (omit for unlimited)'),
            'include_byok_in_limit': _bool('count BYOK usage against the limit'),
            'disabled': _bool('disable or re-enable the key, for update'),
            'offset': _num('pagination offset, for list'),
        }},
        'handler': _t_provision,
    },
    'openrouter_set_key': {
        'description': 'Store the caller\'s OpenRouter key in the off-tree keystore '
                       '(~/.mod/openrouter/key.json, 0600) so later calls need no key '
                       'argument. Keys are never returned by any tool. Pass '
                       'provisioning_key to store the key-management key separately.',
        'inputSchema': {'type': 'object', 'properties': {
            'key': _str('inference key, sk-or-v1-…'),
            'provisioning_key': _str('provisioning key, for openrouter_provision only'),
            'persist': _bool('write to disk (default true)'),
        }},
        'handler': _t_set_key,
    },
    'openrouter_raw': {
        'description': 'Escape hatch: call any OpenRouter route directly with the '
                       "caller's key attached. For anything not normalized above — new "
                       'or beta endpoints, or fields the summaries drop.',
        'inputSchema': {'type': 'object', 'properties': {
            'path': _str('path under https://openrouter.ai/api/v1, e.g. /models/user'),
            'method': _str('GET (default), POST, PATCH, DELETE'),
            'body': {'type': 'object', 'description': 'JSON body'},
            'params': {'type': 'object', 'description': 'query parameters'},
            'provisioning': _bool('authenticate with the provisioning key instead'),
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
    """Run one tool. Raises ORError/ValueError with a readable message."""
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
    except ORError as e:
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
            'serverInfo': {'name': 'openrouter', 'version': version()},
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
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50600)))
    else:
        serve_stdio()
