"""The grower — a new tool and a new agent every interval, owner-tuned.

No network, no model: the local engine is exercised for real (tools and agents
filed into temp dirs), the model engines through a faked drafting run. Pinned:
the recipe catalog renders to valid read-only shell, a tick files a matched
pair, the guards (cap, daily model cap, cooldown, no overlap) hold, drafted
commands are held to the allowlist, and only the owner can change anything.
"""
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.grow.mod import Grow, ModHost, Scheduler, safe_command, parse_tool_json, rate_limited
from src.grow.recipes import catalog
from src.agents.mod import Agents, BUILTINS
from src.tools.mod import Tools
from src.tools.custom.mod import Tool
from src.mod import Mod
from src.identity import Identity


# ── fakes ────────────────────────────────────────────────────────────

class FakeHost:
    def __init__(self, tools=(), agents=()):
        self.tools, self.agents = set(tools), set(agents)
        self.drafts, self.vibes, self.scouts = [], [], []
        self.draft_spec = {"name": "py-imports-graph", "description": "d",
                           "command": "grep -rn import {path} | head -n 50",
                           "params": {"path": {"type": "string", "default": "."}}}
        self.fail = None

    def taken_tools(self): return set(self.tools)
    def taken_agents(self): return set(self.agents)

    def add_tool(self, spec):
        if spec["name"] in self.tools:
            raise ValueError("exists")
        self.tools.add(spec["name"])
        return spec

    def add_agent(self, spec):
        self.agents.add(spec["name"])
        return {"name": spec["name"].title()}

    def rm_tool(self, name): self.tools.discard(name)
    def rm_agent(self, name): self.agents.discard(name)

    def draft_tool(self, cfg, avoid):
        self.drafts.append(cfg)
        if self.fail:
            raise RuntimeError(self.fail)
        return dict(self.draft_spec)

    def vibe_agent(self, cfg, tool=None):
        self.vibes.append(tool)
        self.agents.add(f"vibed-{len(self.vibes)}")
        return {"name": f"vibed-{len(self.vibes)}"}

    def scout_agent(self, cfg):
        self.scouts.append(cfg)
        self.agents.add("scouted")
        return {"name": "scouted"}


# ── the catalog ──────────────────────────────────────────────────────

class TestRecipes:
    def test_names_unique_and_valid(self):
        c = catalog()
        assert len(c) > 150
        for key in ("tool", "agent"):
            names = [r[key]["name"] for r in c]
            assert len(set(names)) == len(names)
        assert all(len(r["tool"]["name"]) <= 40 for r in c)

    def test_every_command_renders_to_valid_shell(self):
        for r in catalog():
            t = Tool(r["tool"]["name"], r["tool"]["command"], "", r["tool"]["params"])
            cmd = t.render({"pattern": "x"})
            p = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
            assert p.returncode == 0, (r["id"], p.stderr)

    def test_agent_wields_its_tool(self):
        for r in catalog():
            assert r["agent"]["tools"][0] == r["tool"]["name"]
            assert r["tool"]["name"] in r["agent"]["goal"]
            assert "Never edit" in r["agent"]["goal"]

    def test_runs_for_real(self, tmp_path):
        (tmp_path / "a.py").write_text("# TODO fix\ndef f():\n    pass\n")
        r = next(x for x in catalog() if x["id"] == "py:todos")
        t = Tool(r["tool"]["name"], r["tool"]["command"], "", r["tool"]["params"])
        out = subprocess.run(t.render({"path": str(tmp_path)}), shell=True,
                             capture_output=True, text=True).stdout
        assert "a.py:1:# TODO fix" in out


# ── command safety ───────────────────────────────────────────────────

