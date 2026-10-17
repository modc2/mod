"""
tests for the agent graph protocol

The graph connects agents; it does not build one. These cover the three
things that claim makes true:

    - the vocabulary (protocol.py): kinds, ports, the gate's fixed op set
    - the runner: pipelines, gates, fan-out + join, routers, loops, budgets
    - the registry: validation, ownership, save/fork/delete, seeds

run:
    cd ~/mod/mod/orbit/agent && python3 -m pytest tests/test_graph.py -v
"""
import os
import sys
import json
import shutil
import tempfile
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.graph.protocol import KINDS, OPS, descriptor
from src.graph.runner import GraphRun, run_graph, match_rule, match_rules, msg
from src.graph.mod import Graphs


# ── helpers ──────────────────────────────────────────────────────────

def N(nid, kind, **data):
    return {"id": nid, "kind": kind, "x": 0, "y": 0, "data": data}


def E(a, b, port="out"):
    return {"id": f"{a}-{b}-{port}", "from": a, "to": b, "port": port}


def echo(prefix=""):
    """An executor that answers with what it was given — every node's work is
    visible without a model in the loop."""
    calls = []

    def execute(kind, node, message):
        calls.append((node["id"], message.get("text", "")))
        name = (node.get("data") or {}).get("agent") or node["id"]
        return {"ok": True, "text": f"{prefix}{name}:{message.get('text','')}",
                "agent": name}
    execute.calls = calls
    return execute


# ── protocol ─────────────────────────────────────────────────────────

def test_protocol_has_no_agent_parts():
    """A graph connects agents. Prompt/model/toolbox/memory are what an agent
    is made OF — if one of them shows up as a node kind, the canvas has gone
    back to building agents."""
    for part in ("prompt", "model", "toolbox", "memory"):
        assert part not in KINDS


def test_every_kind_declares_ports_and_fan():
    for name, spec in KINDS.items():
        assert "out" in spec and "fan" in spec, name
        assert spec["fan"] in ("each", "gather"), name
        assert spec.get("doc"), name


def test_descriptor_is_json_serialisable():
    json.dumps(descriptor())


# ── predicates ───────────────────────────────────────────────────────

def test_rule_ops():
    m = msg("Looks good to me", agent="reviewer")
    assert match_rule(m, {"op": "contains", "value": "looks good"})
    assert match_rule(m, {"op": "not_contains", "value": "broken"})
    assert match_rule(m, {"op": "matches", "value": r"good\s+to"})
    assert match_rule(m, {"op": "longer_than", "value": 5})
    assert match_rule(m, {"op": "shorter_than", "value": 500})
    assert match_rule(m, {"op": "contains", "value": "reviewer", "field": "agent"})
    assert match_rule(m, {"op": "not_empty"})
    assert not match_rule(msg(""), {"op": "not_empty"})


def test_unknown_op_never_passes():
    assert not match_rule(msg("anything"), {"op": "rm -rf", "value": "x"})


def test_empty_rules_are_an_open_gate():
    assert match_rules(msg("x"), [])
    assert match_rules(msg("x"), [{"op": ""}])


def test_data_fields_are_readable():
    m = msg("hi", data={"score": 7})
    assert match_rule(m, {"op": "gt", "value": 5, "field": "data.score"})
    assert match_rule(m, {"op": "gt", "value": 5, "field": "score"})
    assert not match_rule(m, {"op": "lt", "value": 5, "field": "score"})


# ── the runner ───────────────────────────────────────────────────────

def test_pipeline_feeds_each_agent_the_last_one_s_output():
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="architect"),
                   N("b", "agent", agent="builder"), N("out", "output")],
         "edges": [E("in", "a"), E("a", "b"), E("b", "out")]}
    ex = echo()
    res = run_graph(g, "build a thing", execute=ex)
    assert res["ok"]
    assert res["answer"] == "builder:architect:build a thing"
    assert [c[0] for c in ex.calls] == ["a", "b"]


