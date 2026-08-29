"""
tests for the agent framework

covers:
    - tool registry (discovery, loading, caching, schema, errors, the fleet)
    - individual tools (bash, read, write, edit, glob, grep, search, task, websurf, claudecode)
    - agents registry (discovery, create, remove, schema)
    - memory
    - agent (parse_steps, _extract_step, run_plan, init_memory, tool wiring)
    - mod class (test, status, forward, gate/acl)
    - api endpoints

run:
    cd ~/mod/mod/orbit/agent && python3 -m pytest tests/test_agent.py -v
"""
import os
import sys
import json
import time
import threading
import tempfile
import shutil
import pytest
from pathlib import Path

# make sure imports resolve from the agent root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.tools.builtin.mod import Builtins
from src.tools.mod import Tools
from src.agents.mod import Agents
from src.memory.memory import Memory
from src.memory.registry import Memories
from src.toolbox.mod import Toolboxes

BUILTIN_COUNT = 26  # 23 that act on the world + recall/remember/toolbox, which
                    # act on the agent's own sub-components
# shipped agents. Custom agents live in the same directory, so counts are
# lower bounds — a host with their own agents installed still passes.
AGENT_COUNT = 9
# custom tools persist off-tree — tests get their own file so a run never
# reads or clobbers the host's real ~/.mod/agent/tools.json
TOOLS_PATH = "/tmp/agent_test_tools.json"


# ═══════════════════════════════════════════════════════════════════════
#  FIXTURES
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def builtin():
    return Builtins()

@pytest.fixture
def tools(tmpdir):
    return Tools(path=os.path.join(tmpdir, "tools.json"))

@pytest.fixture
def agents():
    return Agents()

@pytest.fixture
def memory():
    m = Memory()
    m.clear()
    return m

