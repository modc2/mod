"""Vibecoding an agent — POST /agents/vibe and the machinery under it.

The model run itself is faked (a vibe draft is one finish step carrying a
JSON spec), so these tests pin the parts that must not drift: spec parsing,
name minting against the taken list, tool validation against the catalog,
and the save path filing the agent under the caller.
"""
import json
import os
import sys
import shutil
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.mod import Mod
from src.agents.mod import Agents, BUILTINS
from src.tools.mod import Tools
from src.toolbox.mod import Toolboxes

TOOLS_PATH = Path("/tmp/agent_vibe_test_tools.json")


def _make_mod():
    mod = Mod.__new__(Mod)
    mod.agents = Agents()
    mod.tools = Tools(path=TOOLS_PATH)
    mod.toolboxes = Toolboxes(tools=mod.tools)
    mod._snapped = []
    mod.model = None
    mod._tool_names = None
    mod._owner = None
    mod.auth = None
    mod.key = None
    # signed in as a test address, allowed to run — the gate itself is the
    # identity/ACL modules' own test surface, not this one's
    mod.identity = SimpleNamespace(
        require_signed_in=lambda key=None, operation=None: None,
        addr=lambda key=None: "0xvibe")
    mod.require_allowed = lambda key, op: None
    return mod


def _finish_trace(spec) -> list:
    return [{"tool": "finish",
             "params": {"summary": "```json\n" + json.dumps(spec) + "\n```"}}]


CATALOG = {
    "tools": [{"name": n, "description": n} for n in
              ("think", "finish", "read", "grep", "bash", "write")],
    "toolboxes": [{"name": "explore", "tools": ["read", "grep"]}],
    "via": "local",
}


class TestParseAgentJson:
    def test_fenced_block(self):
        spec = Mod._parse_agent_json('here\n```json\n{"prompt": "You are x"}\n```')
        assert spec == {"prompt": "You are x"}

    def test_bare_braces(self):
        assert Mod._parse_agent_json('{"goal": "g", "names": ["a"]}')["goal"] == "g"

    def test_no_prompt_is_no_spec(self):
        assert Mod._parse_agent_json('{"name": "x"}') is None
        assert Mod._parse_agent_json('no json at all') is None


class TestFreeAgentName:
    def test_preferred_is_honored_verbatim(self):
        mod = _make_mod()
        # even a taken name — colliding is create()'s error to raise
        assert mod._free_agent_name("My Agent!") == "my-agent"
        assert mod._free_agent_name("default") == "default"

    def test_first_untaken_idea_wins(self):
        mod = _make_mod()
        taken = mod.agents.ls()[0]
        assert mod._free_agent_name(None, ideas=[taken, "surely-not-taken"]) \
            == "surely-not-taken"

    def test_all_ideas_taken_gets_a_suffix(self):
        mod = _make_mod()
        taken = list(mod.agents.ls())
        got = mod._free_agent_name(None, ideas=[taken[0]])
        assert got not in {n.lower() for n in taken}
        assert got.startswith(taken[0].lower())

    def test_description_fallback(self):
        mod = _make_mod()
        got = mod._free_agent_name(None, ideas=[], description="review python diffs")
        assert got == "review-python-agent"


class TestVibeCleanTools:
    def test_inventions_dropped_and_reported(self):
        mod = _make_mod()
        tools, dropped = mod._vibe_clean_tools(["read", "quantum-leap"], CATALOG)
        assert tools == ["read"]
        assert dropped == ["quantum-leap"]

    def test_toolbox_expands(self):
        mod = _make_mod()
        tools, dropped = mod._vibe_clean_tools(["explore", "think"], CATALOG)
        assert tools == ["read", "grep", "think"]
        assert dropped == []

    def test_empty_means_unrestricted(self):
        mod = _make_mod()
        tools, dropped = mod._vibe_clean_tools(["nope"], CATALOG)
        assert tools is None
        assert dropped == ["nope"]


class TestVibeCatalog:
    def test_local_fallback_reads_the_registry(self):
        mod = _make_mod()
        cat = mod._vibe_catalog()
        names = {t["name"] for t in cat["tools"]}
        assert "read" in names and "think" in names
        assert cat["via"] in ("local", "mcp")
        assert any(b.get("name") for b in cat["toolboxes"])

    def test_finish_survives_validation_without_a_registry_entry(self):
        mod = _make_mod()
        cat = mod._vibe_catalog()
        assert "finish" not in {t["name"] for t in cat["tools"]}
        tools, dropped = mod._vibe_clean_tools(["think", "finish"], cat)
        assert tools == ["think", "finish"] and dropped == []


