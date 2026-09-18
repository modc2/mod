"""
graph - the agent graph protocol

A graph is how agents are connected, never how one is built. An agent is made
in the registry (src/agents/) — its prompt, model, toolbox and memory belong to
it — and a graph picks it up whole. What a graph adds is everything that
happens *between* agents: the order they run in, the conditions that decide
whether the next one runs at all, the branches that run at once, and the point
where those branches come back together.

    input ─▶ architect ─▶ gate(contains "PLAN") ─▶ builder ─▶ judge ─▶ output
                                    └─ fail ─▶ architect (loop, max 2)

Three nouns, and that is the whole idea:

    nodes   an agent, or one of the control kinds in protocol.py
    edges   where a message goes when it leaves a port
    ports   the named outputs a node can answer on (pass/fail, out/err …)

Everything else is in two files next door: protocol.py is the vocabulary (what
kinds exist, what ports they have, what a gate may test), runner.py is the
execution (waves, budgets, the message net). This file is the registry: the
graphs that have been saved, who owns them, whether one is wired correctly,
and the entry point that runs one.

Graphs are private user state, so they live OFF-tree under ~/.mod/agent/ like
prompts and notes, are owned by the address that saved them, and are pinned to
localfs so a flow can be handed to someone else as a CID.

Usage:
    graphs = Graphs(identity=ident, agents=agents, run_agent=fn, run_tool=fn)
    graphs.ls()
    graphs.save({"id": "review", "nodes": [...], "edges": [...]}, key=k)
    graphs.run("review", "check src/api for dead routes")
"""
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import mod as m
except ImportError:
    m = None

try:
    from .protocol import KINDS, OPS, descriptor
    from .runner import GraphRun, MAX_NODES
except ImportError:  # running standalone
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.graph.protocol import KINDS, OPS, descriptor
    from src.graph.runner import GraphRun, MAX_NODES

try:
    from src.identity import Identity
except ImportError:
    Identity = None