@pytest.fixture
def tmpdir():
    d = tempfile.mkdtemp(prefix="agent_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)

@pytest.fixture
def tmpfile(tmpdir):
    p = os.path.join(tmpdir, "test.txt")
    Path(p).write_text("line one\nline two\nline three\nhello world\n")
    return p


# ═══════════════════════════════════════════════════════════════════════
#  BUILT-IN TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════════════

class TestBuiltinRegistry:
    def test_ls_returns_all_tools(self, builtin):
        names = builtin.ls()
        assert len(names) == BUILTIN_COUNT
        for expected in ["bash", "read", "write", "edit", "glob", "grep",
                         "search", "task", "websurf", "claudecode"]:
            assert expected in names

    def test_get_returns_instance(self, builtin):
        bash = builtin.get("bash")
        assert hasattr(bash, "forward")
        assert hasattr(bash, "description")

    def test_get_caches_instances(self, builtin):
        a = builtin.get("bash")
        b = builtin.get("bash")
        assert a is b

    def test_get_unknown_tool_raises(self, builtin):
        with pytest.raises(KeyError, match="tool not found"):
            builtin.get("nonexistent_tool_xyz")

    def test_run_delegates_to_forward(self, builtin):
        r = builtin.run("bash", command="echo registry_test")
        assert r["success"]
        assert "registry_test" in r["stdout"]

    def test_forward_no_name_returns_list(self, builtin):
        r = builtin.forward()
        assert "tools" in r
        assert "total" in r
        assert r["total"] == BUILTIN_COUNT

    def test_forward_with_name_runs_tool(self, builtin):
        r = builtin.forward("bash", command="echo forward_test")
        assert r["success"]

    def test_schema_returns_all(self, builtin):
        schema = builtin.schema()
        assert len(schema) == BUILTIN_COUNT
        for name, info in schema.items():
            assert "description" in info, f"{name} schema missing description"
            assert "params" in info, f"{name} schema missing params"

    def test_schema_filtered(self, builtin):
        schema = builtin.schema(["bash", "read"])
        assert len(schema) == 2
        assert "bash" in schema
        assert "read" in schema

    def test_schema_params_have_types(self, builtin):
        schema = builtin.schema(["bash"])
        params = schema["bash"]["params"]
        assert "command" in params
        assert params["command"]["required"] is True
        assert "timeout" in params
        assert params["timeout"]["required"] is False


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: BASH
# ═══════════════════════════════════════════════════════════════════════

class TestBashTool:
    def test_echo(self, builtin):
        r = builtin.run("bash", command="echo hello")
        assert r["success"]
        assert r["stdout"].strip() == "hello"
        assert r["code"] == 0

    def test_failing_command(self, builtin):
        r = builtin.run("bash", command="exit 1")
        assert not r["success"]
        assert r["code"] == 1

    def test_stderr(self, builtin):
        r = builtin.run("bash", command="echo err >&2")
        assert "err" in r["stderr"]

    def test_cwd(self, builtin, tmpdir):
        r = builtin.run("bash", command="pwd", cwd=tmpdir)
        assert r["success"]
        assert tmpdir in r["stdout"] or os.path.realpath(tmpdir) in r["stdout"]

    def test_timeout(self, builtin):
        r = builtin.run("bash", command="sleep 10", timeout=1)
        assert not r["success"]
        assert "timeout" in r["stderr"]

    def test_multiline_output(self, builtin):
        r = builtin.run("bash", command="echo a; echo b; echo c")
        assert r["success"]
        lines = r["stdout"].strip().split("\n")
        assert lines == ["a", "b", "c"]

    def test_pipe(self, builtin):
        r = builtin.run("bash", command="echo 'hello world' | tr 'h' 'H'")
        assert r["success"]
        assert "Hello" in r["stdout"]


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: READ
# ═══════════════════════════════════════════════════════════════════════

class TestReadTool:
    def test_read_file(self, builtin, tmpfile):
        r = builtin.run("read", file_path=tmpfile)
        assert r["success"]
        assert "line one" in r["content"]
        assert r["total"] == 4
        assert r["lines"] == 4

    def test_read_with_offset(self, builtin, tmpfile):
        r = builtin.run("read", file_path=tmpfile, offset=1)
        assert r["success"]
        assert "line two" in r["content"]
        assert "line one" not in r["content"]

    def test_read_with_limit(self, builtin, tmpfile):
        r = builtin.run("read", file_path=tmpfile, limit=2)
        assert r["success"]
        assert r["lines"] == 2

    def test_read_nonexistent(self, builtin):
        r = builtin.run("read", file_path="/tmp/this_file_does_not_exist_xyz.txt")
        assert not r["success"]
        assert "not found" in r["error"]

    def test_read_directory(self, builtin, tmpdir):
        r = builtin.run("read", file_path=tmpdir)
        assert not r["success"]
        assert "not a file" in r["error"]


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: WRITE
# ═══════════════════════════════════════════════════════════════════════

class TestWriteTool:
    def test_write_new_file(self, builtin, tmpdir):
        p = os.path.join(tmpdir, "new.txt")
        r = builtin.run("write", file_path=p, content="hello")
        assert r["success"]
        assert Path(p).read_text() == "hello"
        assert r["bytes"] == 5

    def test_write_creates_dirs(self, builtin, tmpdir):
        p = os.path.join(tmpdir, "a", "b", "c", "deep.txt")
        r = builtin.run("write", file_path=p, content="deep")
        assert r["success"]
        assert Path(p).read_text() == "deep"

    def test_write_overwrites(self, builtin, tmpfile):
        r = builtin.run("write", file_path=tmpfile, content="overwritten")
        assert r["success"]
        assert Path(tmpfile).read_text() == "overwritten"


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: EDIT
# ═══════════════════════════════════════════════════════════════════════

class TestEditTool:
    def test_single_replace(self, builtin, tmpfile):
        r = builtin.run("edit", file_path=tmpfile, old_string="line one", new_string="LINE ONE")
        assert r["success"]
        assert r["replacements"] == 1
        content = Path(tmpfile).read_text()
        assert "LINE ONE" in content
        assert "line two" in content

    def test_replace_all(self, builtin, tmpdir):
        p = os.path.join(tmpdir, "multi.txt")
        Path(p).write_text("aaa bbb aaa ccc aaa")
        r = builtin.run("edit", file_path=p, old_string="aaa", new_string="XXX", replace_all=True)
        assert r["success"]
        assert r["replacements"] == 3
        assert Path(p).read_text() == "XXX bbb XXX ccc XXX"

    def test_string_not_found(self, builtin, tmpfile):
        r = builtin.run("edit", file_path=tmpfile, old_string="NONEXISTENT", new_string="X")
        assert not r["success"]
        assert "not found" in r["error"]

    def test_multiline_replace(self, builtin, tmpfile):
        r = builtin.run("edit", file_path=tmpfile, old_string="line one\nline two", new_string="REPLACED")
        assert r["success"]
        assert "REPLACED" in Path(tmpfile).read_text()


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: GLOB
# ═══════════════════════════════════════════════════════════════════════

class TestGlobTool:
    def test_find_py_files(self, builtin):
        r = builtin.run("glob", pattern="*.py", path=os.path.join(os.path.dirname(__file__), ".."))
        assert r["success"]
        assert r["total"] > 0

    def test_find_in_tmpdir(self, builtin, tmpdir):
        Path(os.path.join(tmpdir, "a.py")).touch()
        Path(os.path.join(tmpdir, "b.py")).touch()
        Path(os.path.join(tmpdir, "c.txt")).touch()
        r = builtin.run("glob", pattern="*.py", path=tmpdir)
        assert r["success"]
        assert r["total"] == 2

    def test_no_matches(self, builtin, tmpdir):
        r = builtin.run("glob", pattern="*.xyz_nonexistent", path=tmpdir)
        assert r["success"]
        assert r["total"] == 0


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: GREP
# ═══════════════════════════════════════════════════════════════════════

class TestGrepTool:
    def test_find_pattern(self, builtin, tmpfile):
        r = builtin.run("grep", pattern="hello", path=tmpfile)
        assert r["success"]
        assert r["total"] == 1
        assert r["matches"][0]["text"] == "hello world"
        assert r["matches"][0]["line"] == 4

    def test_regex(self, builtin, tmpfile):
        r = builtin.run("grep", pattern="line (one|two)", path=tmpfile)
        assert r["success"]
        assert r["total"] == 2

    def test_case_insensitive(self, builtin, tmpdir):
        p = os.path.join(tmpdir, "case.txt")
        Path(p).write_text("Hello\nhello\nHELLO\n")
        r = builtin.run("grep", pattern="hello", path=p, ignore_case=True)
        assert r["success"]
        assert r["total"] == 3

    def test_bad_regex(self, builtin, tmpfile):
        r = builtin.run("grep", pattern="[invalid", path=tmpfile)
        assert not r["success"]
        assert "bad regex" in r["error"]

    def test_no_matches(self, builtin, tmpfile):
        r = builtin.run("grep", pattern="ZZZNOTHERE", path=tmpfile)
        assert r["success"]
        assert r["total"] == 0


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: SEARCH (web)
# ═══════════════════════════════════════════════════════════════════════

class TestSearchTool:
    def test_empty_query(self, builtin):
        r = builtin.run("search", query="")
        assert not r["success"]
        assert "empty" in r["error"]

    def test_search_returns_dict(self, builtin):
        r = builtin.run("search", query="python")
        assert isinstance(r, dict)
        assert "success" in r
        assert "results" in r


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: WEBSURF
# ═══════════════════════════════════════════════════════════════════════

class TestWebsurfTool:
    def test_empty_url(self, builtin):
        r = builtin.run("websurf", url="")
        assert not r["success"]
        assert "empty" in r["error"]

    def test_returns_dict(self, builtin):
        r = builtin.run("websurf", url="https://httpbin.org/html")
        assert isinstance(r, dict)
        assert "success" in r

    def test_bad_url(self, builtin):
        r = builtin.run("websurf", url="https://this-domain-does-not-exist-xyz.invalid")
        assert not r["success"]
        assert "error" in r


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: CLAUDECODE
# ═══════════════════════════════════════════════════════════════════════

class TestClaudeCodeTool:
    def test_empty_prompt(self, builtin):
        r = builtin.run("claudecode", prompt="")
        assert not r["success"]
        assert "empty" in r["error"]

    def test_tool_has_description(self, builtin):
        tool = builtin.get("claudecode")
        assert "claude" in tool.description.lower() or "code" in tool.description.lower()

    def test_schema_has_prompt_param(self, builtin):
        schema = builtin.schema(["claudecode"])
        assert "claudecode" in schema
        assert "prompt" in schema["claudecode"]["params"]
        assert schema["claudecode"]["params"]["prompt"]["required"] is True


# ═══════════════════════════════════════════════════════════════════════
#  TOOL: TASK
# ═══════════════════════════════════════════════════════════════════════

class TestTaskTool:
    def test_task_returns_dict(self, builtin):
        r = builtin.run("task", prompt="test")
        assert isinstance(r, dict)
        assert "success" in r


# ═══════════════════════════════════════════════════════════════════════
#  AGENTS REGISTRY
# ═══════════════════════════════════════════════════════════════════════

class TestAgentsRegistry:
    def test_ls_returns_all_agents(self, agents):
        names = agents.ls()
        assert len(names) >= AGENT_COUNT
        for expected in ["default", "architect", "reviewer", "debugger",
                         "builder", "refactorer", "safety",
                         "claude-code", "codex"]:
            assert expected in names

    def test_get_returns_config(self, agents):
        config = agents.get("architect")
        assert config["name"] == "Architect"
        assert "description" in config
        assert "goal" in config
        assert config["goal"] is not None
        assert "icon" in config
        assert isinstance(config["tools"], list)

    def test_get_default_agent(self, agents):
        config = agents.get("default")
        assert config["name"] == "Default"
        assert config["goal"] is None  # uses base goal
        assert config["tools"] is None  # every tool

    def test_get_unknown_raises(self, agents):
        with pytest.raises(KeyError, match="agent not found"):
            agents.get("nonexistent_agent_xyz")

    def test_schema_returns_all(self, agents):
        schema = agents.schema()
        assert len(schema) >= AGENT_COUNT
        for name, info in schema.items():
            assert "description" in info, f"{name} missing description"

    def test_forward_no_name_lists_all(self, agents):
        r = agents.forward()
        assert "agents" in r
        assert "total" in r
        assert r["total"] >= AGENT_COUNT
        assert "schemas" in r

    def test_forward_with_name_gets_config(self, agents):
        r = agents.forward("safety")
        assert r["name"] == "Safety"
        assert "goal" in r

    def test_safety_agent_has_tools(self, agents):
        config = agents.get("safety")
        assert "read" in config["tools"]
        assert "think" in config["tools"]
        assert "grep" in config["tools"]

    def test_chains(self, agents):
        chains = agents.chains()
        assert "debug-fix" in chains
        assert "plan-build-review" in chains
        assert len(chains["debug-fix"]["steps"]) == 2
        assert len(chains["plan-build-review"]["steps"]) == 3

    def test_create_and_remove(self, agents):
        """Test creating and removing a custom agent."""
        name = "test-custom-agent"
        try:
            config = agents.create(name, description="test agent", goal="test goal")
            assert config["name"] == "Test Custom Agent"
            assert name in agents.ls()
            # remove
            r = agents.remove(name)
            assert r["removed"] == name
            assert name not in agents.ls()
        finally:
            # cleanup in case test fails
            agent_dir = agents._dir / name
            if agent_dir.exists():
                shutil.rmtree(agent_dir)

    def test_create_duplicate_raises(self, agents):
        with pytest.raises(FileExistsError):
            agents.create("default")

    def test_remove_builtin_raises(self, agents):
        with pytest.raises(PermissionError, match="cannot remove built-in"):
            agents.remove("default")

    def test_remove_nonexistent_raises(self, agents):
        with pytest.raises(KeyError, match="agent not found"):
            agents.remove("nonexistent_agent_xyz")


# ═══════════════════════════════════════════════════════════════════════
#  AGENT OWNERSHIP  (owner = creator; unowned = the host owns it)
# ═══════════════════════════════════════════════════════════════════════

class TestAgentOwnership:
    HOST = "0xaaa0000000000000000000000000000000000001"
    USER = "0xbbb0000000000000000000000000000000000002"
    OTHER = "0xccc0000000000000000000000000000000000003"

    @pytest.fixture
    def owned(self, agents):
        """Registry with a known host. No verifier is configured here, so a
        bare address stands in for a signed token."""
        from src.identity import Identity
        return agents.bind(Identity(host=self.HOST))

    def test_builtins_belong_to_the_host(self, owned):
        cfg = owned.get("architect")
        assert cfg["owner"] == self.HOST and cfg["owner_source"] == "host"
        assert owned.can_manage("architect", self.HOST)
        assert not owned.can_manage("architect", self.USER)

    def test_host_can_remove_a_builtin(self, owned, tmp_path):
        import shutil
        backup = tmp_path / "safety"
        shutil.copytree(owned._dir / "safety", backup)
        try:
            assert owned.remove("safety", key=self.HOST)["removed"] == "safety"
            assert "safety" not in owned.ls()
        finally:
            shutil.copytree(backup, owned._dir / "safety", dirs_exist_ok=True)
            owned._cache.pop("safety", None)

    def test_creator_owns_it_and_strangers_cannot_touch_it(self, owned):
        name = "test-owned-agent"
        try:
            cfg = owned.create(name, description="mine", goal="g", key=self.USER)
            assert cfg["owner"] == self.USER and cfg["owner_source"] == "item"
            for stranger in (None, self.OTHER):
                with pytest.raises(PermissionError):
                    owned.remove(name, key=stranger)
                with pytest.raises(PermissionError):
                    owned.update(name, description="hijacked", key=stranger)
            # the owner may edit, and editing keeps ownership
            owned.update(name, description="edited", key=self.USER)
            assert owned.get(name)["owner"] == self.USER
            # the host may remove anything
            assert owned.remove(name, key=self.HOST)["removed"] == name
        finally:
            agent_dir = owned._dir / name
            if agent_dir.exists():
                shutil.rmtree(agent_dir)

    def test_anonymous_create_is_refused(self, owned):
        """Nobody to own it, so it never gets made."""
        name = "test-anon-agent"
        try:
            with pytest.raises(PermissionError):
                owned.create(name, goal="g")
            assert name not in owned.ls()
        finally:
            agent_dir = owned._dir / name
            if agent_dir.exists():
                shutil.rmtree(agent_dir)

    def test_unbound_registry_leaves_unowned_agents_open(self, agents):
        """No host known (local/dev) — unowned agents stay manageable, but
        built-ins still are not."""
        name = "test-unbound-agent"
        try:
            assert agents.create(name, goal="g")["owner_source"] is None
            assert agents.can_manage(name)
            assert not agents.can_manage("default")
            assert agents.remove(name)["removed"] == name
        finally:
            agent_dir = agents._dir / name
            if agent_dir.exists():
                shutil.rmtree(agent_dir)


# ═══════════════════════════════════════════════════════════════════════
#  MEMORY
# ═══════════════════════════════════════════════════════════════════════

class TestMemory:
    def test_add_and_get(self, memory):
        memory.add("k1", "v1")
        assert memory.get("k1") == "v1"

    def test_add_dict(self, memory):
        memory.add({"a": 1, "b": 2})
        assert memory.get("a") == 1
        assert memory.get("b") == 2

    def test_get_all(self, memory):
        memory.add("x", 10)
        memory.add("y", 20)
        all_mem = memory.get()
        assert all_mem["x"] == 10
        assert all_mem["y"] == 20

    def test_get_missing_returns_none(self, memory):
        assert memory.get("nonexistent") is None

    def test_keys(self, memory):
        memory.add("a", 1)
        memory.add("b", 2)
        assert sorted(memory.keys()) == ["a", "b"]

    def test_rm(self, memory):
        memory.add("k", "v")
        memory.rm("k")
        assert memory.get("k") is None

    def test_clear(self, memory):
        memory.add("a", 1)
        memory.clear()
        assert memory.get() == {}

    def test_update(self, memory):
        memory.add("a", 1)
        memory.update({"a": 99, "b": 2})
        assert memory.get("a") == 99
        assert memory.get("b") == 2

    def test_update_non_dict_raises(self, memory):
        with pytest.raises(AssertionError):
            memory.update("not a dict")

    def test_builtin_test(self, memory):
        assert memory.test() is True


# ═══════════════════════════════════════════════════════════════════════
#  MEMORY AS RETRIEVAL (one scorer, every layer)
# ═══════════════════════════════════════════════════════════════════════

class TestRetrieval:
    """The ranking engine every layer is searched with."""

    def _rank(self, q, docs, **kw):
        from src.memory.retrieval import rank
        return rank(q, docs, text_of=lambda d: d["t"], **kw)

    def test_a_rare_word_beats_a_common_one(self):
        docs = [{"t": "the deploy script runs on every host"},
                {"t": "the deploy script runs on every host and pins tzdata"},
                {"t": "deploy deploy deploy"}]
        hits = self._rank("tzdata deploy", docs)
        # 'deploy' is in every document and says nothing; 'tzdata' picks one out
        assert hits[0][1]["t"].endswith("tzdata")

    def test_no_match_is_no_hit(self):
        assert self._rank("kangaroo", [{"t": "nothing about that here"}]) == []

    def test_stop_words_alone_match_nothing(self):
        # otherwise "what is the thing" would retrieve the whole store
        assert self._rank("what is the", [{"t": "what is the thing"}]) == []

    def test_scores_are_bounded_and_ordered(self):
        docs = [{"t": "relay port 8412"}, {"t": "relay"}]
        hits = self._rank("relay port", docs)
        assert [h[0] for h in hits] == sorted([h[0] for h in hits], reverse=True)
        assert all(0 < s <= 1 for s, _ in hits)

    def test_recency_breaks_a_tie(self):
        old = {"t": "the same words exactly", "ts": time.time() - 400 * 86400}
        new = {"t": "the same words exactly", "ts": time.time()}
        hits = self._rank("same words exactly", [old, new],
                          ts_of=lambda d: d.get("ts"))
        assert hits[0][1] is new


class TestMemoryRetrieve:
    """retrieve(): one question, every layer, one shape."""

    def _mem(self):
        return Memory(persist=False)

    def test_hits_carry_their_layer(self):
        mem = self._mem()
        mem.remember("relay", "the relay listens on port 8412")
        mem.exchange("where does the relay run?", "on box seven",
                     session="s1", who="0xabc")
        mem.observe({"tool": "bash", "params": {"command": "systemctl start relay"}})
        layers = {h["layer"] for h in mem.retrieve("relay", who="0xabc")}
        assert layers == {"semantic", "dialogue", "episodic"}

    def test_one_layer_can_be_asked_alone(self):
        mem = self._mem()
        mem.remember("relay", "the relay listens on port 8412")
        mem.exchange("relay?", "yes", session="s1", who="0xabc")
        hits = mem.retrieve("relay", layers=["semantic"], who="0xabc")
        assert hits and {h["layer"] for h in hits} == {"semantic"}

    def test_dialogue_stays_scoped_to_its_caller(self):
        mem = self._mem()
        mem.exchange("my secret question", "my secret answer",
                     session="s1", who="0xabc")
        mine = mem.retrieve("secret", who="0xabc")
        theirs = mem.retrieve("secret", who="0xdef")
        assert any("secret answer" in h["text"] for h in mine)
        assert not theirs

    def test_steps_are_retrievable_not_just_tailable(self):
        mem = self._mem()
        for i in range(5):
            mem.observe({"tool": "read", "params": {"file_path": f"/tmp/f{i}.py"}})
        mem.observe({"tool": "bash", "params": {"command": "pytest -q tests/"}})
        hits = mem.recall_episodes("pytest")
        assert hits and hits[0]["tool"] == "bash"

    def test_layers_are_ranked_against_each_other(self):
        # scoring each layer alone makes the numbers incomparable: a step that
        # shares one word with the question would outrank the fact that
        # answers it, because that word is all its own layer has ever seen
        mem = self._mem()
        mem.remember("memory modules",
                     "an agent is built with default or ephemeral memory")
        for i in range(20):
            mem.observe({"tool": "read",
                         "params": {"file_path": f"/root/.mod/agent/work/{i}"}})
        top = mem.retrieve("agent memory modules", k=3)[0]
        assert top["layer"] == "semantic"

    def test_a_query_word_nothing_has_seen_does_not_sink_the_rest(self):
        # "run" when the fact says "runs": the store can only be searched on
        # what is in it, so an unfindable word is not evidence of a weak match
        mem = self._mem()
        mem.remember("test command", "the suite runs with pytest")
        assert mem.retrieve("how do I run the pytest suite")

    def test_working_memory_is_opt_in(self):
        # it is the prompt being written right now — retrieving it would hand
        # the model back what it is already reading
        mem = self._mem()
        mem.add("goal", "fix the flaky relay test")
        assert not mem.retrieve("relay")
        assert mem.retrieve("relay", layers=["working"])

    def test_compile_still_renders_a_prompt_block(self):
        mem = self._mem()
        mem.remember("style", "tabs not spaces")
        assert "RECALLED FACTS" in mem.compile("what style?")

    def test_a_fact_another_process_stored_is_retrievable(self, tmpdir):
        # the API, the :50119 service and the CLI all write the same file — a
        # store cached for the life of a process answers from a startup snapshot
        served = Memory(dir=tmpdir)
        assert served.recall("quokkas") == []          # warms the cache
        cli = Memory(dir=tmpdir)
        cli.remember("probe", "the probe fact mentions quokkas")
        assert [f["id"] for f in served.recall("quokkas")] == ["probe"]

    def test_a_local_fact_survives_someone_elses_write(self, tmpdir):
        a, b = Memory(dir=tmpdir), Memory(dir=tmpdir)
        a.remember("mine", "written by a")
        b.remember("theirs", "written by b")
        assert {f["id"] for f in a.facts()} == {"mine", "theirs"}


class TestMemoryRegistry:
    """Memory is a component: pluggable, with a default."""

    def _reg(self):
        return Memories()

    def test_default_is_listed_first(self):
        assert self._reg().ls()[0] == "default"

    def test_every_module_speaks_the_same_interface(self):
        reg = self._reg()
        for name in reg.ls():
            mem = reg.make(name, fresh=True, persist=False)
            assert mem.retrieve("x") == [] or isinstance(mem.retrieve("x"), list)
            assert reg.name_of(mem) == name

    def test_ephemeral_never_persists(self):
        eph = self._reg().make("ephemeral", fresh=True)
        assert eph.persist is False
        assert eph.episodic.path is None and eph.semantic.path is None
        assert eph.save("/tmp/agent_ephemeral_should_not_exist.json") is False
        assert not os.path.exists("/tmp/agent_ephemeral_should_not_exist.json")

    def test_an_instance_is_shared_so_it_can_remember(self):
        reg = self._reg()
        assert reg.make("default") is reg.make("default")

    def test_an_unknown_module_is_an_error_not_a_silent_default(self):
        with pytest.raises(KeyError):
            self._reg().make("nope")

    def test_a_dotted_name_falls_back_rather_than_failing_a_run(self):
        # an agent built against a module that moved loses its memory, not its
        # ability to answer
        mem = self._reg().make("not.a.real.module")
        assert hasattr(mem, "retrieve")

    def test_registry_self_test(self):
        assert self._reg().test()["passed"]


class TestSubComponentTools:
    """The tools that reach back into the agent box: recall, remember, toolbox."""

    def _agent(self):
        from src.mod import Agent
        agent = Agent.__new__(Agent)
        agent.agents = Agents()
        agent.memories = Memories()
        agent.memory = agent.memories.make("ephemeral", fresh=True)
        agent.tools = Tools(path=TOOLS_PATH)
        agent.toolboxes = Toolboxes(tools=agent.tools)
        agent.tools.bind(agent)
        agent._snapped = []
        agent._tool_names = None
        return agent

    def test_the_three_tools_ship(self):
        for name in ("recall", "remember", "toolbox"):
            assert name in Builtins().ls()

    def test_remember_then_recall_round_trip(self):
        agent = self._agent()
        agent.tools.run("remember", fact="deploy",
                        content="deploys run through scripts/ship.sh")
        hits = agent.tools.run("recall", query="how do we deploy")["hits"]
        assert any("ship.sh" in h["text"] for h in hits)

    def test_recall_reaches_the_live_memory_not_a_new_one(self):
        agent = self._agent()
        agent.memory.remember("port", "the api listens on 50117")
        assert agent.tools.run("recall", query="which port")["total"] == 1

    def test_an_unbound_tool_says_so_rather_than_inventing_memory(self):
        from src.tools.builtin.mod import Builtins as B
        tool = B().get("recall")           # no agent bound
        assert tool.forward(query="anything")["success"] is False

    def test_toolbox_lists_the_bundles(self):
        agent = self._agent()
        out = agent.tools.run("toolbox")
        assert out["success"] and any(b["name"] == "vcs" for b in out["toolboxes"])

    def test_toolbox_grows_the_run_schema(self):
        agent = self._agent()
        agent.memory.add("tools", agent.tool_schema(["read"]))
        out = agent.tools.run("toolbox", box="vcs")
        assert out["success"] and "git" in out["added"]
        assert "git" in agent.memory.get("tools") and "read" in agent.memory.get("tools")

    def test_toolbox_leaves_the_module_loadout_alone(self):
        # widening one run must not widen every later one
        agent = self._agent()
        agent.tools.run("toolbox", box="vcs")
        assert agent._tool_names is None

    def test_a_sandboxed_run_cannot_pull_the_fleet_in(self):
        agent = self._agent()
        agent._allowed_paths = ["/tmp/portal"]
        agent.toolboxes._custom["fleety"] = {"tools": ["read", "mod.git"],
                                             "description": "with a module in it"}
        out = agent.tools.run("toolbox", box="fleety")
        assert out["blocked"] == ["mod.git"]
        assert "mod.git" not in (agent.memory.get("tools") or {})

    def test_unknown_box_is_reported_with_the_options(self):
        out = self._agent().tools.run("toolbox", box="nope")
        assert out["success"] is False and "vcs" in out["error"]

    def test_parts_shows_the_whole_box(self):
        from src.mod import Agent
        agent = Agent()
        parts = agent.parts()
        assert set(parts) == {"model", "memory", "toolbox", "tools", "prompt"}
        assert parts["memory"]["module"] == "default"
        assert any(o["name"] == "ephemeral" for o in parts["memory"]["options"])


# ═══════════════════════════════════════════════════════════════════════
#  AGENT (unit tests without LLM)
# ═══════════════════════════════════════════════════════════════════════

class TestAgent:
    """Test agent components that don't need an LLM connection."""

    def _make_agent(self):
        from src.mod import Agent
        agent = Agent.__new__(Agent)
        agent.agents = Agents()
        agent.memories = Memories()
        agent.memory = Memory()
        agent.memory.clear()
        agent.model = None
        agent._tool_names = None
        agent._session_keys = {}
        agent._snapped = []
        from src.toolbox.mod import Toolboxes
        agent.tools = Tools(path=TOOLS_PATH)
        agent.toolboxes = Toolboxes(tools=agent.tools)
        agent.goal = Agent.goal
        agent.output_format = Agent.output_format
        agent.anchors = Agent.anchors
        from src.billing import Meter
        agent.meter = Meter()
        agent._provider = Agent.PROVIDERS['openrouter']
        return agent

    # ── tool wiring ──

    def test_tool_ls(self):
        agent = self._make_agent()
        assert "bash" in agent.tools.ls()
        assert len(agent.tools.ls()) == BUILTIN_COUNT

    def test_tool_get(self):
        agent = self._make_agent()
        bash = agent.tool("bash")
        assert hasattr(bash, "forward")

    def test_run_tool(self):
        agent = self._make_agent()
        r = agent.run_tool("bash", command="echo agent_test")
        assert r["success"]
        assert "agent_test" in r["stdout"]

    def test_tool_schema(self):
        agent = self._make_agent()
        schema = agent.tool_schema()
        assert len(schema) == BUILTIN_COUNT
        assert "bash" in schema
        assert "claudecode" in schema
        assert "websurf" in schema

    def test_tool_schema_filtered(self):
        agent = self._make_agent()
        agent._tool_names = ["bash", "read"]
        schema = agent.tool_schema()
        assert len(schema) == 2

    def test_the_fleet_is_reachable_but_not_loaded(self):
        """Every mod is a potential tool: in the registry, out of the prompt
        until it is asked for by name."""
        agent = self._make_agent()
        fleet = agent.tools.mods.ls()
        if not fleet:
            pytest.skip("no mod protocol on this host")
        assert all(n.startswith("mod.") for n in fleet)
        assert fleet[0] not in agent.tools.ls()           # not in the default set
        assert fleet[0] in agent.all_tools(mods=True)     # but it is a tool
        assert fleet[0] not in agent.tool_schema()        # not in the prompt
        assert fleet[0] in agent.tool_schema([fleet[0]])  # unless you ask

    # ── agents wiring ──

    def test_agents_ls(self):
        agent = self._make_agent()
        assert "architect" in agent.agents.ls()
        assert len(agent.agents.ls()) >= AGENT_COUNT

    # ── parse_steps ──

    def test_parse_steps_single(self):
        agent = self._make_agent()
        output = '<PLAN>\n<STEP>{"tool": "bash", "params": {"command": "ls"}}</STEP>\n</PLAN>'
        steps, raw = agent.parse_steps(output)
        assert len(steps) == 1
        assert steps[0]["tool"] == "bash"
        assert raw == output

    def test_parse_steps_finish(self):
        agent = self._make_agent()
        output = '<PLAN>\n<STEP>{"tool": "finish", "params": {}}</STEP>\n</PLAN>'
        steps, raw = agent.parse_steps(output)
        assert len(steps) == 1
        assert steps[0]["tool"] == "finish"

    def test_parse_steps_multiple(self):
        agent = self._make_agent()
        output = (
            '<PLAN>\n'
            '<STEP>{"tool": "read", "params": {"file_path": "/tmp/x"}}</STEP>\n'
            '<STEP>{"tool": "finish", "params": {}}</STEP>\n'
            '</PLAN>'
        )
        steps, raw = agent.parse_steps(output)
        assert len(steps) == 2

    def test_parse_steps_empty(self):
        agent = self._make_agent()
        steps, raw = agent.parse_steps("no steps here")
        assert steps == []
        assert raw == "no steps here"

    def test_parse_steps_bad_json(self):
        agent = self._make_agent()
        output = '<PLAN>\n<STEP>not json</STEP>\n</PLAN>'
        steps, raw = agent.parse_steps(output)
        assert steps == []

    def test_parse_steps_trailing_comma_repaired(self):
        agent = self._make_agent()
        output = '<PLAN>\n<STEP>{"tool": "bash", "params": {"command": "ls"},}</STEP>\n</PLAN>'
        steps, raw = agent.parse_steps(output)
        assert len(steps) == 1
        assert steps[0]["tool"] == "bash"

    def test_parse_steps_code_fence_repaired(self):
        agent = self._make_agent()
        output = '<PLAN>\n<STEP>```json\n{"tool": "read", "params": {"file_path": "/tmp/x"}}\n```</STEP>\n</PLAN>'
        steps, raw = agent.parse_steps(output)
        assert len(steps) == 1
        assert steps[0]["tool"] == "read"

    def test_parse_steps_extra_brace_repaired(self):
        agent = self._make_agent()
        output = ('<PLAN>\n<STEP>{"tool": "fetch", "params": '
                  '{"url": "https://api.example.com/x?a=1&b=2"}}}</STEP>\n</PLAN>')
        steps, raw = agent.parse_steps(output)
        assert len(steps) == 1
        assert steps[0]["tool"] == "fetch"
        assert steps[0]["params"]["url"] == "https://api.example.com/x?a=1&b=2"

    def test_first_object_ignores_braces_in_strings(self):
        agent = self._make_agent()
        s = '{"tool": "write", "params": {"content": "a } b {"}} trailing junk }'
        assert json.loads(agent._first_object(s))["params"]["content"] == "a } b {"

    def test_parse_steps_missing_params(self):
        """Models routinely omit params on finish — that's still a step."""
        agent = self._make_agent()
        steps, raw = agent.parse_steps('<STEP>{"tool": "finish"}</STEP>')
        assert len(steps) == 1
        assert steps[0]["params"] == {}

    def test_parse_steps_double_encoded_params(self):
        agent = self._make_agent()
        output = '<STEP>{"tool": "bash", "params": "{\\"command\\": \\"ls\\"}"}</STEP>'
        steps, raw = agent.parse_steps(output)
        assert steps[0]["params"] == {"command": "ls"}

    def test_parse_steps_truncated_value_repaired(self):
        """max_tokens cut the answer mid-string — keep what arrived."""
        agent = self._make_agent()
        output = '<STEP>{"tool": "bash", "params": {"command": "grep -rn foo</STEP>'
        steps, raw = agent.parse_steps(output)
        assert len(steps) == 1
        assert steps[0]["params"]["command"] == "grep -rn foo"

    def test_parse_steps_truncated_key_repaired(self):
        """Cut off after a key: drop the orphan, keep the complete params."""
        agent = self._make_agent()
        output = '<STEP>{"tool": "bash", "params": {"command": "ls", "cwd":</STEP>'
        steps, raw = agent.parse_steps(output)
        assert steps[0]["params"] == {"command": "ls"}

    def test_repair_keeps_commas_inside_strings(self):
        """The repair pass must not rewrite the model's own text."""
        agent = self._make_agent()
        output = '<STEP>{"tool": "bash", "params": {"command": "ls a, ]",}}</STEP>'
        steps, raw = agent.parse_steps(output)
        assert steps[0]["params"]["command"] == "ls a, ]"

    def test_parse_steps_python_dict_repaired(self):
        agent = self._make_agent()
        steps, raw = agent.parse_steps("<STEP>{'tool': 'bash', 'params': {'command': 'ls'}}</STEP>")
        assert steps[0]["params"] == {"command": "ls"}

    def test_parse_steps_rejects_non_string_tool(self):
        """run_plan calls .lower() on the tool name — a number would crash it."""
        agent = self._make_agent()
        steps, raw = agent.parse_steps('<STEP>{"tool": 42, "params": {}}</STEP>')
        assert steps == []

    def test_parse_steps_stray_open_anchor(self):
        agent = self._make_agent()
        output = '<STEP>oops <STEP>{"tool": "bash", "params": {"command": "ls"}}</STEP>'
        steps, raw = agent.parse_steps(output)
        assert len(steps) == 1
        assert steps[0]["tool"] == "bash"

    def test_parse_steps_streamed_chunks(self):
        """Chunked input, anchors split across boundaries — same result, raw intact."""
        agent = self._make_agent()
        chunks = ['<ST', 'EP>{"tool": "bash", "params": {"command": "ls"}}</ST', 'EP>',
                  ' then <STEP>{"tool": "finish", "params": {"summary": "done"}}</STEP>']
        steps, raw = agent.parse_steps(iter(chunks))
        assert [s["tool"] for s in steps] == ["bash", "finish"]
        assert raw == ''.join(chunks)

    def test_parse_steps_is_linear(self):
        """Guard the scan against going quadratic again: the old per-character
        rescan took ~6s on this input, the linear one is milliseconds."""
        import time
        agent = self._make_agent()
        output = ('thinking out loud. ' * 6000) + '<STEP>{"tool": "finish", "params": {}}</STEP>'
        t = time.perf_counter()
        steps, raw = agent.parse_steps(output)
        elapsed = time.perf_counter() - t
        assert len(steps) == 1
        assert elapsed < 1.0, f"parse_steps took {elapsed:.2f}s on {len(output)} chars"

    # ── plan fallback: never leak anchors to the user ──

    def test_plan_unparseable_step_retries_not_answers(self):
        """A broken tool call must not end the run as the user-facing answer."""
        agent = self._make_agent()
        plan = agent.plan('Here is the plan:\n<PLAN>\n<STEP>{"tool": broken}</STEP>\n</PLAN>')
        assert len(plan) == 1
        assert plan[0]["tool"] == "invalid"
        assert plan[0].get("error")

    def test_plan_plain_text_answer_strips_anchors(self):
        agent = self._make_agent()
        plan = agent.plan('<PLAN>\nThe weather is sunny.\n</PLAN>')
        assert plan[0]["tool"] == "response"
        assert plan[0]["result"] == "The weather is sunny."

    # ── _extract_step ──

    def test_extract_step_valid(self):
        agent = self._make_agent()
        text = 'blah <STEP>{"tool": "bash", "params": {"command": "ls"}}</STEP> blah'
        step = agent._extract_step(text)
        assert step is not None
        assert step["tool"] == "bash"

    def test_extract_step_invalid_json(self):
        agent = self._make_agent()
        text = 'blah <STEP>{{broken</STEP> blah'
        step = agent._extract_step(text)
        assert step is None

    # ── run_plan ──

    def test_run_plan_executes_tools(self):
        agent = self._make_agent()
        plan = [
            {"tool": "bash", "params": {"command": "echo plan_test"}},
            {"tool": "finish", "params": {}},
        ]
        result = agent.run_plan(plan, safety=False)
        assert result[0]["result"]["success"]
        assert "plan_test" in result[0]["result"]["stdout"]

    def test_run_plan_stops_at_finish(self):
        agent = self._make_agent()
        plan = [
            {"tool": "finish", "params": {}},
            {"tool": "bash", "params": {"command": "echo should_not_run"}},
        ]
        result = agent.run_plan(plan, safety=False)
        assert "result" not in result[1]

    def test_run_plan_unknown_tool(self):
        agent = self._make_agent()
        plan = [{"tool": "nonexistent_tool_xyz", "params": {}}]
        result = agent.run_plan(plan, safety=False)
        assert "result" in result[0] or "error" in result[0]

    def test_run_plan_empty(self):
        agent = self._make_agent()
        result = agent.run_plan([], safety=False)
        assert result == []

    # ── repeat-call guard ──

    def test_run_plan_blocks_repeat_of_failed_call(self):
        """The same failing call runs twice, then gets its old result back —
        this is what kept the loop re-fetching a 403 URL until steps ran out."""
        agent = self._make_agent()
        agent._failed_calls = {}
        call = {"tool": "bash", "params": {"command": "exit 3"}}
        for _ in range(2):
            r = agent.run_plan([dict(call)], safety=False)
            assert not r[0]["result"]["success"]
            assert "error" not in r[0]
        r = agent.run_plan([dict(call)], safety=False)
        assert "repeat call blocked" in r[0]["error"]
        assert r[0]["result"]["code"] == 3       # the prior result rides along

    def test_run_plan_repeats_successful_calls(self):
        """Only failures are blocked — re-reading a file after an edit is a
        legitimate identical call with a different answer."""
        agent = self._make_agent()
        agent._failed_calls = {}
        call = {"tool": "bash", "params": {"command": "echo again"}}
        for _ in range(4):
            r = agent.run_plan([dict(call)], safety=False)
            assert r[0]["result"]["success"]

    def test_run_plan_different_params_not_blocked(self):
        agent = self._make_agent()
        agent._failed_calls = {}
        for cmd in ("exit 1", "exit 1", "exit 2"):
            r = agent.run_plan([{"tool": "bash", "params": {"command": cmd}}],
                               safety=False)
        assert "error" not in r[0]

    def test_step_failed_counts_self_reported_failure(self):
        """fetch answers 403 with success=False and never raises — the loop
        has to read that as a failure or the recovery hint never fires."""
        from src.mod import _step_failed
        assert _step_failed({"tool": "fetch", "result": {"success": False, "status": 403}})
        assert _step_failed({"tool": "read", "error": "boom"})
        assert _step_failed({"tool": "x", "result": {"error": "nope"}})
        assert not _step_failed({"tool": "bash", "result": {"success": True}})
        assert not _step_failed({"tool": "finish", "params": {}})

    # ── init_memory ──

    def test_init_memory(self):
        agent = self._make_agent()
        tools = agent.tool_schema()
        agent.init_memory(query="test query", path="/tmp", tools=tools)
        mem = agent.memory.get()
        assert mem["query"] == "test query"
        assert mem["goal"] == agent.goal
        assert "tools" in mem

    # ── e2e plan (simulated) ──

    def test_plan_execute(self):
        agent = self._make_agent()
        fake_output = '<PLAN>\n<STEP>{"tool": "bash", "params": {"command": "echo e2e"}}</STEP>\n</PLAN>'
        result = agent.plan(fake_output, safety=False)
        assert len(result) == 1
        assert result[0]["result"]["success"]

    # ── forced final answer ──

    def test_has_answer_variants(self):
        agent = self._make_agent()
        assert not agent._has_answer([[{"tool": "bash", "params": {}}]])
        assert not agent._has_answer([[{"tool": "finish", "params": {"summary": "  "}}]])
        assert agent._has_answer([[{"tool": "finish", "params": {"summary": "all done"}}]])
        assert agent._has_answer([[{"tool": "response", "params": {}, "result": "hi"}]])

    def test_force_answer_plain_text(self):
        agent = self._make_agent()
        agent._images = []
        agent.init_memory(query="q", path="/tmp", tools={})
        client = type("M", (), {"forward": staticmethod(lambda *a, **k: "the actual answer")})()
        step = agent._force_answer(client=client, short="openrouter", model="x",
                                   max_tokens=10, temperature=0.0, free=True)
        assert step["tool"] == "response"
        assert step["result"] == "the actual answer"

    def test_force_answer_honors_finish_step(self):
        agent = self._make_agent()
        agent._images = []
        agent.init_memory(query="q", path="/tmp", tools={})
        out = '<PLAN><STEP>{"tool": "finish", "params": {"summary": "final words"}}</STEP></PLAN>'
        client = type("M", (), {"forward": staticmethod(lambda *a, **k: out)})()
        step = agent._force_answer(client=client, short="openrouter", model="x",
                                   max_tokens=10, temperature=0.0, free=True)
        assert step["result"] == "final words"

    def test_force_answer_model_error_returns_none(self):
        agent = self._make_agent()
        agent._images = []
        agent.init_memory(query="q", path="/tmp", tools={})
        def boom(*a, **k):
            raise RuntimeError("provider down")
        client = type("M", (), {"forward": staticmethod(boom)})()
        assert agent._force_answer(client=client, short="openrouter", model="x",
                                   max_tokens=10, temperature=0.0, free=True) is None

    def test_plan_with_finish(self):
        agent = self._make_agent()
        fake_output = (
            '<PLAN>\n'
            '<STEP>{"tool": "bash", "params": {"command": "echo step1"}}</STEP>\n'
            '<STEP>{"tool": "finish", "params": {}}</STEP>\n'
            '</PLAN>'
        )
        result = agent.plan(fake_output, safety=False)
        assert len(result) == 2
        assert result[1]["tool"] == "finish"


# ═══════════════════════════════════════════════════════════════════════
#  INTEGRATION: write -> edit -> read -> grep pipeline
# ═══════════════════════════════════════════════════════════════════════

class TestToolPipeline:
    def test_full_pipeline(self, builtin, tmpdir):
        p = os.path.join(tmpdir, "pipeline.py")
        builtin.run("write", file_path=p, content="def hello():\n    return 'world'\n")
        r = builtin.run("glob", pattern="*.py", path=tmpdir)
        assert r["total"] == 1
        r = builtin.run("grep", pattern="def hello", path=tmpdir)
        assert r["total"] == 1
        r = builtin.run("read", file_path=p)
        assert "hello" in r["content"]
        r = builtin.run("edit", file_path=p, old_string="'world'", new_string="'earth'")
        assert r["success"]
        r = builtin.run("read", file_path=p)
        assert "'earth'" in r["content"]

    def test_multi_file_grep(self, builtin, tmpdir):
        for i in range(5):
            p = os.path.join(tmpdir, f"file{i}.py")
            content = f"TARGET_{i} = True\n" if i % 2 == 0 else f"other = False\n"
            builtin.run("write", file_path=p, content=content)
        r = builtin.run("grep", pattern="TARGET", path=tmpdir, file_pattern="*.py")
        assert r["success"]
        assert r["total"] == 3


# ═══════════════════════════════════════════════════════════════════════
#  CUSTOM TOOLS
# ═══════════════════════════════════════════════════════════════════════

class TestCustomTools:
    def _tools(self, tmpdir):
        from src.tools.mod import Tools
        return Tools(path=os.path.join(tmpdir, "tools.json")).custom

    def test_add_infers_params_from_template(self, tmpdir):
        t = self._tools(tmpdir)
        tool = t.add("loc", "wc -l {path}", description="Count lines")
        assert tool["params"] == {"path": {"type": "string", "required": True}}
        assert tool["kind"] == "custom"
        assert t.ls() == ["loc"]

    def test_schema_matches_builtin_shape(self, tmpdir):
        t = self._tools(tmpdir)
        t.add("loc", "wc -l {path}", description="Count lines")
        s = t.schema()["loc"]
        assert s["description"] == "Count lines"
        assert s["params"]["path"]["required"] is True

    def test_run_renders_and_executes(self, tmpdir):
        t = self._tools(tmpdir)
        t.add("say", "echo {msg}")
        r = t.run("say", msg="hello tools")
        assert r["success"] and "hello tools" in r["stdout"]

    def test_params_are_shell_quoted(self, tmpdir):
        t = self._tools(tmpdir)
        marker = os.path.join(tmpdir, "pwned")
        t.add("say", "echo {msg}")
        r = t.run("say", msg=f"x; touch {marker}")
        assert r["success"]
        assert not os.path.exists(marker), "a param must not open a second command"

    def test_missing_required_param_raises(self, tmpdir):
        t = self._tools(tmpdir)
        t.add("say", "echo {msg}")
        with pytest.raises(ValueError):
            t.run("say")

    def test_default_fills_in(self, tmpdir):
        t = self._tools(tmpdir)
        t.add("say", "echo {msg}",
              params={"msg": {"type": "string", "required": False, "default": "quiet"}})
        assert "quiet" in t.run("say")["stdout"]

    def test_builtin_names_are_reserved(self, tmpdir):
        t = self._tools(tmpdir)
        with pytest.raises(ValueError):
            t.add("bash", "echo no")
        with pytest.raises(ValueError):
            t.add("finish", "echo no")

    def test_bad_name_rejected(self, tmpdir):
        t = self._tools(tmpdir)
        with pytest.raises(ValueError):
            t.add("Bad Name!", "echo no")

    def test_command_required(self, tmpdir):
        t = self._tools(tmpdir)
        with pytest.raises(ValueError):
            t.add("empty", "   ")

    def test_persists_across_instances(self, tmpdir):
        self._tools(tmpdir).add("loc", "wc -l {path}")
        assert self._tools(tmpdir).ls() == ["loc"]

    def test_rm(self, tmpdir):
        t = self._tools(tmpdir)
        t.add("loc", "wc -l {path}")
        assert t.rm("loc")["existed"]
        assert t.ls() == []
        assert t.rm("loc")["existed"] is False

    def test_timeout_is_bounded(self, tmpdir):
        t = self._tools(tmpdir)
        t.add("slow", "sleep 5", timeout=1)
        r = t.run("slow")
        assert not r["success"] and "timeout" in r["stderr"]

    def test_registry_self_test(self, tmpdir):
        assert self._tools(tmpdir).test()["passed"]


# ═══════════════════════════════════════════════════════════════════════
#  MOD CLASS
# ═══════════════════════════════════════════════════════════════════════

class TestMod:
    def _make_mod(self):
        from src.mod import Mod, Agent
        mod = Mod.__new__(Mod)
        mod.agents = Agents()
        from src.toolbox.mod import Toolboxes
        mod.tools = Tools(path=TOOLS_PATH)
        mod.toolboxes = Toolboxes(tools=mod.tools)
        mod._snapped = []
        mod.memories = Memories()
        mod.memory = Memory()
        mod.memory.clear()
        mod.model = None
        mod._tool_names = None
        mod.api_port = 50117
        mod.app_port = 3117
        mod.src_dir = Path(os.path.join(os.path.dirname(__file__), '..', 'src'))
        mod.module_dir = Path(os.path.join(os.path.dirname(__file__), '..'))
        mod._owner = None  # no owner = unrestricted
        mod._portal_root = "/tmp/agent_test_portal"
        mod._acl_path = Path("/tmp/agent_test_acl.json")
        mod._acl = {}
        mod._public_actions = {'status', 'health', 'tools', 'schema',
                               'agents', 'agent', 'chains'}
        mod._admin_actions = {'run', 'plan', 'tool_run', 'serve', 'kill',
                              'test', 'grant', 'revoke', 'acl'}
        mod.key = None
        mod.auth = None
        mod.goal = Agent.goal
        mod.output_format = Agent.output_format
        mod.anchors = Agent.anchors
        return mod

    def test_mod_status(self):
        mod = self._make_mod()
        s = mod.status()
        assert s["module"] == "agent"
        assert s["ports"]["api"] == 50117
        assert s["ports"]["app"] == 3117
        assert "tools" in s
        assert len(s["tools"]) == BUILTIN_COUNT
        assert "agents" in s
        assert len(s["agents"]) >= AGENT_COUNT

    def test_mod_inherits_agent(self):
        mod = self._make_mod()
        assert hasattr(mod, "forward")
        assert hasattr(mod, "plan")
        assert hasattr(mod, "parse_steps")
        assert hasattr(mod, "run_plan")
        assert hasattr(mod, "run_tool")
        assert hasattr(mod, "tool_schema")

    def test_mod_forward_no_action(self):
        mod = self._make_mod()
        info = mod.forward()
        assert info["module"] == "agent"
        assert "actions" in info
        assert "run" in info["actions"]
        assert "grant" in info["actions"]

    def test_mod_forward_status(self):
        mod = self._make_mod()
        s = mod.forward("status")
        assert s["module"] == "agent"

    def test_mod_kill_returns_dict(self):
        mod = self._make_mod()
        r = mod.kill()
        assert isinstance(r, dict)
        assert "killed" in r

    def test_mod_description(self):
        from src.mod import Mod
        assert len(Mod.description) > 0


class TestCustomToolsOnAgent:
    """To the agent loop a custom tool is just another built-in."""

    def _mod(self):
        mod = TestMod()._make_mod()
        mod.tools.rm("t_loc")   # leftovers from a killed run would skew asserts
        return mod

    def test_schema_merges_custom_tools(self):
        mod = self._mod()
        mod.tools.add("t_loc", "wc -l {path}", description="lines")
        try:
            schema = mod.tool_schema()
            assert "t_loc" in schema and "bash" in schema
            assert schema["t_loc"]["custom"] is True
        finally:
            mod.tools.rm("t_loc")

    def test_snapped_toolbox_filters_custom_tools_too(self):
        mod = self._mod()
        mod.tools.add("t_loc", "wc -l {path}")
        try:
            mod.toolboxes.add("t_box", ["read", "t_loc"], "custom + shipped")
            mod.snap("t_box")
            assert set(mod.tool_schema()) == {"read", "t_loc"}
        finally:
            mod.unsnap()
            mod.toolboxes.rm("t_box")
            mod.tools.rm("t_loc")

    def test_run_plan_dispatches_to_a_custom_tool(self):
        mod = self._mod()
        mod.tools.add("t_loc", "echo {msg}")
        try:
            plan = mod.run_plan([{"tool": "t_loc", "params": {"msg": "from the loop"}}])
            assert "from the loop" in plan[0]["result"]["stdout"]
        finally:
            mod.tools.rm("t_loc")

    def test_status_lists_custom_tools(self):
        mod = self._mod()
        mod.tools.add("t_loc", "wc -l {path}")
        try:
            assert "t_loc" in mod.status()["custom_tools"]
        finally:
            mod.tools.rm("t_loc")


# ═══════════════════════════════════════════════════════════════════════
#  GATE / ACCESS CONTROL
# ═══════════════════════════════════════════════════════════════════════

class TestGate:
    def _make_mod_with_owner(self, owner="0xowner"):
        from src.mod import Mod, Agent
        mod = Mod.__new__(Mod)
        mod.agents = Agents()
        from src.toolbox.mod import Toolboxes
        mod.tools = Tools(path=TOOLS_PATH)
        mod.toolboxes = Toolboxes(tools=mod.tools)
        mod._snapped = []
        mod.memories = Memories()
        mod.memory = Memory()
        mod.memory.clear()
        mod.model = None
        mod._tool_names = None
        mod.api_port = 50117
        mod.app_port = 3117
        mod.src_dir = Path(os.path.join(os.path.dirname(__file__), '..', 'src'))
        mod.module_dir = Path(os.path.join(os.path.dirname(__file__), '..'))
        mod._owner = owner
        mod._portal_root = "/tmp/agent_test_portal"
        mod._acl_path = Path(tempfile.mktemp(suffix=".json"))
        mod._acl = {}
        mod._public_actions = {'status', 'health', 'tools', 'schema',
                               'agents', 'agent', 'chains'}
        mod._admin_actions = {'run', 'plan', 'tool_run', 'serve', 'kill',
                              'test', 'grant', 'revoke', 'acl'}
        mod.key = None
        mod.auth = None
        mod.goal = Agent.goal
        mod.output_format = Agent.output_format
        mod.anchors = Agent.anchors
        return mod

    def test_owner_can_access_everything(self):
        mod = self._make_mod_with_owner("0xowner")
        assert mod.is_allowed("0xowner", "run")
        assert mod.is_allowed("0xowner", "grant")
        assert mod.is_allowed("0xowner", "status")

    def test_public_actions_open_to_all(self):
        mod = self._make_mod_with_owner("0xowner")
        assert mod.is_allowed("0xrandom", "status")
        assert mod.is_allowed("0xrandom", "health")
        assert mod.is_allowed("0xrandom", "tools")
        assert mod.is_allowed("0xrandom", "schema")
        assert mod.is_allowed("0xrandom", "agents")

    def test_admin_actions_blocked_for_non_owner(self):
        mod = self._make_mod_with_owner("0xowner")
        assert not mod.is_allowed("0xrandom", "run")
        assert not mod.is_allowed("0xrandom", "tool_run")
        assert not mod.is_allowed("0xrandom", "grant")

    def test_forward_blocks_unauthorized_run(self):
        mod = self._make_mod_with_owner("0xowner")
        with pytest.raises(PermissionError, match="requires admin"):
            mod.forward("run", key="0xunauthorized", query="hack")

    def test_forward_allows_public_actions(self):
        mod = self._make_mod_with_owner("0xowner")
        # should not raise
        r = mod.forward("status", key="0xrandom")
        assert r["module"] == "agent"

    def test_grant_access(self):
        mod = self._make_mod_with_owner("0xowner")
        # owner grants access
        r = mod.forward("grant", key="0xowner", address="0xuser1", actions=["run", "tool_run"])
        assert r["granted"] == "0xuser1"
        assert r["actions"] == ["run", "tool_run"]
        # user1 can now run
        assert mod.is_allowed("0xuser1", "run")
        assert mod.is_allowed("0xuser1", "tool_run")
        # but not grant
        assert not mod.is_allowed("0xuser1", "grant")

    def test_grant_wildcard(self):
        mod = self._make_mod_with_owner("0xowner")
        mod.forward("grant", key="0xowner", address="0xadmin2", actions=["*"])
        assert mod.is_allowed("0xadmin2", "run")
        assert mod.is_allowed("0xadmin2", "grant")
        assert mod.is_allowed("0xadmin2", "kill")

    def test_revoke_access(self):
        mod = self._make_mod_with_owner("0xowner")
        mod.forward("grant", key="0xowner", address="0xuser1", actions=["run"])
        assert mod.is_allowed("0xuser1", "run")
        mod.forward("revoke", key="0xowner", address="0xuser1")
        assert not mod.is_allowed("0xuser1", "run")

    def test_non_owner_cannot_grant(self):
        mod = self._make_mod_with_owner("0xowner")
        with pytest.raises(PermissionError, match="requires admin"):
            mod.forward("grant", key="0xrandom", address="0xfriend")

    def test_non_owner_cannot_revoke(self):
        mod = self._make_mod_with_owner("0xowner")
        with pytest.raises(PermissionError, match="requires admin"):
            mod.forward("revoke", key="0xrandom", address="0xowner")

    def test_non_owner_cannot_view_acl(self):
        mod = self._make_mod_with_owner("0xowner")
        with pytest.raises(PermissionError, match="requires admin"):
            mod.forward("acl", key="0xrandom")

    def test_acl_shows_grants(self):
        mod = self._make_mod_with_owner("0xowner")
        mod.forward("grant", key="0xowner", address="0xuser1", actions=["run"])
        r = mod.forward("acl", key="0xowner")
        assert r["owner"] == "0xowner"
        assert "0xuser1" in r["grants"]
        assert "run" in r["grants"]["0xuser1"]["actions"]

    def test_acl_persists_to_disk(self):
        mod = self._make_mod_with_owner("0xowner")
        mod.forward("grant", key="0xowner", address="0xuser1", actions=["run", "tool_run"])
        # reload from disk
        mod._acl = mod._load_acl()
        assert "0xuser1" in mod._acl
        # cleanup
        if mod._acl_path.exists():
            mod._acl_path.unlink()

    def test_default_grant_actions(self):
        mod = self._make_mod_with_owner("0xowner")
        r = mod.forward("grant", key="0xowner", address="0xuser2")
        # default is ['run', 'tool_run']
        assert r["actions"] == ["run", "tool_run"]

    def test_revoke_nonexistent_is_safe(self):
        mod = self._make_mod_with_owner("0xowner")
        r = mod.forward("revoke", key="0xowner", address="0xnobody")
        assert r["was_granted"] is False

    # ── _run: prompt override + memory note injection ────────────────

    def _capture_run(self, mod):
        """Stub mod.run to record the run's goal and forwarded kwargs.

        The goal reaches the loop as a run argument, not by assignment on the
        module: one Mod serves every caller, so a persona swapped onto the
        object would outlive the run that asked for it."""
        captured = {}
        def fake_run(**kw):
            captured['goal'] = kw.get('goal') or mod.goal
            captured['kwargs'] = kw
            return []
        mod.run = fake_run
        return captured

    def test_run_prompt_overrides_goal(self):
        from src.mod import Agent
        mod = TestMod._make_mod(self)
        captured = self._capture_run(mod)
        mod._run(query="hi", prompt="You are a haiku bot.")
        assert captured['goal'] == "You are a haiku bot."
        # …and the module's own prompt was never touched
        assert mod.goal == Agent.goal

    def test_run_prompt_beats_agent_goal(self):
        mod = TestMod._make_mod(self)
        captured = self._capture_run(mod)
        mod._run(query="hi", agent_type="reviewer", prompt="CUSTOM")
        assert captured['goal'] == "CUSTOM"

    def test_run_memory_ids_inject_notes(self):
        from src.library.mod import Library
        mod = TestMod._make_mod(self)
        tmp = tempfile.mkdtemp()
        try:
            mod.library = Library(dir=tmp)
            n1 = mod.library.note_add("style", "prefer tabs")
            mod.library.note_add("other", "ignored note")
            captured = self._capture_run(mod)
            mod._run(query="hi", memory_ids=[n1["id"]])
            notes = captured['kwargs'].get('notes', '')
            assert "[style]" in notes and "prefer tabs" in notes
            assert "ignored note" not in notes
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_run_without_memory_ids_skips_library(self):
        mod = TestMod._make_mod(self)  # no mod.library set — must not be touched
        captured = self._capture_run(mod)
        mod._run(query="hi")
        assert 'notes' not in captured['kwargs']

    def test_run_forwards_images(self):
        mod = TestMod._make_mod(self)
        captured = self._capture_run(mod)
        mod._run(query="what is this", images=["data:image/png;base64,AAA"])
        assert captured['kwargs']['images'] == ["data:image/png;base64,AAA"]


class TestImageAttachments:
    """Pasted images ride to the model as a leading multimodal turn."""

    def _agent(self):
        from src.mod import Agent
        return Agent.__new__(Agent)   # no provider/model construction needed

    def test_image_turn_shape(self):
        a = self._agent()
        a._images = ["data:image/png;base64,AAA", "https://x/y.png"]
        turn = a._image_turn()
        assert len(turn) == 1 and turn[0]['role'] == 'user'
        parts = turn[0]['content']
        assert parts[0]['type'] == 'text'
        assert [p['image_url']['url'] for p in parts[1:]] == a._images

    def test_run_caps_and_cleans_images(self):
        from src.mod import Agent
        a = self._agent()
        a.model = object()
        a._provider = 'venice'
        # a run resolves its own client per provider (Agent._client)
        a._clients, a._client_why = {'venice': object()}, {}
        a._images = ['stale']
        a.tool_schema = lambda *_a, **_k: {}
        a.memory = type('M', (), {'compile': lambda self, q, **kw: None})()
        # the loop never starts: init_memory bails, so this exercises exactly
        # the normalization + the note the model is told about
        captured = {}
        def fake_init(**kw):
            captured['kw'] = kw
            raise RuntimeError('stop here')
        a.init_memory = fake_init
        with pytest.raises(RuntimeError):
            Agent.run(a, query='hi', images=['a', '', None, 'b'] + [f'x{i}' for i in range(10)])
        assert a._images == ['a', 'b'] + [f'x{i}' for i in range(6)]
        assert '8 image(s) attached' in captured['kw']['attachments']


# ═══════════════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

class TestApi:
    def _get_app(self):
        try:
            from src.api.api import app
            from fastapi.testclient import TestClient
            return TestClient(app)
        except ImportError:
            pytest.skip("fastapi not installed")

    def test_health(self):
        client = self._get_app()
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["module"] == "agent"

    def test_tools(self):
        client = self._get_app()
        r = client.get("/tools")
        assert r.status_code == 200
        data = r.json()
        names = [t["name"] for t in data["tools"]]
        assert len(names) >= BUILTIN_COUNT
        assert {"bash", "claudecode", "websurf"} <= set(names)
        # the fleet is not in the default listing, but its size is reported
        assert not any(n.startswith("mod.") for n in names)
        assert isinstance(data["fleet"], int)

    def test_tools_with_the_fleet(self):
        client = self._get_app()
        r = client.get("/tools", params={"mods": "true", "q": "git", "limit": 10})
        assert r.status_code == 200
        mods = [t for t in r.json()["tools"] if t["kind"] == "mod"]
        if not mods:
            pytest.skip("no mod protocol on this host")
        assert all(t["name"].startswith("mod.") for t in mods)
        assert set(mods[0]["params"]) == {"fn", "params"}

    def test_mod_tools_route(self):
        client = self._get_app()
        r = client.get("/tools/mods", params={"q": "chain", "limit": 5})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] <= 5
        assert all(e["kind"] == "mod" for e in data["mods"])

    def test_schema(self):
        client = self._get_app()
        r = client.get("/schema")
        assert r.status_code == 200
        data = r.json()
        assert "bash" in data
        assert "claudecode" in data
        assert "params" in data["bash"]

    def test_tool_run(self):
        client = self._get_app()
        r = client.post("/tools/bash/run", json={"name": "bash",
                                                 "params": {"command": "echo api_test"}})
        assert r.status_code == 200
        data = r.json()
        assert data["tool"] == "bash"
        assert data["result"]["success"]
        assert "api_test" in data["result"]["stdout"]

    def test_tool_run_unknown(self):
        client = self._get_app()
        r = client.post("/tools/nonexistent_xyz/run", json={"name": "nonexistent_xyz",
                                                            "params": {}})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_status(self):
        client = self._get_app()
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert "tools" in data
        assert len(data["tools"]) == BUILTIN_COUNT

    def test_agents_list(self):
        client = self._get_app()
        r = client.get("/agents")
        assert r.status_code == 200
        data = r.json()
        assert "agents" in data
        assert len(data["agents"]) >= AGENT_COUNT

    def test_agent_get(self):
        client = self._get_app()
        r = client.get("/agents/architect")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Architect"

    def test_agent_not_found(self):
        client = self._get_app()
        r = client.get("/agents/nonexistent_xyz")
        assert "error" in r.json()

    def test_chains(self):
        client = self._get_app()
        r = client.get("/chains")
        assert r.status_code == 200
        data = r.json()
        assert "debug-fix" in data

    def test_library(self):
        client = self._get_app()
        r = client.get("/library")
        assert r.status_code == 200
        data = r.json()
        assert "items" in data and "facets" in data
        kinds = data["facets"]["kinds"]
        # lower bound: tool docs installed from the aggregator index here too
        assert kinds.get("tool", 0) >= BUILTIN_COUNT
        assert kinds.get("agent", 0) >= AGENT_COUNT
        assert kinds.get("prompt", 0) >= 1  # seeded defaults

    def test_library_kind_filter(self):
        client = self._get_app()
        r = client.get("/library", params={"kind": "tool"})
        data = r.json()
        assert data["total"] >= BUILTIN_COUNT
        assert all(i["kind"] == "tool" for i in data["items"])
        builtin = [i for i in data["items"] if i.get("builtin")]
        assert len(builtin) == BUILTIN_COUNT

    def _as_host(self):
        """The live library, rebound so this test acts as the host."""
        from src.api.api import get_mod
        from src.identity import Identity
        from src.library.mod import Library
        lib = get_mod().library
        return Library(dir=str(lib.dir),
                       identity=Identity(host=lib.identity.host,
                                         is_host=lambda k: True))

    def test_prompt_roundtrip(self):
        """Saving takes a sign-in; a host-owned prompt is the host's to delete."""
        client = self._get_app()
        assert client.post("/prompts", json={
            "name": "api test prompt", "text": "do the api test thing",
            "tags": ["apitest"]}).json().get("code") == 403
        as_host = self._as_host()
        entry = as_host.prompt_add("api test prompt", "do the api test thing",
                                   tags=["apitest"])
        try:
            r = client.get("/library", params={"q": "api test prompt", "kind": "prompt"})
            item = next(i for i in r.json()["items"] if i["id"] == entry["id"])
            assert item["owner_source"] == "host"
            # tag facet includes the new tag
            assert "apitest" in r.json()["facets"]["tags"]
            # anonymous delete is refused — the host owns it
            assert client.delete(f"/prompts/{entry['id']}").json().get("code") == 403
        finally:
            # ...but the host can remove anything they own by default
            assert as_host.prompt_rm(entry["id"])["removed"] == entry["id"]
            assert all(p["id"] != entry["id"]
                       for p in client.get("/prompts").json()["prompts"])

    def test_memory_roundtrip(self):
        """Memory notes are owned like prompts — sign in to make one."""
        client = self._get_app()
        assert client.post("/memory", json={
            "name": "api test note", "content": "remember the api test",
            "tags": ["apitest"]}).json().get("code") == 403
        as_host = self._as_host()
        entry = as_host.note_add("api test note", "remember the api test",
                                 tags=["apitest"])
        try:
            r = client.get("/memory")
            note = next(n for n in r.json()["memory"] if n["id"] == entry["id"])
            assert note["owner_source"] == "host"
            r = client.get("/library", params={"kind": "memory", "tag": "apitest"})
            assert any(i["id"] == entry["id"] for i in r.json()["items"])
            # anonymous delete is refused — the note has an owner
            assert client.delete(f"/memory/{entry['id']}").json().get("code") == 403
        finally:
            assert as_host.note_rm(entry["id"])["removed"] == entry["id"]

    def test_agent_create_needs_sign_in(self):
        """No signed-in caller, no agent — there would be nobody to own it."""
        from src.api.api import get_mod
        client = self._get_app()
        name = "api-test-anon-agent"
        r = client.post("/agents", json={"name": name, "goal": "g"})
        assert r.json().get("code") == 403
        assert name not in get_mod().agents.ls()
        assert not (get_mod().agents._dir / name).exists()

    def test_prompt_validation(self):
        client = self._get_app()
        r = client.post("/prompts", json={"name": "x", "text": ""})
        assert "error" in r.json()

    def test_library_formats_serves_the_docs(self):
        """The console's upload panel renders docs/uploads.md from here."""
        client = self._get_app()
        d = client.get("/library/formats").json()
        assert set(d["kinds"]) == {"prompt", "tool", "memory", "agent"}
        assert d["doc"].startswith("# Upload your own")

    def test_upload_rejects_unusable_files(self):
        """Both upload doors reject before anything lands in the library."""
        client = self._get_app()
        assert "error" in client.post("/library/upload", json={"text": "  "}).json()
        assert "error" in client.post("/library/upload",
                                      json={"text": "hi", "kind": "nope"}).json()
        r = client.post("/library/upload/file",
                        files={"file": ("empty.md", b"", "text/markdown")})
        assert "error" in r.json()

    def test_import_rejects_a_junk_cid(self):
        client = self._get_app()
        assert "error" in client.post("/library/import", json={"cid": ""}).json()
        assert "error" in client.post("/agents/import", json={"cid": "not-a-cid"}).json()


