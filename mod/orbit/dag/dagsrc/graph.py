"""graph — a DAG spec, read strictly and checked before anything runs.

A graph is JSON. Steps are calls into the fleet; edges are inferred from the
`${...}` in their arguments (see refs.py). Parsing is where a graph is allowed
to be wrong, because a graph that is wrong at step 9 of 10 has already spent
nine calls to find out.

    {
      "name": "what-is-this-wallet",
      "inputs": {"wallet": {"required": true}},
      "steps": [
        {"id": "what",  "tool": "solana__sol_account",   "args": {"address": "${inputs.wallet}"}},
        {"id": "held",  "tool": "solana__sol_portfolio", "args": {"address": "${inputs.wallet}"}},
        {"id": "top",   "use": "expr", "value": "${held.positions}", "sort_by": "usd",
         "desc": true, "limit": 3},
        {"id": "risk",  "foreach": "${top}", "tool": "solana__sol_token",
         "args": {"mint": "${item.mint}"}}
      ]
    }

`what` and `held` have no reference to each other, so they run at the same
time. `risk` runs once per element of `top`, in parallel, and its output is the
list of results in order.
"""

import json
import re

from . import refs

ID = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]*$')
USES = ('mcp', 'mod', 'http', 'expr', 'graph')
STEP_KEYS = {
    'id', 'use', 'name', 'description', 'tool', 'server', 'url', 'headers',
    'call', 'args', 'method', 'body', 'value', 'graph', 'inputs', 'foreach',
    'if', 'unless', 'needs', 'retries', 'retry_delay', 'timeout', 'pick',
    'where', 'sort_by', 'desc', 'limit', 'continue_on_error', 'concurrency',
    'wake',
}
GRAPH_KEYS = {'name', 'title', 'description', 'version', 'inputs', 'steps',
              'max_parallel', 'timeout', 'output', 'defaults'}


class SpecError(Exception):
    """The graph is not runnable. Every message names the step."""