def test_gate_blocks_a_branch():
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="rev"),
                   N("g", "gate", rules=[{"op": "contains", "value": "SHIP"}]),
                   N("out", "output")],
         "edges": [E("in", "a"), E("a", "g"), E("g", "out", "pass")]}
    res = run_graph(g, "x", execute=echo())
    # nothing reached the output, and the run says where it stopped
    assert res["answer"] == ""
    assert [t["kind"] for t in res["trail"]] == ["input", "agent", "gate"]
    assert res["trail"][-1]["ports"] == ["fail"]


def test_gate_fail_port_routes_elsewhere():
    g = {"nodes": [N("in", "input"), N("g", "gate", rules=[{"op": "contains", "value": "zzz"}]),
                   N("yes", "agent", agent="ship"), N("no", "agent", agent="fix")],
         "edges": [E("in", "g"), E("g", "yes", "pass"), E("g", "no", "fail")]}
    ex = echo()
    res = run_graph(g, "nope", execute=ex)
    assert [c[0] for c in ex.calls] == ["no"]


def test_fanout_runs_branches_and_join_merges_them():
    g = {"nodes": [N("in", "input"),
                   N("a", "agent", agent="sec"), N("b", "agent", agent="perf"),
                   N("j", "join", mode="concat"), N("out", "output")],
         "edges": [E("in", "a"), E("in", "b"), E("a", "j"), E("b", "j"), E("j", "out")]}
    res = run_graph(g, "review this", execute=echo())
    assert "sec:review this" in res["answer"]
    assert "perf:review this" in res["answer"]
    joined = [t for t in res["trail"] if t["kind"] == "join"][0]
    assert joined["visit"] == 1  # one firing, both branches in it


def test_join_does_not_hang_on_a_branch_a_gate_blocked():
    g = {"nodes": [N("in", "input"),
                   N("a", "agent", agent="one"),
                   N("gate", "gate", rules=[{"op": "contains", "value": "never"}]),
                   N("b", "agent", agent="two"),
                   N("j", "join"), N("out", "output")],
         "edges": [E("in", "a"), E("in", "gate"), E("gate", "b", "pass"),
                   E("a", "j"), E("b", "j"), E("j", "out")]}
    res = run_graph(g, "go", execute=echo())
    assert res["answer"]  # it fired with the one branch that arrived
    assert "one:go" in res["answer"]


def test_join_vote_picks_the_agreed_answer():
    def execute(kind, node, message):
        return {"ok": True, "text": {"a": "yes", "b": "yes", "c": "no"}[node["id"]],
                "agent": node["id"]}
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="a"), N("b", "agent", agent="b"),
                   N("c", "agent", agent="c"), N("j", "join", mode="vote"), N("out", "output")],
         "edges": [E("in", "a"), E("in", "b"), E("in", "c"),
                   E("a", "j"), E("b", "j"), E("c", "j"), E("j", "out")]}
    assert run_graph(g, "?", execute=execute)["answer"] == "yes"


def test_router_takes_the_first_matching_route():
    g = {"nodes": [N("in", "input"),
                   N("r", "router", routes=[
                       {"port": "bug", "rules": [{"op": "contains", "value": "crash"}]},
                       {"port": "feature", "rules": [{"op": "contains", "value": "add"}]}]),
                   N("bugfix", "agent", agent="debugger"),
                   N("feat", "agent", agent="builder"),
                   N("other", "agent", agent="default")],
         "edges": [E("in", "r"), E("r", "bugfix", "bug"), E("r", "feat", "feature"),
                   E("r", "other", "else")]}
    ex = echo()
    run_graph(g, "it crashes on save", execute=ex)
    assert [c[0] for c in ex.calls] == ["bugfix"]
    ex2 = echo()
    run_graph(g, "something else entirely", execute=ex2)
    assert [c[0] for c in ex2.calls] == ["other"]


def test_loop_counts_passes_and_exits():
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="fixer"),
                   N("l", "loop", max=3), N("out", "output")],
         "edges": [E("in", "a"), E("a", "l"), E("l", "a", "out"), E("l", "out", "done")]}
    ex = echo()
    res = run_graph(g, "start", execute=ex)
    assert len(ex.calls) == 3          # three passes, then done
    assert res["answer"].startswith("fixer:")