# ═══════════════════════════════════════════════════════════════════════
#  LIBRARY (unified prompts / tool docs / memory / agents index)
# ═══════════════════════════════════════════════════════════════════════

class TestLibrary:
    def _lib(self, tmpdir, registries=False):
        from src.library.mod import Library
        if registries:
            return Library(tools=Tools(path=TOOLS_PATH), agents=Agents(), dir=tmpdir)
        return Library(dir=tmpdir)

    def test_prompts_seeded_once(self, tmpdir):
        from src.library.mod import SEED_PROMPTS
        lib = self._lib(tmpdir)
        assert len(lib.prompts()) == len(SEED_PROMPTS)
        # second read does not re-seed
        assert len(lib.prompts()) == len(SEED_PROMPTS)

    def test_prompt_add_upsert_rm(self, tmpdir):
        lib = self._lib(tmpdir)
        p = lib.prompt_add("mine", "do things", tags=["custom"])
        assert any(x["id"] == p["id"] for x in lib.prompts())
        # upsert by id replaces, doesn't duplicate
        before = len(lib.prompts())
        lib.prompt_add("mine v2", "do more things", id=p["id"])
        assert len(lib.prompts()) == before
        assert [x for x in lib.prompts() if x["id"] == p["id"]][0]["name"] == "mine v2"
        lib.prompt_rm(p["id"])
        assert not any(x["id"] == p["id"] for x in lib.prompts())
        with pytest.raises(KeyError):
            lib.prompt_rm(p["id"])

    def test_prompt_requires_name_and_text(self, tmpdir):
        lib = self._lib(tmpdir)
        with pytest.raises(ValueError):
            lib.prompt_add("", "text")
        with pytest.raises(ValueError):
            lib.prompt_add("name", "")

    def test_notes_crud(self, tmpdir):
        lib = self._lib(tmpdir)
        assert lib.notes() == []
        n = lib.note_add("conventions", "always run tests", tags=["project"])
        assert lib.notes()[0]["name"] == "conventions"
        lib.note_add("conventions v2", "always run tests twice", id=n["id"])
        assert len(lib.notes()) == 1
        lib.note_rm(n["id"])
        assert lib.notes() == []

    def test_items_aggregates_all_kinds(self, tmpdir):
        lib = self._lib(tmpdir, registries=True)
        lib.note_add("k", "v")
        out = lib.items()
        kinds = out["facets"]["kinds"]
        assert kinds["tool"] == BUILTIN_COUNT
        assert kinds["agent"] >= AGENT_COUNT
        assert kinds["prompt"] >= 1
        assert kinds["memory"] == 1

    def test_items_filters_compose(self, tmpdir):
        lib = self._lib(tmpdir, registries=True)
        lib.note_add("deploy runbook", "restart with pm2", tags=["ops"])
        # q filter searches name/description/body/tags across kinds
        out = lib.items(q="pm2")
        assert out["total"] == 1 and out["items"][0]["kind"] == "memory"
        # kind filter
        out = lib.items(kind="prompt")
        assert all(i["kind"] == "prompt" for i in out["items"])
        # tag filter within a kind
        out = lib.items(kind="memory", tag="ops")
        assert out["total"] == 1
        out = lib.items(kind="memory", tag="nope")
        assert out["total"] == 0
        # kind facet counts survive the kind filter (for pill counts)
        out = lib.items(kind="memory")
        assert out["facets"]["kinds"]["tool"] == BUILTIN_COUNT

    def test_forward_protocol(self, tmpdir):
        lib = self._lib(tmpdir)
        assert "prompts" in lib.forward("prompts")
        e = lib.forward("memory_add", name="a", content="b")
        assert e["id"].startswith("m-")
        assert lib.forward("memory")["memory"][0]["name"] == "a"
        lib.forward("memory_rm", id=e["id"])
        out = lib.forward(None, kind="prompt")
        assert "items" in out

    def test_selftest(self):
        from src.library.mod import Library
        assert Library().test() is True