class TestAgentVibe:
    def _vibed_mod(self, spec):
        mod = _make_mod()
        mod._vibe_catalog = lambda key=None: CATALOG
        mod._run = lambda **kw: _finish_trace(spec)
        return mod

    def test_draft_comes_back_unsaved(self):
        spec = {"names": ["night-owl"], "icon": "◆", "description": "d",
                "prompt": "You are an owl.", "tools": ["read", "made-up"]}
        mod = self._vibed_mod(spec)
        before = set(mod.agents.ls())
        out = mod.agent_vibe("an agent that reads code at night")
        assert "error" not in out
        d = out["draft"]
        assert d["name"] == "night-owl"
        assert d["goal"] == "You are an owl."
        assert d["tools"] == ["read"]
        assert out["tools_dropped"] == ["made-up"]
        assert set(mod.agents.ls()) == before          # nothing filed

    def test_taken_idea_is_skipped(self):
        spec = {"names": ["default", "fresh-name"], "prompt": "p"}
        mod = self._vibed_mod(spec)
        out = mod.agent_vibe("something with a taken name idea")
        assert out["draft"]["name"] == "fresh-name"

    def test_caller_name_beats_the_drafter(self):
        spec = {"names": ["their-idea"], "prompt": "p"}
        mod = self._vibed_mod(spec)
        out = mod.agent_vibe("whatever the model thinks", name="My Pick")
        assert out["draft"]["name"] == "my-pick"

    def test_short_description_refused(self):
        mod = self._vibed_mod({"prompt": "p"})
        try:
            mod.agent_vibe("nah")
            assert False, "should have raised"
        except ValueError:
            pass

    def test_no_spec_is_an_error_with_the_answer(self):
        mod = _make_mod()
        mod._vibe_catalog = lambda key=None: CATALOG
        mod._run = lambda **kw: [{"tool": "finish", "params": {"summary": "sorry"}}]
        out = mod.agent_vibe("an agent that does a thing")
        assert "error" in out and out["answer"] == "sorry"

    def test_save_files_the_agent(self):
        spec = {"names": ["vibe-smoke-test-agent"], "icon": "◆",
                "description": "d", "prompt": "You are a smoke test.",
                "tools": ["read"]}
        mod = self._vibed_mod(spec)
        name = "vibe-smoke-test-agent"
        agent_dir = mod.agents._dir / name
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
        try:
            out = mod.agent_vibe("a smoke test agent", save=True)
            assert out.get("saved") is True
            assert name in mod.agents.ls()
            cfg = mod.agents.get(name)
            assert cfg["goal"] == "You are a smoke test."
            assert cfg["tools"] == ["read"]
        finally:
            if agent_dir.exists():
                shutil.rmtree(agent_dir)
            mod.agents._cache.pop(name, None)


class TestVibeBuilderShips:
    def test_registered_and_off_the_board(self):
        mod = _make_mod()
        assert "vibe-builder" in mod.agents.ls()
        assert "vibe-builder" in BUILTINS
        cfg = mod.agents.get("vibe-builder")
        assert cfg["builtin"] is True
        assert cfg["tools"] == ["think", "finish"]
        assert getattr(cfg["cls"], "arena", True) is False


class TestDraftHarness:
    """Vibecoding through a harness CLI — the build console path.

    The harness run itself is faked; what these pin is the dispatch: the
    alias resolving to a real runner, the drafter's goal riding along as the
    CLI's system prompt, and this module's own loop never being entered.
    """

    def _harness_mod(self, spec):
        from src.harness.mod import Harness
        mod = _make_mod()
        mod._vibe_catalog = lambda key=None: CATALOG
        mod.harness = Harness()
        mod.calls = []

        def boom(**kw):
            raise AssertionError("a harness draft must not enter the loop")
        mod._run = boom

        def fake_harness(name, **kw):
            mod.calls.append((name, kw))
            return _finish_trace(spec)
        mod._run_harness = fake_harness
        return mod

    def test_build_alias_reaches_the_buildmod_runner(self):
        spec = {"names": ["forge-hand"], "prompt": "You forge."}
        mod = self._harness_mod(spec)
        out = mod.agent_vibe("an agent drafted by the build console",
                             harness="build")
        assert out["draft"]["name"] == "forge-hand"
        name, kw = mod.calls[0]
        assert name == "buildmod"
        # the drafter's own instructions become the CLI's system prompt
        assert "You design agents" in (kw.get("goal") or "")
        assert kw.get("agent_type") == "vibe-builder"

    def test_unknown_harness_is_refused_with_the_list(self):
        mod = self._harness_mod({"prompt": "p"})
        try:
            mod.agent_vibe("an agent on a made-up engine", harness="warp9")
            assert False, "should have raised"
        except ValueError as e:
            assert "buildmod" in str(e)

    def test_spec_in_a_response_step_still_counts(self):
        # a CLI harness often narrates: the JSON lands in a response step and
        # the finish is prose
        spec = {"names": ["night-scribe"], "prompt": "You write."}
        mod = self._harness_mod(spec)
        trace = [
            {"tool": "response", "result": "```json\n" + json.dumps(spec) + "\n```"},
            {"tool": "finish", "params": {"summary": "done — created the spec above"}},
        ]
        mod._run_harness = lambda name, **kw: trace
        out = mod.agent_vibe("an agent whose spec is mid-trace", harness="buildmod")
        assert out["draft"]["name"] == "night-scribe"


