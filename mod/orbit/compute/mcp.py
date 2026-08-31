#!/usr/bin/env python3
"""compute mcp — one MCP tool layer over every compute market.

Twenty-two tools instead of a hundred: the aggregation *is* the interface. An
agent that learns `compute_search` → `compute_quote` → `compute_rent` →
`compute_stop` can drive Targon, Lium, Cathedral, Akash, Vast, Nosana, Prime,
Polaris and its own hardware without learning any of them.

Self-contained: JSON-RPC 2.0 hand-rolled on the stdlib, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50510 # Streamable HTTP — POST /mcp

The module's own API server mounts `handle()` at /mcp too, so the tools, the
REST routes and the console can never drift apart.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

import auth                               # noqa: E402
import node as N                          # noqa: E402
from hub import CONFIRM_USD, Hub          # noqa: E402
from providers import CAPS, REGISTRY      # noqa: E402
from providers.base import ProviderError  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'One interface to every compute market: Targon and Lium (Bittensor), Akash, '
    'Vast.ai, Clore, Nosana, Aleph Cloud, Cathedral (confidential TDX), Prime '
    'Intellect, Polaris, Hyperbolic, RunPod, Fluence, Shadeform, and your own '
    'docker hosts. Offers and rentals share one shape, '
    'priced in USD/hr, addressed as provider:ref. Start with compute_providers '
    '(who is reachable, who needs a key, who is no-KYC), then compute_search '
    '(fan-out, cheapest first), compute_map (the same fan-out answered as places on a map — where the machines physically are), compute_quote (cost for N hours + cheaper '
    'alternatives), compute_rent (spend-guarded), compute_instances / '
    'compute_logs, and compute_stop — stopping is what ends the billing. '
    'Every call spends the caller\'s own credits on their own provider account; '
    'there is no shared key. compute_raw is the escape hatch to a provider\'s '
    'native API when the normalized surface is not enough.'
)

_PROVIDER_ENUM = list(REGISTRY)


def _hub(args):
    """Per-call keys (`keys: {provider: key}`) never leave this process."""
    return Hub(keys=args.pop('keys', None) if isinstance(args, dict) else None)


# ── tools ──

def _t_providers(a):
    return _hub(a).providers(kyc=a.get('kyc'), cap=a.get('cap'),
                             names=a.get('provider'))


def _t_mods(a):
    import mods
    keys = a.pop('keys', None) if isinstance(a, dict) else None
    return mods.lane(names=a.get('mod'), keys=keys,
                     compare=a.get('compare', True), sample=a.get('sample', 6),
                     gpu=a.get('gpu'), max_usd_hr=a.get('max_usd_hr'),
                     min_gpus=a.get('min_gpus'), min_vram_gb=a.get('min_vram_gb'),
                     kind=a.get('kind'))


def _t_search(a):
    return _hub(a).search(
        provider=a.get('provider'), kyc=a.get('kyc'), sort=a.get('sort') or 'price',
        gpu=a.get('gpu'), min_gpus=a.get('min_gpus'), min_vram_gb=a.get('min_vram_gb'),
        max_usd_hr=a.get('max_usd_hr'), min_usd_hr=a.get('min_usd_hr'),
        region=a.get('region'), kind=a.get('kind'),
        available_only=a.get('available_only', True), limit=a.get('limit') or 40)


def _t_map(a):
    return _hub(a).map(
        provider=a.get('provider'), kyc=a.get('kyc'),
        gpu=a.get('gpu'), min_gpus=a.get('min_gpus'), min_vram_gb=a.get('min_vram_gb'),
        max_usd_hr=a.get('max_usd_hr'), min_usd_hr=a.get('min_usd_hr'),
        region=a.get('region'), kind=a.get('kind'),
        available_only=a.get('available_only', True), limit=a.get('limit') or 2000)


def _t_offer(a):
    return _hub(a).offer(a['id'])


def _t_quote(a):
    return _hub(a).quote(a['id'], hours=a.get('hours') or 1)


def _t_rent(a):
    h = _hub(a)
    opts = {k: v for k, v in a.items()
            if k in ('name', 'image', 'cmd', 'ssh_key', 'disk_gb', 'gpus',
                     'template_id', 'region', 'host', 'gpu_count')}
    return h.rent(a['id'], hours=a.get('hours') or 1,
                  confirm=bool(a.get('confirm')), **opts)


def _t_instances(a):
    return _hub(a).instances(provider=a.get('provider'))


def _t_status(a):
    return _hub(a).status(a['id'])


def _t_logs(a):
    return _hub(a).logs(a['id'], tail=a.get('tail') or 200)


def _t_exec(a):
    return _hub(a).exec(a['id'], a['cmd'])


def _t_stop(a):
    return _hub(a).stop(a['id'])


def _t_balance(a):
    return _hub(a).balance(provider=a.get('provider'))


def _t_set_key(a):
    return _hub(a).set_key(a['provider'], a['key'], persist=a.get('persist', True))


def _t_raw(a):
    return _hub(a).raw(a['provider'], a['path'], method=a.get('method') or 'GET',
                       body=a.get('body'), params=a.get('params'))


# ── nodes: the rented box, running mod ──

def _t_deploy(a):
    keys = a.pop('keys', None)
    return N.deploy(id=a.get('id'), ssh=a.get('ssh'), instance=a.get('instance'),
                    docker=a.get('docker'), name=a.get('name'),
                    hours=a.get('hours') or 1, confirm=bool(a.get('confirm')),
                    image=a.get('image'), profile=a.get('profile') or 'lite',
                    ports=a.get('ports'), gpus=a.get('gpus'),
                    wait=bool(a.get('wait')), keys=keys)


def _t_nodes(a):
    if a.get('node'):
        return N.probe(a['node']) if a.get('probe', True) else N._public(N.get(a['node']))
    return N.nodes(state=a.get('state'))


def _t_node_sh(a):
    return N.sh(a['node'], a['cmd'], timeout=float(a.get('timeout') or 120))


def _t_node_mods(a):
    if a.get('mod'):
        return N.ctl(a['node'], 'fns', mod=a['mod'])
    return N.ctl(a['node'], 'mods')


def _t_node_call(a):
    return N.call(a['node'], a['mod'], fn=a.get('fn') or 'forward',
                  args=a.get('args'), kwargs=a.get('kwargs'), init=a.get('init'),
                  timeout=float(a.get('timeout') or 300))


def _t_node_push(a):
    return N.push(a['node'], a['mod'], restart=bool(a.get('restart')))


def _t_node_rm(a):
    keys = a.pop('keys', None)
    return N.destroy(a['node'], release=bool(a.get('release')), keys=keys)


def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _num(desc):
    return {'type': 'number', 'description': desc}


_ID = _str('offer or instance id as provider:ref, e.g. lium:b7095b41 — '
           'ids come from compute_search or compute_instances')

TOOLS = {
    'compute_providers': {
        'description': 'Every compute market this module can reach: what it can do '
                       '(search/rent/logs/stop…), whether a key is set, whether it '
                       'needs KYC, what pays for it (TAO, AKT, NOS, USDC, card) and '
                       'who holds custody. Call this first — it tells you which '
                       'providers a search will actually reach.',
        'inputSchema': {'type': 'object', 'properties': {
            'kyc': _str('only providers with this KYC level', enum=['none', 'email', 'account', 'full']),
            'cap': _str('only providers that support this verb', enum=list(CAPS)),
            'provider': _str('comma-separated provider names'),
        }},
        'handler': _t_providers,
    },
    'compute_mods': {
        'description': 'The three markets that also ship as their own modules on '
                       'this box — targon (Bittensor SN4), lium (SN51) and cathedral '
                       '(confidential TDX) — read through those modules instead of '
                       'straight from the upstream, each answer set beside what the '
                       'direct lane reads from the same market. Use it to see which '
                       'sibling module is reachable, what it serves, and where it has '
                       'drifted from the market it fronts. Offer ids are the '
                       'upstream\'s own, so they round-trip into compute_quote and '
                       'compute_rent, which always go direct.',
        'inputSchema': {'type': 'object', 'properties': {
            'mod': _str('comma-separated modules: targon, lium, cathedral'),
            'compare': {'type': 'boolean',
                        'description': 'also read the same market directly and report '
                                       'the difference (default true)'},
            'sample': _num('offers to return per module (default 6, 0 for all)'),
            'gpu': _str('GPU to match, loosely: "H100", "4090"'),
            'min_gpus': _num('at least this many GPUs on one node'),
            'min_vram_gb': _num('at least this much VRAM per GPU'),
            'max_usd_hr': _num('price ceiling per hour'),
            'kind': _str('gpu | cpu | confidential | job | storage | all'),
        }},
        'handler': _t_mods,
    },
    'compute_search': {
        'description': 'Search every market at once for something rentable, cheapest '
                       'first, in one normalized shape (usd_hr, gpu, gpus, vram_gb, '
                       'cpu, ram_gb, region, available). Providers that fail or lack a '
                       'key are reported in `providers` instead of failing the search.',
        'inputSchema': {'type': 'object', 'properties': {
            'gpu': _str('GPU to match, loosely: "H100", "4090", "a100"'),
            'min_gpus': _num('at least this many GPUs on one node'),
            'min_vram_gb': _num('at least this much VRAM per GPU'),
            'max_usd_hr': _num('price ceiling per hour'),
            'min_usd_hr': _num('price floor per hour — with max_usd_hr it is a band, '
                               'which is how you skip the $0.001 junk tier'),
            'region': _str('substring of the region/country'),
            'provider': _str('comma-separated providers to limit the fan-out to'),
            'kyc': _str('only markets at this KYC level (use "none" for permissionless)',
                        enum=['none', 'email', 'account', 'full']),
            'kind': _str('gpu | cpu | confidential | job | storage | all — '
                         'storage is priced per GB-hour and is hidden unless asked for'),
            'available_only': {'type': 'boolean', 'description': 'skip sold-out offers (default true)'},
            'sort': _str('price (default) | vram | gpus'),
            'limit': _num('rows to return (default 40, max 200)'),
        }},
        'handler': _t_search,
    },
    'compute_map': {
        'description': 'Where the compute physically is. The same fan-out as '
                       'compute_search, answered as places instead of rows: one point '
                       'per city or country with how many offers are there, which '
                       'markets, and what the cheapest and median cost. Use it to pick '
                       'a jurisdiction or to sit next to your data, then re-run '
                       'compute_search with that region. Offers whose market publishes '
                       'no location are counted in `unplaced` — never placed on a guess.',
        'inputSchema': {'type': 'object', 'properties': {
            'gpu': _str('GPU to match, loosely: "H100", "4090", "a100"'),
            'min_gpus': _num('at least this many GPUs on one node'),
            'min_vram_gb': _num('at least this much VRAM per GPU'),
            'max_usd_hr': _num('price ceiling per hour'),
            'min_usd_hr': _num('price floor per hour'),
            'region': _str('substring of the region/country'),
            'provider': _str('comma-separated providers to limit the fan-out to'),
            'kyc': _str('only markets at this KYC level (use "none" for permissionless)',
                        enum=['none', 'email', 'account', 'full']),
            'kind': _str('gpu | cpu | confidential | job | storage | all'),
            'available_only': {'type': 'boolean', 'description': 'skip sold-out offers (default true)'},
            'limit': _num('offers to place (default 2000)'),
        }},
        'handler': _t_map,
    },
    'compute_offer': {
        'description': 'Re-read one offer from its provider — confirms it still exists '
                       'and is still that price before you rent it.',
        'inputSchema': {'type': 'object', 'properties': {'id': _ID}, 'required': ['id']},
        'handler': _t_offer,
    },
    'compute_quote': {
        'description': 'What an offer costs for N hours, whether it trips the spend '
                       'guard, and what the same GPU costs on the other markets. Use '
                       'before compute_rent — this is the only call that shows the '
                       'cross-market comparison.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'hours': _num('hours to price (default 1)')},
            'required': ['id']},
        'handler': _t_quote,
    },
    'compute_rent': {
        'description': f'Rent an offer. SPENDS THE CALLER\'S MONEY and keeps spending '
                       f'until compute_stop. Estimates over ${CONFIRM_USD} return a '
                       f'quote with needs_confirm instead of renting; call again with '
                       f'confirm=true. Chain-native markets (Akash, Nosana) return a '
                       f'ready-to-sign plan rather than renting, because this module '
                       f'holds no wallet.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID,
            'hours': _num('hours to hold it (also the auto-termination where supported)'),
            'confirm': {'type': 'boolean', 'description': 'yes, spend the money'},
            'name': _str('label for the rental (default "mod")'),
            'image': _str('docker image, where the provider takes one'),
            'cmd': _str('command to run on start'),
            'ssh_key': _str('SSH public key to install'),
            'disk_gb': _num('disk to attach, where the provider takes one'),
            'gpus': _num('how many GPUs of the node to take'),
            'region': _str('region, where the provider takes one'),
        }, 'required': ['id']},
        'handler': _t_rent,
    },
    'compute_instances': {
        'description': 'Everything you have running across every market, with the '
                       'combined burn rate in USD/hr. Only providers with a key are '
                       'polled.',
        'inputSchema': {'type': 'object', 'properties': {
            'provider': _str('limit to these providers')}},
        'handler': _t_instances,
    },
    'compute_status': {
        'description': 'One rental: state, price, ssh line.',
        'inputSchema': {'type': 'object', 'properties': {'id': _ID}, 'required': ['id']},
        'handler': _t_status,
    },
    'compute_logs': {
        'description': "A rental's logs.",
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'tail': _num('lines from the end (default 200)')},
            'required': ['id']},
        'handler': _t_logs,
    },
    'compute_exec': {
        'description': 'Run a command inside a rental, where the provider supports it '
                       '(otherwise use the ssh line from compute_status).',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _ID, 'cmd': _str('command to run')}, 'required': ['id', 'cmd']},
        'handler': _t_exec,
    },
    'compute_stop': {
        'description': 'Stop a rental and end its billing. This is the call that stops '
                       'the meter — suspending or ignoring a box does not.',
        'inputSchema': {'type': 'object', 'properties': {'id': _ID}, 'required': ['id']},
        'handler': _t_stop,
    },
    'compute_balance': {
        'description': "Prepaid balance on each provider account you hold a key for.",
        'inputSchema': {'type': 'object', 'properties': {
            'provider': _str('limit to these providers')}},
        'handler': _t_balance,
    },
    'compute_set_key': {
        'description': 'Store an API key for one provider in the off-tree keystore '
                       '(~/.mod/compute/keys.json, 0600). Keys are never returned by '
                       'any tool. Chain-native markets (Akash, Nosana) take no key.',
        'inputSchema': {'type': 'object', 'properties': {
            'provider': _str('provider name', enum=_PROVIDER_ENUM),
            'key': _str('the API key'),
            'persist': {'type': 'boolean', 'description': 'write it to disk (default true)'},
        }, 'required': ['provider', 'key']},
        'handler': _t_set_key,
    },
    'compute_raw': {
        'description': "Escape hatch: call a provider's own API directly, with that "
                       "provider's key attached. For anything the normalized surface "
                       'does not cover (volumes, templates, receipts, subnet weights).',
        'inputSchema': {'type': 'object', 'properties': {
            'provider': _str('provider name', enum=_PROVIDER_ENUM),
            'path': _str('path under that provider\'s upstream base, e.g. /volumes'),
            'method': _str('GET (default), POST, DELETE…'),
            'body': {'type': 'object', 'description': 'JSON body'},
            'params': {'type': 'object', 'description': 'query parameters'},
        }, 'required': ['provider', 'path']},
        'handler': _t_raw,
    },
    'compute_deploy': {
        'description': 'Turn compute into a machine you can use: rent (or adopt) a '
                       'box, put a container on it, install the mod protocol, and '
                       'register it as a node you can drive from here. Give exactly '
                       'one of id (rent this offer), instance (a rental you already '
                       'have), ssh (any box you can already reach) or docker=true (a '
                       'container on this machine, nothing rented). Renting spends '
                       'money under the same spend guard as compute_rent. The lite '
                       'profile is ~15s and installs mod core only; full also '
                       'installs requirements.txt, torch included, and takes minutes.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('offer to rent first, as provider:ref'),
            'instance': _str('a rental you already have, as provider:ref'),
            'ssh': _str('a box you can already reach: user@host or "ssh -p N user@host"'),
            'docker': {'type': 'boolean', 'description': 'a container on this machine'},
            'name': _str('a name for the node'),
            'hours': _num('hours to rent, when renting'),
            'confirm': {'type': 'boolean', 'description': 'yes, spend the money'},
            'image': _str('docker image (default python:3.12-slim)'),
            'profile': _str('lite (default) or full', enum=['lite', 'full']),
            'ports': _str('container ports to publish, e.g. "8000,3000"'),
            'gpus': _str('docker --gpus value, e.g. "all"'),
            'wait': {'type': 'boolean',
                     'description': 'block until the install finishes (default false — '
                                    'it runs in the background and compute_nodes shows '
                                    'progress)'},
        }},
        'handler': _t_deploy,
    },
    'compute_nodes': {
        'description': 'Every node this module has deployed, with state (new, '
                       'booting, ready, unreachable, gone), the mod version and '
                       'module count on it, and the combined burn rate. Pass node=ID '
                       'for one, which re-probes it live.',
        'inputSchema': {'type': 'object', 'properties': {
            'node': _str('one node id, e.g. n-73e368'),
            'state': _str('filter the list by state'),
            'probe': {'type': 'boolean', 'description': 'live-probe a single node (default true)'},
        }},
        'handler': _t_nodes,
    },
    'compute_node_sh': {
        'description': 'Run a shell command on a node, over whichever transport it '
                       'was reached by (docker exec, SSH, or the provider\'s exec '
                       'API). Returns stdout, stderr and the exit code.',
        'inputSchema': {'type': 'object', 'properties': {
            'node': _str('node id'), 'cmd': _str('the command'),
            'timeout': _num('seconds (default 120)')}, 'required': ['node', 'cmd']},
        'handler': _t_node_sh,
    },
    'compute_node_mods': {
        'description': 'What the mod protocol on that node can do: every module it '
                       'has, or — with mod=NAME — that module\'s callable functions. '
                       'This is the map you read before compute_node_call.',
        'inputSchema': {'type': 'object', 'properties': {
            'node': _str('node id'),
            'mod': _str('a module on the node, to list its fns instead')},
            'required': ['node']},
        'handler': _t_node_mods,
    },
    'compute_node_call': {
        'description': 'Call a module function on the node and bring the answer '
                       'back. The node runs it — this is how a rented GPU box gets '
                       'driven without opening a port on it.',
        'inputSchema': {'type': 'object', 'properties': {
            'node': _str('node id'), 'mod': _str('module name on the node'),
            'fn': _str('function (default forward)'),
            'args': {'type': 'array', 'description': 'positional arguments'},
            'kwargs': {'type': 'object', 'description': 'keyword arguments'},
            'init': {'type': 'object', 'description': 'constructor kwargs for the module'},
            'timeout': _num('seconds (default 300)'),
        }, 'required': ['node', 'mod']},
        'handler': _t_node_call,
    },
    'compute_node_push': {
        'description': 'Send a module from this machine to the node — the local '
                       'directory as it is right now, not a git checkout — so the '
                       'node can run code you just wrote. Deploy installs mod core '
                       'only; this is how everything else gets there.',
        'inputSchema': {'type': 'object', 'properties': {
            'node': _str('node id'), 'mod': _str('a local module name, e.g. compute'),
            'restart': {'type': 'boolean', 'description': 'run `m <mod>/serve` after'}},
            'required': ['node', 'mod']},
        'handler': _t_node_push,
    },
    'compute_node_rm': {
        'description': 'Tear a node down. release=true also stops the rental it '
                       'rides on, which is the call that ends the billing — without '
                       'it the box keeps costing money after the container is gone.',
        'inputSchema': {'type': 'object', 'properties': {
            'node': _str('node id'),
            'release': {'type': 'boolean', 'description': 'also stop the rental'}},
            'required': ['node']},
        'handler': _t_node_rm,
    },
}


# ── JSON-RPC 2.0 ──

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool. Raises ProviderError/ValueError with a readable message."""
    tool = TOOLS.get(name)
    if not tool:
        raise ValueError(f'unknown tool: {name} — have {", ".join(TOOLS)}')
    return tool['handler'](dict(args or {}))


def _call(id_, params, owner=False):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        if not owner:
            auth.guard_tool(name, args)
        result = call_tool(name, args)
    except ProviderError as e:
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
    Tools that spend money or touch a node are refused without it.
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
            'serverInfo': {'name': 'compute', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': [
            {'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]})
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
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50510)))
    else:
        serve_stdio()
