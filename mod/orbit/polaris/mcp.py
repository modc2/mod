#!/usr/bin/env python3
"""polaris mcp — the Polaris GPU cloud as MCP tools.

Fourteen tools over one REST API, in the order an agent actually needs them:
polaris_gpus to see what is for sale, polaris_quote to price it, polaris_rent
to take it, polaris_instances / polaris_ssh to use it, polaris_stop to end the
billing. Everything else is the account: credits, usage, deployments, logs.

Self-contained: JSON-RPC 2.0 hand-rolled on the stdlib, no `mcp` package.

    python3 mcp.py                      # stdio — one JSON message per line
    python3 mcp.py --http --port 50870  # Streamable HTTP — POST /mcp

api.py mounts `handle()` at /mcp too, so the tools, the REST routes and the
console are the same code and can never drift.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

import auth                                        # noqa: E402
from client import CONFIRM_USD, Polaris, PolarisError  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'The Polaris GPU cloud (polaris.computer): GPUs and CPU boxes billed by the '
    'second, sixteen one-click template deployments, and a 400-model inference '
    'catalog. Start with polaris_gpus (the catalog is public — no key needed), '
    'then polaris_quote for what a rental costs and what is cheaper with the '
    'same VRAM, then polaris_rent, which spends real credits and refuses '
    f'estimates over ${CONFIRM_USD} without confirm=true. polaris_instances is '
    'what you are paying for right now and polaris_ssh is how you get into it; '
    'provisioning takes 1-3 minutes, so poll. polaris_stop is the only thing '
    'that ends the billing — an instance left running bills until the balance '
    'is gone. polaris_status is money, boxes and deployments in one call, with '
    'hours of runway. Every call spends the caller\'s own credits: pass '
    'key=pi_sk_… to use your account, or run over stdio where you are the '
    'owner. polaris_raw is the escape hatch to routes not wrapped here.'
)


def _client(args):
    """Per-call key (`key: pi_sk_…`) never leaves this process."""
    key = args.pop('key', None) if isinstance(args, dict) else None
    return Polaris(key=key)


def _str(desc, enum=None):
    out = {'type': 'string', 'description': desc}
    if enum:
        out['enum'] = enum
    return out


def _num(desc):
    return {'type': 'number', 'description': desc}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_KEY = _str('your own Polaris API key (pi_sk_…). Omit to use the server\'s.')


# ── handlers ──

def _t_gpus(a):
    return _client(a).gpus(gpu=a.get('gpu'), min_vram_gb=a.get('min_vram_gb'),
                           max_usd_hr=a.get('max_usd_hr'), kind=a.get('kind'),
                           available_only=a.get('available_only', True),
                           sort=a.get('sort') or 'price', limit=a.get('limit') or 40)


def _t_pricing(a):
    return _client(a).pricing()


def _t_templates(a):
    c = _client(a)
    if a.get('template_id'):
        return c.template(a['template_id'])
    return c.templates(category=a.get('category'), q=a.get('q'))


def _t_models(a):
    return _client(a).models(q=a.get('q'), provider=a.get('provider'),
                             max_prompt_price=a.get('max_prompt_price'),
                             min_context=a.get('min_context'),
                             sort=a.get('sort') or 'name', limit=a.get('limit') or 40)


def _t_quote(a):
    return _client(a).quote(a['gpu_type'], hours=a.get('hours') or 1,
                            spot=a.get('spot', True), quantity=a.get('quantity') or 1)


def _t_rent(a):
    return _client(a).rent(a['gpu_type'], name=a.get('name') or 'mod',
                           hours=a.get('hours') or 1, confirm=a.get('confirm', False),
                           ssh_public_key=a.get('ssh_public_key'),
                           spot=a.get('spot', True), quantity=a.get('quantity') or 1)


def _t_instances(a):
    c = _client(a)
    return c.instance(a['instance_id']) if a.get('instance_id') \
        else c.instances(state=a.get('state'))


def _t_ssh(a):
    return _client(a).ssh(a.get('instance_id'))


def _t_stop(a):
    return _client(a).stop(a['instance_id'])


def _t_deployments(a):
    c = _client(a)
    return c.deployment(a['deployment_id']) if a.get('deployment_id') \
        else c.deployments(state=a.get('state'), kind=a.get('kind'))


def _t_logs(a):
    return _client(a).deployment_logs(a['deployment_id'], tail=a.get('tail') or 200)


def _t_account(a):
    c = _client(a)
    return {'account': c.account(), 'stats': c.stats(), 'keys': c.api_keys()}


def _t_credits(a):
    c = _client(a)
    out = {'credits': c.credits()}
    if a.get('history'):
        out['history'] = c.history(limit=a.get('limit') or 20)
    out['packs'] = c.packs()
    return out


def _t_usage(a):
    return _client(a).usage(billing=a.get('billing', True))


def _t_status(a):
    return _client(a).status()


def _t_activity(a):
    return _client(a).activity(limit=a.get('limit') or 25)


def _t_raw(a):
    return _client(a).raw(a['path'], method=a.get('method') or 'GET',
                          body=a.get('body'), params=a.get('params'),
                          auth=a.get('auth', True))


TOOLS = {
    'polaris_gpus': {
        'description': 'The Polaris catalog: every GPU and CPU box for sale, with '
                       'spot and on-demand price per hour, VRAM, architecture and '
                       'whether it is available right now. Cheapest first. Public — '
                       'needs no key. Call this before quoting or renting: the '
                       '`gpu_type` field on each row is the exact string the rent '
                       'call wants.',
        'inputSchema': {'type': 'object', 'properties': {
            'gpu': _str('substring match on the name, e.g. "H100" or "A100"'),
            'min_vram_gb': _num('only types with at least this much VRAM'),
            'max_usd_hr': _num('only types at or under this spot price per hour'),
            'kind': _str('gpu or cpu — Polaris sells both', enum=['gpu', 'cpu']),
            'available_only': _bool('drop sold-out types (default true)'),
            'sort': _str('price, vram or name', enum=['price', 'vram', 'name']),
            'limit': _num('rows to return (default 40)'),
            'key': _KEY,
        }},
        'handler': _t_gpus,
    },
    'polaris_pricing': {
        'description': 'The billing table behind the catalog: the rate each class of '
                       'machine is actually charged at, spot and on-demand, keyed by '
                       'the billing key that shows up on the ledger. Use it to '
                       'reconcile a bill; use polaris_gpus to decide what to rent.',
        'inputSchema': {'type': 'object', 'properties': {'key': _KEY}},
        'handler': _t_pricing,
    },
    'polaris_templates': {
        'description': 'The one-click images Polaris can deploy onto a box: Ollama, '
                       'Jupyter, ComfyUI, LangFlow, n8n, Transformer Lab, a browser '
                       'desktop, agent runtimes (OpenClaw, ElizaOS, AutoGPT, CrewAI, '
                       'smolagents) and raw docker. Pass template_id for one in full, '
                       'including the parameters it takes and the port it serves on.',
        'inputSchema': {'type': 'object', 'properties': {
            'template_id': _str('one template, e.g. "ollama" or "jupyter"'),
            'category': _str('ai_ml, agents, development, desktop, games'),
            'q': _str('substring match on name or description'),
            'key': _KEY,
        }},
        'handler': _t_templates,
    },
    'polaris_models': {
        'description': 'The inference catalog Polaris fronts — 400+ models from every '
                       'major lab with context length and per-token prices in USD. '
                       'This is the model list, not a chat endpoint. Filtering happens '
                       'in this module because the upstream ignores query parameters '
                       'and returns all of them.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('substring match on model id or name'),
            'provider': _str('comma-separated: anthropic, openai, google, meta, …'),
            'max_prompt_price': _num('only models at or under this USD per input token'),
            'min_context': _num('only models with at least this context length'),
            'sort': _str('name, price or context', enum=['name', 'price', 'context']),
            'limit': _num('rows to return (default 40)'),
            'key': _KEY,
        }},
        'handler': _t_models,
    },
    'polaris_quote': {
        'description': 'What a rental would cost before you commit to it: the hourly '
                       'rate, the total for N hours, whether the type is actually '
                       'available, and anything cheaper with the same VRAM. Costs '
                       'nothing to ask, and tells you whether polaris_rent will '
                       'demand confirm=true. Always quote before renting.',
        'inputSchema': {'type': 'object', 'properties': {
            'gpu_type': _str('exact name from polaris_gpus, e.g. "H100 SXM5 80GB"'),
            'hours': _num('how long you intend to run it (default 1)'),
            'spot': _bool('spot pricing — cheaper, preemptible (default true)'),
            'quantity': _num('number of instances, 1-4 (default 1)'),
            'key': _KEY,
        }, 'required': ['gpu_type']},
        'handler': _t_quote,
    },
    'polaris_rent': {
        'description': 'Provision an instance. THIS SPENDS REAL MONEY from the '
                       f'caller\'s Polaris credits. Estimates above ${CONFIRM_USD} '
                       'are refused unless confirm=true, and the refusal carries the '
                       'quote so you can show it before retrying. `hours` prices the '
                       'quote only — billing runs by the second until polaris_stop, '
                       'not until the hours run out. The box comes up in 1-3 minutes; '
                       'poll polaris_instances, then polaris_ssh.',
        'inputSchema': {'type': 'object', 'properties': {
            'gpu_type': _str('exact name from polaris_gpus'),
            'name': _str('instance name (default "mod")'),
            'hours': _num('hours to price the guard against (default 1)'),
            'confirm': _bool('true to actually spend the money'),
            'spot': _bool('spot pricing — cheaper, preemptible (default true)'),
            'quantity': _num('number of instances, 1-4 (default 1)'),
            'ssh_public_key': _str('the key to authorize; defaults to ~/.ssh/*.pub'),
            'key': _KEY,
        }, 'required': ['gpu_type']},
        'handler': _t_rent,
    },
    'polaris_instances': {
        'description': 'Every instance on the account, with status, address, hourly '
                       'cost and the combined burn rate in USD/hr. Pass instance_id '
                       'for one. This is the truth about what is costing money right '
                       'now — check it before renting more.',
        'inputSchema': {'type': 'object', 'properties': {
            'instance_id': _str('one instance, by id or name'),
            'state': _str('filter by status, e.g. running or provisioning'),
            'key': _KEY,
        }},
        'handler': _t_instances,
    },
    'polaris_ssh': {
        'description': 'The ssh command for a running instance, plus its browser '
                       'terminal URL when the image ships one. Fails with a clear '
                       'status while the box is still provisioning — that is normal '
                       'for the first couple of minutes, so poll rather than give up.',
        'inputSchema': {'type': 'object', 'properties': {
            'instance_id': _str('one instance; omit for every instance with an address'),
            'key': _KEY,
        }},
        'handler': _t_ssh,
    },
    'polaris_stop': {
        'description': 'Terminate an instance. THIS IS WHAT ENDS THE BILLING — an '
                       'instance left running bills by the second until the balance '
                       'is gone. Anything on the box that was not saved elsewhere is '
                       'lost. Irreversible.',
        'inputSchema': {'type': 'object', 'properties': {
            'instance_id': _str('the instance to terminate'),
            'key': _KEY,
        }, 'required': ['instance_id']},
        'handler': _t_stop,
    },
    'polaris_deployments': {
        'description': 'Template deployments — a workload (Ollama, Jupyter, an agent) '
                       'running on a Polaris box, with its status, endpoint URL and '
                       'hourly cost. Pass deployment_id for one in full, including '
                       'host, port, progress and the parameters it was given.',
        'inputSchema': {'type': 'object', 'properties': {
            'deployment_id': _str('one deployment'),
            'state': _str('filter by status: running, stopped, failed'),
            'kind': _str('filter by type: serverless, raw_compute'),
            'key': _KEY,
        }},
        'handler': _t_deployments,
    },
    'polaris_logs': {
        'description': 'Provisioning and runtime logs for a deployment — the first '
                       'place to look when a deployment says failed and does not say '
                       'why.',
        'inputSchema': {'type': 'object', 'properties': {
            'deployment_id': _str('the deployment to read'),
            'tail': _num('last N lines (default 200)'),
            'key': _KEY,
        }, 'required': ['deployment_id']},
        'handler': _t_logs,
    },
    'polaris_account': {
        'description': 'Who the key belongs to and what it is allowed: tier, compute '
                       'hours used against the limit, deployment cap, storage quota, '
                       'plus the account\'s API keys as metadata (never the secrets).',
        'inputSchema': {'type': 'object', 'properties': {'key': _KEY}},
        'handler': _t_account,
    },
    'polaris_credits': {
        'description': 'The prepaid balance and what it permits. `status` is the gate: '
                       'active over $5, low_balance under it, restricted at zero, '
                       'where new instances are refused. Set history=true for the '
                       'ledger of what spent it. Top-up packs are included; buying '
                       'one is a browser flow, not an API call.',
        'inputSchema': {'type': 'object', 'properties': {
            'history': _bool('include the credit ledger'),
            'limit': _num('ledger entries (default 20)'),
            'key': _KEY,
        }},
        'handler': _t_credits,
    },
    'polaris_usage': {
        'description': 'Two different meters: API requests made with each key, and '
                       'compute seconds billed this period. Use it to explain a bill '
                       'that credits alone does not.',
        'inputSchema': {'type': 'object', 'properties': {
            'billing': _bool('include the compute-seconds meter (default true)'),
            'key': _KEY,
        }},
        'handler': _t_usage,
    },
    'polaris_activity': {
        'description': 'The account event tape: what deployed, what an agent said, '
                       'what fell over and when. Newest first.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('events to return (default 25)'),
            'key': _KEY,
        }},
        'handler': _t_activity,
    },
    'polaris_status': {
        'description': 'One call for the whole picture: balance, account quota, every '
                       'instance with the combined burn rate, every deployment, and '
                       'hours of runway at the current burn. Start here when asked '
                       '"what is running" or "how much have I got left".',
        'inputSchema': {'type': 'object', 'properties': {'key': _KEY}},
        'handler': _t_status,
    },
    'polaris_raw': {
        'description': 'Polaris\'s own API, unnormalized — the escape hatch for routes '
                       'this module has not wrapped. `path` is relative to '
                       'https://api.polaris.computer/api. Owner-only: it can reach '
                       'anything the key can, including writes.',
        'inputSchema': {'type': 'object', 'properties': {
            'path': _str('e.g. /billing/credits/history'),
            'method': _str('GET, POST or DELETE', enum=['GET', 'POST', 'DELETE']),
            'body': {'type': 'object', 'description': 'JSON body for POST'},
            'params': {'type': 'object', 'description': 'query parameters'},
            'auth': _bool('send the bearer token (default true)'),
            'key': _KEY,
        }, 'required': ['path']},
        'handler': _t_raw,
    },
}


def call_tool(name, args):
    tool = TOOLS.get(name)
    if tool is None:
        raise PolarisError(f'no tool {name}', status=404,
                           hint=f'have: {", ".join(TOOLS)}')
    return tool['handler'](dict(args or {}))


# ── JSON-RPC ──

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def _call(id_, params, owner=False):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        if not owner:
            auth.guard_tool(name, args)
        result = call_tool(name, args)
    except PolarisError as e:
        # A tool failure is a *successful* JSON-RPC response carrying isError,
        # per the MCP spec, so the model reads the hint and retries.
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), indent=2)}],
                             'structuredContent': e.dict(), 'isError': True})
    except KeyError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name}: missing argument {e}'}],
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


def handle(body, owner=False):
    """One JSON-RPC message in, one response out (None for notifications).

    `owner` comes from the transport: stdio means the caller started this
    process, HTTP means they proved it with the token or came from localhost.
    Tools that spend money are refused without it.
    """
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
            'serverInfo': {'name': 'polaris', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params, owner=owner)
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
            # stdio: whoever started this process is the operator by definition.
            resp = handle(body, owner=True)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50870)))
    else:
        serve_stdio()
