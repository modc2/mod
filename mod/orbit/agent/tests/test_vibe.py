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
