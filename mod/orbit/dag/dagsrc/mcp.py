#!/usr/bin/env python3
"""dag mcp — nine tools for composing the fleet instead of calling it one tool
at a time.

The fleet already exposes hundreds of MCP tools. What it has never had is a way
to say "these four, in this shape, with the output of one feeding the next" in a
single call. That is what `dag_run` is. `dag_plan` is the half of it that costs
nothing: it checks every tool name and required argument against the live hub
and prices the run in calls and waves before a single one is spent.

An agent writing a graph should go: dag_tools (find the tools) -> dag_plan
(check the graph) -> dag_run (spend the calls) -> dag_save (keep it).

Self-contained JSON-RPC 2.0 on the standard library.

    python3 -m dagsrc.mcp             # stdio
    python3 mod.py serve           # http, on the module's port
"""

import json
import os
import sys

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dagsrc import plan as planner, refs, runner, store, targets
    from dagsrc.graph import Graph, SpecError
else:
    from . import plan as planner, refs, runner, store, targets
    from .graph import Graph, SpecError

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_OUT = int(os.environ.get('DAG_MAX_RESULT_CHARS', 40000))

INSTRUCTIONS = (
    'Run a DAG over the mod fleet. A graph is JSON: a list of steps, each one a '
    'call — an MCP tool on any server the hub carries (the default), a mod fn, '
    'or an HTTP route — plus `expr` steps that reshape data between them. Steps '
    'reference each other with ${...}, e.g. args:{"mint":"${price.prices[0].mint}"}, '
    'and those references ARE the edges: nothing declares dependencies, and '
    'anything independent runs in parallel. `foreach` fans a step out over a '
    'list. Start with dag_tools to find the tools that exist (there are '
    'hundreds), then dag_plan, which checks every tool name and required '
    'argument against the live fleet and tells you how many calls the run will '
    'cost WITHOUT making any. Then dag_run. dag_save keeps a graph by name so '
    'later runs are just dag_run {graph:"name", inputs:{...}}.'
)

EXAMPLE = {
    'name': 'wallet-report',
    'inputs': {'wallet': {'required': True, 'description': 'a Solana address'}},
    'steps': [
        {'id': 'what', 'tool': 'solana__sol_account',
         'args': {'address': '${inputs.wallet}'}},
        {'id': 'held', 'tool': 'solana__sol_portfolio',
         'args': {'address': '${inputs.wallet}'}},
        {'id': 'top', 'use': 'expr', 'value': '${held.tokens}',
         'sort_by': 'value_usd', 'desc': True, 'limit': 3},
        {'id': 'risk', 'foreach': '${top}', 'tool': 'solana__sol_token',
         'args': {'mint': '${item.mint}'}, 'pick': 'risk'},
    ],
    'output': {'kind': '${what.kind?}', 'usd': '${held.total_usd}',
               'top': '${top.symbol}', 'risk': '${risk}'},
}


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _obj(desc):
    return {'type': 'object', 'description': desc, 'additionalProperties': True}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


def _num(desc):
    return {'type': 'number', 'description': desc}


_GRAPH_ARG = {
    'graph': {'description': 'The graph: either the name of a saved one, or the '
                             'whole spec as an object — {name, inputs, steps[, '
                             'output]}. See the `example` field of dag_info for a '
                             'complete one.'},
    'inputs': _obj('Values for the graph\'s declared inputs'),
}


# ── the tools ────────────────────────────────────────────────────

def _spec(args, field='graph'):
    """A graph argument is a saved name or a literal spec. Both, here."""
    g = args.get(field)
    if g is None and args.get('steps'):
        g = {k: v for k, v in args.items() if k in
             ('name', 'title', 'description', 'inputs', 'steps', 'output',
              'max_parallel', 'timeout')}
    if isinstance(g, str):
        try:
            return json.loads(g) if g.lstrip().startswith('{') else store.load_graph(g)
        except json.JSONDecodeError as e:
            raise SpecError(f'graph looked like JSON but is not: {e}')
    if isinstance(g, dict):
        return g
    raise SpecError('pass graph= as the name of a saved graph or as the spec '
                    'object itself')


def t_run(a):
    spec = _spec(a)
    depth = int(a.get('_depth') or 0)
    rec = runner.Run(Graph(spec), inputs=a.get('inputs'), depth=depth,
                     dry_run=bool(a.get('dry_run'))).execute()
    return _trim(rec, verbose=bool(a.get('verbose')))


def t_plan(a):
    return planner.plan(_spec(a), inputs=a.get('inputs'),
                        check_tools=a.get('check_tools', True))


def t_tools(a):
    q = (a.get('q') or '').lower()
    limit = int(a.get('limit') or 30)
    index = targets.tool_index()
    rows = []
    for name, tool in index.items():
        desc = tool.get('description') or ''
        if q and q not in name.lower() and q not in desc.lower():
            continue
        schema = tool.get('inputSchema') or {}
        rows.append({
            'tool': name,
            'server': name.split('__')[0] if '__' in name else 'hub',
            'description': desc if a.get('full') else desc[:220],
            'required': schema.get('required') or [],
            'args': sorted((schema.get('properties') or {})),
        })
    if a.get('server'):
        rows = [r for r in rows if r['server'] == a['server']]
    rows.sort(key=lambda r: (r['server'], r['tool']))
    return {'count': len(rows), 'total': len(index),
            'tools': rows[:limit],
            'note': f'{len(rows)} of {len(index)} fleet tools match'
                    + (f'; showing {limit}' if len(rows) > limit else '')}