# ═══════════════════════════════════════════════════════════════════════
#  UPLOADS  (bring your own prompt / tool doc / memory note / agent)
# ═══════════════════════════════════════════════════════════════════════

class TestUploadFormats:
    """formats.py — one parser behind every upload path."""

    def _parse(self, text, filename=None, kind=None):
        from src.library import formats
        return formats.parse(text, filename, kind)

    def test_frontmatter_split(self):
        from src.library.formats import split_frontmatter
        meta, body = split_frontmatter("---\nname: x\ntags: [a, b]\n---\nthe body\n")
        assert meta["name"] == "x" and meta["tags"] == ["a", "b"]
        assert body == "the body"

    def test_mini_yaml_without_pyyaml(self):
        from src.library.formats import _mini_yaml
        meta = _mini_yaml('name: x\ndescription: "quoted, comma"\ntools:\n  - bash\n  - git')
        assert meta["name"] == "x"
        assert meta["description"] == "quoted, comma"
        assert meta["tools"] == ["bash", "git"]

    def test_agent_from_markdown(self):
        item = self._parse(
            "---\ntype: agent\nname: Release Captain\ndescription: cuts releases\n"
            "icon: X\ntools: [bash, git]\nmodel: anthropic/claude-sonnet-4.5\n---\n"
            "You cut releases.", "whatever.md")
        assert item["kind"] == "agent"
        assert item["name"] == "release-captain"      # slugged for the registry
        assert item["label"] == "Release Captain"
        assert item["body"] == "You cut releases."
        assert item["tools"] == ["bash", "git"]
        assert item["icon"] == "X" and item["model"] == "anthropic/claude-sonnet-4.5"

    def test_agent_from_json(self):
        item = self._parse('{"type": "agent", "name": "x", "goal": "be brief"}')
        assert item["kind"] == "agent" and item["body"] == "be brief"

    def test_kind_from_filename(self):
        assert self._parse("# pdf\ninstructions", "skills/pdf/SKILL.md")["kind"] == "tool"
        assert self._parse("body", "notes/x.memory.md")["kind"] == "memory"
        assert self._parse("body", "x.agent.md")["kind"] == "agent"

    def test_kind_from_shape(self):
        assert self._parse("---\nname: a\ngoal: g\n---\n")["kind"] == "agent"
        assert self._parse("---\nname: a\nlicense: MIT\n---\nbody")["kind"] == "tool"

    def test_explicit_kind_wins(self):
        item = self._parse("---\ntype: agent\nname: a\n---\nbody", "a.agent.md",
                           kind="prompt")
        assert item["kind"] == "prompt"

    def test_plain_text_is_a_prompt_named_after_the_file(self):
        item = self._parse("just be brief", "brevity.txt")
        assert item["kind"] == "prompt" and item["name"] == "brevity"

    def test_bad_input_raises(self, ):
        import pytest as p
        from src.library import formats
        with p.raises(ValueError):
            formats.parse("   ")                       # empty file
        with p.raises(ValueError):
            formats.parse("{not json}", "x.json")      # broken json
        with p.raises(ValueError):
            formats.parse("body", None, kind="nope")   # unknown kind
        with p.raises(ValueError):
            formats.parse("---\nname: a\n---\n")       # nothing to install


class TestUpload:
    """library.upload / import_cid — the file lands in the right collection."""

    def _lib(self, tmpdir, agents=False):
        from src.library.mod import Library
        return Library(dir=tmpdir, agents=Agents() if agents else None)

    def test_upload_prompt(self, tmpdir):
        lib = self._lib(tmpdir)
        out = lib.upload("---\nname: brief\ntags: [style]\n---\nbe brief", "brief.prompt.md")
        assert out["kind"] == "prompt"
        saved = [p for p in lib.prompts() if p["name"] == "brief"][0]
        assert saved["text"] == "be brief" and saved["tags"] == ["style"]

    def test_upload_memory_note(self, tmpdir):
        lib = self._lib(tmpdir)
        out = lib.upload('{"type": "memory", "name": "conv", "content": "run tests"}',
                         "conv.json")
        assert out["kind"] == "memory"
        assert lib.notes()[0]["content"] == "run tests"

    def test_upload_tool_doc(self, tmpdir):
        lib = self._lib(tmpdir)
        out = lib.upload("---\nname: pdf\ndescription: PDFs\n---\n# PDF\nuse pdftk",
                         "skills/pdf/SKILL.md")
        assert out["kind"] == "tool"
        doc = lib.installed_tools()[0]
        assert doc["source"] == "upload" and doc["body"].startswith("# PDF")

    def test_upload_agent_installs_and_updates(self, tmpdir):
        lib = self._lib(tmpdir, agents=True)
        name = "upload-test-agent"
        md = (f"---\ntype: agent\nname: {name}\ndescription: a test\n"
              "tools: [bash]\n---\nBe brief.")
        try:
            out = lib.upload(md, "x.md")
            assert out["kind"] == "agent" and out["name"] == name
            assert out["item"]["tools"] == ["bash"]
            # re-uploading your own agent updates it in place
            again = lib.upload(md.replace("Be brief.", "Be terse."), "x.md")
            assert again["item"]["goal"] == "Be terse."
            assert name in lib._agents.ls()
        finally:
            shutil.rmtree(Path(__file__).parent.parent / "src" / "agents" / name,
                          ignore_errors=True)
            _drop_agent_cid(name)

    def test_upload_agent_needs_the_registry(self, tmpdir):
        lib = self._lib(tmpdir)
        with pytest.raises(RuntimeError):
            lib.upload("---\ntype: agent\nname: nope\n---\nbody", "x.md")

    def test_upload_too_large(self, tmpdir):
        lib = self._lib(tmpdir)
        with pytest.raises(ValueError):
            lib.upload("x" * (lib.MAX_UPLOAD_CHARS + 1), "big.md")

    def test_import_cid_routes_by_bundle_type(self, tmpdir):
        lib = self._lib(tmpdir)
        note = lib.note_add("shared", "content")
        if not note.get("cid"):
            pytest.skip("localfs unavailable")
        out = lib.import_cid(note["cid"])
        assert out["kind"] == "memory" and out["item"]["name"] == "shared"
        # asserting the wrong kind is refused
        with pytest.raises(ValueError):
            lib.import_cid(note["cid"], kind="agent")

    def test_forward_actions(self, tmpdir):
        lib = self._lib(tmpdir)
        out = lib.forward("upload", text="---\nname: f\n---\nbody", filename="f.md")
        assert out["kind"] == "prompt"
        spec = lib.forward("formats")
        assert "agent" in spec["kinds"] and "# Upload your own" in spec["doc"]


def _drop_agent_cid(name):
    """Keep a test's autopinned agent out of the committed CID index."""
    index = Path(__file__).parent.parent / "src" / "agents" / ".agent_cids.json"
    try:
        entries = json.loads(index.read_text())
    except Exception:
        return
    kept = [e for e in entries if e.get("name") != name]
    if len(kept) != len(entries):
        index.write_text(json.dumps(kept, indent=2))


