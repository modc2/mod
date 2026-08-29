"""plan — check a graph against the live fleet before spending a call on it.

Parsing catches what is wrong with the document. This catches what is wrong
with the document *here*: a tool that no server in the fleet carries, a
required argument nobody filled in, a server that is currently down. With 669
tools across 38 servers, a typo in a tool name is the normal failure, and
finding it at step 7 of 9 costs six calls and the state they changed.

Nothing here calls a tool. It reads the hub's index and compares.
"""

import difflib

from . import refs, targets
from .graph import Graph, SpecError


def plan(spec, inputs=None, check_tools=True):
    """Everything knowable about a graph without running it."""
    graph = spec if isinstance(spec, Graph) else Graph(spec)
    issues, index, servers = [], {}, {}
    if check_tools:
        try:
            index = targets.tool_index()
        except targets.StepError as e:
            issues.append({'level': 'warn', 'step': None, 'issue': 'hub',
                           'message': f'{e} — tool names cannot be checked'})
        try:
            servers = {s['id']: s for s in (targets.servers() or [])}
        except targets.StepError:
            pass

    for step in graph.steps:
        if step.use == 'mcp' and index:
            issues += _check_tool(step, index, servers)
        if step.use == 'graph':
            issues += _check_subgraph(step)
        issues += _check_refs(step, graph)

    binding = None
    try:
        binding = graph.bind(inputs)
    except SpecError as e:
        issues.append({'level': 'error', 'step': None, 'issue': 'inputs',
                       'message': str(e)})

    calls = sum(1 for s in graph.steps if s.use != 'expr')
    fanout = [s.id for s in graph.steps if s.foreach is not None]
    return {
        'ok': not any(i['level'] == 'error' for i in issues),
        'graph': graph.dict(),
        'ascii': graph.ascii(),
        'inputs': binding,
        'waves': len(graph.levels()),
        'calls': calls,
        'note': (f'{calls} call(s) in {len(graph.levels())} wave(s)'
                 + (f'; {", ".join(fanout)} fan out, so the real count depends on '
                    'what upstream returns' if fanout else '')),
        'issues': issues,
    }


def _check_tool(step, index, servers):
    name = step.target()
    if name in index:
        server = name.split('__')[0]
        row = servers.get(server)
        if row and row.get('probe') and not row['probe'].get('ok'):
            return [{'level': 'warn', 'step': step.id, 'issue': 'server_down',
                     'message': f'{server} is not answering right now '
                                f'({row["probe"].get("error") or "no reason given"})'
                                ' — the hub will try to wake it'}]
        return _check_args(step, index[name])
    close = difflib.get_close_matches(name, list(index), n=3, cutoff=0.5)
    if not close and '__' in name:
        bare = name.split('__')[-1]
        close = [t for t in index if t.endswith('__' + bare)][:3]
    return [{'level': 'error', 'step': step.id, 'issue': 'no_such_tool',
             'message': f'no tool named {name!r} in the fleet'
                        + (f' — did you mean {", ".join(close)}?' if close else
                           ' — GET /tools?q= searches the 669 that exist')}]


def _check_args(step, tool):
    """Required arguments, per the tool's own inputSchema."""
    out = []
    schema = tool.get('inputSchema') or {}
    required = schema.get('required') or []
    props = schema.get('properties') or {}
    given = set(step.args or {})
    for name in required:
        if name not in given:
            out.append({'level': 'error', 'step': step.id, 'issue': 'missing_arg',
                        'message': f'{step.target()} requires {name!r} '
                                   f'({(props.get(name) or {}).get("description") or "no description"})'})
    if props:
        for name in given - set(props):
            out.append({'level': 'warn', 'step': step.id, 'issue': 'unknown_arg',
                        'message': f'{step.target()} does not document an argument '
                                   f'{name!r} — it takes '
                                   f'{", ".join(sorted(props)) or "none"}'})
    return out


def _check_subgraph(step):
    from . import store
    try:
        store.load_graph(step.graph)
        return []
    except store.StoreError as e:
        return [{'level': 'error', 'step': step.id, 'issue': 'no_such_graph',
                 'message': str(e)}]


def _check_refs(step, graph):
    """References that name a step which will never have run by then.

    The topological order already guarantees a referenced step runs first, so
    what is left to catch is a reference into a step's output shape, which
    cannot be known statically — those are left alone. What IS catchable: a
    reference to an input the graph never declares.
    """
    out = []
    declared = set(graph.declared_inputs)
    for expr in refs.refs([step.args, step.value, step.foreach, step.url,
                           getattr(step, 'if'), step.unless, step.inputs]):
        raw = expr.strip().rstrip('?').strip()
        try:
            parts = refs.tokens(raw)
        except refs.RefError as e:
            out.append({'level': 'error', 'step': step.id, 'issue': 'bad_ref',
                        'message': str(e)})
            continue
        if parts and parts[0] == 'inputs' and len(parts) > 1:
            if str(parts[1]) not in declared:
                out.append({'level': 'warn', 'step': step.id, 'issue': 'undeclared_input',
                            'message': f'${{{expr}}} uses an input the graph does not '
                                       f'declare — add it under "inputs" so a caller '
                                       f'knows to pass it'})
        elif parts and parts[0] not in refs.ROOTS and str(parts[0]) not in graph.by_id:
            out.append({'level': 'error', 'step': step.id, 'issue': 'bad_ref',
                        'message': f'${{{expr}}} names neither a step nor one of '
                                   f'{", ".join(refs.ROOTS)}'})
    return out