def t_servers(a):
    rows = targets.servers() or []
    out = [{'server': s.get('id'), 'url': s.get('url'), 'source': s.get('source'),
            'ok': (s.get('probe') or {}).get('ok'),
            'tools': (s.get('probe') or {}).get('toolCount'),
            'note': (s.get('note') or '')[:160]} for s in rows]
    out.sort(key=lambda r: (not r['ok'], r['server'] or ''))
    return {'count': len(out), 'up': sum(1 for r in out if r['ok']), 'servers': out}


def t_save(a):
    spec = _spec(a)
    name = a.get('name') or spec.get('name')
    Graph(spec)                     # never save a graph that will not parse
    return store.save_graph(name, spec)


def t_graphs(a):
    name = a.get('name')
    if name:
        spec = store.load_graph(name)
        return {'name': name, 'spec': spec, 'plan': Graph(spec).dict(),
                'ascii': Graph(spec).ascii()}
    return {'graphs': store.graphs()}


def t_delete(a):
    return store.delete_graph(a['name'])


def t_runs(a):
    if a.get('run'):
        return _trim(store.load_run(a['run']), verbose=bool(a.get('verbose')))
    return {'runs': store.runs(limit=a.get('limit') or 20, graph=a.get('graph'),
                               status=a.get('status'))}


def t_info(a):
    return info()


def _trim(rec, verbose=False):
    """A run record, small enough to hand back to a model.

    Outputs are what the caller asked for and are kept whole up to a cap; the
    per-step outputs are the working, and are summarised unless asked for.
    """
    rec = dict(rec)
    rec.pop('plan', None)
    steps = []
    for s in rec.get('steps') or []:
        s = dict(s)
        if not verbose and 'out' in s:
            s['out'] = _cap(s['out'], 1200)
        steps.append(s)
    rec['steps'] = steps
    if 'outputs' in rec:
        rec['outputs'] = _cap(rec['outputs'], MAX_OUT)
    return rec


def _cap(value, limit):
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return value
    return {'truncated': True, 'chars': len(text),
            'preview': text[:limit] + '…',
            'note': 'ask for this step with verbose=true, or add a `pick` to the '
                    'step so it returns only the part you need'}