class TestVault:
    """Encrypted per-provider API-key vault (AES-256-GCM under a user passphrase)."""

    KEY = "sk-or-v1-vaulttestkey1234567890"
    PASS = "correct horse battery"

    def _mod(self, tmpdir):
        from src.mod import Mod
        mod = Mod()
        mod._vault_dir = Path(tmpdir) / "vault"
        mod._session_keys = {}
        return mod

    def test_save_encrypted_and_unlocked(self, tmpdir):
        mod = self._mod(tmpdir)
        r = mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        assert r["encrypted"] is True
        assert r["unlocked"] is True
        assert self.KEY not in r.values()  # only masked forms leave the server
        # sealed file exists and never contains the plaintext key
        blob_path = mod._vault_path("openrouter")
        assert blob_path.exists()
        raw = blob_path.read_text()
        assert self.KEY not in raw
        blob = json.loads(raw)
        assert blob["cipher"] == "aes-256-gcm"
        assert blob["kdf"] == "pbkdf2-sha256"

    def test_key_info_states(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        info = mod.key_info("openrouter")
        assert info["encrypted"] and info["unlocked"] and info["source"] == "session"
        mod.vault_lock("openrouter")
        info = mod.key_info("openrouter")
        assert info["encrypted"] and not info["unlocked"]

    def test_lock_wipes_session_key(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        assert mod._session_keys["openrouter"] == self.KEY
        r = mod.vault_lock("openrouter")
        assert r["was_unlocked"] is True
        assert "openrouter" not in mod._session_keys

    def test_unlock_roundtrip(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        mod.vault_lock("openrouter")
        r = mod.vault_unlock("openrouter", self.PASS)
        assert r["unlocked"] is True
        assert mod._session_keys["openrouter"] == self.KEY

    def test_wrong_passphrase_rejected(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        mod.vault_lock("openrouter")
        with pytest.raises(PermissionError):
            mod.vault_unlock("openrouter", "wrong passphrase")
        assert "openrouter" not in mod._session_keys

    def test_unlock_without_vault(self, tmpdir):
        mod = self._mod(tmpdir)
        with pytest.raises(ValueError):
            mod.vault_unlock("openrouter", self.PASS)

    def test_vault_rm(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        r = mod.vault_rm("openrouter")
        assert r["removed"] is True
        assert mod._vault_read("openrouter") is None
        assert "openrouter" not in mod._session_keys

    def test_session_key_wins_priority(self, tmpdir):
        mod = self._mod(tmpdir)
        mod._session_keys["openrouter"] = "sk-or-v1-sessionwins000000000"
        assert mod._provider_keys("openrouter") == ["sk-or-v1-sessionwins000000000"]

    def test_short_passphrase_rejected(self, tmpdir):
        mod = self._mod(tmpdir)
        with pytest.raises(ValueError):
            mod.set_api_key(self.KEY, "openrouter", passphrase="abc")

    def test_venice_supported(self, tmpdir):
        mod = self._mod(tmpdir)
        info = mod.key_info("venice")
        assert info["supported"] is True
        # venice keys have no forced prefix but must not be trivially short
        with pytest.raises(ValueError):
            mod.set_api_key("short", "venice")
        r = mod.set_api_key("venice-key-abcdef123456", "venice", passphrase=self.PASS)
        assert r["encrypted"] and r["provider"] == "venice"

    def test_vaults_are_per_provider(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        mod.set_api_key("venice-key-abcdef123456", "venice", passphrase="other pass 42")
        mod.vault_lock("openrouter")
        assert mod.key_info("venice")["unlocked"]
        assert not mod.key_info("openrouter")["unlocked"]
        # openrouter passphrase must not open the venice vault
        mod.vault_lock("venice")
        with pytest.raises(PermissionError):
            mod.vault_unlock("venice", self.PASS)

    def test_unknown_provider_rejected(self, tmpdir):
        mod = self._mod(tmpdir)
        with pytest.raises(ValueError):
            mod.set_api_key("sk-whatever-123456789", "nope", passphrase=self.PASS)


class TestVaultRemember:
    """Stay-unlocked device seal: unlock once, survive restarts."""

    KEY = TestVault.KEY
    PASS = TestVault.PASS

    def _mod(self, tmpdir):
        from src.mod import Mod
        mod = Mod()
        mod._vault_dir = Path(tmpdir) / "vault"
        mod._session_keys = {}
        return mod

    def _restart(self, tmpdir):
        """A fresh Mod over the same vault dir — i.e. an API restart."""
        mod = self._mod(tmpdir)
        mod._vault_resume()
        return mod

    def test_remembered_by_default(self, tmpdir):
        mod = self._mod(tmpdir)
        r = mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        assert r["remembered"] is True
        p = mod._remember_path("openrouter")
        assert p.exists()
        assert self.KEY not in p.read_text()   # sealed, not stashed
        assert mod.key_info("openrouter")["remembered"] is True

    def test_survives_restart_without_passphrase(self, tmpdir):
        self._mod(tmpdir).set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        back = self._restart(tmpdir)
        assert back._session_keys["openrouter"] == self.KEY
        assert back.key_info("openrouter")["unlocked"] is True

    def test_opt_out_is_session_only(self, tmpdir):
        mod = self._mod(tmpdir)
        r = mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS, remember=False)
        assert r["remembered"] is False
        assert not mod._remember_path("openrouter").exists()
        assert self._restart(tmpdir)._session_keys == {}

    def test_unlock_remembers_too(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS, remember=False)
        mod.vault_lock("openrouter")
        assert mod.vault_unlock("openrouter", self.PASS)["remembered"] is True
        assert self._restart(tmpdir)._session_keys["openrouter"] == self.KEY

    def test_lock_ends_the_remembering(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        mod.vault_lock("openrouter")
        assert not mod._remember_path("openrouter").exists()
        back = self._restart(tmpdir)
        assert back._session_keys == {}
        assert back.key_info("openrouter")["unlocked"] is False

    def test_vault_rm_forgets_device_seal(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        mod.vault_rm("openrouter")
        assert not mod._remember_path("openrouter").exists()
        assert self._restart(tmpdir)._session_keys == {}

    def test_expired_seal_is_pruned(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        p = mod._remember_path("openrouter")
        blob = json.loads(p.read_text())
        blob["expires"] = time.time() - 1
        p.write_text(json.dumps(blob))
        assert mod._remember_read("openrouter") is None
        assert not p.exists()
        assert self._restart(tmpdir)._session_keys == {}

    def test_rotated_device_key_drops_stale_seal(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        (mod._vault_dir / '.device.key').write_bytes(os.urandom(32))
        back = self._restart(tmpdir)
        assert back._session_keys == {}
        assert not back._remember_path("openrouter").exists()

    def test_device_seal_never_holds_plaintext(self, tmpdir):
        mod = self._mod(tmpdir)
        mod.set_api_key(self.KEY, "openrouter", passphrase=self.PASS)
        for f in Path(mod._vault_dir).iterdir():
            assert self.KEY.encode() not in f.read_bytes()


# ═══════════════════════════════════════════════════════════════════════
#  TOOLBOXES (snap-on tool bundles)
# ═══════════════════════════════════════════════════════════════════════

class TestToolboxes:
    @pytest.fixture
    def boxes(self, tools, tmpdir):
        # the `tools` fixture, not the module-level fixture *function* — a box
        # validates its members against a live registry
        from src.toolbox.mod import Toolboxes
        return Toolboxes(tools=tools, path=os.path.join(tmpdir, "toolboxes.json"))

    def test_builtins_present(self, boxes):
        for name in ("core", "explore", "code", "verify", "vcs", "web", "meta"):
            assert name in boxes.ls()
            assert boxes.get(name).builtin

    def test_builtin_tools_all_exist(self, boxes, builtin):
        available = set(builtin.ls())
        for name in boxes.ls():
            box = boxes.get(name)
            assert set(box.tools) <= available, f"{name} references missing tools"

    def test_resolve_union_dedupes(self, boxes):
        union = boxes.resolve(["core", "code"])
        assert "bash" in union and "patch" in union
        assert len(union) == len(set(union))
        # order preserved: core's tools come first
        assert union.index("bash") < union.index("patch")

    def test_custom_box_persists(self, boxes, builtin, tmpdir):
        boxes.add("mybox", ["bash", "git"], "my custom loadout")
        assert "mybox" in boxes.ls()
        from src.toolbox.mod import Toolboxes
        reloaded = Toolboxes(tools=tools, path=os.path.join(tmpdir, "toolboxes.json"))
        assert reloaded.get("mybox").tools == ["bash", "git"]
        assert reloaded.rm("mybox")["existed"]

    def test_custom_box_validates_tools(self, boxes):
        with pytest.raises(ValueError):
            boxes.add("bad", ["not-a-tool"])

    def test_builtins_protected(self, boxes):
        with pytest.raises(PermissionError):
            boxes.add("core", ["bash"])
        with pytest.raises(PermissionError):
            boxes.rm("core")

    def test_schema_scoped_to_box(self, boxes):
        schema = boxes.schema(["vcs"])
        assert set(schema.keys()) == {"git", "diff"}

    def test_forward_protocol(self, boxes):
        info = boxes.forward()
        assert info["total"] == len(boxes.ls())
        assert boxes.forward("core")["name"] == "core"

    def test_builtin_test(self, boxes):
        assert boxes.test()["passed"]


class TestAgentSnap:
    """Toolboxes snap onto the agent and scope its live tool set."""

    @pytest.fixture
    def agent(self):
        from src.mod import Agent
        return Agent()

    def test_default_unfiltered(self, agent):
        assert agent.active_tools() is None
        assert len(agent.tool_schema()) == BUILTIN_COUNT

    def test_snap_scopes_schema(self, agent):
        agent.snap("vcs")
        state = agent.snapped()
        assert state["snapped"] == ["vcs"]
        assert state["filtered"]
        assert set(agent.tool_schema().keys()) == {"git", "diff"}

    def test_snap_union(self, agent):
        agent.snap("vcs")
        agent.snap("web")
        assert set(agent.active_tools()) == {"git", "diff", "fetch", "websurf"}

    def test_unsnap(self, agent):
        agent.snap("vcs")
        agent.snap("web")
        agent.unsnap("vcs")
        assert agent.snapped()["snapped"] == ["web"]
        agent.unsnap()
        assert agent.active_tools() is None
        assert len(agent.tool_schema()) == BUILTIN_COUNT

    def test_snap_unknown_raises(self, agent):
        with pytest.raises(KeyError):
            agent.snap("no-such-box")

    def test_explicit_tools_beat_snap(self, agent):
        agent.snap("vcs")
        agent._tool_names = ["bash"]
        assert agent.active_tools() == ["bash"]


class TestAgentSelect:
    """select() pins the loadout to an exact list — the console's per-tool switch."""

    @pytest.fixture
    def agent(self):
        from src.mod import Agent
        return Agent()

    def test_select_pins_exact_list(self, agent):
        state = agent.select(["bash", "read"])
        assert state["source"] == "selection" and state["filtered"]
        assert agent.active_tools() == ["bash", "read"]
        assert set(agent.tool_schema().keys()) == {"bash", "read"}

    def test_select_refines_a_snapped_box(self, agent):
        agent.snap("vcs")
        agent.select(["git"])
        state = agent.snapped()
        assert agent.active_tools() == ["git"]     # the pick wins…
        assert state["snapped"] == ["vcs"]          # …but the box stays visible

    def test_select_none_hands_back_to_the_box(self, agent):
        agent.snap("vcs")
        agent.select(["git"])
        state = agent.select(None)
        assert state["source"] == "toolboxes"
        assert set(agent.active_tools()) == {"git", "diff"}

    def test_empty_selection_is_a_reset(self, agent):
        agent.select(["bash"])
        assert agent.select([])["source"] == "all"
        assert agent.active_tools() is None

    def test_select_rejects_unknown_tools(self, agent):
        with pytest.raises(ValueError):
            agent.select(["bash", "not-a-tool"])

    def test_select_dedupes(self, agent):
        assert agent.select(["bash", "bash", "read"])["tools"] == ["bash", "read"]

    def test_snapping_a_box_clears_the_selection(self, agent):
        agent.select(["bash"])
        agent.snap("vcs")
        assert set(agent.active_tools()) == {"git", "diff"}

    def test_unsnap_all_clears_everything(self, agent):
        agent.snap("vcs")
        agent.select(["git"])
        state = agent.unsnap()
        assert state["source"] == "all" and not state["filtered"]
        assert agent.active_tools() is None

    def test_unfiltered_set_counts_custom_tools_too(self, agent):
        agent.tools.add("t_sel", "echo {x}")
        try:
            assert "t_sel" in agent.snapped()["tools"]
        finally:
            agent.tools.rm("t_sel")

    def test_select_can_pick_a_custom_tool(self, agent):
        agent.tools.add("t_sel", "echo {x}")
        try:
            agent.select(["bash", "t_sel"])
            assert set(agent.tool_schema().keys()) == {"bash", "t_sel"}
        finally:
            agent.tools.rm("t_sel")


# ═══════════════════════════════════════════════════════════════════════
#  MEMORY SUBSYSTEM (working / episodic / semantic layers)
# ═══════════════════════════════════════════════════════════════════════

class TestMemorySubsystem:
    @pytest.fixture
    def mem(self, tmpdir):
        return Memory(dir=tmpdir)

    def test_observe_appends_episode(self, mem):
        mem.observe({"tool": "bash", "params": {"command": "ls"}, "result": "ok"})
        trail = mem.episodes(5)
        assert trail[-1]["tool"] == "bash"
        assert trail[-1]["session"] == mem.session

    def test_episodes_persist_to_disk(self, mem, tmpdir):
        mem.observe({"tool": "read", "params": {"file_path": "/tmp/a.py"}})
        fresh = Memory(dir=tmpdir)
        assert fresh.episodes(5)[-1]["tool"] == "read"

    def test_observe_truncates_result(self, mem):
        mem.observe({"tool": "bash", "params": {}, "result": "x" * 5000})
        assert len(mem.episodes(1)[0]["result"]) <= 500

    def test_observe_tracks_files_and_errors(self, mem):
        mem.observe({"tool": "write", "params": {"file_path": "/tmp/w.py"}})
        mem.observe({"tool": "read", "params": {"file_path": "/tmp/r.py"}})
        mem.observe({"tool": "bash", "params": {}, "error": "boom"})
        assert "/tmp/w.py" in mem.get_files_written()
        assert "/tmp/r.py" in mem.get_files_read()
        assert "boom" in mem.get_errors()[0]

    def test_remember_recall(self, mem):
        mem.remember("db-schema", "users table has an email column", tags=["db"])
        mem.remember("style", "prefer tabs over spaces")
        hits = mem.recall("what column is in the users table?")
        assert hits and hits[0]["id"] == "db-schema"
        assert hits[0]["score"] > 0

    def test_facts_persist_to_disk(self, mem, tmpdir):
        mem.remember("port", "the api listens on 50117")
        fresh = Memory(dir=tmpdir)
        assert any(f["id"] == "port" for f in fresh.facts())
        assert fresh.forget("port")["existed"]

    def test_recall_empty_query(self, mem):
        assert mem.recall("") == []

    def test_compile_includes_recalled_facts(self, mem):
        mem.remember("style", "prefer tabs over spaces")
        block = mem.compile("what style tabs spaces?")
        assert "RECALLED FACTS" in block and "tabs" in block

    def test_compile_episode_trail(self, mem):
        mem.observe({"tool": "bash", "params": {"command": "ls"}})
        block = mem.compile(episodes=5)
        assert "RECENT STEPS" in block

    def test_status(self, mem):
        mem.add("k", "v")
        mem.observe({"tool": "think", "params": {}})
        mem.remember("f", "a fact")
        s = mem.status()
        assert s["working_keys"] == ["k"]
        assert s["episodes"] >= 1
        assert s["facts"] >= 1

    def test_forward_protocol(self, mem):
        info = mem.forward()
        assert info["module"] == "agent.memory"
        mem.forward("remember", name="x", content="y content")
        assert mem.forward("recall", query="y content")
        assert mem.forward("status")["facts"] >= 1

    def test_persist_false_stays_in_ram(self, tmpdir):
        mem = Memory(dir=tmpdir, persist=False)
        mem.observe({"tool": "bash", "params": {}})
        mem.remember("x", "y")
        assert not os.path.exists(os.path.join(tmpdir, "episodes.jsonl"))
        assert not os.path.exists(os.path.join(tmpdir, "facts.json"))

    def test_agent_emit_step_observes(self, tmpdir):
        from src.mod import Agent
        agent = Agent()
        agent.memory = Memory(dir=tmpdir)
        agent._emit_step({"tool": "bash", "params": {"command": "ls"}, "result": "ok"})
        assert agent.memory.episodes(1)[0]["tool"] == "bash"


# ═══════════════════════════════════════════════════════════════════════
#  DIALOGUE LAYER (what the user and the agent said to each other)
# ═══════════════════════════════════════════════════════════════════════

class TestDialogueMemory:
    """The memory module is where a conversation is stored and read back.

    Each console run is its own conversation, so continuity between them is
    this layer or nothing — and the scoping is the whole safety story: two
    visitors on one host must never be reminded of each other's chats.
    """

    @pytest.fixture
    def mem(self, tmpdir):
        return Memory(dir=tmpdir)

    def test_an_exchange_is_recorded_and_read_back(self, mem):
        mem.exchange("what port?", "8412", session="s1", who="0xabc", agent="default")
        turn = mem.exchanges(5, who="0xabc")[-1]
        assert turn["query"] == "what port?" and turn["answer"] == "8412"
        assert turn["agent"] == "default"

    def test_it_survives_a_restart(self, mem, tmpdir):
        mem.exchange("what port?", "8412", session="s1", who="0xabc")
        assert Memory(dir=tmpdir).exchanges(5, who="0xabc")[-1]["answer"] == "8412"

    def test_long_turns_are_clipped(self, mem):
        mem.exchange("q" * 9000, "a" * 9000, session="s1")
        turn = mem.exchanges(1, session="s1")[-1]
        assert len(turn["query"]) <= Memory.MAX_TURN
        assert len(turn["answer"]) <= Memory.MAX_TURN

    def test_a_signed_in_caller_is_remembered_across_conversations(self, mem):
        mem.exchange("my port is 8412", "noted", session="chat-1", who="0xabc")
        block = mem.compile("what was my port?", session="chat-2", who="0xabc")
        assert "8412" in block

    def test_one_visitor_never_reads_another(self, mem):
        mem.exchange("my secret is hunter2", "noted", session="s1", who="0xabc")
        assert "hunter2" not in mem.compile("secret?", session="s2", who="0xdef")
        # anonymous: their own session only, and nothing a signed-in caller said
        assert "hunter2" not in mem.compile("secret?", session="s1")

    def test_an_anonymous_session_still_has_continuity(self, mem):
        mem.exchange("my port is 8412", "noted", session="s1")
        assert "8412" in mem.compile("what port?", session="s1")

    def test_older_turns_come_back_by_keyword(self, mem):
        mem.exchange("the relay listens on 8412", "noted", session="s1", who="0xabc")
        for i in range(5):
            mem.exchange(f"unrelated question {i}", f"unrelated answer {i}",
                         session="s1", who="0xabc")
        block = mem.compile("remind me about the relay", session="s1", who="0xabc")
        assert "RELATED PAST TURNS" in block and "8412" in block

    def test_nothing_said_compiles_to_nothing(self, mem):
        assert mem.compile("anything?", session="s-empty") == ""

    def test_forward_speaks_the_layer(self, mem):
        mem.forward("exchange", query="q", answer="a", session="s1", who="0xabc")
        assert mem.forward("exchanges", n=5, who="0xabc")[-1]["answer"] == "a"
        assert mem.forward("status")["exchanges"] >= 1


class TestRunRemembersTheConversation:
    """The run loop's half of it: a session makes a run a remembered turn."""

    def test_a_finished_run_leaves_one_exchange(self, tmpdir):
        from src.mod import Mod
        mod = Mod()
        mod.memory = Memory(dir=tmpdir)
        history = [[{"tool": "read", "params": {}},
                    {"tool": "finish", "params": {"summary": "the port is 8412"}}]]
        mod._remember_exchange("what port?", history, session="s1", who="0xabc")
        assert mod.memory.exchanges(1, who="0xabc")[-1]["answer"] == "the port is 8412"

    def test_the_answer_is_the_last_thing_the_user_read(self, tmpdir):
        from src.mod import Mod
        assert Mod._answer_text([[{"tool": "finish", "params": {"summary": "first"}}],
                                 [{"tool": "response", "result": "last"}]]) == "last"
        # a blank finish is not an answer — the run has to have said something
        assert Mod._answer_text([[{"tool": "finish", "params": {"summary": "  "}}]]) == ""

    def test_a_run_that_never_answered_is_not_a_turn(self, tmpdir):
        from src.mod import Mod
        mod = Mod()
        mod.memory = Memory(dir=tmpdir)
        mod._remember_exchange("q", [[{"tool": "error", "params": {}, "error": "boom"}]],
                               session="s1", who="0xabc")
        assert mod.memory.exchanges(5, who="0xabc") == []


class TestToolboxMemoryApi:
    """API surface for toolboxes + the memory subsystem."""

    def _client(self):
        try:
            from src.api.api import app
            from fastapi.testclient import TestClient
            return TestClient(app)
        except ImportError:
            pytest.skip("fastapi not installed")

    def test_list_toolboxes(self):
        client = self._client()
        r = client.get("/toolboxes")
        assert r.status_code == 200
        data = r.json()
        names = [b["name"] for b in data["toolboxes"]]
        assert "core" in names and "vcs" in names
        assert "snapped" in data

    def test_get_toolbox(self):
        client = self._client()
        r = client.get("/toolboxes/vcs")
        data = r.json()
        assert data["name"] == "vcs"
        assert data["resolved"]["missing"] == []

    def test_get_unknown_toolbox(self):
        client = self._client()
        assert "error" in client.get("/toolboxes/nope").json()

    def test_memory_state(self):
        client = self._client()
        r = client.get("/memory/state")
        assert r.status_code == 200

    def test_memory_recall_route(self):
        client = self._client()
        r = client.get("/memory/recall", params={"q": "anything"})
        assert r.status_code == 200
        assert "facts" in r.json()

    def test_memory_episodes_route(self):
        client = self._client()
        r = client.get("/memory/episodes")
        assert r.status_code == 200
        assert "episodes" in r.json()

    def test_run_request_accepts_toolbox(self):
        from src.api.api import RunRequest
        req = RunRequest(query="hi", toolbox="core", toolboxes=["vcs"])
        assert req.toolbox == "core" and req.toolboxes == ["vcs"]

    # ── /tools: one registry the console can render ──

    def test_list_tools_includes_the_built_ins(self):
        client = self._client()
        r = client.get("/tools")
        assert r.status_code == 200
        data = r.json()
        names = [t["name"] for t in data["tools"]]
        assert "bash" in names
        bash = next(t for t in data["tools"] if t["name"] == "bash")
        assert bash["kind"] == "builtin" and bash["builtin"] and bash["active"]
        assert bash["description"] and "command" in bash["params"]
        assert "snapped" in data and "toolboxes" in data

    def test_tool_crud_roundtrip(self):
        client = self._client()
        client.delete("/tools/t_api")
        r = client.post("/tools", json={"name": "t_api", "command": "echo {msg}",
                                        "description": "api echo"}).json()
        assert r.get("name") == "t_api", r
        assert r["params"]["msg"]["required"]
        listed = client.get("/tools").json()["tools"]
        entry = next(t for t in listed if t["name"] == "t_api")
        assert entry["kind"] == "custom" and entry["builtin"] is False
        run = client.post("/tools/t_api/run", json={"params": {"msg": "hey"}}).json()
        assert "hey" in run["result"]["stdout"]
        assert client.delete("/tools/t_api").json()["existed"]

    def test_tool_cannot_shadow_a_built_in(self):
        client = self._client()
        r = client.post("/tools", json={"name": "bash", "command": "echo no"}).json()
        assert "error" in r

    # ── the loadout: which of those tools the model actually gets ──

    def test_select_route_pins_and_resets(self):
        client = self._client()
        try:
            state = client.post("/tools/select", json={"tools": ["bash", "read"]}).json()
            assert state["tools"] == ["bash", "read"] and state["source"] == "selection"
            listed = client.get("/tools").json()["tools"]
            assert next(t for t in listed if t["name"] == "bash")["active"]
            assert not next(t for t in listed if t["name"] == "git")["active"]
        finally:
            back = client.post("/tools/select", json={"tools": None}).json()
        assert back["source"] == "all" and back["filtered"] is False

    def test_select_route_rejects_unknown_tools(self):
        client = self._client()
        assert "error" in client.post("/tools/select", json={"tools": ["nope"]}).json()

    def test_snap_then_unsnap_all_route(self):
        client = self._client()
        try:
            state = client.post("/toolboxes/vcs/snap").json()
            assert state["snapped"] == ["vcs"] and set(state["tools"]) == {"git", "diff"}
        finally:
            state = client.post("/toolboxes/unsnap").json()
        assert state["snapped"] == [] and state["source"] == "all"

    def test_a_fleet_tool_can_join_the_loadout(self):
        client = self._client()
        fleet = client.get("/tools/mods", params={"limit": 1}).json()["mods"]
        if not fleet:
            pytest.skip("no mod protocol on this host")
        name = fleet[0]["name"]
        try:
            state = client.post("/tools/select", json={"tools": ["bash", name]}).json()
            assert state["tools"] == ["bash", name]
            # and it stays in the registry listing even with nothing matching it
            listed = client.get("/tools").json()["tools"]
            assert next(t for t in listed if t["name"] == name)["active"]
        finally:
            client.post("/tools/select", json={"tools": None})


# ═══════════════════════════════════════════════════════════════════════
#  CREDITS — prepaid USDT/USDC ledger for the public key
# ═══════════════════════════════════════════════════════════════════════

class TestCredits:
    ADDR = "0xAbC0000000000000000000000000000000000aBc"
    OWNER = "0x7d7c323496eD80E16d47b036607c586fB33dd123"

    @pytest.fixture
    def credits(self, tmpdir):
        from src.credits import Credits
        return Credits(str(tmpdir), deposit_address=self.OWNER)

    def test_info_shape(self, credits):
        info = credits.info(self.ADDR)
        assert info["enabled"] is True
        assert info["deposit"]["address"] == self.OWNER.lower()
        assert set(info["deposit"]["networks"]) == {"base", "ethereum"}
        assert info["account"]["balance"] == 0.0

    def test_credit_and_charge(self, credits):
        credits.credit(self.ADDR, 5, kind="grant")
        assert credits.balance(self.ADDR) == 5.0
        out = credits.charge_steps(self.ADDR, 12, note="run")
        assert out["charged"] == round(12 * credits.price_per_step, 6)
        assert out["balance"] == round(5 - out["charged"], 6)

    def test_charge_clamps_to_balance(self, credits):
        credits.credit(self.ADDR, 0.05, kind="grant")
        out = credits.charge_steps(self.ADDR, 10_000)
        assert out["charged"] == 0.05 and out["balance"] == 0.0
        # a drained account is never negative and further charges are free no-ops
        assert credits.charge_steps(self.ADDR, 10)["charged"] == 0.0

    def test_addresses_case_insensitive(self, credits):
        credits.credit(self.ADDR.lower(), 1)
        assert credits.balance(self.ADDR.upper().replace("0X", "0x")) == 1.0

    def test_ledger_persists(self, credits, tmpdir):
        from src.credits import Credits
        credits.credit(self.ADDR, 2.5)
        reloaded = Credits(str(tmpdir), deposit_address=self.OWNER)
        assert reloaded.balance(self.ADDR) == 2.5
        assert reloaded.info(self.ADDR)["account"]["history"][0]["type"] == "deposit"

    def test_deposit_rejects_bad_input(self, credits):
        with pytest.raises(ValueError):
            credits.verify_deposit("0x123", "base")            # not a tx hash
        with pytest.raises(ValueError):
            credits.verify_deposit("0x" + "a" * 64, "polygon")  # unsupported network

    def test_deposit_replay_guard(self, credits):
        tx = "0x" + "b" * 64
        credits._state["txs"][tx] = {"amount": 1}
        with pytest.raises(ValueError, match="already credited"):
            credits.verify_deposit(tx, "base")

    def test_deposits_disabled_without_address(self, tmpdir):
        from src.credits import Credits
        import os
        env = os.environ.pop("AGENT_DEPOSIT_ADDRESS", None)
        try:
            c = Credits(str(tmpdir))
            assert c.info()["enabled"] is False
            with pytest.raises(ValueError, match="disabled"):
                c.verify_deposit("0x" + "c" * 64, "base")
        finally:
            if env is not None:
                os.environ["AGENT_DEPOSIT_ADDRESS"] = env

    def test_balance_unlocks_run_gate(self, tmpdir):
        """A positive credit balance lets a guest run; a drained one doesn't."""
        from src.credits import Credits

        class Gate:
            from src.mod import Mod
            _acl = {}
            _owner = TestCredits.OWNER.lower()
            auth = None
            key = None
            _public_actions = set()
            is_owner = Mod.is_owner
            _resolve_address = Mod._resolve_address
            is_allowed = Mod.is_allowed

        gate = Gate()
        gate.credits = Credits(str(tmpdir), deposit_address=self.OWNER)
        assert gate.is_allowed(self.ADDR, "run") is False
        gate.credits.credit(self.ADDR, 1)
        assert gate.is_allowed(self.ADDR, "run") is True
        gate.credits.charge_steps(self.ADDR, 10_000)
        assert gate.is_allowed(self.ADDR, "run") is False


# ═══════════════════════════════════════════════════════════════════════
#  BILLING — metered provider cost, margin, and the treasury books
# ═══════════════════════════════════════════════════════════════════════

class TestMeter:
    """The meter prices a call from the provider's own catalog — both
    catalog shapes, and both call shapes (a string and a stream)."""

    class FakeModel:
        """Stands in for a provider module: just the catalog we price from."""
        def __init__(self, catalog):
            self._catalog = catalog

        def model2info(self):
            return self._catalog

    OPENROUTER = {"anthropic/claude-opus-5": {
        "pricing": {"prompt": "0.000005", "completion": "0.000025"}}}
    VENICE = {"deepseek-v3.2": {
        "model_spec": {"pricing": {"input": {"usd": 2.0}, "output": {"usd": 10.0}}}}}

    def _meter(self):
        from src.billing import Meter
        return Meter()

    def test_openrouter_rates_are_per_token(self):
        meter = self._meter()
        model = self.FakeModel(self.OPENROUTER)
        # 4000 chars -> 1000 tokens in, 400 chars -> 100 tokens out
        cost = meter.price(model, "openrouter", "anthropic/claude-opus-5", 1000, 100)
        assert cost == round(1000 * 5e-6 + 100 * 25e-6, 8)

    def test_venice_rates_are_per_million_tokens(self):
        meter = self._meter()
        model = self.FakeModel(self.VENICE)
        cost = meter.price(model, "venice", "deepseek-v3.2", 1_000_000, 1_000_000)
        assert cost == 12.0

    def test_unknown_model_is_unpriced(self):
        meter = self._meter()
        assert meter.price(self.FakeModel({}), "openrouter", "who/knows", 10, 10) is None

    def test_stream_is_counted_as_it_passes(self):
        meter = self._meter()
        model = self.FakeModel(self.OPENROUTER)
        meter.open(provider="openrouter", model="anthropic/claude-opus-5")
        chunks = ["a" * 100, "b" * 300]     # 400 chars out = 100 tokens
        out = meter.watch(iter(chunks), model_obj=model, provider="openrouter",
                          model="anthropic/claude-opus-5", prompt="x" * 4000)
        assert list(out) == chunks          # the stream itself is untouched
        usage = meter.take()
        assert usage["calls"] == 1 and usage["priced"] is True
        assert usage["prompt_tokens"] == 1000 and usage["completion_tokens"] == 100
        assert usage["cost"] == round(1000 * 5e-6 + 100 * 25e-6, 8)

    def test_multiplier_scales_the_estimate(self):
        from src.billing import Meter
        meter = Meter(multiplier=2.0)
        model = self.FakeModel(self.OPENROUTER)
        assert meter.price(model, "openrouter", "anthropic/claude-opus-5", 1000, 0) \
            == round(2 * 1000 * 5e-6, 8)

    def test_tally_accumulates_across_calls_then_clears(self):
        meter = self._meter()
        model = self.FakeModel(self.OPENROUTER)
        meter.open(provider="openrouter", model="anthropic/claude-opus-5")
        for _ in range(3):
            # open() again mid-run (a chain stage) must not reset the total
            meter.open(provider="openrouter", model="anthropic/claude-opus-5")
            meter.watch("z" * 400, model_obj=model, provider="openrouter",
                        model="anthropic/claude-opus-5", prompt="")
        usage = meter.take()
        assert usage["calls"] == 3 and usage["completion_tokens"] == 300
        assert meter.take()["calls"] == 0          # taking clears the thread

    def test_tallies_are_per_thread(self):
        import threading
        meter = self._meter()
        model = self.FakeModel(self.OPENROUTER)
        seen = {}

        def run(name, chars):
            meter.open(provider="openrouter", model="anthropic/claude-opus-5")
            meter.watch("z" * chars, model_obj=model, provider="openrouter",
                        model="anthropic/claude-opus-5", prompt="")
            seen[name] = meter.take()

        threads = [threading.Thread(target=run, args=(n, c))
                   for n, c in (("a", 400), ("b", 4000))]
        for t in threads: t.start()
        for t in threads: t.join()
        assert seen["a"]["completion_tokens"] == 100
        assert seen["b"]["completion_tokens"] == 1000


class TestTreasury:
    """Deposits fund the provider keys; the margin is what we keep."""

    ADDR = "0xAbC0000000000000000000000000000000000aBc"
    OWNER = "0x7d7c323496eD80E16d47b036607c586fB33dd123"

    @pytest.fixture
    def credits(self, tmpdir):
        from src.credits import Credits
        c = Credits(str(tmpdir), deposit_address=self.OWNER)
        c.set_config(fee_rate=0.05)
        return c

    def test_quote_adds_the_margin(self, credits):
        q = credits.quote(1.0)
        assert q == {"cost": 1.0, "fee": 0.05, "total": 1.05}

    def test_usage_charge_splits_cost_and_fee(self, credits):
        credits.credit(self.ADDR, 10, kind="grant")
        out = credits.charge_usage(self.ADDR, 2.0, model="anthropic/claude-opus-5")
        assert out["charged"] == 2.1 and out["cost"] == 2.0 and out["fee"] == 0.1
        assert credits.balance(self.ADDR) == 7.9
        book = credits.treasury()
        assert book["provider_cost"] == 2.0 and book["fees"] == 0.1
        assert book["revenue"] == 2.1

    def test_fee_rate_is_tunable(self, credits):
        credits.set_config(fee_rate=0.2)
        credits.credit(self.ADDR, 10, kind="grant")
        out = credits.charge_usage(self.ADDR, 1.0)
        assert out["charged"] == 1.2 and out["fee"] == 0.2
        assert credits.set_config(fee_rate=0)["fee_rate"] == 0
        assert credits.charge_usage(self.ADDR, 1.0)["fee"] == 0.0

    def test_fee_rate_is_bounded(self, credits):
        with pytest.raises(ValueError):
            credits.set_config(fee_rate=-1)

    def test_clamped_charge_still_splits(self, credits):
        credits.credit(self.ADDR, 0.21, kind="grant")
        out = credits.charge_usage(self.ADDR, 10.0)      # quoted 10.50, only 0.21 there
        assert out["charged"] == 0.21 and out["quoted"] == 10.5
        assert round(out["cost"] + out["fee"], 6) == 0.21
        assert credits.balance(self.ADDR) == 0.0

    def test_step_fallback_is_booked_the_same_way(self, credits):
        credits.credit(self.ADDR, 5, kind="grant")
        out = credits.charge_steps(self.ADDR, 10)
        assert out["basis"] == "steps" and out["charged"] == round(10 * credits.price_per_step, 6)
        assert round(out["cost"] * (1 + credits.fee_rate), 6) == out["charged"]

    def test_deposits_and_grants_book_separately(self, credits):
        credits.credit(self.ADDR, 3, kind="deposit")
        credits.credit(self.ADDR, 2, kind="grant")
        book = credits.treasury()
        assert book["deposits"] == 3.0 and book["grants"] == 2.0

    def test_topup_needed_is_credits_at_cost_minus_provider_balance(self, credits):
        credits.credit(self.ADDR, 10.5, kind="deposit")
        book = credits.treasury({"openrouter": {"balance": 4.0},
                                 "venice": {"balance": 1.0}})
        assert book["user_credits"] == 10.5
        assert book["funding_required"] == 10.0        # 10.50 / 1.05
        assert book["provider_balance"] == 5.0
        assert book["topup_needed"] == 5.0

    def test_topup_is_recorded_and_funds_the_float(self, credits):
        credits.credit(self.ADDR, 10, kind="deposit")
        credits.record_topup("openrouter", 6, ref="inv-1")
        book = credits.treasury()
        assert book["topups"]["openrouter"] == 6.0 and book["topups_total"] == 6.0
        assert book["float"] == 4.0
        assert book["ledger"][0]["type"] == "topup" and book["ledger"][0]["ref"] == "inv-1"

    def test_topup_rejects_unknown_provider(self, credits):
        with pytest.raises(ValueError, match="unknown provider"):
            credits.record_topup("anthropic", 5)

    def test_withdrawal_is_capped_at_earned_margin(self, credits):
        credits.credit(self.ADDR, 10, kind="grant")
        credits.charge_usage(self.ADDR, 2.0)             # earns 0.10
        with pytest.raises(ValueError, match="margin"):
            credits.record_withdrawal(1.0)
        assert credits.record_withdrawal(0.1)["fees_available"] == 0.0

    def test_drift_baselines_on_first_live_read(self, credits):
        credits.credit(self.ADDR, 10, kind="grant")
        credits.treasury({"openrouter": {"balance": 5.0, "usage": 100.0}})
        credits.charge_usage(self.ADDR, 1.0)             # we billed 1.00 of cost
        book = credits.treasury({"openrouter": {"balance": 4.0, "usage": 101.5}})
        metered = book["providers"]["openrouter"]["metered"]
        assert metered["actual"] == 1.5 and metered["billed"] == 1.0
        assert metered["ratio"] == 1.5                   # the estimate under-billed

    def test_budget_stops_a_run_that_outspends_its_credits(self):
        """A charge is clamped to the balance, so the loop has to stop itself —
        otherwise a dust account could burn an Opus run on the module's key."""
        from src.mod import Agent
        from src.billing import Meter
        from src.memory.mod import Memory
        from src.tools.mod import Tools
        from src.toolbox.mod import Toolboxes
        from src.agents.mod import Agents

        agent = Agent.__new__(Agent)
        agent.agents, agent.memory = Agents(), Memory()
        agent.memory.clear()
        agent.tools = Tools()
        agent.toolboxes = Toolboxes(tools=agent.tools)
        agent._tool_names, agent._snapped, agent._session_keys = None, [], {}
        agent.goal, agent.output_format = Agent.goal, Agent.output_format
        agent.anchors = Agent.anchors
        agent._provider = Agent.PROVIDERS['openrouter']
        agent.meter = Meter()
        # a model that keeps calling a tool — only the budget can end this run
        agent.model = type('M', (), {'forward': staticmethod(
            lambda *a, **k: '<STEP>{"tool": "think", "params": {"thought": "hm"}}</STEP>')})()
        # the loop takes its client from the per-provider cache, not .model
        agent._clients = {Agent.PROVIDERS['openrouter']: agent.model}
        agent._client_why = {}
        seen = []
        def budget(cost):
            seen.append(cost)
            return len(seen) < 3        # affordable for two steps, then not

        steps = agent.run(query='burn credits', steps=10, model='x', budget=budget)
        assert len(seen) == 3           # consulted after every executed step
        assert steps[-1]['tool'] == 'error' and 'top up' in steps[-1]['error']

    def test_books_persist(self, credits, tmpdir):
        from src.credits import Credits
        credits.credit(self.ADDR, 10, kind="deposit")
        credits.charge_usage(self.ADDR, 1.0)
        credits.record_topup("venice", 2)
        book = Credits(str(tmpdir), deposit_address=self.OWNER).treasury()
        assert book["deposits"] == 10.0 and book["fees"] == 0.05
        assert book["topups"]["venice"] == 2.0


class TestTopupVerification:
    """A top-up is read back off the provider key, not typed from memory.

    Neither provider sells credits over an API, so the purchase happens on
    their page; what the module can do is see it land and book exactly that.
    """

    ADDR = "0xAbC0000000000000000000000000000000000aBc"
    OWNER = "0x7d7c323496eD80E16d47b036607c586fB33dd123"

    @pytest.fixture
    def credits(self, tmpdir):
        from src.credits import Credits
        return Credits(str(tmpdir), deposit_address=self.OWNER)

    def test_first_sight_of_a_key_books_nothing(self, credits):
        """Credits bought before we ever looked aren't ours to claim."""
        out = credits.verify_topup("openrouter", {"purchased": 477.0, "balance": -0.15})
        assert out["booked"] == 0.0 and out["mark"] == 477.0
        assert credits.treasury()["topups"].get("openrouter", 0) == 0

    def test_a_purchase_on_the_key_is_booked_exactly(self, credits):
        credits.verify_topup("openrouter", {"purchased": 477.0})
        out = credits.verify_topup("openrouter", {"purchased": 527.0, "balance": 49.85})
        assert out["booked"] == 50.0 and out["pending"] == 0.0
        book = credits.treasury()
        assert book["topups"]["openrouter"] == 50.0
        entry = book["ledger"][0]
        assert entry["verified"] and entry["amount"] == 50.0
        assert "477.0 \u2192 527.0" in entry["ref"]

    def test_the_same_purchase_is_never_booked_twice(self, credits):
        credits.verify_topup("openrouter", {"purchased": 477.0})
        credits.verify_topup("openrouter", {"purchased": 527.0})
        again = credits.verify_topup("openrouter", {"purchased": 527.0})
        assert again["booked"] == 0.0 and "nothing new" in again["reason"]
        assert credits.treasury()["topups"]["openrouter"] == 50.0

    def test_usage_alone_is_not_a_top_up(self, credits):
        """OpenRouter's meter counts credits bought, so spending can't move it."""
        credits.verify_topup("openrouter", {"purchased": 477.0, "balance": 20.0})
        out = credits.verify_topup("openrouter", {"purchased": 477.0, "balance": 3.0})
        assert out["booked"] == 0.0

    def test_treasury_surfaces_a_purchase_waiting_to_be_booked(self, credits):
        credits.treasury({"openrouter": {"purchased": 477.0, "balance": 0.0}})
        book = credits.treasury({"openrouter": {"purchased": 502.0, "balance": 25.0}})
        assert book["topup_pending"] == 25.0
        top = book["providers"]["openrouter"]["topup"]
        assert top["pending"] == 25.0 and top["exact"] is True
        assert top["url"] == "https://openrouter.ai/settings/credits"

    def test_venice_mark_follows_the_balance_down(self, credits):
        """Venice reports only what is left, so the mark trails spending —
        otherwise a top-up after a spend would be booked short."""
        credits.treasury({"venice": {"balance": 10.0}})
        credits.treasury({"venice": {"balance": 4.0}})       # spent 6
        out = credits.verify_topup("venice", {"balance": 24.0})
        assert out["booked"] == 20.0 and out["exact"] is False

    def test_a_hand_logged_topup_is_not_booked_again_on_verify(self, credits):
        credits.treasury({"openrouter": {"purchased": 100.0, "balance": 0.0}})
        credits.record_topup("openrouter", 25, ref="receipt-1")
        out = credits.verify_topup("openrouter", {"purchased": 125.0, "balance": 25.0})
        assert out["booked"] == 0.0
        assert credits.treasury()["topups"]["openrouter"] == 25.0

    def test_an_unreadable_key_books_nothing_and_says_why(self, credits):
        out = credits.verify_topup("openrouter", {"error": "no API key configured"})
        assert out["booked"] == 0.0 and out["reason"] == "no API key configured"

    def test_the_module_reads_the_purchase_off_its_own_key(self, tmpdir):
        """End to end through the mod: balance() -> meter -> booked top-up."""
        from src.mod import Mod
        from src.credits import Credits
        mod = Mod.__new__(Mod)
        mod._owner = None                     # no owner set: is_owner passes
        mod.credits = Credits(str(tmpdir))
        # the key as it stands: 477 bought, all of it spent
        mod.balance = lambda provider='openrouter': {'balance': -0.15, 'total_credits': 477.0}
        assert mod.credit_topup_verify('openrouter')['booked'] == 0.0
        # ...and after $25 is bought on openrouter.ai
        mod.balance = lambda provider='openrouter': {'balance': 24.85, 'total_credits': 502.0}
        out = mod.credit_topup_verify('openrouter')
        assert out['booked'] == 25.0 and out['balance'] == 24.85

    def test_unknown_provider_is_rejected(self, credits):
        with pytest.raises(ValueError, match="unknown provider"):
            credits.verify_topup("anthropic", {"balance": 5.0})


# ═══════════════════════════════════════════════════════════════════════
#  DISCOVER — the internet-wide tool aggregator
# ═══════════════════════════════════════════════════════════════════════

class TestDiscover:
    """Offline-only: every source adapter is stubbed, so the suite never
    depends on GitHub/npm being reachable or on rate-limit headroom."""

    def _d(self, tmpdir):
        from src.discover.mod import Discover
        return Discover(dir=str(tmpdir))

    def test_self_test(self, tmpdir):
        assert self._d(tmpdir).test() is True

    def test_sources_catalog(self, tmpdir):
        from src.discover.mod import SOURCE_IDS
        srcs = self._d(tmpdir).sources()
        assert [s["id"] for s in srcs] == SOURCE_IDS
        assert all(s.get("label") and s.get("about") for s in srcs)

    def test_frontmatter_parsing(self, tmpdir):
        from src.discover.mod import parse_frontmatter
        fm = parse_frontmatter(
            "---\nname: pdf-tools\ndescription: Work with PDFs\n"
            "tags:\n  - docs\n  - files\n---\n\n# Body\n")
        assert fm["name"] == "pdf-tools"
        assert fm["tags"] == ["docs", "files"]
        # no frontmatter, and malformed frontmatter, both degrade quietly
        assert parse_frontmatter("# Just a readme") == {}
        assert parse_frontmatter("") == {}

    def test_scan_merges_and_ranks(self, tmpdir):
        d = self._d(tmpdir)
        d.src_github = lambda q, l: [
            {"id": "gh:a/pdf-skill", "source": "github", "kind": "tool",
             "name": "pdf-skill", "description": "PDF things",
             "repo": "https://github.com/a/pdf-skill", "stars": 120, "tags": []},
            {"id": "gh:b/unrelated", "source": "github", "kind": "tool",
             "name": "unrelated", "description": "nothing to do with it",
             "repo": "https://github.com/b/unrelated", "stars": 90000, "tags": []},
        ]
        d.src_npm = lambda q, l: [
            {"id": "npm:pdf-skill", "source": "npm", "kind": "package",
             "name": "pdf-skill", "description": "npm copy",
             "repo": "https://github.com/A/pdf-skill.git", "tags": []},
        ]
        out = d.search("pdf", sources=["github", "npm"], limit=10)
        # the same repo from two registries collapses into one card
        assert out["total"] == 2
        # relevance beats a 90k-star repo that merely matched full text
        assert out["items"][0]["name"] == "pdf-skill"
        assert "npm" in (out["items"][0].get("also") or [])
        # the merged card keeps the winner's kind — the npm duplicate is gone
        assert out["facets"]["kinds"] == {"tool": 2}

    def test_kind_filter(self, tmpdir):
        d = self._d(tmpdir)
        d.src_mcp = lambda q, l: [
            {"id": "mcp:x", "source": "mcp", "kind": "mcp", "name": "x",
             "description": "", "repo": "", "tags": []}]
        assert d.search("", sources=["mcp"], kind="tool")["total"] == 0
        assert d.search("", sources=["mcp"], kind="mcp")["total"] == 1

    def test_dead_source_degrades_to_partial_results(self, tmpdir):
        d = self._d(tmpdir)
        d.src_github = lambda q, l: (_ for _ in ()).throw(RuntimeError("rate limit"))
        d.src_npm = lambda q, l: [
            {"id": "npm:ok", "source": "npm", "kind": "package", "name": "ok",
             "description": "still here", "repo": "", "tags": []}]
        out = d.search("x", sources=["github", "npm"], limit=5)
        assert out["total"] == 1                       # npm survived
        assert "rate limit" in out["errors"]["github"]
        assert out["sources"]["github"]["error"]

    def test_cache_serves_repeat_scans(self, tmpdir):
        d = self._d(tmpdir)
        calls = []

        def once(q, l):
            calls.append(q)
            return [{"id": "npm:a", "source": "npm", "kind": "package", "name": "a",
                     "description": "", "repo": "", "tags": []}]

        d.src_npm = once
        d.search("q", sources=["npm"])
        d.search("q", sources=["npm"])
        assert len(calls) == 1                         # second scan hit the cache
        assert d.search("q", sources=["npm"])["sources"]["npm"]["cached"] is True
        d.search("q", sources=["npm"], fresh=True)
        assert len(calls) == 2                         # fresh bypasses it

    def test_scanned_items_are_recallable(self, tmpdir):
        """A registry record has no page to re-fetch, so detail/install rely
        on the scan having remembered it by id."""
        d = self._d(tmpdir)
        d.src_mcp = lambda q, l: [
            {"id": "mcp:io.example/thing", "source": "mcp", "kind": "mcp",
             "name": "thing", "description": "does things", "repo": "",
             "tags": ["mcp"], "install": {"remote": "https://example.com/mcp"}}]
        assert d.recall("mcp:io.example/thing") is None
        d.search("thing", sources=["mcp"])
        assert d.recall("mcp:io.example/thing")["name"] == "thing"
        # install resolves from the index without hitting the registry again
        d.search = lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-searched"))
        doc = d.tool_doc("mcp:io.example/thing")
        assert doc["name"] == "thing"
        assert "https://example.com/mcp" in doc["body"]

    def test_unknown_source_rejected(self, tmpdir):
        with pytest.raises(ValueError):
            self._d(tmpdir).search("x", sources=["not-a-registry"])

    def test_token_is_stored_off_tree(self, tmpdir):
        d = self._d(tmpdir)
        env = os.environ.pop("GITHUB_TOKEN", None)
        try:
            assert d.token() is None
            d.set_token("ghp_example")
            assert d.token() == "ghp_example"
            assert not (Path(__file__).resolve().parents[1] / "github.token").exists()
            d.set_token("")
            assert d.token() is None
        finally:
            if env is not None:
                os.environ["GITHUB_TOKEN"] = env

    def test_tool_doc_from_mcp_record(self, tmpdir):
        """Non-SKILL.md sources still yield an installable reference card."""
        d = self._d(tmpdir)
        d.detail = lambda i: {
            "id": i, "source": "mcp", "kind": "mcp", "name": "postgres",
            "title": "io.example/postgres", "description": "Query Postgres",
            "url": "https://example.com", "repo": "", "tags": ["mcp"],
            "install": {"remote": "https://example.com/mcp", "tools": ["query"]},
        }
        doc = d.tool_doc("mcp:io.example/postgres")
        assert doc["name"] == "postgres" and doc["kind"] == "mcp"
        assert "https://example.com/mcp" in doc["body"]
        assert "query" in doc["body"]


class TestInstalledToolDocs:
    """Installing a scanned result adds a document to the library —
    never an executable — and it stays addressable by CID."""

    def _lib(self, tmpdir):
        from src.library.mod import Library
        return Library(tools=Tools(path=TOOLS_PATH), dir=str(tmpdir))

    def test_install_upsert_and_index(self, tmpdir):
        lib = self._lib(tmpdir)
        assert lib.installed_tools() == []
        s = lib.tool_add("pdf", "# PDF\nsteps", "Handle PDFs", tags=["docs"],
                          source="github", url="https://github.com/a/b/SKILL.md",
                          origin_id="gh:a/b")
        # re-installing the same origin refreshes in place
        s2 = lib.tool_add("pdf", "# PDF\nnewer steps", origin_id="gh:a/b")
        assert s2["id"] == s["id"] and len(lib.installed_tools()) == 1
        assert s2["url"] == s["url"]                   # provenance survives a refresh
        item = [i for i in lib.items(kind="tool")["items"] if i["id"] == s["id"]][0]
        assert item["external"] is True
        assert item["tags"].count("github") == 1       # source tag isn't duplicated
        assert "installed" in item["tags"]

    def test_builtin_tools_are_untouched(self, tmpdir):
        """External installs never shadow or replace the code tool registry."""
        lib = self._lib(tmpdir)
        builtin = {i["name"] for i in lib.items(kind="tool")["items"] if i.get("builtin")}
        lib.tool_add("bash", "# not the real bash tool", origin_id="gh:evil/bash")
        after = lib.items(kind="tool")["items"]
        assert {i["name"] for i in after if i.get("builtin")} == builtin
        assert Builtins().get("bash").description              # still the real one

    def test_uninstall(self, tmpdir):
        lib = self._lib(tmpdir)
        s = lib.tool_add("x", "body")
        lib.tool_rm(s["id"])
        assert lib.installed_tools() == []
        with pytest.raises(KeyError):
            lib.tool_rm(s["id"])

    def test_requires_name_and_body(self, tmpdir):
        lib = self._lib(tmpdir)
        with pytest.raises(ValueError):
            lib.tool_add("", "body")
        with pytest.raises(ValueError):
            lib.tool_add("name", "")

    def test_tool_docs_selects_for_run_context(self, tmpdir):
        lib = self._lib(tmpdir)
        a = lib.tool_add("a", "body a")
        lib.tool_add("b", "body b")
        assert [d["name"] for d in lib.tool_docs([a["id"]])] == ["a"]
        assert lib.tool_docs([]) == []
        assert lib.tool_docs(["nope"]) == []

    def test_body_is_clipped(self, tmpdir):
        lib = self._lib(tmpdir)
        s = lib.tool_add("big", "x" * 400_000)
        assert len(s["body"]) == lib.MAX_TOOL_CHARS


# ═══════════════════════════════════════════════════════════════════════
#  HARNESS  (external agent CLIs run as agents: claude code, codex)
# ═══════════════════════════════════════════════════════════════════════

class TestHarnessRegistry:
    @pytest.fixture
    def harness(self):
        from src.harness.mod import Harness
        return Harness()

    def test_ls_lists_runners(self, harness):
        names = [h["name"] for h in harness.ls()]
        assert names == ["claude", "codex", "claudemod", "buildmod"]
        for h in harness.ls():
            assert isinstance(h["available"], bool)   # depends on the host
            assert h["install"]

    def test_console_runners_name_their_module(self, harness):
        # the two job-server harnesses are whole modules, not a local binary
        assert harness.info("claudemod")["module"] == "claude"
        assert harness.info("buildmod")["module"] == "build"

    def test_get_unknown_raises(self, harness):
        with pytest.raises(KeyError, match="unknown harness"):
            harness.get("nope")

    def test_run_unknown_raises(self, harness):
        with pytest.raises(KeyError):
            harness.run("nope", "hi")

    def test_forward_lists(self, harness):
        r = harness.forward()
        assert len(r["harnesses"]) == 4
        assert set(r["available"]) <= {"claude", "codex", "claudemod", "buildmod"}

    def test_claude_command(self, harness):
        cmd = harness.get("claude").command("fix it", goal="be nice", model="opus")
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert cmd[cmd.index("--model") + 1] == "opus"
        assert cmd[cmd.index("--append-system-prompt") + 1] == "be nice"
        assert cmd[-1] == "fix it"                    # the prompt is an argument

    def test_codex_command(self, harness):
        cmd = harness.get("codex").command("fix it", goal="be nice", path="/tmp")
        assert cmd[:2] == ["codex", "exec"]
        assert "--json" in cmd
        assert cmd[cmd.index("-C") + 1] == "/tmp"
        assert cmd[-1] == "be nice\n\nfix it"         # codex has no system-prompt flag

    def test_run_missing_binary_explains_install(self, harness, monkeypatch):
        runner = harness.get("claude")
        monkeypatch.setattr(type(runner), "path", lambda self: None)
        monkeypatch.setattr(harness, "get", lambda name: runner)
        with pytest.raises(RuntimeError, match="npm install"):
            harness.run("claude", "hi")


class TestHarnessTranslation:
    """CLI events in, the console's step dicts out.

    The translation lives in the runner modules (orbit/claudecode,
    orbit/codexcli) — one session per run, reached through the registry."""

    def _steps(self, runner, events):
        out = []
        for e in events:
            out += runner.steps(e)
        return out

    def _session(self, name):
        from src.harness.mod import Harness
        return Harness().get(name).session()

    def _claude(self):
        return self._session("claude")

    def _codex(self):
        return self._session("codex")

    def test_claude_tool_step_carries_its_result(self):
        r = self._claude()
        steps = self._steps(r, [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "a.py"}]}},
        ])
        assert steps == [{"tool": "bash", "params": {"command": "ls"}, "result": "a.py"}]

    def test_claude_narration_lands_before_the_tool_that_follows(self):
        r = self._claude()
        steps = self._steps(r, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "I'll look around."},
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "a.py"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "code"}]}},
        ])
        assert [s["tool"] for s in steps] == ["response", "read"]
        assert steps[0]["result"] == "I'll look around."

    def test_claude_result_becomes_finish(self):
        r = self._claude()
        steps = self._steps(r, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "done!"}]}},
            {"type": "result", "subtype": "success", "result": "done!"},
        ])
        # the last message IS the answer — it belongs in finish, not the trace
        assert steps == [{"tool": "finish", "params": {"summary": "done!"}}]

    def test_claude_error_result(self):
        r = self._claude()
        steps = r.steps({"type": "result", "subtype": "error_during_execution",
                         "is_error": True, "result": "boom"})
        assert steps[0]["tool"] == "error" and steps[0]["error"] == "boom"

    def test_claude_tool_error_is_kept_on_the_step(self):
        r = self._claude()
        steps = self._steps(r, [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": "not found"}], "is_error": True}]}},
        ])
        assert steps[0]["error"] == "not found" and "result" not in steps[0]

    def test_codex_command_and_message(self):
        r = self._codex()
        steps = self._steps(r, [
            {"type": "item.completed", "item": {
                "type": "command_execution", "command": "ls",
                "aggregated_output": "a.py", "exit_code": 0}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "all set"}},
            {"type": "turn.completed"},
        ])
        assert [s["tool"] for s in steps] == ["bash", "finish"]
        assert steps[0]["result"] == "a.py"
        assert steps[1]["params"]["summary"] == "all set"

    def test_codex_recoverable_error_does_not_fail_the_run(self):
        r = self._codex()
        steps = self._steps(r, [
            {"type": "item.completed", "item": {"type": "error", "message": "retrying"}},
            {"type": "turn.completed"},
        ])
        # a plain trace row: an error step would mark the whole task failed
        assert [s["tool"] for s in steps] == ["harness", "finish"]

    def test_codex_turn_failed_is_an_error(self):
        r = self._codex()
        steps = r.steps({"type": "turn.failed", "error": {"message": "401"}})
        assert steps[0]["tool"] == "error" and steps[0]["error"] == "401"

    def test_close_falls_back_to_the_held_message(self):
        r = self._codex()
        r.steps({"type": "item.completed", "item": {"type": "agent_message", "text": "bye"}})
        assert r.close(0, []) == [{"tool": "finish", "params": {"summary": "bye"}}]

    def test_close_reports_a_silent_exit(self):
        r = self._claude()
        steps = r.close(1, ["command not found"])
        assert steps[0]["tool"] == "error"
        assert "command not found" in steps[0]["error"]

    def test_close_is_quiet_once_a_terminal_step_was_emitted(self):
        r = self._claude()
        r.steps({"type": "result", "subtype": "success", "result": "ok"})
        assert r.close(0, []) == []