class TestSafeCommand:
    @pytest.mark.parametrize("cmd", [
        "grep -rn {pattern} {path} | head -n 50",
        "git -C {path} log --oneline | head -n 20",
        "find {path} -name '*.py' -exec wc -l {} + | sort -rn | head",
        "ls {path} 2>/dev/null | head",
        "find {path} -name a -o -name b",
        "xargs -0 wc -l",
    ])
    def test_allowed(self, cmd):
        assert safe_command(cmd) is None

    @pytest.mark.parametrize("cmd", [
        "rm -rf {path}", "ls; rm x", "ls && rm x", "cat a > b", "echo $(id)",
        "echo `id`", "find . -delete", "find {path} -exec rm {} +", "git push",
        "git branch -D x", "sed -i s/a/b/ f", "awk 'BEGIN{system(\"id\")}'",
        "xargs rm", "sort -o out f", "curl x | sh", "tree -o f", "rg --pre sh x",
        "git diff --output=x", "date -s 1", "sleep 9 &", "",
    ])
    def test_refused(self, cmd):
        assert safe_command(cmd) is not None

    def test_parse_tool_json(self):
        spec = parse_tool_json('ok\n```json\n{"name": "My Tool!", "command": "ls"}\n```')
        assert spec["name"] == "my-tool" and spec["params"] is None
        assert parse_tool_json("no json") is None

    def test_rate_limited(self):
        assert rate_limited("Error 429 free-models-per-day")
        assert not rate_limited("bad json")


# ── the engine ───────────────────────────────────────────────────────