class TestTaskVibe:
    """arena_task_draft's vibe surface: harness dispatch and the save flag."""

    def _task_mod(self, spec):
        mod = _make_mod()
        mod.arena = SimpleNamespace(
            validate_task=lambda s: dict(s),
            slugify=lambda t: str(t).lower().replace(" ", "-"))
        mod._run = lambda **kw: _finish_trace(spec)
        return mod

    TASK = {"title": "Sort a file", "prompt": "sort {workdir}/x.txt",
            "description": "sorting", "steps": 4}

    def test_draft_comes_back_unsaved(self):
        mod = self._task_mod(self.TASK)
        saved = []
        mod.arena_task_add = lambda spec, key=None: saved.append(spec)
        out = mod.arena_task_draft("a task about sorting a file")
        assert out["schema"] == "agent"
        assert out["draft"]["title"] == "Sort a file"
        assert "saved" not in out and not saved

    def test_save_files_a_valid_draft(self):
        mod = self._task_mod(self.TASK)
        filed = []
        mod.arena_task_add = (
            lambda spec, key=None: filed.append(spec) or {"ok": True})
        out = mod.arena_task_draft("a task about sorting a file", save=True)
        assert out["saved"] is True
        assert filed and filed[0]["title"] == "Sort a file"

    def test_invalid_draft_never_saves(self):
        mod = self._task_mod(self.TASK)

        def refuse(s):
            raise ValueError("prompt too long")
        mod.arena.validate_task = refuse
        filed = []
        mod.arena_task_add = lambda spec, key=None: filed.append(spec)
        out = mod.arena_task_draft("a task about sorting a file", save=True)
        assert out["invalid"] == "prompt too long"
        assert "saved" not in out and not filed

    def test_harness_draft_hands_the_task_builder_goal_over(self):
        from src.harness.mod import Harness
        mod = self._task_mod(self.TASK)
        mod.harness = Harness()
        calls = []

        def fake_harness(name, **kw):
            calls.append((name, kw))
            return _finish_trace(self.TASK)
        mod._run_harness = fake_harness
        mod._run = None  # the loop must not be entered
        out = mod.arena_task_draft("a task drafted by the build console",
                                   harness="build")
        assert out["draft"]["title"] == "Sort a file"
        name, kw = calls[0]
        assert name == "buildmod"
        assert kw.get("agent_type") == "task-builder"
        assert kw.get("goal")  # the shipped task-builder's own prompt


# ── vibecode a flow — POST /graphs/vibe and the machinery under it ──

from src.graph.mod import Graphs

GRAPHS_DIR = Path("/tmp/agent_vibe_test_graphs")

FLOW = {
    "name": "Review Loop",
    "description": "plan, build, judge",
    "nodes": [
        {"id": "in", "kind": "input", "data": {"label": "request"}},
        {"id": "plan", "kind": "agent", "data": {"agent": "architect", "prompt": "plan it"}},
        {"id": "out", "kind": "output", "data": {"label": "answer"}},
    ],
    "edges": [
        {"from": "in", "to": "plan", "port": "out"},
        {"from": "plan", "to": "out", "port": "out"},
    ],
}


def _graph_mod():
    shutil.rmtree(GRAPHS_DIR, ignore_errors=True)
    mod = _make_mod()
    ident = SimpleNamespace(
        require_signed_in=lambda key=None, operation=None: None,
        addr=lambda key=None: "0xvibe",
        is_the_host=lambda key=None: False,
        owner_of=lambda a: {"owner": a},
        require=lambda owner, key, op=None: None)
    mod.graphs = Graphs(identity=ident, agents=mod.agents, dir=str(GRAPHS_DIR))
    return mod