class TestHarnessAgents:
    """Agents that hand their run to a CLI instead of this module's loop."""

    def test_shipped_harness_agents(self, agents):
        assert agents.get("claude-code")["harness"] == "claude"
        assert agents.get("codex")["harness"] == "codex"
        assert agents.get("claude-mod")["harness"] == "claudemod"
        assert agents.get("build-mod")["harness"] == "buildmod"
        assert agents.get("default")["harness"] is None

    def test_create_with_harness_round_trips(self, agents):
        name = "test-harness-agent"
        try:
            cfg = agents.create(name, description="mine", harness="claude")
            assert cfg["harness"] == "claude"
            assert agents.get(name)["harness"] == "claude"
            # an edit that doesn't mention the harness keeps it
            assert agents.update(name, description="still mine")["harness"] == "claude"
            # and an explicit None hands the run back to our own loop
            assert agents.update(name, harness=None)["harness"] is None
        finally:
            agent_dir = agents._dir / name
            if agent_dir.exists():
                shutil.rmtree(agent_dir)

    def test_unknown_harness_rejected(self, agents):
        with pytest.raises(ValueError, match="unknown harness"):
            agents.create("test-bogus-harness", harness="gpt-cli")
        assert "test-bogus-harness" not in agents.ls()

    def test_library_tags_harness_agents(self, tmpdir):
        from src.library.mod import Library
        lib = Library(tools=Tools(path=TOOLS_PATH), agents=Agents(), dir=tmpdir)
        item = next(i for i in lib.items(kind="agent")["items"]
                    if i["name"] == "claude-code")
        assert item["harness"] == "claude"
        assert "harness" in item["tags"] and "claude" in item["tags"]