def slugify(s: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in str(s or "").lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


class Graphs:
    description = "Agent graph registry — save, validate and run graphs of agents"

    def __init__(self, identity=None, agents=None, dir: str = None,
                 run_agent: Callable = None, run_tool: Callable = None):
        self.dir = Path(dir) if dir else Path.home() / ".mod" / "agent"
        self.agents = agents
        self.identity = identity or (Identity() if Identity else None)
        # injected so the registry never imports the agent loop: the API hands
        # in a closure that meters credits into the caller's task, the tests
        # hand in a stub
        self._run_agent = run_agent
        self._run_tool = run_tool

    # ── the protocol itself ──────────────────────────────────────────

    @staticmethod
    def kinds() -> Dict[str, Any]:
        """What a graph may be made of — read by the console's palette."""
        return descriptor()

    # ── storage ──────────────────────────────────────────────────────

    @property
    def _path(self) -> Path:
        return self.dir / "graphs.json"

    def _read(self) -> Dict[str, Dict]:
        try:
            data = json.loads(self._path.read_text())
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _write(self, data: Dict[str, Dict]):
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, default=str))

    def _all(self) -> Dict[str, Dict]:
        """Everything saved, with the shipped starters folded in on first read."""
        data = self._read()
        seeded = False
        for g in self._seeds():
            if g["id"] not in data:
                data[g["id"]] = g
                seeded = True
        if seeded:
            self._write(data)
        return data

    def _seeds(self) -> List[Dict]:
        """The chain presets, as graphs. They were already pipelines — a list
        of agents each feeding the next — so they are the honest starting
        point for a graph language rather than a separate feature."""
        presets = {}
        chains = Path(__file__).resolve().parents[1] / "agents" / "chains.json"
        try:
            presets = json.loads(chains.read_text())
        except Exception:
            return []
        out = []
        for slug, spec in presets.items():
            steps = spec.get("steps") or []
            if not steps:
                continue
            nodes = [{"id": "in", "kind": "input", "x": 40, "y": 160,
                      "data": {"label": "request"}}]
            edges = []
            prev = "in"
            for i, st in enumerate(steps):
                nid = f"a{i}"
                nodes.append({"id": nid, "kind": "agent", "x": 300 + i * 280, "y": 140,
                              "data": {"agent": st.get("agent", "default"),
                                       "prompt": st.get("prompt", "")}})
                edges.append({"id": f"e{i}", "from": prev, "to": nid, "port": "out"})
                prev = nid
            nodes.append({"id": "out", "kind": "output", "x": 300 + len(steps) * 280,
                          "y": 160, "data": {"label": "answer"}})
            edges.append({"id": "eo", "from": prev, "to": "out", "port": "out"})
            out.append({"id": slug, "name": spec.get("name", slug),
                        "description": spec.get("description", ""),
                        "nodes": nodes, "edges": edges, "seed": True,
                        "created": 0, "updated": 0})
        return out

    # ── reads ────────────────────────────────────────────────────────

    def ls(self, key: Any = None) -> List[Dict]:
        """Every graph, newest first, each with its owner and a one-line shape."""
        out = []
        for g in self._all().values():
            out.append(self._card(g, key))
        out.sort(key=lambda g: (0 if g.get("mine") else 1, -(g.get("updated") or 0)))
        return out

    def _card(self, g: Dict, key: Any = None) -> Dict:
        nodes = g.get("nodes") or []
        agents = [n.get("data", {}).get("agent") for n in nodes if n.get("kind") == "agent"]
        owner = self.identity.owner_of(g.get("owner")) if self.identity else {"owner": g.get("owner")}
        me = self.identity.addr(key) if (self.identity and key) else None
        return {k: v for k, v in g.items() if k not in ("nodes", "edges")} | {
            **owner,
            "mine": bool(me and owner.get("owner") and me == owner.get("owner")),
            "nodes": len(nodes),
            "edges": len(g.get("edges") or []),
            "agents": [a for a in dict.fromkeys(agents) if a],
            "kinds": sorted({n.get("kind") for n in nodes if n.get("kind")}),
        }

    def get(self, graph_id: str, key: Any = None) -> Dict:
        g = self._all().get(str(graph_id))
        if not g:
            raise KeyError(f"graph not found: {graph_id}")
        owner = self.identity.owner_of(g.get("owner")) if self.identity else {}
        return {**g, **owner, "valid": self.validate(g)}

    # ── validation ───────────────────────────────────────────────────

    def validate(self, graph: Dict) -> Dict[str, Any]:
        """What is wrong with this graph, said once, in words.

        Errors stop a run; warnings are shapes that work but rarely mean what
        was drawn — an agent nothing reaches, a cycle with no loop node to
        count it.
        """
        nodes = {n.get("id"): n for n in (graph.get("nodes") or []) if n.get("id")}
        edges = graph.get("edges") or []
        errors: List[str] = []
        warnings: List[str] = []
        if not nodes:
            errors.append("the graph is empty")
        for nid, n in nodes.items():
            kind = n.get("kind")
            if kind not in KINDS:
                errors.append(f"unknown node kind '{kind}'")
                continue
            data = n.get("data") or {}
            if kind == "agent" and not data.get("agent"):
                errors.append(f"an Agent node has no agent picked")
            if kind == "agent" and data.get("agent") and self.agents:
                try:
                    self.agents.get(data["agent"])
                except Exception:
                    errors.append(f"'{data['agent']}' is not an agent in the registry")
            if kind == "judge" and not data.get("agent"):
                errors.append("a Judge node has no agent to ask")
            if kind == "tool" and not data.get("tool"):
                errors.append("a Tool node has no tool picked")
            if kind == "gate" and not (data.get("rules") or []):
                warnings.append("a Gate with no rules lets everything through")
            if kind == "router" and not (data.get("routes") or []):
                warnings.append("a Router with no routes sends everything to 'else'")
        for e in edges:
            if e.get("from") not in nodes or e.get("to") not in nodes:
                errors.append("an edge points at a node that isn't there")
        targets = {e.get("to") for e in edges}
        sources = {e.get("from") for e in edges}
        starts = [n for n in nodes.values() if n.get("kind") == "input"]
        if not starts and nodes and not [n for n in nodes if n not in targets]:
            errors.append("nothing to start from — add an Input node")
        for nid, n in nodes.items():
            if n.get("kind") in ("input",):
                continue
            if nid not in targets:
                warnings.append(f"'{self._label(n)}' is wired to nothing upstream — it never runs")
            elif nid not in sources and n.get("kind") not in ("output", "human"):
                warnings.append(f"'{self._label(n)}' produces something nothing reads")
        for cycle in self._cycles(nodes, edges):
            if not any(nodes[c].get("kind") == "loop" for c in cycle):
                errors.append("a cycle with no Loop node in it — put one on the "
                              "back edge so the passes are counted")
        return {"ok": not errors, "errors": list(dict.fromkeys(errors)),
                "warnings": list(dict.fromkeys(warnings))}

    @staticmethod
    def _label(n: Dict) -> str:
        d = n.get("data") or {}
        return str(d.get("label") or d.get("agent") or n.get("kind") or n.get("id"))

    @staticmethod
    def _cycles(nodes: Dict, edges: List[Dict]) -> List[List[str]]:
        """Every cycle, as the list of nodes on it (Tarjan, small graphs)."""
        adj: Dict[str, List[str]] = {n: [] for n in nodes}
        for e in edges:
            if e.get("from") in adj and e.get("to") in adj:
                adj[e["from"]].append(e["to"])
        found, state, stack = [], {}, []

        def walk(u):
            state[u] = 1
            stack.append(u)
            for v in adj[u]:
                if state.get(v, 0) == 0:
                    walk(v)
                elif state.get(v) == 1:
                    found.append(stack[stack.index(v):])
            stack.pop()
            state[u] = 2

        for n in list(adj):
            if state.get(n, 0) == 0:
                walk(n)
        return found

    # ── writes ───────────────────────────────────────────────────────

    def save(self, graph: Dict, key: Any = None) -> Dict:
        """Create or update a graph. A graph belongs to the address that saved
        it; a shipped starter is host-owned, so editing one lands a copy under
        your own name rather than changing what everybody else opens."""
        if self.identity:
            self.identity.require_signed_in(key, "save a graph")
        graph = dict(graph or {})
        gid = slugify(graph.get("id") or graph.get("name") or "")
        if not gid:
            raise ValueError("a graph needs a name")
        data = self._all()
        existing = data.get(gid)
        addr = self.identity.addr(key) if self.identity else None
        if existing:
            if existing.get("seed") and not (self.identity and self.identity.is_the_host(key)):
                # a starter is everyone's — fork it instead of overwriting
                gid = self._free_id(data, f"{gid}-{(addr or 'my')[-4:]}")
                existing = None
            elif self.identity:
                self.identity.require(existing.get("owner"), key, f"edit graph '{gid}'")
        nodes = [self._clean_node(n) for n in (graph.get("nodes") or [])]
        ids = {n["id"] for n in nodes}
        edges = [{"id": e.get("id") or uuid.uuid4().hex[:8],
                  "from": e.get("from"), "to": e.get("to"),
                  "port": e.get("port") or "out"}
                 for e in (graph.get("edges") or [])
                 if e.get("from") in ids and e.get("to") in ids]
        now = int(time.time())
        rec = {
            "id": gid,
            "name": graph.get("name") or gid,
            "description": graph.get("description", ""),
            "nodes": nodes,
            "edges": edges,
            "viewport": graph.get("viewport") or None,
            "owner": (existing or {}).get("owner") or addr,
            "created": (existing or {}).get("created") or now,
            "updated": now,
        }
        rec["cid"] = self._pin(rec)
        data[gid] = rec
        self._write(data)
        return {**rec, "valid": self.validate(rec)}

    @staticmethod
    def _free_id(data: Dict, base: str) -> str:
        gid, i = base, 2
        while gid in data:
            gid, i = f"{base}-{i}", i + 1
        return gid

    @staticmethod
    def _clean_node(n: Dict) -> Dict:
        """Keep a node to the protocol's own shape — a saved graph is a
        document other people run, so nothing rides along in it uninspected."""
        kind = n.get("kind")
        if kind not in KINDS:
            raise ValueError(f"unknown node kind: {kind}")
        allowed = {f["key"] for f in KINDS[kind].get("fields") or []}
        allowed |= {"routes", "rules", "params", "label"}
        data = {k: v for k, v in (n.get("data") or {}).items() if k in allowed}
        return {"id": str(n.get("id") or uuid.uuid4().hex[:8]),
                "kind": kind,
                "x": float(n.get("x") or 0), "y": float(n.get("y") or 0),
                "data": data}

    def rm(self, graph_id: str, key: Any = None) -> Dict:
        data = self._all()
        g = data.get(str(graph_id))
        if not g:
            raise KeyError(f"graph not found: {graph_id}")
        if g.get("seed") and not (self.identity and self.identity.is_the_host(key)):
            raise PermissionError("that is a shipped starter — it belongs to the host")
        if self.identity:
            self.identity.require(g.get("owner"), key, f"delete graph '{graph_id}'")
        del data[str(graph_id)]
        self._write(data)
        return {"deleted": graph_id}

    # ── sharing ──────────────────────────────────────────────────────

    def _localfs(self):
        if not m:
            return None
        try:
            return m.mod("localfs")()
        except Exception:
            return None

    def _pin(self, rec: Dict) -> Optional[str]:
        """Pin the portable shape — content only, so the same graph drawn
        twice gets the same CID."""
        fs = self._localfs()
        if fs is None:
            return None
        try:
            return fs.put({"type": "graph", "name": rec.get("name", ""),
                           "description": rec.get("description", ""),
                           "nodes": rec.get("nodes", []), "edges": rec.get("edges", [])})
        except Exception:
            return None

    def import_cid(self, cid: str, key: Any = None) -> Dict:
        fs = self._localfs()
        if fs is None:
            raise RuntimeError("no localfs store on this host")
        blob = fs.get(cid.split("/")[-1]) if hasattr(fs, "get") else None
        if isinstance(blob, str):
            blob = json.loads(blob)
        if not isinstance(blob, dict) or blob.get("type") != "graph":
            raise ValueError("that CID is not a graph")
        return self.save({k: blob.get(k) for k in ("name", "description", "nodes", "edges")}, key=key)

    # ── running ──────────────────────────────────────────────────────

    def run(self, graph: Any, query: str = "", key: Any = None,
            execute: Callable = None, on_event: Callable = None,
            max_nodes: int = MAX_NODES, parallel: int = 4,
            inputs: Dict = None, run_agent: Callable = None,
            run_tool: Callable = None) -> Dict[str, Any]:
        """Run a saved graph (by id) or one handed over inline.

        An unsaved graph runs too: the canvas has to be testable before it is
        worth keeping, and a graph you cannot try is a graph nobody finishes.
        """
        g = self.get(graph, key=key) if isinstance(graph, str) else dict(graph or {})
        check = self.validate(g)
        if not check["ok"]:
            return {"ok": False, "error": "; ".join(check["errors"]), "valid": check}
        run = GraphRun(g, execute=execute or self._executor(key, run_agent, run_tool),
                       on_event=on_event, max_nodes=max_nodes, parallel=parallel)
        res = run.run(query, inputs)
        return {**res, "valid": check}

    def _executor(self, key: Any = None, run_agent: Callable = None,
                  run_tool: Callable = None) -> Callable:
        """The default executor: node → the module's own agent loop.

        The API overrides the two runners with closures that meter credits and
        stream steps into the caller's task; the dispatch below — which node
        kind means what — stays in one place either way.
        """
        run_agent = run_agent or self._run_agent
        run_tool = run_tool or self._run_tool

        def execute(kind: str, node: Dict, message: Dict) -> Dict[str, Any]:
            data = node.get("data") or {}
            text = message.get("text", "")
            if kind == "tool":
                if not run_tool:
                    return {"ok": False, "error": "no tool runner bound"}
                return run_tool(data.get("tool"), self.fill(data.get("params"), text), key=key)
            if not run_agent:
                return {"ok": False, "error": "no agent runner bound"}
            if kind == "judge":
                question = data.get("question") or "Does this meet the bar?"
                query = (f"{question}\n\n"
                         f"Answer with YES or NO on the first line, then one "
                         f"sentence of why.\n\n--- what to judge ---\n{text}")
                return run_agent(agent=data.get("agent") or "reviewer", query=query,
                                 model=data.get("model"), steps=int(data.get("steps") or 4),
                                 free=data.get("free", True), key=key, node=node)
            prompt = (data.get("prompt") or "").strip()
            query = f"{prompt}\n\n--- input ---\n{text}" if prompt else text
            return run_agent(agent=data.get("agent") or "default", query=query,
                             model=data.get("model"), steps=data.get("steps"),
                             toolbox=data.get("toolbox"), free=data.get("free"),
                             key=key, node=node)
        return execute

    @staticmethod
    def fill(params: Any, text: str) -> Dict[str, Any]:
        """`{{text}}` in a tool's parameters is the incoming message."""
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {"input": params}
        if not isinstance(params, dict):
            return {}
        return {k: (v.replace("{{text}}", text) if isinstance(v, str) else v)
                for k, v in params.items()}

    # ── mod protocol ─────────────────────────────────────────────────

    def forward(self, action: str = None, key: Any = None, **kwargs):
        actions = {
            None: lambda: {"graphs": self.ls(key)},
            "ls": lambda: {"graphs": self.ls(key)},
            "kinds": lambda: self.kinds(),
            "get": lambda: self.get(kwargs.get("id", ""), key=key),
            "save": lambda: self.save(kwargs.get("graph") or kwargs, key=key),
            "rm": lambda: self.rm(kwargs.get("id", ""), key=key),
            "run": lambda: self.run(kwargs.get("graph") or kwargs.get("id", ""),
                                    kwargs.get("query", ""), key=key),
            "validate": lambda: self.validate(kwargs.get("graph") or {}),
            "import": lambda: self.import_cid(kwargs.get("cid", ""), key=key),
        }
        if action not in actions:
            raise ValueError(f"unknown graph action: {action}")
        return actions[action]()

    def test(self) -> bool:
        """The protocol, end to end, on a stub executor."""
        g = {"id": "t", "nodes": [
            {"id": "in", "kind": "input", "data": {}},
            {"id": "a", "kind": "agent", "data": {"agent": "default", "prompt": "do it"}},
            {"id": "g", "kind": "gate", "data": {"rules": [{"op": "contains", "value": "ok"}]}},
            {"id": "o", "kind": "output", "data": {}}],
            "edges": [{"from": "in", "to": "a", "port": "out"},
                      {"from": "a", "to": "g", "port": "out"},
                      {"from": "g", "to": "o", "port": "pass"}]}
        res = self.run(g, "hello", execute=lambda k, n, msg: {"ok": True, "text": "all ok"})
        assert res["answer"] == "all ok", res
        assert len(res["trail"]) == 4, res["trail"]
        return True