class TestParseGraphJson:
    def test_fenced_block(self):
        spec = Mod._parse_graph_json('sure\n```json\n' + json.dumps(FLOW) + '\n```')
        assert spec and [n["id"] for n in spec["nodes"]] == ["in", "plan", "out"]

    def test_no_nodes_is_no_spec(self):
        assert Mod._parse_graph_json('{"name": "x", "edges": []}') is None
        assert Mod._parse_graph_json('{"nodes": "not a list"}') is None


class TestGraphLayout:
    def test_columns_follow_depth(self):
        nodes = [{"id": "in", "kind": "input"}, {"id": "a", "kind": "agent"},
                 {"id": "out", "kind": "output"}]
        edges = [{"from": "in", "to": "a"}, {"from": "a", "to": "out"}]
        Mod._graph_layout(nodes, edges)
        xs = {n["id"]: n["x"] for n in nodes}
        assert xs["in"] < xs["a"] < xs["out"]

    def test_placed_nodes_stay_put(self):
        nodes = [{"id": "in", "kind": "input", "x": 123.0, "y": 45.0},
                 {"id": "a", "kind": "agent"}]
        Mod._graph_layout(nodes, [{"from": "in", "to": "a"}])
        assert nodes[0]["x"] == 123.0 and nodes[0]["y"] == 45.0
        assert nodes[1]["x"] and nodes[1]["y"]

    def test_a_cycle_does_not_hang_it(self):
        nodes = [{"id": "a", "kind": "agent"}, {"id": "b", "kind": "loop"}]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]
        Mod._graph_layout(nodes, edges)
        assert all(n.get("x") is not None for n in nodes)


class TestGraphVibe:
    def test_short_description_refused(self):
        mod = _graph_mod()
        try:
            mod.graph_vibe("hm")
            assert False, "should refuse"
        except ValueError:
            pass

    def test_draft_comes_back_wired_and_laid_out(self):
        mod = _graph_mod()
        mod._draft_trace = lambda **kw: _finish_trace(FLOW)
        out = mod.graph_vibe("plan it then answer")
        d = out["draft"]
        assert [n["kind"] for n in d["nodes"]] == ["input", "agent", "output"]
        assert all(n["x"] or n["y"] for n in d["nodes"])
        assert all(e["port"] for e in d["edges"])
        assert out["valid"]["ok"], out["valid"]
        assert "saved" not in out

    def test_unknown_kind_dropped_and_reported(self):
        mod = _graph_mod()
        bad = {**FLOW, "nodes": FLOW["nodes"] + [
            {"id": "x", "kind": "prompt", "data": {}}]}
        mod._draft_trace = lambda **kw: _finish_trace(bad)
        out = mod.graph_vibe("plan it then answer")
        assert out["nodes_dropped"] == ["prompt"]
        assert len(out["draft"]["nodes"]) == 3

    def test_edit_rides_the_current_graph_and_keeps_its_id(self):
        mod = _graph_mod()
        seen = {}

        def capture(**kw):
            seen.update(kw)
            return _finish_trace(FLOW)
        mod._draft_trace = capture
        current = {"id": "my-flow", **FLOW}
        out = mod.graph_vibe("add nothing, just rename it", graph=current)
        assert "CURRENT GRAPH" in seen["query"]
        assert out["draft"]["id"] == "my-flow"

    def test_save_files_a_valid_draft(self):
        mod = _graph_mod()
        mod._draft_trace = lambda **kw: _finish_trace(FLOW)
        out = mod.graph_vibe("plan it then answer", save=True)
        assert out["saved"] and out["graph"]["id"] == "review-loop"
        assert any(g["id"] == "review-loop" for g in mod.graphs.ls())

    def test_invalid_draft_never_saves(self):
        mod = _graph_mod()
        bad = {**FLOW, "nodes": [
            FLOW["nodes"][0],
            {"id": "plan", "kind": "agent", "data": {"agent": "no-such-agent"}},
            FLOW["nodes"][2]]}
        mod._draft_trace = lambda **kw: _finish_trace(bad)
        out = mod.graph_vibe("plan it then answer", save=True)
        assert out["invalid"] and "saved" not in out
        assert not any(g["id"] == "review-loop" for g in mod.graphs.ls())

    def test_no_spec_is_an_error_with_the_answer(self):
        mod = _graph_mod()
        mod._draft_trace = lambda **kw: [
            {"tool": "finish", "params": {"summary": "I could not decide"}}]
        out = mod.graph_vibe("plan it then answer")
        assert "error" in out and out["answer"]


class TestFlowBuilderShips:
    def test_registered_and_off_the_board(self):
        mod = _make_mod()
        cfg = mod.agents.get("flow-builder")
        assert cfg["arena"] is False
        assert "flow-builder" in BUILTINS
        assert set(cfg["tools"]) == {"think", "finish"}