class TestHarnessGate:
    """A harness run is the host's own shell, so it's owner only."""

    def _mod(self, is_owner):
        from src.mod import Mod
        from src.harness.mod import Harness
        mod = Mod.__new__(Mod)
        mod.agents = Agents()
        mod.harness = Harness()
        # a native run swaps in the agent's own memory module and back out,
        # so even this thin stub carries the memory component
        mod.memories = Memories()
        mod.memory = Memory()
        mod.is_owner = lambda key=None: is_owner
        mod.allowed_paths_for = lambda key=None: None
        mod.library = None
        return mod

    def test_harness_for(self):
        mod = self._mod(True)
        assert mod.harness_for("claude-code") == "claude"
        assert mod.harness_for("default") is None
        assert mod.harness_for("nope") is None
        assert mod.harness_for(None) is None

    def test_guest_is_refused(self):
        mod = self._mod(False)
        with pytest.raises(PermissionError, match="owner-only"):
            mod._run(agent_type="claude-code", query="hi", key="0xguest")

    def test_refusal_names_the_agent_that_was_picked(self):
        """Not the harness behind it — the console reads this back to the
        visitor as "X runs on the host's own machine", and X has to be the
        thing they clicked. 'buildmod' is a name they never chose."""
        mod = self._mod(False)
        with pytest.raises(PermissionError) as e:
            mod._run(agent_type="claude-code", query="hi", key="0xguest")
        assert "'claude-code' hands the run to the claude CLI" in str(e.value)

    def test_owner_reaches_the_runner(self, monkeypatch):
        mod = self._mod(True)
        seen = {}
        def fake_run(name, query, path=None, goal=None, model=None,
                     timeout=None, on_step=None):
            seen.update(name=name, query=query, path=path, goal=goal)
            return [{"tool": "finish", "params": {"summary": "ok"}}]
        monkeypatch.setattr(mod.harness, "run", fake_run)
        out = mod._run(agent_type="claude-code", query="hi", key="0xowner",
                       path="/tmp", model="anthropic/claude-sonnet-4.5")
        assert out[0]["params"]["summary"] == "ok"
        assert seen["name"] == "claude" and seen["path"] == "/tmp"
        assert "orbit/agent console" in seen["goal"]