class Step:
    __slots__ = tuple(STEP_KEYS) + ('needs', 'raw')

    def __init__(self, spec, index, ids):
        if not isinstance(spec, dict):
            raise SpecError(f'step {index}: expected an object, got '
                            f'{type(spec).__name__}')
        unknown = set(spec) - STEP_KEYS
        if unknown:
            raise SpecError(f'step {index}: unknown field(s) '
                            f'{", ".join(sorted(unknown))} — known fields are '
                            f'{", ".join(sorted(STEP_KEYS))}')
        self.raw = spec
        self.id = str(spec.get('id') or f'step{index + 1}')
        if not ID.match(self.id):
            raise SpecError(f'step {index}: id {self.id!r} must start with a letter '
                            'and hold only letters, digits, _ . -')

        self.tool = spec.get('tool')
        self.server = spec.get('server')
        self.url = spec.get('url')
        self.call = spec.get('call')
        self.value = spec.get('value')
        self.graph = spec.get('graph')
        self.use = spec.get('use') or self._infer()
        if self.use not in USES:
            raise SpecError(f'{self.id}: use={self.use!r} — one of '
                            f'{", ".join(USES)}')

        self.name = spec.get('name') or self.id
        self.description = spec.get('description') or ''
        self.headers = spec.get('headers') or {}
        self.args = spec.get('args', spec.get('params', {})) or {}
        self.method = (spec.get('method') or '').upper() or None
        self.body = spec.get('body')
        self.inputs = spec.get('inputs') or {}
        self.foreach = spec.get('foreach')
        setattr(self, 'if', spec.get('if'))
        self.unless = spec.get('unless')
        self.retries = int(spec.get('retries') or 0)
        self.retry_delay = float(spec.get('retry_delay') or 1.0)
        self.timeout = spec.get('timeout')
        self.pick = spec.get('pick')
        self.where = spec.get('where')
        self.sort_by = spec.get('sort_by')
        self.desc = bool(spec.get('desc'))
        # limit/timeout may be a ${...}, so they are kept raw and read as
        # numbers at run time, once the reference has a value.
        self.limit = spec.get('limit')
        self.continue_on_error = bool(spec.get('continue_on_error'))
        self.concurrency = int(spec.get('concurrency') or 0)
        self.wake = spec.get('wake', True)

        if self.use == 'mcp' and not self.tool:
            raise SpecError(f'{self.id}: an mcp step needs tool= '
                            '(server__tool, or tool= with server=)')
        if self.use == 'mod' and not self.call:
            raise SpecError(f'{self.id}: a mod step needs call="<mod>/<fn>"')
        if self.use == 'http' and not self.url:
            raise SpecError(f'{self.id}: an http step needs url=')
        if self.use == 'graph' and not self.graph:
            raise SpecError(f'{self.id}: a graph step needs graph="<saved name>"')
        if self.retries < 0 or self.retries > 10:
            raise SpecError(f'{self.id}: retries must be 0-10')

        declared = spec.get('needs')
        declared = [declared] if isinstance(declared, str) else list(declared or [])
        inferred = refs.depends_on(
            [self.args, self.value, self.foreach, getattr(self, 'if'), self.unless,
             self.url, self.body, self.inputs, self.headers, self.pick], ids)
        self.needs = sorted(set(declared) | inferred)

    def _infer(self):
        """The `use` you would have typed. Most steps are MCP tool calls, so
        `tool` alone is a complete step and nobody writes use=mcp by hand."""
        if self.tool:
            return 'mcp'
        if self.call:
            return 'mod'
        if self.url:
            return 'http'
        if self.graph:
            return 'graph'
        return 'expr'

    def target(self):
        """What this step calls, as one string for a log or a console."""
        if self.use == 'mcp':
            return (f'{self.server}__{self.tool}' if self.server and
                    '__' not in str(self.tool) else str(self.tool))
        if self.use == 'mod':
            return str(self.call)
        if self.use == 'http':
            return f'{self.method or "GET"} {self.url}'
        if self.use == 'graph':
            return f'graph:{self.graph}'
        return 'expr'

    def dict(self):
        d = {'id': self.id, 'use': self.use, 'target': self.target(),
             'needs': self.needs}
        if self.foreach is not None:
            d['foreach'] = self.foreach
        if getattr(self, 'if') is not None:
            d['if'] = getattr(self, 'if')
        if self.description:
            d['description'] = self.description
        return d


