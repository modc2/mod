"""
graph/runner - executing a graph of agents.

Not a pipeline runner with branches bolted on: a message net. Nodes do not
call each other, they emit messages onto their output ports, and an edge is
the only thing that decides where a message goes next. That is what makes a
gate cheap (it forwards or it does not), a fan-out free (two edges off one
port) and a loop expressible at all (an edge pointing back, through the one
node that counts passes).

Execution runs in waves. Everything ready at the same moment runs at the same
moment — a fan-out of three agents is three agents running, not three in a row
— and the wave ends when they have all emitted. That keeps ordering legible
(a wave is a rank of the graph) without a scheduler nobody can debug.

Two guards stand between a wired mistake and a bill: `max_nodes`, the total
number of node firings a run may spend, and the per-node visit cap, which a
loop node raises for the cycle it guards. Both are cheap to reason about and
neither depends on the graph being acyclic.

The run never blocks on a person. A Human node files what reached it and the
branch ends there, parked; the graph's result says which branches are open.
"""
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

try:
    from .protocol import KINDS
except ImportError:  # running standalone
    from src.graph.protocol import KINDS


MAX_NODES = 40          # total node firings in one run
MAX_VISITS = 12         # firings of any single node (a loop's own max wins)
MAX_PARALLEL = 4        # agents running at once in one wave
MAX_WAVES = 60


def msg(text: str = "", node: str = "", port: str = "out", ok: bool = True,
        agent: str = None, data: Dict = None, hops: int = 0) -> Dict[str, Any]:
    """One message on an edge — the envelope in protocol.py."""
    return {"text": text or "", "data": dict(data or {}), "from": node,
            "port": port, "ok": bool(ok), "agent": agent, "hops": int(hops)}


# ── predicates ───────────────────────────────────────────────────────