class TestDefaultAgent:
    """An unnamed run lands on Claude Code — for whoever is allowed to run it."""

    def _mod(self, is_owner):
        return TestHarnessGate()._mod(is_owner)

    def _no_cli(self, mod, monkeypatch):
        runner = mod.harness.get("claude")
        monkeypatch.setattr(type(runner), "path", lambda self: None)

    def test_host_gets_claude_code(self):
        mod = self._mod(True)
        expected = ("claude-code" if mod.harness.get("claude").available()
                    else "default")          # depends on the host's PATH
        assert mod.default_agent("0xowner") == expected

    def test_guest_stays_on_the_native_loop(self):
        # a harness run is owner-only, so it can't be anyone's default
        assert self._mod(False).default_agent("0xguest") == "default"

    def test_missing_cli_falls_back(self, monkeypatch):
        mod = self._mod(True)
        self._no_cli(mod, monkeypatch)
        assert mod.default_agent("0xowner") == "default"

    def test_unnamed_run_goes_to_the_default(self, monkeypatch):
        mod = self._mod(True)
        if not mod.harness.get("claude").available():
            pytest.skip("claude CLI not installed on this host")
        seen = {}
        monkeypatch.setattr(mod.harness, "run",
                            lambda name, **kw: seen.update(name=name) or [])
        mod._run(query="hi", key="0xowner", path="/tmp")
        assert seen["name"] == "claude"

    def test_named_agent_still_wins(self, monkeypatch):
        mod = self._mod(True)
        called = {}
        mod.run = lambda **kw: called.update(kw) or []
        mod.goal = "base"
        mod._run(agent_type="architect", query="hi", key="0xowner")
        assert called["query"] == "hi"       # native loop, not the CLI


class TestLfmProviders:
    """The three LFM providers: no key, no bill, and a live model list."""

    def _mod(self):
        from src.mod import Mod
        return Mod()

    @pytest.mark.parametrize("provider", ["liquidai", "liquidai-cloud", "browser"])
    def test_a_client_exists_without_any_key(self, provider):
        mod = self._mod()
        assert mod.has_model(provider)
        info = mod.key_info(provider)
        assert info["keyless"] and info["configured"] and info["key"] is None

    @pytest.mark.parametrize("provider", ["liquidai", "liquidai-cloud", "browser"])
    def test_runs_on_them_cost_nothing(self, provider):
        mod = self._mod()
        assert mod.is_free_provider(provider)
        # every model prices at zero, including one typed in by hand
        client = mod._client(provider)
        assert mod.meter.price(client, provider, "LiquidAI/anything", 1e6, 1e6) == 0.0

    def test_paid_providers_are_untouched(self):
        mod = self._mod()
        assert not mod.is_free_provider("openrouter")
        assert not mod.key_info("openrouter").get("keyless")

    def test_model_list_falls_back_when_liquidai_is_down(self, monkeypatch):
        from src import liquid
        mod = self._mod()
        monkeypatch.setattr(liquid.CATALOG, "repos", lambda *a, **k: [])
        monkeypatch.setattr(liquid.CATALOG, "cloud_models", lambda *a, **k: [])
        # the curated list keeps the console selectable with the module offline
        assert mod.provider_models("browser") == mod.MODELS["browser"]

    def test_balance_reports_free_rather_than_an_error(self):
        assert self._mod().balance("browser")["balance"] is None


class TestBrowserBridge:
    """A run parks a request, the tab answers it — or doesn't."""

    def _bridge(self):
        from src.liquid import Bridge
        return Bridge()

    def _ask(self, bridge, session, out):
        def run():
            bridge.bind(session)
            try:
                out["text"] = bridge.ask({"model": "m", "messages": []}, timeout=5)
            except Exception as e:
                out["error"] = str(e)
        t = threading.Thread(target=run)
        t.start()
        return t

    def test_request_goes_out_and_the_answer_comes_back(self):
        bridge, events, out = self._bridge(), [], {}
        bridge.open("s", events.append)
        t = self._ask(bridge, "s", out)
        time.sleep(0.2)
        assert events[0]["type"] == "model_request" and events[0]["model"] == "m"
        bridge.deliver(events[0]["id"], text="hello")
        t.join(5)
        assert out["text"] == "hello"

    def test_no_open_tab_fails_immediately(self):
        bridge, out = self._bridge(), {}
        self._ask(bridge, "nobody", out).join(5)
        assert "console" in out["error"]

    def test_a_closed_tab_releases_the_run(self):
        bridge, out = self._bridge(), {}
        bridge.open("s", lambda ev: None)
        t = self._ask(bridge, "s", out)
        time.sleep(0.2)
        bridge.close("s")
        t.join(5)
        assert "went away" in out["error"]

    def test_an_error_from_the_tab_reaches_the_run(self):
        bridge, events, out = self._bridge(), [], {}
        bridge.open("s", events.append)
        t = self._ask(bridge, "s", out)
        time.sleep(0.2)
        bridge.deliver(events[0]["id"], error="WebGPU said no")
        t.join(5)
        assert out["error"] == "WebGPU said no"

    def test_delivering_to_nobody_says_so(self):
        assert self._bridge().deliver("gone", text="x")["delivered"] is False


# ═══════════════════════════════════════════════════════════════════════
#  THE PROMPT, AND THE CALLS THAT COME BACK
# ═══════════════════════════════════════════════════════════════════════

SCHEMAS = {
    'bash': {'description': 'Run shell commands and return output',
             'params': {'command': {'type': 'str', 'required': True},
                        'cwd': {'type': 'str', 'required': False}}},
    'read': {'description': 'Read file contents. Slices by line.',
             'params': {'file_path': {'type': "<class 'str'>", 'required': True},
                        'offset': {'type': 'int', 'required': False}}},
    'write': {'description': 'Write content to file_path',
              'params': {'file_path': {'type': 'str', 'required': True},
                         'content': {'type': 'str', 'required': True}}},
    'think': {'description': 'Think it through',
              'params': {'thought': {'type': 'str', 'required': True}}},
}


class TestPromptRender:
    """Working memory as text a model can read — not a Python dict repr."""

    def _state(self, **kw):
        from src.mod import Agent
        state = {'goal': 'be useful', 'output_format': Agent.output_format,
                 'query': 'fix the bug', 'tools': SCHEMAS, 'path': '/tmp/w',
                 'steps': 10, 'step': 0}
        state.update(kw)
        return state

    def test_sections_not_a_dict_repr(self):
        from src.prompt import render
        text = render(self._state())
        assert "# TASK\nfix the bug" in text
        assert '# TOOLS' in text and '# YOUR NEXT STEP' in text
        assert "{'query':" not in text and "<class 'str'>" not in text

    def test_a_tool_reads_as_a_signature(self):
        from src.prompt import render
        text = render(self._state())
        assert 'read(file_path:str, offset?:int)' in text
        assert 'finish(summary:str)' in text

    def test_history_is_trimmed_oldest_first(self):
        from src.prompt import render
        history = [[{'tool': 'bash', 'params': {'command': f'echo {i}'},
                     'result': 'x' * 4000}] for i in range(30)]
        text = render(self._state(history=history), compact=True)
        assert 'earlier step(s) trimmed' in text
        assert len(text) < 12000                     # not 30 × 4000 chars
        assert 'echo 29' in text                     # the newest survives whole

    def test_the_loops_note_on_a_step_survives(self):
        from src.prompt import render
        history = [[{'tool': 'read', 'params': {}, 'result': 'ok',
                     'note': 'you already ran this'}]]
        assert 'you already ran this' in render(self._state(history=history))

    def test_the_last_step_says_so(self):
        from src.prompt import render
        assert 'last chance' in render(self._state(step=9, steps=10))
        assert 'last chance' not in render(self._state(step=0, steps=10))

    def test_compact_carries_a_worked_example(self):
        from src.prompt import render
        assert '"command": "ls -la"' in render(self._state(), compact=True)
        assert '"command": "ls -la"' not in render(self._state())

    def test_answer_mode_drops_the_tools_and_the_format(self):
        from src.prompt import render
        text = render(self._state(), answer=True)
        assert '# TOOLS (' not in text          # no list to call from
        assert '<STEP>{' not in text            # and no format to call in
        assert 'WRITE THE ANSWER' in text
        assert 'fix the bug' in text          # the task is still the task

    def test_unplaced_context_still_reaches_the_model(self):
        from src.prompt import render
        assert 'a note the caller attached' in render(
            self._state(notes='a note the caller attached'))
        assert 'fork(x)' in render(self._state(**{'fork(x)': 'some files'}))


class TestStepParsing:
    """A tool call written in any harness's dialect is still a tool call."""

    def _parse(self, text):
        from src.steps import parse
        return parse(text, schemas=SCHEMAS)

    @pytest.mark.parametrize("text,expected", [
        ('<STEP>{"tool": "bash", "params": {"command": "ls"}}</STEP>',
         {'tool': 'bash', 'params': {'command': 'ls'}}),
        ('<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>',
         {'tool': 'bash', 'params': {'command': 'ls'}}),
        ('sure!\n```json\n{"tool": "bash", "params": {"command": "ls"}}\n```',
         {'tool': 'bash', 'params': {'command': 'ls'}}),
        ('<|tool_call_start|>[bash(command="ls")]<|tool_call_end|>',
         {'tool': 'bash', 'params': {'command': 'ls'}}),
        ('{"function": {"name": "bash", "arguments": "{\\"command\\": \\"ls\\"}"}}',
         {'tool': 'bash', 'params': {'command': 'ls'}}),
        ('{"bash": {"command": "ls"}}',
         {'tool': 'bash', 'params': {'command': 'ls'}}),
        ("{'tool': 'bash', 'params': {'command': 'ls'}}",
         {'tool': 'bash', 'params': {'command': 'ls'}}),
        ('TOOL: bash\nPARAMS: {"command": "ls"}',
         {'tool': 'bash', 'params': {'command': 'ls'}}),
    ])
    def test_every_dialect_lands_on_the_same_call(self, text, expected):
        assert self._parse(text) == [expected]

    def test_another_harnesses_tool_names_are_mapped(self):
        assert self._parse('{"tool": "read_file", "params": {"path": "/tmp/a"}}') == \
            [{'tool': 'read', 'params': {'file_path': '/tmp/a'}}]
        assert self._parse('{"name": "run_command", "arguments": {"cmd": "ls"}}') == \
            [{'tool': 'bash', 'params': {'command': 'ls'}}]

    def test_a_lone_argument_lands_on_the_required_param(self):
        assert self._parse('bash("ls -la")') == \
            [{'tool': 'bash', 'params': {'command': 'ls -la'}}]
        assert self._parse('{"tool": "think", "params": "hmm"}') == \
            [{'tool': 'think', 'params': {'thought': 'hmm'}}]

    def test_one_unknown_argument_on_one_empty_slot_is_that_slot(self):
        assert self._parse('{"tool": "write", "params": {"file_path": "/a", "text": "hi"}}') == \
            [{'tool': 'write', 'params': {'file_path': '/a', 'content': 'hi'}}]

    def test_prose_is_not_a_tool_call(self):
        assert self._parse('I looked at the files and they seem fine.') == []
        assert self._parse('the config is {"debug": true} by default') == []

    def test_a_call_beats_the_json_quoted_beside_it(self):
        text = ('here is the format: {"tool": "write", "params": {}}\n'
                '<STEP>{"tool": "bash", "params": {"command": "ls"}}</STEP>')
        assert self._parse(text) == [{'tool': 'bash', 'params': {'command': 'ls'}}]

    def test_the_call_after_the_thinking_wins(self):
        text = ('<think>I could read(file_path="a") but the user wants a list'
                '</think>[bash(command="ls")]')
        assert self._parse(text) == [{'tool': 'bash', 'params': {'command': 'ls'}}]

    def test_an_unknown_tool_resolves_to_nothing(self):
        from src.steps import resolve_name
        assert resolve_name('frobnicate', list(SCHEMAS)) is None
        assert resolve_name('finish', list(SCHEMAS)) == 'finish'


class TestLoopReadsAnyDialect:
    """The same tolerance, wired into the loop the console runs."""

    def _agent(self):
        from src.mod import Agent
        agent = Agent.__new__(Agent)
        agent.memory = Memory()
        agent.memory.clear()
        agent.memory.add('tools', SCHEMAS)
        agent.tools = Tools(path=TOOLS_PATH)
        agent.anchors = Agent.anchors
        agent._on_step = None
        return agent

    def test_an_anchored_call_in_another_dialect_is_repaired(self):
        agent = self._agent()
        step = agent._step_from_json('{"tool": "read_file", "params": {"path": "/tmp/a"}}')
        assert step == {'tool': 'read', 'params': {'file_path': '/tmp/a'}}

    def test_a_call_with_no_anchors_at_all_still_runs(self, tmpdir):
        agent = self._agent()
        target = os.path.join(tmpdir, 'a.txt')
        Path(target).write_text('hello')
        plan = agent.plan(f'<tool_call>{{"name": "read", '
                          f'"arguments": {{"file_path": "{target}"}}}}</tool_call>')
        assert plan[0]['tool'] == 'read'
        assert 'hello' in str(plan[0].get('result'))

    def test_prose_is_still_the_answer(self):
        agent = self._agent()
        plan = agent.plan('There are three files in that directory.')
        assert plan[0]['tool'] == 'response'
        assert plan[0]['result'] == 'There are three files in that directory.'

    def test_the_scratchpad_never_reaches_the_user(self):
        agent = self._agent()
        plan = agent.plan('<think>hmm, what do they want</think>Three files.')
        assert plan[0]['result'] == 'Three files.'


class TestCirclingGuard:
    """A read-only call already made is answered from the trail, not re-run."""

    def _agent(self):
        from src.mod import Agent
        agent = Agent.__new__(Agent)
        agent.memory = Memory()
        agent.memory.clear()
        agent.tools = Tools(path=TOOLS_PATH)
        agent._on_step = None
        agent._allowed_paths = None
        agent._failed_calls, agent._done_calls = {}, {}
        return agent

    def test_the_second_identical_read_comes_from_the_first(self, tmpdir):
        agent = self._agent()
        target = os.path.join(tmpdir, 'a.txt')
        Path(target).write_text('one')
        first = agent.run_plan([{'tool': 'read', 'params': {'file_path': target}}])
        Path(target).write_text('two')          # changed behind the agent's back
        again = agent.run_plan([{'tool': 'read', 'params': {'file_path': target}}])
        assert again[0]['repeat'] and 'already ran' in again[0]['note']
        assert again[0]['result'] == first[0]['result']
        assert 'Do NOT call it again' in str(agent.memory.get('hint'))

    def test_a_relative_path_means_the_runs_directory(self, tmpdir):
        agent = self._agent()
        agent._path = str(tmpdir)
        Path(os.path.join(tmpdir, 'a.txt')).write_text('the right file')
        plan = agent.run_plan([{'tool': 'read', 'params': {'file_path': 'a.txt'}}])
        assert 'the right file' in str(plan[0]['result'])

    def test_an_omitted_path_means_the_runs_directory_too(self, tmpdir):
        agent = self._agent()
        agent._path = str(tmpdir)
        agent.memory.add('tools', agent.tools.schema(['tree']))
        Path(os.path.join(tmpdir, 'only-here.txt')).write_text('x')
        plan = agent.run_plan([{'tool': 'tree', 'params': {}}])
        assert 'only-here.txt' in str(plan[0]['result'])

    def test_an_absolute_path_is_left_alone(self, tmpdir):
        agent = self._agent()
        agent._path = '/nowhere'
        target = os.path.join(tmpdir, 'b.txt')
        Path(target).write_text('absolute')
        plan = agent.run_plan([{'tool': 'read', 'params': {'file_path': target}}])
        assert 'absolute' in str(plan[0]['result'])

    def test_a_write_invalidates_what_was_cached(self, tmpdir):
        agent = self._agent()
        target = os.path.join(tmpdir, 'a.txt')
        Path(target).write_text('one')
        agent.run_plan([{'tool': 'read', 'params': {'file_path': target}}])
        agent.run_plan([{'tool': 'write', 'params': {'file_path': target,
                                                     'content': 'two'}}])
        again = agent.run_plan([{'tool': 'read', 'params': {'file_path': target}}])
        assert not again[0].get('repeat')
        assert 'two' in str(again[0]['result'])


class TestLocalFirstDefault:
    """Nobody pays for a run they didn't ask to pay for."""

    def _mod(self):
        from src.mod import Mod
        return Mod()

    def test_the_default_provider_is_a_local_one(self, monkeypatch):
        mod = self._mod()
        monkeypatch.setattr(mod, 'provider_models',
                            lambda p: ['LiquidAI/LFM2.5-1.2B-Instruct']
                            if p == 'liquidai' else [])
        mod._default_provider = None
        assert mod.default_provider() == 'liquidai'
        assert mod.is_free_provider(mod.default_provider())

    def test_it_falls_back_when_nothing_local_is_serving(self, monkeypatch):
        mod = self._mod()
        monkeypatch.setattr(mod, 'provider_models', lambda p: [])
        monkeypatch.setattr(mod, 'key_info',
                            lambda p, **k: {'configured': p == 'venice'})
        mod._default_provider = None
        assert mod.default_provider() == 'venice'

    def test_a_personas_prompt_does_not_outlive_its_run(self):
        """One Mod serves every caller: a swapped-in prompt that stuck was
        answering later strangers' questions as somebody else's agent."""
        from src.mod import Agent
        mod = self._mod()
        seen = {}
        mod.run = lambda **kw: seen.update(kw) or []
        mod._run(query='hi', prompt='You are a haiku bot.')
        assert seen['goal'] == 'You are a haiku bot.'
        assert mod.goal == Agent.goal
        assert 'haiku' not in mod.context()

    def test_a_runs_memory_module_is_its_own(self):
        mod = self._mod()
        default = mod.memory
        ephemeral = mod.memories.make('ephemeral')
        mod._bind_memory(ephemeral)
        assert mod.memory is ephemeral
        mod._unbind_memory()
        assert mod.memory is default

    def test_two_runs_at_once_do_not_share_a_scratchpad(self):
        """One Mod serves the whole host. Two runs writing one working memory
        compiled each other's prompts — the console asking about a file got an
        arena match's task, tools and history, and answered that instead."""
        import threading
        mod = self._mod()
        prompts, ready = {}, threading.Barrier(2)

        def run(name):
            mod._bind_memory()
            try:
                mod.init_memory(query=f'question from {name}', tools={})
                ready.wait(timeout=5)          # …now interleave
                prompts[name] = mod.context()
            finally:
                mod._unbind_memory()

        threads = [threading.Thread(target=run, args=(n,)) for n in ('a', 'b')]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        assert 'question from a' in prompts['a'] and 'question from b' not in prompts['a']
        assert 'question from b' in prompts['b'] and 'question from a' not in prompts['b']

    def test_one_runs_context_never_reaches_the_next(self):
        """Working memory is a scratchpad on a process-wide singleton."""
        mod = self._mod()
        mod.init_memory(query='first', tools={}, notes='a note only run one had')
        mod.memory.add('hint', 'something the first run was told')
        mod.init_memory(query='second', tools={})
        text = mod.context()
        assert 'second' in text
        assert 'a note only run one had' not in text and 'was told' not in text

    def test_a_small_model_gets_the_compact_prompt(self):
        mod = self._mod()
        assert mod.compact_prompt('liquidai', 'LiquidAI/LFM2.5-1.2B-Instruct')
        assert mod.compact_prompt('openrouter', 'qwen/qwen3-4b')
        # …and a big model is not mistaken for one by the digits in its name
        assert not mod.compact_prompt('openrouter', 'anthropic/claude-opus-5')
        assert not mod.compact_prompt('venice', 'llama-3.3-70b')
        assert not mod.compact_prompt('openrouter', 'google/gemma-4-31b')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