def test_loop_exits_early_when_the_condition_matches():
    seen = {"n": 0}

    def execute(kind, node, message):
        seen["n"] += 1
        return {"ok": True, "text": "DONE" if seen["n"] == 2 else "keep going"}
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="fixer"),
                   N("l", "loop", max=8, until=[{"op": "contains", "value": "DONE"}]),
                   N("out", "output")],
         "edges": [E("in", "a"), E("a", "l"), E("l", "a", "out"), E("l", "out", "done")]}
    res = run_graph(g, "start", execute=execute)
    assert seen["n"] == 2
    assert res["answer"] == "DONE"


def test_a_cycle_cannot_outrun_the_node_budget():
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="x"), N("l", "loop", max=999)],
         "edges": [E("in", "a"), E("a", "l"), E("l", "a", "out")]}
    res = run_graph(g, "go", execute=echo(), max_nodes=9)
    assert res["nodes_run"] <= 9
    assert res["stopped"]


def test_a_failed_agent_leaves_by_err():
    def execute(kind, node, message):
        if node["id"] == "a":
            return {"ok": False, "error": "provider down"}
        return {"ok": True, "text": "recovered", "agent": "backup"}
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="main"),
                   N("b", "agent", agent="backup"), N("out", "output")],
         "edges": [E("in", "a"), E("a", "b", "err"), E("b", "out")]}
    res = run_graph(g, "go", execute=execute)
    assert res["answer"] == "recovered"
    assert res["ok"] is False   # the run recovered, but a step did fail


def test_judge_reads_yes_and_no():
    def execute(kind, node, message):
        return {"ok": True, "text": "NO — the review missed the auth path"}
    g = {"nodes": [N("in", "input"), N("j", "judge", agent="reviewer", question="good?"),
                   N("ok", "agent", agent="ship"), N("bad", "agent", agent="redo")],
         "edges": [E("in", "j"), E("j", "ok", "pass"), E("j", "bad", "fail")]}
    trail = run_graph(g, "a review", execute=execute)["trail"]
    assert [t["kind"] for t in trail] == ["input", "judge", "agent"]
    assert trail[-1]["name"] == "redo"


def test_judge_passes_the_message_on_not_its_own_verdict():
    """The judge decides the port; what travels is still the work being
    judged, or the next agent would review the review."""
    g = {"nodes": [N("in", "input"), N("j", "judge", agent="reviewer"), N("out", "output")],
         "edges": [E("in", "j"), E("j", "out", "pass")]}
    res = run_graph(g, "the actual work", execute=lambda k, n, m: {"ok": True, "text": "YES"})
    assert res["answer"] == "the actual work"
    assert res["outputs"][-1]["text"] == "the actual work"


def test_human_node_parks_instead_of_blocking():
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="writer"),
                   N("h", "human", note="check before it ships")],
         "edges": [E("in", "a"), E("a", "h")]}
    res = run_graph(g, "draft", execute=echo())
    assert len(res["parked"]) == 1
    assert res["parked"][0]["note"] == "check before it ships"


def test_tool_node_fills_the_message_into_params():
    got = {}

    def execute(kind, node, message):
        got.update({"kind": kind, "params": Graphs.fill(node["data"].get("params"),
                                                        message["text"])})
        return {"ok": True, "text": "fetched"}
    g = {"nodes": [N("in", "input"), N("t", "tool", tool="fetch", params={"url": "{{text}}"}),
                   N("out", "output")],
         "edges": [E("in", "t"), E("t", "out")]}
    run_graph(g, "https://example.com", execute=execute)
    assert got["params"] == {"url": "https://example.com"}


def test_a_graph_with_no_output_still_answers():
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="x")],
         "edges": [E("in", "a")]}
    assert run_graph(g, "go", execute=echo())["answer"] == "x:go"


def test_trail_records_every_firing_in_order():
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="one"),
                   N("b", "agent", agent="two"), N("out", "output")],
         "edges": [E("in", "a"), E("a", "b"), E("b", "out")]}
    res = run_graph(g, "go", execute=echo())
    assert [t["name"] for t in res["trail"]] == ["input", "one", "two", "output"]
    assert all("ms" in t for t in res["trail"])