TOOLS = {
    'dag_run': {
        'description': 'RUN A GRAPH over the fleet. Steps whose dependencies are '
                       'satisfied run at the same time; a step that references '
                       '${other.field} waits for `other` and receives its output. '
                       'Pass graph= as a saved name or as the whole spec. A step '
                       'that fails does not stop branches that never depended on '
                       'it — those keep going, and everything downstream of the '
                       'failure is reported as skipped with the reason. Run '
                       'dag_plan first: it costs nothing and catches the tool name '
                       'you got wrong.',
        'inputSchema': {'type': 'object', 'properties': {
            **_GRAPH_ARG,
            'dry_run': _bool('Walk the graph without calling anything — every step '
                             'returns what it WOULD have called, with its arguments '
                             'resolved. The cheapest way to see whether the '
                             '${...} wiring is right.'),
            'verbose': _bool('Return every step\'s full output instead of a summary'),
        }, 'required': ['graph']},
        'handler': t_run,
    },
    'dag_plan': {
        'description': 'CHECK A GRAPH WITHOUT RUNNING IT. Parses it, finds cycles, '
                       'infers the dependency edges from the ${...} references, and '
                       'checks every tool name and required argument against the '
                       'live fleet — a misspelled tool comes back with the closest '
                       'real names. Says how many calls the run will cost and in how '
                       'many waves. No tool is called.',
        'inputSchema': {'type': 'object', 'properties': {
            **_GRAPH_ARG,
            'check_tools': _bool('Check names against the hub (default true; set '
                                 'false to validate a graph offline)'),
        }, 'required': ['graph']},
        'handler': t_plan,
    },
    'dag_tools': {
        'description': 'SEARCH EVERY TOOL IN THE FLEET — the catalogue you write a '
                       'graph against. Hundreds of tools across dozens of servers, '
                       'each returned with the arguments it takes and the ones it '
                       'requires, under the exact `server__tool` name a step should '
                       'use. Start here.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('Match against tool names and descriptions'),
            'server': _str('Only this server\'s tools'),
            'limit': _num('How many to return (default 30)'),
            'full': _bool('Full descriptions instead of the first 220 characters'),
        }},
        'handler': t_tools,
    },
    'dag_servers': {
        'description': 'Which MCP servers the fleet is currently offering, whether '
                       'each is answering, and how many tools it carries. A step '
                       'aimed at a server that is down still works — the hub wakes '
                       'a scaled-to-zero module on the way through — but a server '
                       'that has been down for a while is worth knowing about '
                       'before a 40-call run.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': t_servers,
    },
    'dag_save': {
        'description': 'Save a graph by name so later runs are just '
                       'dag_run {graph:"name", inputs:{...}}. The graph is parsed '
                       'first, so a spec that cannot run is never stored.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('What to call it'),
            'graph': _obj('The spec'),
        }, 'required': ['graph']},
        'handler': t_save,
    },
    'dag_graphs': {
        'description': 'List saved graphs, or fetch one by name with its execution '
                       'plan drawn as text.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('Fetch just this one, in full')}},
        'handler': t_graphs,
    },
    'dag_delete': {
        'description': 'Delete a saved graph. Runs already recorded are kept.',
        'inputSchema': {'type': 'object',
                        'properties': {'name': _str('The graph to delete')},
                        'required': ['name']},
        'handler': t_delete,
    },
    'dag_runs': {
        'description': 'Run history, or one run in full: per-step status, duration, '
                       'output and — for anything that failed — which step caused it '
                       'and which steps were skipped as a consequence. A run record '
                       'is written as the run goes, so this also follows one that is '
                       'still going.',
        'inputSchema': {'type': 'object', 'properties': {
            'run': _str('A run id — returns that run in full'),
            'graph': _str('Only runs of this graph'),
            'status': _str('ok | failed | running'),
            'limit': _num('How many (default 20)'),
            'verbose': _bool('Full step outputs'),
        }},
        'handler': t_runs,
    },
    'dag_info': {
        'description': 'What this module is, the shape of a graph spec, every field '
                       'a step accepts, and a complete worked example. Read this '
                       'before writing your first graph.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': t_info,
    },
}


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def info():
    return {
        'name': 'dag',
        'version': version(),
        'what': 'run a DAG over the mods — MCP tools, mod fns and HTTP routes as '
                'steps, wired by ${...} references and executed in parallel',
        'spec': {
            'graph': {
                'name': 'a name',
                'inputs': '{param: {required: true|false, default: ..., '
                          'description: ...}} — referenced as ${inputs.param}',
                'steps': 'the list; order does not matter, references do',
                'output': 'optional — what the run returns, e.g. '
                          '{"usd": "${held.total_usd}"}. Without it the run '
                          'returns the leaf steps.',
                'max_parallel': 'how many steps at once (default 8)',
                'timeout': 'seconds for the whole run',
            },
            'step': {
                'id': 'referenced as ${id.path}',
                'tool': 'server__tool — an MCP tool (this is the default kind)',
                'server': 'alternative to the server__ prefix',
                'url': 'an MCP endpoint to call directly, bypassing the hub',
                'call': '"<mod>/<fn>" — call a mod fn instead of a tool',
                'use': 'mcp | mod | http | expr | graph — inferred from the above',
                'args': 'the call arguments; ${...} anywhere inside',
                'foreach': 'a list — run this step once per item, in parallel, '
                           'with ${item} and ${index} bound',
                'if / unless': 'skip the step unless the condition is truthy',
                'needs': 'extra dependencies, when a step must wait for one it '
                         'does not reference',
                'pick': 'a path into the result — store only that part',
                'where / sort_by / desc / limit': 'filter and cut a list result',
                'retries / retry_delay / timeout': 'per step',
                'continue_on_error': 'a failure here does not fail the run',
            },
            'refs': {
                '${step.field}': "another step's output",
                '${inputs.name}': 'a run parameter',
                '${item} ${index}': 'inside a foreach',
                '${x.y?}': 'missing is None instead of an error',
                '${list.field}': 'a field over every element of a list',
            },
        },
        'example': EXAMPLE,
        'tools': sorted(TOOLS),
        'hub': targets.HUB,
        'state': store.DIR,
    }


# ── JSON-RPC ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool. Shared with the REST layer, so /run and an MCP tools/call
    cannot answer the same question differently."""
    tool = TOOLS.get(name)
    if not tool:
        raise SpecError(f'no tool named {name!r} — {", ".join(TOOLS)}')
    args = dict(args or {})
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, '') and not (
                required == 'graph' and args.get('steps')):
            raise SpecError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        return _result(id_, {
            'content': [{'type': 'text',
                         'text': json.dumps(out, default=str, indent=2)}],
            'structuredContent': out if isinstance(out, dict) else None,
            'isError': False})
    except (SpecError, store.StoreError, targets.StepError, refs.RefError) as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps({'error': str(e)})}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'bad arguments for {name}: {e}'}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def tool_list():
    return [{'name': n, 'description': t['description'],
             'inputSchema': t['inputSchema']} for n, t in TOOLS.items()]


def handle(body, depth=0):
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
            'serverInfo': {'name': 'dag', 'title': 'DAG', 'version': version()},
            'instructions': INSTRUCTIONS})
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        # A graph run through this server can itself contain dag_* steps. The
        # depth travels with the request so that recursion terminates rather
        # than discovering the fleet's connection limit.
        if depth and (params.get('name') or '') == 'dag_run':
            params = dict(params)
            params['arguments'] = {**(params.get('arguments') or {}),
                                   '_depth': depth}
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


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
    serve_stdio()
