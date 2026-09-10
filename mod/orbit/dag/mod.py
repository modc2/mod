"""dag — run a DAG over the mods.

The fleet is three hundred modules and, right now, 669 MCP tools across 38
servers. Every one of them is one call. Nothing composes them: to ask a
question that needs four tools you write a script, and the script is not a
thing the fleet can see, save, check or re-run.

A dag is that script as a document. Steps are calls — an MCP tool, a mod fn, an
HTTP route — and they reference each other:

    {"id": "held",  "tool": "solana__sol_portfolio", "args": {"address": "${inputs.wallet}"}}
    {"id": "top",   "use": "expr", "value": "${held.tokens}", "sort_by": "value_usd",
     "desc": true, "limit": 3}
    {"id": "risk",  "foreach": "${top}", "tool": "solana__sol_token",
     "args": {"mint": "${item.mint}"}}

Nothing declares a dependency. `${held.tokens}` IS the edge, so the graph is
built from what the steps actually want, and anything independent runs at the
same time.

    m dag                                   # the spec, and a worked example
    m dag/tools polymarket                  # what can be called
    m dag/plan examples/wallet.json         # check it, price it, call nothing
    m dag/run examples/wallet.json wallet=9WzDXwB…
    m dag/save wallet-report examples/wallet.json
    m dag/run wallet-report wallet=9WzDXwB…  # by name, from then on
    m dag/runs                              # what has been run, and how it went
    m dag/serve                             # API, console and MCP on one port

PLAN BEFORE YOU RUN
    `plan` reads the hub's tool index and checks every tool name and every
    required argument against the fleet as it is right now, then says what the
    run will cost in calls and waves. It calls nothing. With hundreds of tools
    the normal failure is a tool name that does not exist, and finding that at
    step 7 of 9 costs six calls and whatever they changed.

FAILURE IS LOCAL
    A step that fails does not stop a branch that never depended on it. What is
    downstream of the failure is `skipped`, and each skip names the step that
    caused it — so a failed run reads as one cause and its consequences.

THIS IS LOOPBACK-ONLY BY DEFAULT
    A graph can call any tool in the fleet, including the ones that sign
    transactions, and the hub trusts a caller on this box. So running one is
    restricted to this box until you write a secret to
    ~/.mod/dag/server.secret; reading — the catalogue, saved graphs, history —
    is open, and a dry run is a read.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds a mod.py that would shadow
# the protocol's own `mod` package for anything importing us afterwards.
if HERE not in sys.path:
    sys.path.append(HERE)

from dagsrc import plan as planner          # noqa: E402
from dagsrc import mcp as mcpsrv            # noqa: E402
from dagsrc import runner, store, targets   # noqa: E402
from dagsrc.graph import Graph              # noqa: E402


class Mod:
    description = """
    dag — run a DAG over the mods. Steps are fleet calls (any of the fleet's
    MCP tools, a mod fn, an HTTP route) plus declarative `expr` steps that
    reshape data between them. Steps reference each other with ${...} and those
    references are the edges, so the graph builds itself and independent
    branches run in parallel. foreach fans a step out over a list. `plan`
    checks every tool name and required argument against the live fleet and
    prices the run before a single call is spent. Graphs are saved by name,
    every run is recorded, and the same nine tools answer over REST, MCP and a
    browser console on one port.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('DAG_PORT') or cfg.get('port', 50810))
        self.base = cfg.get('base_path', '/dag')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What a graph looks like, every field a step takes, and an example."""
        from dagsrc import api
        return api.info()

    forward = info

    @staticmethod
    def _spec(graph):
        """A graph argument is a saved name, a path to a .json, or the spec.

        All three, because all three are what somebody actually types.
        """
        if isinstance(graph, dict):
            return graph
        text = str(graph)
        for path in (text, os.path.join(HERE, text),
                     os.path.join(HERE, 'examples', text),
                     os.path.join(HERE, 'examples', text + '.json')):
            if path.endswith('.json') and os.path.isfile(path):
                with open(path) as f:
                    return json.load(f)
        if text.lstrip().startswith('{'):
            return json.loads(text)
        return store.load_graph(text)

    # ── the point of the module ──────────────────────────────────

    def run(self, graph, dry_run=False, verbose=False, **inputs):
        """Run a graph. Extra kwargs are its inputs: m dag/run report wallet=0x…"""
        rec = runner.Run(Graph(self._spec(graph)), inputs=inputs,
                         dry_run=bool(dry_run)).execute()
        return mcpsrv._trim(rec, verbose=bool(verbose))

    def plan(self, graph, check_tools=True, **inputs):
        """Check a graph against the live fleet and price it. Calls nothing."""
        return planner.plan(self._spec(graph), inputs=inputs,
                            check_tools=bool(check_tools))

    def draw(self, graph):
        """The execution order as text — one wave per indent level."""
        return Graph(self._spec(graph)).ascii()

    def tools(self, q=None, server=None, limit=30, full=False):
        """Search every MCP tool in the fleet — the catalogue you write against."""
        return mcpsrv.call_tool('dag_tools', {'q': q, 'server': server,
                                              'limit': limit, 'full': full})

    def servers(self):
        """The fleet's MCP servers, and which of them are answering."""
        return mcpsrv.call_tool('dag_servers', {})

    # ── saved graphs ─────────────────────────────────────────────

    def save(self, name, graph):
        """Save a graph by name. It is parsed first, so a broken one is never
        stored."""
        return store.save_graph(name, self._spec(graph))

    def graphs(self, name=None):
        """Every saved graph, or one of them with its plan drawn."""
        return mcpsrv.call_tool('dag_graphs', {'name': name})

    def delete(self, name):
        """Delete a saved graph. Its run records are kept."""
        return store.delete_graph(name)

    # ── history ──────────────────────────────────────────────────

    def runs(self, limit=20, graph=None, status=None):
        """What has been run: id, graph, status, calls and duration."""
        return store.runs(limit=limit, graph=graph, status=status)

    def show(self, run, verbose=True):
        """One run in full — every step, its output and, if it failed, why."""
        return mcpsrv._trim(store.load_run(run), verbose=bool(verbose))

    def prune(self, keep=500):
        """Drop the oldest run records."""
        return store.prune(int(keep))

    # ── surfaces ─────────────────────────────────────────────────

    def mcp(self, tool=None, **args):
        """The MCP registry, or one tool invoked with no transport in the way."""
        if tool is None:
            return {'tools': mcpsrv.tool_list(), 'count': len(mcpsrv.TOOLS),
                    'instructions': mcpsrv.INSTRUCTIONS}
        return mcpsrv.call_tool(tool, args)

    def mcp_config(self, url=None):
        """Drop-in client config for anything that speaks MCP over HTTP."""
        return {'mcpServers': {'dag': {
            'type': 'http', 'url': url or f'http://localhost:{self.port}/mcp'}}}

    def serve(self, port=None, bind=None, background=False):
        """REST, the console and the MCP server, on one port."""
        port = int(port or self.port)
        bind = bind or os.environ.get('DAG_BIND', '127.0.0.1')
        if not background:
            from dagsrc import api
            return api.serve(port, bind=bind)
        proc = subprocess.Popen(
            [sys.executable, '-m', 'dagsrc.api', '--port', str(port), '--bind', bind],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE)
        return {'pid': proc.pid, 'port': port, 'bind': bind,
                'api': f'http://{bind}:{port}/',
                'app': f'http://{bind}:{port}{self.base}',
                'mcp': f'http://{bind}:{port}/mcp'}

    def kill(self, port=None):
        """Stop whatever holds the port. Targets the port, never a name — this
        box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': pids}

    def health(self):
        """Is the hub answering, and how much is reachable through it."""
        try:
            index = targets.tool_index(timeout=8)
            return {'ok': True, 'fleet_tools': len(index),
                    'hub': targets.HUB, 'graphs': len(store.graphs())}
        except targets.StepError as e:
            return {'ok': False, 'hub': targets.HUB, 'error': str(e),
                    'fix': 'start the hub (m mcp/serve), or point DAG_MCP_HUB '
                           'somewhere else — steps with an explicit url= still work'}

    def test(self, offline=False):
        """Run the module's tests. offline=1 skips anything needing the fleet."""
        env = {**os.environ, **({'DAG_OFFLINE': '1'} if offline else {})}
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True, env=env)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None


if __name__ == '__main__':
    print(json.dumps(Mod().info(), indent=2, default=str))