def test_events_stream_every_node():
    events = []
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="one"), N("out", "output")],
         "edges": [E("in", "a"), E("a", "out")]}
    run_graph(g, "go", execute=echo(), on_event=events.append)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "graph_start"
    assert kinds.count("node_start") == 3
    assert "message" in kinds


# ── the registry ─────────────────────────────────────────────────────

@pytest.fixture
def reg():
    d = tempfile.mkdtemp()
    yield Graphs(dir=d)
    shutil.rmtree(d, ignore_errors=True)


def test_seeds_are_the_old_chain_presets(reg):
    ids = [g["id"] for g in reg.ls()]
    assert "full-review" in ids
    full = reg.get("full-review")
    assert [n["kind"] for n in full["nodes"]] == ["input", "agent", "agent", "output"]
    assert full["valid"]["ok"]


def test_validate_catches_a_graph_that_cannot_run(reg):
    bad = {"nodes": [N("in", "input"), N("a", "agent")], "edges": [E("in", "a")]}
    v = reg.validate(bad)
    assert not v["ok"]
    assert any("no agent picked" in e for e in v["errors"])


def test_validate_rejects_a_cycle_with_no_loop_node(reg):
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="x"), N("b", "agent", agent="y")],
         "edges": [E("in", "a"), E("a", "b"), E("b", "a")]}
    v = reg.validate(g)
    assert not v["ok"]
    assert any("Loop node" in e for e in v["errors"])


def test_validate_allows_a_cycle_through_a_loop(reg):
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="x"), N("l", "loop", max=2)],
         "edges": [E("in", "a"), E("a", "l"), E("l", "a", "out")]}
    assert reg.validate(g)["ok"]


def test_validate_warns_about_an_orphan(reg):
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="x"), N("orphan", "agent", agent="y")],
         "edges": [E("in", "a")]}
    v = reg.validate(g)
    assert v["ok"]
    assert any("never runs" in w for w in v["warnings"])


def test_save_keeps_only_protocol_fields(reg):
    saved = reg.save({"name": "my flow",
                      "nodes": [{"id": "in", "kind": "input", "x": 1, "y": 2,
                                 "data": {"label": "go", "cmd": "rm -rf /"}}],
                      "edges": []})
    assert saved["id"] == "my-flow"
    assert saved["nodes"][0]["data"] == {"label": "go"}


def test_save_drops_edges_to_nodes_that_are_not_there(reg):
    saved = reg.save({"name": "f", "nodes": [N("a", "input")],
                      "edges": [E("a", "ghost")]})
    assert saved["edges"] == []


def test_a_run_refuses_an_invalid_graph(reg):
    res = reg.run({"nodes": [N("a", "agent")], "edges": []}, "go",
                  execute=lambda *a: {"ok": True, "text": "x"})
    assert not res["ok"] and "no agent picked" in res["error"]


def test_unsaved_graphs_run(reg):
    g = {"nodes": [N("in", "input"), N("a", "agent", agent="default"), N("out", "output")],
         "edges": [E("in", "a"), E("a", "out")]}
    res = reg.run(g, "hello", execute=echo())
    assert res["answer"] == "default:hello"


def test_run_a_saved_graph_by_id(reg):
    reg.save({"name": "two step", "nodes": [
        N("in", "input"), N("a", "agent", agent="default"), N("out", "output")],
        "edges": [E("in", "a"), E("a", "out")]})
    res = reg.run("two-step", "go", execute=echo())
    assert res["answer"] == "default:go"


def test_delete(reg):
    reg.save({"name": "temp", "nodes": [N("in", "input")], "edges": []})
    reg.rm("temp")
    with pytest.raises(KeyError):
        reg.get("temp")


def test_a_seed_is_forked_not_overwritten(reg):
    reg.save({"id": "full-review", "name": "Full Review",
              "nodes": [N("in", "input")], "edges": []})
    ids = [g["id"] for g in reg.ls()]
    assert "full-review" in ids                       # the starter is untouched
    assert len([i for i in ids if i.startswith("full-review")]) == 2
    assert len(reg.get("full-review")["nodes"]) == 4


def test_self_test(reg):
    assert reg.test()