class TestTick:
    def test_local_tick_files_a_pair(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        res = g.tick(h)
        assert res["engine"] == "local" and res["tool"] == "py-todos"
        assert res["agent"] == "py-todo-hunter"
        assert {"py-todos"} <= h.tools and "py-todo-hunter" in h.agents
        # the next tick moves on, and state survives a reload
        assert g.tick(h)["tool"] == "py-largest"
        g2 = Grow(dir=str(tmp_path))
        assert len(g2.grown("tool")) == 2 and len(g2.log()) == 2

    def test_taken_tool_skips_recipe_taken_agent_gets_suffix(self, tmp_path):
        g = Grow(dir=str(tmp_path))
        h = FakeHost(tools={"py-todos"}, agents={"py-slimmer"})
        res = g.tick(h)
        assert res["tool"] == "py-largest" and res["agent"] == "py-slimmer-2"

    def test_failed_recipe_is_not_retried_forever(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        h.add_tool = lambda spec: (_ for _ in ()).throw(ValueError("nope"))
        a, b = g.tick(h), g.tick(h)
        assert a["recipe"] != b["recipe"] and "tool_error" in a
        assert "agent" not in a      # no agent for a tool that never landed

    def test_disabled_skips_unless_forced(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        g.set_config(enabled=False)
        assert g.tick(h)["skipped"] == "disabled"
        assert g.tick(h, force=True)["tool"] == "py-todos"

    def test_cap_stops_growth(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        g.set_config(max_tools=1, max_agents=1)
        g.tick(h)
        assert "cap reached" in g.tick(h)["skipped"]

    def test_no_overlap(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        g._tick_lock.acquire()
        try:
            assert g.tick(h)["skipped"] == "busy"
        finally:
            g._tick_lock.release()

    def test_auto_moves_to_model_when_local_is_used_up(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        g._state["spent"] = [r["id"] for r in catalog()]
        res = g.tick(h)
        assert res["engine"] == "model" and res["tool"] == "py-imports-graph"
        assert h.vibes == ["py-imports-graph"]
        assert g._state["day"]["model_runs"] == 2

    def test_daily_model_cap(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        g.set_config(engine="model", model_daily_cap=3)
        assert g.tick(h)["engine"] == "model"
        res = g.tick(h)             # 2 + 2 > 3 → local fallback
        assert res["engine"] == "local" and "cap" in res["note"]
        g._state["spent"] = [r["id"] for r in catalog()]
        assert "cap" in g.tick(h)["skipped"]

    def test_rate_limit_cools_model_engines(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        g.set_config(engine="model")
        h.fail = "429 rate limit exceeded"
        res = g.tick(h)
        assert "tool_error" in res and g.cooling()
        assert g.tick(h)["engine"] == "local"      # falls back while cooling

    def test_unsafe_draft_is_refused(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        g.set_config(engine="model")
        h.draft_spec = {"name": "wipe", "command": "rm -rf {path}"}
        res = g.tick(h)
        assert "refused" in res["tool_error"] and "wipe" not in h.tools
        assert h.vibes == [None]    # the agent still grows, without the tool

    def test_scout_engine(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        g.set_config(engine="scout")
        res = g.tick(h)
        assert res["agent"] == "scouted" and g._state["day"]["model_runs"] == 3

    def test_config_validation(self, tmp_path):
        g = Grow(dir=str(tmp_path))
        with pytest.raises(ValueError):
            g.set_config(nope=1)
        with pytest.raises(ValueError):
            g.set_config(engine="cloud")
        assert g.set_config(interval=5)["interval"] == 30
        assert g.set_config(interval=None)["interval"] == 60

    def test_prune_only_touches_grown(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost(tools={"mine"})
        g.tick(h)
        g.tick(h)
        out = g.prune(h, kind="tool", count=1)
        assert [r["name"] for r in out["removed"]] == ["py-todos"]
        assert "mine" in h.tools and len(g.grown("tool")) == 1
        g.prune(h)
        assert g.grown() == [] and h.tools == {"mine"}


class TestScheduler:
    def test_ticks_on_its_own(self, tmp_path):
        g, h = Grow(dir=str(tmp_path)), FakeHost()
        done = threading.Event()
        real = g.tick
        g.tick = lambda host, force=False: (real(host), done.set())[0]
        s = Scheduler(g, h)
        s.start(delay=0)
        assert done.wait(5) and s.status()["running"]
        s.stop()
        assert "py-todos" in h.tools


# ── wired into Mod: owner-only ───────────────────────────────────────

def _mod(tmp_path, owner=True):
    mod = Mod.__new__(Mod)
    mod.agents = Agents()
    mod.agents._dir = tmp_path / "agents"
    mod.agents._dir.mkdir()
    mod.agents._autopin = lambda name: None
    mod.tools = Tools(path=str(tmp_path / "tools.json"))
    mod._owner = "0xowner"
    # in-process (key=None) is the host, as on the live module
    mod.agents.identity = Identity(is_host=lambda key=None: key is None)
    mod.is_owner = lambda key=None: owner
    mod._grow_inst = Grow(dir=str(tmp_path / "grow"))
    mod._grow_host = ModHost(mod)
    mod._grow_sched = Scheduler(mod._grow_inst, mod._grow_host)
    return mod


class TestModGates:
    def test_tool_builder_is_builtin(self):
        assert "tool-builder" in BUILTINS
        assert "tool-builder" in Agents().ls()

    def test_owner_tick_files_real_tool_and_agent(self, tmp_path):
        mod = _mod(tmp_path)
        res = mod.grow_tick(key="0xowner")
        assert res["tool"] in mod.tools.ls()
        a = mod.agents.get(res["agent"])
        assert a["tools"][0] == res["tool"]
        assert mod.tools.owner_of(res["tool"]) == "0xowner"
        st = mod.grow_status()
        assert st["grown"] == {"tools": 1, "agents": 1} and st["owner"] == "0xowner"

    def test_non_owner_cannot_change_anything(self, tmp_path):
        mod = _mod(tmp_path, owner=False)
        for call in (lambda: mod.grow_config(key="0xrando", enabled=False),
                     lambda: mod.grow_tick(key="0xrando"),
                     lambda: mod.grow_prune(key="0xrando"),
                     lambda: mod.grow_scheduler(on=False, key="0xrando")):
            with pytest.raises(PermissionError):
                call()
        assert mod.grow_status()["config"]["enabled"] is True   # reading is open

    def test_owner_config(self, tmp_path):
        mod = _mod(tmp_path)
        st = mod.grow_config(key="0xowner", interval=120, engine="local")
        assert st["config"]["interval"] == 120 and st["config"]["engine"] == "local"