def _field(message: Dict[str, Any], field: str) -> Any:
    """Read the field a rule names. `data.x` reaches into the payload."""
    field = (field or "text").strip()
    if field.startswith("data."):
        return (message.get("data") or {}).get(field[5:])
    if field in ("text", "agent", "port", "from"):
        return message.get(field)
    # an unqualified name that isn't a message field is a data key — writing
    # `score` and meaning `data.score` is the common case, not a typo
    data = message.get("data") or {}
    return data.get(field, message.get(field))


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def match_rule(message: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    """One predicate against one message. Unknown ops never pass — a rule
    nobody implements must not be a rule that silently lets everything by."""
    op = str(rule.get("op") or "").strip()
    val = rule.get("value")
    raw = _field(message, rule.get("field"))
    text = "" if raw is None else str(raw)
    needle = "" if val is None else str(val)
    ci = text.lower()
    cn = needle.lower()
    try:
        if op == "contains":
            return cn in ci
        if op == "not_contains":
            return cn not in ci
        if op == "matches":
            return re.search(needle, text, re.I | re.S) is not None
        if op == "equals":
            return ci.strip() == cn.strip()
        if op == "starts_with":
            return ci.lstrip().startswith(cn)
        if op == "ends_with":
            return ci.rstrip().endswith(cn)
        if op == "longer_than":
            return len(text) > (_num(val) or 0)
        if op == "shorter_than":
            return len(text) < (_num(val) or 0)
        if op == "gt":
            n = _num(raw)
            return n is not None and n > (_num(val) or 0)
        if op == "lt":
            n = _num(raw)
            return n is not None and n < (_num(val) or 0)
        if op == "empty":
            return not text.strip()
        if op == "not_empty":
            return bool(text.strip())
        if op == "ok":
            return bool(message.get("ok", True))
        if op == "failed":
            return not message.get("ok", True)
    except re.error:
        return False
    return False


def match_rules(message: Dict[str, Any], rules: List[Dict], mode: str = "all") -> bool:
    """A rule set. No rules is an open gate — an empty condition is not a
    condition, and a half-written gate that blocked everything would look
    exactly like a working graph that produced nothing."""
    rules = [r for r in (rules or []) if r and r.get("op")]
    if not rules:
        return True
    if str(mode).lower() == "any":
        return any(match_rule(message, r) for r in rules)
    return all(match_rule(message, r) for r in rules)


# ── the run ──────────────────────────────────────────────────────────

class GraphRun:
    """One execution of one graph.

    `execute` is injected rather than imported: the API hands in a closure
    that meters credits and streams steps into the caller's task, the tests
    hand in a stub, and neither the protocol nor this file has to know which
    it got. It is called as execute(kind, node, message) and returns
    {"text", "ok", "error", "trace", "agent"}.
    """

    def __init__(self, graph: Dict[str, Any], execute: Callable = None,
                 on_event: Callable = None, max_nodes: int = MAX_NODES,
                 parallel: int = MAX_PARALLEL):
        self.graph = graph or {}
        self.nodes: Dict[str, Dict] = {n["id"]: n for n in (graph.get("nodes") or []) if n.get("id")}
        self.edges: List[Dict] = [e for e in (graph.get("edges") or [])
                                  if e.get("from") in self.nodes and e.get("to") in self.nodes]
        self.execute = execute
        self.on_event = on_event
        self.max_nodes = int(max_nodes or MAX_NODES)
        self.parallel = max(1, int(parallel or 1))
        self.fired = 0
        self.visits: Dict[str, int] = {}
        self.loops: Dict[str, int] = {}
        self.inbox: Dict[str, List[Dict]] = {}
        self.trail: List[Dict] = []       # every node firing, in order
        self.outputs: List[Dict] = []     # what reached an output node
        self.parked: List[Dict] = []      # what reached a human node
        self.stopped: Optional[str] = None
        self._lock = threading.Lock()

    # ── wiring ──
    def out_edges(self, node_id: str, port: str) -> List[Dict]:
        """Edges leaving one port. An edge with no port named is on the node's
        first port — a graph drawn before a node grew a second one still runs."""
        default = self._ports(self.nodes[node_id])[:1]
        hits = []
        for e in self.edges:
            if e.get("from") != node_id:
                continue
            p = e.get("port") or (default[0] if default else "out")
            if p == port:
                hits.append(e)
        return hits

    def in_edges(self, node_id: str) -> List[Dict]:
        return [e for e in self.edges if e.get("to") == node_id]

    @staticmethod
    def _ports(node: Dict) -> List[str]:
        kind = node.get("kind")
        if kind == "router":
            routes = [str(r.get("port") or r.get("name") or "").strip()
                      for r in ((node.get("data") or {}).get("routes") or [])]
            return [p for p in routes if p] + ["else"]
        return list(KINDS.get(kind, {}).get("out") or ["out"])

    def _emit(self, event: str, **kw):
        if not self.on_event:
            return
        try:
            self.on_event({"event": event, "t": time.time(), **kw})
        except Exception:
            pass

    # ── the loop ──
    def run(self, query: str = "", inputs: Dict[str, str] = None) -> Dict[str, Any]:
        started = time.time()
        queue: List[tuple] = []
        seeds = [n for n in self.nodes.values() if n.get("kind") == "input"]
        if not seeds:
            # a graph drawn without an explicit entry still has one: every node
            # nothing points at. Refusing to run it would be pedantry.
            seeds = [n for n in self.nodes.values() if not self.in_edges(n["id"])]
        if not seeds:
            return self._result(started, error="nothing to start from — add an Input node")
        for n in seeds:
            text = (inputs or {}).get(n["id"], query)
            queue.append((n["id"], msg(text, node="", port="out")))

        self._emit("graph_start", nodes=len(self.nodes), edges=len(self.edges))
        waves = 0
        while waves < MAX_WAVES:
            if queue:
                wave, queue = queue, []
                ready = self._partition(wave)
            else:
                # nothing left in flight: release every gatherer still holding
                # messages. A join waiting on a branch a gate blocked is a join
                # that will wait forever, so "nothing more can arrive" is what
                # `wait: all` actually means.
                holding = [nid for nid, held in self.inbox.items() if held]
                if not holding:
                    break
                ready = [(nid, self._gathered(nid)) for nid in holding]
            if not ready:
                waves += 1
                continue
            waves += 1
            for node_id, out in self._fire_wave(ready):
                for port, message in out:
                    outs = self.out_edges(node_id, port)
                    for e in outs:
                        forward = dict(message, hops=message.get("hops", 0) + 1)
                        self._emit("message", **{"from": node_id, "to": e["to"],
                                                 "port": port,
                                                 "text": (forward.get("text") or "")[:400]})
                        queue.append((e["to"], forward))
                    if not outs and port not in ("err", "fail"):
                        # a live port wired to nothing is a leaf: keep what it
                        # said, so a graph without an Output still answers
                        node = self.nodes[node_id]
                        self.outputs.append({"node": node_id, "port": port,
                                             "label": (node.get("data") or {}).get("label")
                                                      or self._name(node),
                                             "text": message.get("text", ""),
                                             "agent": message.get("agent"),
                                             "terminal": False})
            if self.stopped:
                break
        return self._result(started)

    def _partition(self, wave: List[tuple]) -> List[tuple]:
        """Split a wave into what fires now. Anything arriving at a gatherer
        goes into its inbox instead and fires when the node is ready — or,
        failing that, when the run has nothing else left to deliver."""
        ready: List[tuple] = []
        arrived: List[str] = []
        for node_id, message in wave:
            node = self.nodes.get(node_id)
            if not node:
                continue
            if KINDS.get(node.get("kind"), {}).get("fan") == "gather":
                self.inbox.setdefault(node_id, []).append(message)
                arrived.append(node_id)
            else:
                ready.append((node_id, message))
        for node_id in dict.fromkeys(arrived):
            node = self.nodes[node_id]
            wait = str((node.get("data") or {}).get("wait") or "all").lower()
            need = 1 if wait == "any" else max(1, len(self.in_edges(node_id)))
            if len(self.inbox.get(node_id, [])) >= need:
                ready.append((node_id, self._gathered(node_id)))
        return ready

    def _gathered(self, node_id: str) -> Optional[Dict]:
        held = self.inbox.pop(node_id, [])
        if not held:
            return None
        merged = dict(held[0])
        merged["_gathered"] = held
        return merged

    def _fire_wave(self, ready: List[tuple]) -> List[tuple]:
        ready = [(nid, m) for nid, m in ready if m is not None]
        if not ready:
            return []
        if len(ready) == 1 or self.parallel == 1:
            return [(nid, self._fire(nid, m)) for nid, m in ready]
        with ThreadPoolExecutor(max_workers=min(self.parallel, len(ready))) as pool:
            futures = [(nid, pool.submit(self._fire, nid, m)) for nid, m in ready]
            out = []
            for nid, f in futures:
                try:
                    out.append((nid, f.result()))
                except Exception as e:  # a node that raised is a failed node
                    out.append((nid, [("err", msg(f"node error: {e}", node=nid,
                                                  port="err", ok=False))]))
            return out

    def _budget_left(self, node_id: str) -> Optional[str]:
        node = self.nodes[node_id]
        if self.fired >= self.max_nodes:
            return f"step budget spent ({self.max_nodes} nodes)"
        cap = MAX_VISITS
        if node.get("kind") == "loop":
            cap = max(1, int((node.get("data") or {}).get("max") or 3)) + 1
        if self.visits.get(node_id, 0) >= cap:
            return f"'{self._name(node)}' ran {cap} times — stopping the cycle"
        return None

    @staticmethod
    def _name(node: Dict) -> str:
        data = node.get("data") or {}
        return str(data.get("label") or data.get("agent") or node.get("kind") or node.get("id"))

    def _fire(self, node_id: str, message: Dict) -> List[tuple]:
        node = self.nodes[node_id]
        over = self._budget_left(node_id)
        if over:
            with self._lock:
                self.stopped = self.stopped or over
            self._emit("node_skipped", node=node_id, reason=over)
            return []
        with self._lock:
            self.fired += 1
            self.visits[node_id] = self.visits.get(node_id, 0) + 1
            n = self.visits[node_id]
        kind = node.get("kind")
        self._emit("node_start", node=node_id, kind=kind, name=self._name(node),
                   visit=n, text=(message.get("text") or "")[:400])
        t0 = time.time()
        try:
            out = getattr(self, f"_do_{kind}", self._do_unknown)(node, message)
        except Exception as e:
            out = [("err", msg(f"{kind} node failed: {e}", node=node_id, port="err", ok=False))]
        record = {"node": node_id, "kind": kind, "name": self._name(node),
                  "visit": n, "ms": int((time.time() - t0) * 1000),
                  "in": (message.get("text") or "")[:2000],
                  "ports": [p for p, _ in out],
                  "out": (out[0][1].get("text") if out else "")[:2000] if out else "",
                  "agent": next((m.get("agent") for _, m in out if m.get("agent")), None),
                  "ok": all(m.get("ok", True) for _, m in out) if out else True}
        with self._lock:
            self.trail.append(record)
        self._emit("node_done", **record)
        return out

    # ── node behaviours ──

    def _do_unknown(self, node, message):
        return [("out", msg(message.get("text", ""), node=node["id"], ok=False))]

    def _do_input(self, node, message):
        return [("out", msg(message.get("text", ""), node=node["id"]))]

    def _do_output(self, node, message):
        data = node.get("data") or {}
        with self._lock:
            self.outputs.append({"node": node["id"],
                                 "label": data.get("label") or "output",
                                 "text": message.get("text", ""),
                                 "agent": message.get("agent"),
                                 "terminal": True})
        return []

    def _do_human(self, node, message):
        data = node.get("data") or {}
        with self._lock:
            self.parked.append({"node": node["id"], "note": data.get("note") or "",
                                "text": message.get("text", ""),
                                "agent": message.get("agent")})
        self._emit("parked", node=node["id"], note=data.get("note") or "")
        return []

    def _do_gate(self, node, message):
        data = node.get("data") or {}
        passed = match_rules(message, data.get("rules"), data.get("mode") or "all")
        port = "pass" if passed else "fail"
        return [(port, dict(message, **{"from": node["id"], "port": port}))]

    def _do_router(self, node, message):
        for route in ((node.get("data") or {}).get("routes") or []):
            port = str(route.get("port") or route.get("name") or "").strip()
            if not port:
                continue
            if match_rules(message, route.get("rules"), route.get("mode") or "all"):
                return [(port, dict(message, **{"from": node["id"], "port": port}))]
        return [("else", dict(message, **{"from": node["id"], "port": "else"}))]

    def _do_loop(self, node, message):
        data = node.get("data") or {}
        cap = max(1, int(data.get("max") or 3))
        with self._lock:
            self.loops[node["id"]] = self.loops.get(node["id"], 0) + 1
            passes = self.loops[node["id"]]
        until = [r for r in (data.get("until") or []) if r and r.get("op")]
        # rounds run out, or the exit condition finally matched
        done = passes >= cap or (bool(until)
                                 and match_rules(message, until, data.get("mode") or "all"))
        port = "done" if done else "out"
        out = dict(message, **{"from": node["id"], "port": port})
        out["data"] = {**(message.get("data") or {}), "pass": passes, "max": cap}
        return [(port, out)]

    def _do_join(self, node, message):
        data = node.get("data") or {}
        held = message.get("_gathered") or [message]
        mode = str(data.get("mode") or "concat").lower()
        texts = [(m.get("agent") or m.get("from") or "branch", m.get("text") or "") for m in held]
        if mode == "first":
            text = texts[0][1]
        elif mode == "longest":
            text = max((t for _, t in texts), key=len, default="")
        elif mode == "vote":
            counts: Dict[str, int] = {}
            for _, t in texts:
                key = " ".join((t or "").lower().split())[:400]
                counts[key] = counts.get(key, 0) + 1
            winner = max(counts, key=counts.get) if counts else ""
            text = next((t for _, t in texts
                         if " ".join((t or "").lower().split())[:400] == winner), "")
        else:
            text = "\n\n".join(f"── {name} ──\n{t}" for name, t in texts if t)
        out = msg(text, node=node["id"], ok=all(m.get("ok", True) for m in held))
        out["data"] = {"branches": len(held),
                       "sources": [m.get("agent") or m.get("from") for m in held]}
        return [("out", out)]

    def _do_agent(self, node, message):
        return self._call(node, message, "agent")

    def _do_judge(self, node, message):
        res = self._call(node, message, "judge")
        text = res[0][1].get("text", "") if res else ""
        head = " ".join(text.split())[:240].lower()
        # a judge answers the question; anything that isn't a clear no is a yes,
        # because a judge that cannot be parsed must not silently block a graph
        no = head.startswith("no") or "answer: no" in head or "verdict: no" in head \
            or "fail" == head.strip() or head.startswith("no.")
        port = "fail" if no else "pass"
        out = dict(res[0][1] if res else msg("", node=node["id"]))
        out.update({"from": node["id"], "port": port, "text": message.get("text", "")})
        out["data"] = {**(out.get("data") or {}), "verdict": "no" if no else "yes",
                       "judgement": text}
        return [(port, out)]

    def _do_tool(self, node, message):
        return self._call(node, message, "tool")

    def _call(self, node, message, kind):
        if not self.execute:
            return [("err", msg("no executor bound to this run", node=node["id"],
                                port="err", ok=False))]
        res = self.execute(kind, node, message) or {}
        ok = bool(res.get("ok", not res.get("error")))
        text = res.get("text") or res.get("error") or ""
        out = msg(text, node=node["id"], port="out" if ok else "err", ok=ok,
                  agent=res.get("agent") or (node.get("data") or {}).get("agent"),
                  data=res.get("data"), hops=message.get("hops", 0))
        if res.get("trace"):
            out["data"]["steps"] = len(res["trace"])
        if res.get("task_id"):
            out["data"]["task_id"] = res["task_id"]
        return [("out" if ok else "err", out)]

    # ── result ──
    def _result(self, started: float, error: str = None) -> Dict[str, Any]:
        terminal = [o for o in self.outputs if o.get("terminal")]
        answers = terminal or self.outputs
        return {
            "ok": not error and not any(not s.get("ok", True) for s in self.trail),
            "error": error,
            "graph": self.graph.get("id") or self.graph.get("name"),
            "answer": answers[-1]["text"] if answers else "",
            "outputs": answers,
            "parked": self.parked,
            "trail": self.trail,
            "nodes_run": self.fired,
            "stopped": self.stopped,
            "ms": int((time.time() - started) * 1000),
        }


def run_graph(graph: Dict, query: str = "", execute: Callable = None,
              on_event: Callable = None, max_nodes: int = MAX_NODES,
              parallel: int = MAX_PARALLEL, inputs: Dict = None) -> Dict[str, Any]:
    return GraphRun(graph, execute=execute, on_event=on_event,
                    max_nodes=max_nodes, parallel=parallel).run(query, inputs)