class Graph:
    """A parsed, cycle-free graph with its execution order worked out."""

    def __init__(self, spec):
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError as e:
                raise SpecError(f'not JSON: {e}')
        if not isinstance(spec, dict):
            raise SpecError(f'a graph is an object, got {type(spec).__name__}')
        unknown = set(spec) - GRAPH_KEYS
        if unknown:
            raise SpecError(f'unknown field(s) {", ".join(sorted(unknown))} — '
                            f'known: {", ".join(sorted(GRAPH_KEYS))}')

        raw_steps = spec.get('steps')
        if isinstance(raw_steps, dict):
            raw_steps = [{**v, 'id': k} for k, v in raw_steps.items()]
        if not isinstance(raw_steps, list) or not raw_steps:
            raise SpecError('a graph needs a non-empty "steps" list')
        if len(raw_steps) > 200:
            raise SpecError(f'{len(raw_steps)} steps — the cap is 200')

        self.spec = spec
        self.name = spec.get('name') or 'graph'
        self.title = spec.get('title') or self.name
        self.description = spec.get('description') or ''
        self.version = spec.get('version') or '1'
        self.max_parallel = max(1, int(spec.get('max_parallel') or 8))
        self.timeout = spec.get('timeout')
        self.output = spec.get('output')
        self.defaults = spec.get('defaults') or {}
        self.declared_inputs = self._inputs(spec.get('inputs'))

        ids = []
        for i, s in enumerate(raw_steps):
            sid = str(s.get('id') or f'step{i + 1}') if isinstance(s, dict) else None
            if sid in ids:
                raise SpecError(f'two steps share the id {sid!r}')
            ids.append(sid)
        idset = set(i for i in ids if i)

        self.steps = [Step(s, i, idset) for i, s in enumerate(raw_steps)]
        self.by_id = {s.id: s for s in self.steps}
        for s in self.steps:
            for dep in s.needs:
                if dep not in self.by_id:
                    close = ', '.join(sorted(self.by_id)[:8])
                    raise SpecError(f'{s.id} refers to step {dep!r}, which does not '
                                    f'exist — steps are: {close}')
                if dep == s.id:
                    raise SpecError(f'{s.id} refers to itself')
        self.order = self._topo()

    @staticmethod
    def _inputs(raw):
        """inputs may be a list of names, or names -> {required, default, ...}."""
        if not raw:
            return {}
        if isinstance(raw, list):
            return {str(k): {'required': True} for k in raw}
        if not isinstance(raw, dict):
            raise SpecError('"inputs" is a list of names or an object of them')
        out = {}
        for k, v in raw.items():
            if isinstance(v, dict):
                out[str(k)] = v
            else:                       # inputs: {"wallet": "0xabc"} = a default
                out[str(k)] = {'default': v}
        return out

    def _topo(self):
        """Kahn, with the cycle named when there is one."""
        indeg = {s.id: len(s.needs) for s in self.steps}
        children = {s.id: [] for s in self.steps}
        for s in self.steps:
            for dep in s.needs:
                children[dep].append(s.id)
        ready = [s.id for s in self.steps if indeg[s.id] == 0]
        order, seen = [], 0
        while ready:
            node = ready.pop(0)
            order.append(node)
            seen += 1
            for child in children[node]:
                indeg[child] -= 1
                if indeg[child] == 0:
                    ready.append(child)
        if seen != len(self.steps):
            stuck = [s for s in indeg if indeg[s] > 0]
            raise SpecError('this graph has a cycle, so it is not a DAG and cannot '
                            f'run: {" -> ".join(self._cycle(stuck))}')
        return order

    def _cycle(self, stuck):
        """Walk the remaining nodes to name one actual loop for the error."""
        start = stuck[0]
        path, node, live = [start], start, set(stuck)
        while True:
            nxt = next((d for d in self.by_id[node].needs if d in live), None)
            if nxt is None:
                return path
            if nxt in path:
                return path[path.index(nxt):] + [nxt]
            path.append(nxt)
            node = nxt

    def levels(self):
        """Steps grouped into waves — what a picture of this graph looks like."""
        depth, out = {}, []
        for sid in self.order:
            depth[sid] = max([depth[d] + 1 for d in self.by_id[sid].needs] or [0])
        for sid in self.order:
            while len(out) <= depth[sid]:
                out.append([])
            out[depth[sid]].append(sid)
        return out

    def bind(self, inputs=None):
        """Run parameters checked against the declared inputs, defaults filled."""
        given = dict(inputs or {})
        bound, missing = {}, []
        for name, meta in self.declared_inputs.items():
            if name in given:
                bound[name] = given.pop(name)
            elif 'default' in meta:
                bound[name] = meta['default']
            elif meta.get('required'):
                missing.append(name)
            else:
                bound[name] = None
        if missing:
            raise SpecError('missing required input(s): ' + ', '.join(missing))
        bound.update(given)          # undeclared extras are allowed, and usable
        return bound

    def dict(self):
        return {'name': self.name, 'title': self.title,
                'description': self.description, 'version': self.version,
                'inputs': self.declared_inputs, 'max_parallel': self.max_parallel,
                'steps': [s.dict() for s in self.steps],
                'order': self.order, 'levels': self.levels()}

    def ascii(self):
        """The graph as text, one wave per line. Cheap, and the thing people
        actually want before they run something that costs 40 calls."""
        lines = []
        for i, wave in enumerate(self.levels()):
            for sid in wave:
                s = self.by_id[sid]
                mark = '  ' * i + ('* ' if s.foreach is not None else '- ')
                needs = f'   <- {", ".join(s.needs)}' if s.needs else ''
                lines.append(f'{mark}{s.id}  [{s.use}] {s.target()}{needs}')
        return '\n'.join(lines)
