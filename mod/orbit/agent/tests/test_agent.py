"""
tests for the agent framework

covers:
    - skills registry (discovery, loading, caching, schema, errors)
    - individual skills (bash, read, write, edit, glob, grep, search, task, websurf, claudecode)
    - agents registry (discovery, create, remove, schema)
    - memory
    - agent (parse_steps, _extract_step, run_plan, init_memory, skill wiring)
    - mod class (test, status, forward, gate/acl)
    - api endpoints

run:
    cd ~/mod/mod/orbit/agent && python3 -m pytest tests/test_agent.py -v
"""
import os
import sys
import json
import tempfile
import shutil
import pytest
from pathlib import Path

# make sure imports resolve from the agent root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.skills.mod import Skills
from src.agents.mod import Agents
from src.memory.memory import Memory

SKILL_COUNT = 23
# shipped agents. Custom agents live in the same directory, so counts are
# lower bounds — a host with their own agents installed still passes.
AGENT_COUNT = 7


# ═══════════════════════════════════════════════════════════════════════
#  FIXTURES
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def skills():
    return Skills()

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
#  SKILLS REGISTRY
# ═══════════════════════════════════════════════════════════════════════

class TestSkillsRegistry:
    def test_ls_returns_all_skills(self, skills):
        names = skills.ls()
        assert len(names) == SKILL_COUNT
        for expected in ["bash", "read", "write", "edit", "glob", "grep",
                         "search", "task", "websurf", "claudecode"]:
            assert expected in names

    def test_get_returns_instance(self, skills):
        bash = skills.get("bash")
        assert hasattr(bash, "forward")
        assert hasattr(bash, "description")

    def test_get_caches_instances(self, skills):
        a = skills.get("bash")
        b = skills.get("bash")
        assert a is b

    def test_get_unknown_skill_raises(self, skills):
        with pytest.raises(KeyError, match="skill not found"):
            skills.get("nonexistent_skill_xyz")

    def test_run_delegates_to_forward(self, skills):
        r = skills.run("bash", command="echo registry_test")
        assert r["success"]
        assert "registry_test" in r["stdout"]

    def test_forward_no_name_returns_list(self, skills):
        r = skills.forward()
        assert "skills" in r
        assert "total" in r
        assert r["total"] == SKILL_COUNT

    def test_forward_with_name_runs_skill(self, skills):
        r = skills.forward("bash", command="echo forward_test")
        assert r["success"]

    def test_schema_returns_all(self, skills):
        schema = skills.schema()
        assert len(schema) == SKILL_COUNT
        for name, info in schema.items():
            assert "description" in info, f"{name} schema missing description"
            assert "params" in info, f"{name} schema missing params"

    def test_schema_filtered(self, skills):
        schema = skills.schema(["bash", "read"])
        assert len(schema) == 2
        assert "bash" in schema
        assert "read" in schema

    def test_schema_params_have_types(self, skills):
        schema = skills.schema(["bash"])
        params = schema["bash"]["params"]
        assert "command" in params
        assert params["command"]["required"] is True
        assert "timeout" in params
        assert params["timeout"]["required"] is False


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: BASH
# ═══════════════════════════════════════════════════════════════════════

class TestBashSkill:
    def test_echo(self, skills):
        r = skills.run("bash", command="echo hello")
        assert r["success"]
        assert r["stdout"].strip() == "hello"
        assert r["code"] == 0

    def test_failing_command(self, skills):
        r = skills.run("bash", command="exit 1")
        assert not r["success"]
        assert r["code"] == 1

    def test_stderr(self, skills):
        r = skills.run("bash", command="echo err >&2")
        assert "err" in r["stderr"]

    def test_cwd(self, skills, tmpdir):
        r = skills.run("bash", command="pwd", cwd=tmpdir)
        assert r["success"]
        assert tmpdir in r["stdout"] or os.path.realpath(tmpdir) in r["stdout"]

    def test_timeout(self, skills):
        r = skills.run("bash", command="sleep 10", timeout=1)
        assert not r["success"]
        assert "timeout" in r["stderr"]

    def test_multiline_output(self, skills):
        r = skills.run("bash", command="echo a; echo b; echo c")
        assert r["success"]
        lines = r["stdout"].strip().split("\n")
        assert lines == ["a", "b", "c"]

    def test_pipe(self, skills):
        r = skills.run("bash", command="echo 'hello world' | tr 'h' 'H'")
        assert r["success"]
        assert "Hello" in r["stdout"]


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: READ
# ═══════════════════════════════════════════════════════════════════════

class TestReadSkill:
    def test_read_file(self, skills, tmpfile):
        r = skills.run("read", file_path=tmpfile)
        assert r["success"]
        assert "line one" in r["content"]
        assert r["total"] == 4
        assert r["lines"] == 4

    def test_read_with_offset(self, skills, tmpfile):
        r = skills.run("read", file_path=tmpfile, offset=1)
        assert r["success"]
        assert "line two" in r["content"]
        assert "line one" not in r["content"]

    def test_read_with_limit(self, skills, tmpfile):
        r = skills.run("read", file_path=tmpfile, limit=2)
        assert r["success"]
        assert r["lines"] == 2

    def test_read_nonexistent(self, skills):
        r = skills.run("read", file_path="/tmp/this_file_does_not_exist_xyz.txt")
        assert not r["success"]
        assert "not found" in r["error"]

    def test_read_directory(self, skills, tmpdir):
        r = skills.run("read", file_path=tmpdir)
        assert not r["success"]
        assert "not a file" in r["error"]


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: WRITE
# ═══════════════════════════════════════════════════════════════════════

class TestWriteSkill:
    def test_write_new_file(self, skills, tmpdir):
        p = os.path.join(tmpdir, "new.txt")
        r = skills.run("write", file_path=p, content="hello")
        assert r["success"]
        assert Path(p).read_text() == "hello"
        assert r["bytes"] == 5

    def test_write_creates_dirs(self, skills, tmpdir):
        p = os.path.join(tmpdir, "a", "b", "c", "deep.txt")
        r = skills.run("write", file_path=p, content="deep")
        assert r["success"]
        assert Path(p).read_text() == "deep"

    def test_write_overwrites(self, skills, tmpfile):
        r = skills.run("write", file_path=tmpfile, content="overwritten")
        assert r["success"]
        assert Path(tmpfile).read_text() == "overwritten"


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: EDIT
# ═══════════════════════════════════════════════════════════════════════

class TestEditSkill:
    def test_single_replace(self, skills, tmpfile):
        r = skills.run("edit", file_path=tmpfile, old_string="line one", new_string="LINE ONE")
        assert r["success"]
        assert r["replacements"] == 1
        content = Path(tmpfile).read_text()
        assert "LINE ONE" in content
        assert "line two" in content

    def test_replace_all(self, skills, tmpdir):
        p = os.path.join(tmpdir, "multi.txt")
        Path(p).write_text("aaa bbb aaa ccc aaa")
        r = skills.run("edit", file_path=p, old_string="aaa", new_string="XXX", replace_all=True)
        assert r["success"]
        assert r["replacements"] == 3
        assert Path(p).read_text() == "XXX bbb XXX ccc XXX"

    def test_string_not_found(self, skills, tmpfile):
        r = skills.run("edit", file_path=tmpfile, old_string="NONEXISTENT", new_string="X")
        assert not r["success"]
        assert "not found" in r["error"]

    def test_multiline_replace(self, skills, tmpfile):
        r = skills.run("edit", file_path=tmpfile, old_string="line one\nline two", new_string="REPLACED")
        assert r["success"]
        assert "REPLACED" in Path(tmpfile).read_text()


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: GLOB
# ═══════════════════════════════════════════════════════════════════════

class TestGlobSkill:
    def test_find_py_files(self, skills):
        r = skills.run("glob", pattern="*.py", path=os.path.join(os.path.dirname(__file__), ".."))
        assert r["success"]
        assert r["total"] > 0

    def test_find_in_tmpdir(self, skills, tmpdir):
        Path(os.path.join(tmpdir, "a.py")).touch()
        Path(os.path.join(tmpdir, "b.py")).touch()
        Path(os.path.join(tmpdir, "c.txt")).touch()
        r = skills.run("glob", pattern="*.py", path=tmpdir)
        assert r["success"]
        assert r["total"] == 2

    def test_no_matches(self, skills, tmpdir):
        r = skills.run("glob", pattern="*.xyz_nonexistent", path=tmpdir)
        assert r["success"]
        assert r["total"] == 0


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: GREP
# ═══════════════════════════════════════════════════════════════════════

class TestGrepSkill:
    def test_find_pattern(self, skills, tmpfile):
        r = skills.run("grep", pattern="hello", path=tmpfile)
        assert r["success"]
        assert r["total"] == 1
        assert r["matches"][0]["text"] == "hello world"
        assert r["matches"][0]["line"] == 4

    def test_regex(self, skills, tmpfile):
        r = skills.run("grep", pattern="line (one|two)", path=tmpfile)
        assert r["success"]
        assert r["total"] == 2

    def test_case_insensitive(self, skills, tmpdir):
        p = os.path.join(tmpdir, "case.txt")
        Path(p).write_text("Hello\nhello\nHELLO\n")
        r = skills.run("grep", pattern="hello", path=p, ignore_case=True)
        assert r["success"]
        assert r["total"] == 3

    def test_bad_regex(self, skills, tmpfile):
        r = skills.run("grep", pattern="[invalid", path=tmpfile)
        assert not r["success"]
        assert "bad regex" in r["error"]

    def test_no_matches(self, skills, tmpfile):
        r = skills.run("grep", pattern="ZZZNOTHERE", path=tmpfile)
        assert r["success"]
        assert r["total"] == 0


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: SEARCH (web)
# ═══════════════════════════════════════════════════════════════════════

class TestSearchSkill:
    def test_empty_query(self, skills):
        r = skills.run("search", query="")
        assert not r["success"]
        assert "empty" in r["error"]

    def test_search_returns_dict(self, skills):
        r = skills.run("search", query="python")
        assert isinstance(r, dict)
        assert "success" in r
        assert "results" in r


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: WEBSURF
# ═══════════════════════════════════════════════════════════════════════

class TestWebsurfSkill:
    def test_empty_url(self, skills):
        r = skills.run("websurf", url="")
        assert not r["success"]
        assert "empty" in r["error"]

    def test_returns_dict(self, skills):
        r = skills.run("websurf", url="https://httpbin.org/html")
        assert isinstance(r, dict)
        assert "success" in r

    def test_bad_url(self, skills):
        r = skills.run("websurf", url="https://this-domain-does-not-exist-xyz.invalid")
        assert not r["success"]
        assert "error" in r


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: CLAUDECODE
# ═══════════════════════════════════════════════════════════════════════

class TestClaudeCodeSkill:
    def test_empty_prompt(self, skills):
        r = skills.run("claudecode", prompt="")
        assert not r["success"]
        assert "empty" in r["error"]

    def test_skill_has_description(self, skills):
        skill = skills.get("claudecode")
        assert "claude" in skill.description.lower() or "code" in skill.description.lower()

    def test_schema_has_prompt_param(self, skills):
        schema = skills.schema(["claudecode"])
        assert "claudecode" in schema
        assert "prompt" in schema["claudecode"]["params"]
        assert schema["claudecode"]["params"]["prompt"]["required"] is True


# ═══════════════════════════════════════════════════════════════════════
#  SKILL: TASK
# ═══════════════════════════════════════════════════════════════════════

class TestTaskSkill:
    def test_task_returns_dict(self, skills):
        r = skills.run("task", prompt="test")
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
                         "builder", "refactorer", "safety"]:
            assert expected in names

    def test_get_returns_config(self, agents):
        config = agents.get("architect")
        assert config["name"] == "Architect"
        assert "description" in config
        assert "goal" in config
        assert config["goal"] is not None
        assert "icon" in config
        assert isinstance(config["skills"], list)

    def test_get_default_agent(self, agents):
        config = agents.get("default")
        assert config["name"] == "Default"
        assert config["goal"] is None  # uses base goal
        assert config["skills"] is None  # all skills

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

    def test_safety_agent_has_skills(self, agents):
        config = agents.get("safety")
        assert "read" in config["skills"]
        assert "think" in config["skills"]
        assert "grep" in config["skills"]

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
#  AGENT (unit tests without LLM)
# ═══════════════════════════════════════════════════════════════════════

class TestAgent:
    """Test agent components that don't need an LLM connection."""

    def _make_agent(self):
        from src.mod import Agent
        agent = Agent.__new__(Agent)
        agent.skills = Skills()
        agent.agents = Agents()
        agent.memory = Memory()
        agent.memory.clear()
        agent.model = None
        agent._skill_names = None
        agent._session_keys = {}
        agent._snapped = []
        from src.toolbox.mod import Toolboxes
        agent.toolboxes = Toolboxes(skills=agent.skills)
        agent.goal = Agent.goal
        agent.output_format = Agent.output_format
        agent.anchors = Agent.anchors
        return agent

    # ── skill wiring ──

    def test_skill_ls(self):
        agent = self._make_agent()
        assert "bash" in agent.skills.ls()
        assert len(agent.skills.ls()) == SKILL_COUNT

    def test_skill_get(self):
        agent = self._make_agent()
        bash = agent.skill("bash")
        assert hasattr(bash, "forward")

    def test_run_skill(self):
        agent = self._make_agent()
        r = agent.run_skill("bash", command="echo agent_test")
        assert r["success"]
        assert "agent_test" in r["stdout"]

    def test_skill_schema(self):
        agent = self._make_agent()
        schema = agent.skill_schema()
        assert len(schema) == SKILL_COUNT
        assert "bash" in schema
        assert "claudecode" in schema
        assert "websurf" in schema

    def test_skill_schema_filtered(self):
        agent = self._make_agent()
        agent._skill_names = ["bash", "read"]
        schema = agent.skill_schema()
        assert len(schema) == 2

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

    def test_run_plan_executes_skills(self):
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

    def test_run_plan_unknown_skill(self):
        agent = self._make_agent()
        plan = [{"tool": "nonexistent_skill_xyz", "params": {}}]
        result = agent.run_plan(plan, safety=False)
        assert "result" in result[0] or "error" in result[0]

    def test_run_plan_empty(self):
        agent = self._make_agent()
        result = agent.run_plan([], safety=False)
        assert result == []

    # ── init_memory ──

    def test_init_memory(self):
        agent = self._make_agent()
        tools = agent.skill_schema()
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
        agent.model = type("M", (), {"forward": staticmethod(lambda *a, **k: "the actual answer")})()
        step = agent._force_answer(model="x", max_tokens=10, temperature=0.0, free=True)
        assert step["tool"] == "response"
        assert step["result"] == "the actual answer"

    def test_force_answer_honors_finish_step(self):
        agent = self._make_agent()
        agent._images = []
        agent.init_memory(query="q", path="/tmp", tools={})
        out = '<PLAN><STEP>{"tool": "finish", "params": {"summary": "final words"}}</STEP></PLAN>'
        agent.model = type("M", (), {"forward": staticmethod(lambda *a, **k: out)})()
        step = agent._force_answer(model="x", max_tokens=10, temperature=0.0, free=True)
        assert step["result"] == "final words"

    def test_force_answer_model_error_returns_none(self):
        agent = self._make_agent()
        agent._images = []
        agent.init_memory(query="q", path="/tmp", tools={})
        def boom(*a, **k):
            raise RuntimeError("provider down")
        agent.model = type("M", (), {"forward": staticmethod(boom)})()
        assert agent._force_answer(model="x", max_tokens=10, temperature=0.0, free=True) is None

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

class TestSkillPipeline:
    def test_full_pipeline(self, skills, tmpdir):
        p = os.path.join(tmpdir, "pipeline.py")
        skills.run("write", file_path=p, content="def hello():\n    return 'world'\n")
        r = skills.run("glob", pattern="*.py", path=tmpdir)
        assert r["total"] == 1
        r = skills.run("grep", pattern="def hello", path=tmpdir)
        assert r["total"] == 1
        r = skills.run("read", file_path=p)
        assert "hello" in r["content"]
        r = skills.run("edit", file_path=p, old_string="'world'", new_string="'earth'")
        assert r["success"]
        r = skills.run("read", file_path=p)
        assert "'earth'" in r["content"]

    def test_multi_file_grep(self, skills, tmpdir):
        for i in range(5):
            p = os.path.join(tmpdir, f"file{i}.py")
            content = f"TARGET_{i} = True\n" if i % 2 == 0 else f"other = False\n"
            skills.run("write", file_path=p, content=content)
        r = skills.run("grep", pattern="TARGET", path=tmpdir, file_pattern="*.py")
        assert r["success"]
        assert r["total"] == 3


# ═══════════════════════════════════════════════════════════════════════
#  MOD CLASS
# ═══════════════════════════════════════════════════════════════════════

class TestMod:
    def _make_mod(self):
        from src.mod import Mod, Agent
        mod = Mod.__new__(Mod)
        mod.skills = Skills()
        mod.agents = Agents()
        from src.toolbox.mod import Toolboxes
        mod.toolboxes = Toolboxes(skills=mod.skills)
        mod._snapped = []
        mod.memory = Memory()
        mod.memory.clear()
        mod.model = None
        mod._skill_names = None
        mod.api_port = 50117
        mod.app_port = 3117
        mod.src_dir = Path(os.path.join(os.path.dirname(__file__), '..', 'src'))
        mod.module_dir = Path(os.path.join(os.path.dirname(__file__), '..'))
        mod._owner = None  # no owner = unrestricted
        mod._portal_root = "/tmp/agent_test_portal"
        mod._acl_path = Path("/tmp/agent_test_acl.json")
        mod._acl = {}
        mod._public_actions = {'status', 'health', 'skills', 'schema',
                               'agents', 'agent', 'chains'}
        mod._admin_actions = {'run', 'plan', 'skill', 'serve', 'kill',
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
        assert "skills" in s
        assert len(s["skills"]) == SKILL_COUNT
        assert "agents" in s
        assert len(s["agents"]) >= AGENT_COUNT

    def test_mod_inherits_agent(self):
        mod = self._make_mod()
        assert hasattr(mod, "forward")
        assert hasattr(mod, "plan")
        assert hasattr(mod, "parse_steps")
        assert hasattr(mod, "run_plan")
        assert hasattr(mod, "run_skill")
        assert hasattr(mod, "skill_schema")

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


# ═══════════════════════════════════════════════════════════════════════
#  GATE / ACCESS CONTROL
# ═══════════════════════════════════════════════════════════════════════

class TestGate:
    def _make_mod_with_owner(self, owner="0xowner"):
        from src.mod import Mod, Agent
        mod = Mod.__new__(Mod)
        mod.skills = Skills()
        mod.agents = Agents()
        from src.toolbox.mod import Toolboxes
        mod.toolboxes = Toolboxes(skills=mod.skills)
        mod._snapped = []
        mod.memory = Memory()
        mod.memory.clear()
        mod.model = None
        mod._skill_names = None
        mod.api_port = 50117
        mod.app_port = 3117
        mod.src_dir = Path(os.path.join(os.path.dirname(__file__), '..', 'src'))
        mod.module_dir = Path(os.path.join(os.path.dirname(__file__), '..'))
        mod._owner = owner
        mod._portal_root = "/tmp/agent_test_portal"
        mod._acl_path = Path(tempfile.mktemp(suffix=".json"))
        mod._acl = {}
        mod._public_actions = {'status', 'health', 'skills', 'schema',
                               'agents', 'agent', 'chains'}
        mod._admin_actions = {'run', 'plan', 'skill', 'serve', 'kill',
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
        assert mod.is_allowed("0xrandom", "skills")
        assert mod.is_allowed("0xrandom", "schema")
        assert mod.is_allowed("0xrandom", "agents")

    def test_admin_actions_blocked_for_non_owner(self):
        mod = self._make_mod_with_owner("0xowner")
        assert not mod.is_allowed("0xrandom", "run")
        assert not mod.is_allowed("0xrandom", "skill")
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
        r = mod.forward("grant", key="0xowner", address="0xuser1", actions=["run", "skill"])
        assert r["granted"] == "0xuser1"
        assert r["actions"] == ["run", "skill"]
        # user1 can now run
        assert mod.is_allowed("0xuser1", "run")
        assert mod.is_allowed("0xuser1", "skill")
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
        mod.forward("grant", key="0xowner", address="0xuser1", actions=["run", "skill"])
        # reload from disk
        mod._acl = mod._load_acl()
        assert "0xuser1" in mod._acl
        # cleanup
        if mod._acl_path.exists():
            mod._acl_path.unlink()

    def test_default_grant_actions(self):
        mod = self._make_mod_with_owner("0xowner")
        r = mod.forward("grant", key="0xowner", address="0xuser2")
        # default is ['run', 'skill']
        assert r["actions"] == ["run", "skill"]

    def test_revoke_nonexistent_is_safe(self):
        mod = self._make_mod_with_owner("0xowner")
        r = mod.forward("revoke", key="0xowner", address="0xnobody")
        assert r["was_granted"] is False

    # ── _run: prompt override + memory note injection ────────────────

    def _capture_run(self, mod):
        """Stub mod.run to record the active goal and forwarded kwargs."""
        captured = {}
        def fake_run(**kw):
            captured['goal'] = mod.goal
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
        # goal restored after the run
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
        a._images = ['stale']
        a.skill_schema = lambda *_a, **_k: {}
        a.memory = type('M', (), {'compile': lambda self, q: None})()
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

    def test_skills(self):
        client = self._get_app()
        r = client.get("/skills")
        assert r.status_code == 200
        data = r.json()
        assert "skills" in data
        assert "schemas" in data
        assert len(data["skills"]) == SKILL_COUNT
        assert "bash" in data["skills"]
        assert "claudecode" in data["skills"]
        assert "websurf" in data["skills"]

    def test_schema(self):
        client = self._get_app()
        r = client.get("/schema")
        assert r.status_code == 200
        data = r.json()
        assert "bash" in data
        assert "claudecode" in data
        assert "params" in data["bash"]

    def test_skill_run(self):
        client = self._get_app()
        r = client.post("/skills/run", json={"name": "bash", "params": {"command": "echo api_test"}})
        assert r.status_code == 200
        data = r.json()
        assert data["skill"] == "bash"
        assert data["result"]["success"]
        assert "api_test" in data["result"]["stdout"]

    def test_skill_run_unknown(self):
        client = self._get_app()
        r = client.post("/skills/run", json={"name": "nonexistent_xyz", "params": {}})
        assert r.status_code == 200
        assert "error" in r.json()

    def test_status(self):
        client = self._get_app()
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert "skills" in data
        assert len(data["skills"]) == SKILL_COUNT

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
        # lower bound: skills installed from the aggregator index here too
        assert kinds.get("skill", 0) >= SKILL_COUNT
        assert kinds.get("agent", 0) >= AGENT_COUNT
        assert kinds.get("prompt", 0) >= 1  # seeded defaults

    def test_library_kind_filter(self):
        client = self._get_app()
        r = client.get("/library", params={"kind": "skill"})
        data = r.json()
        assert data["total"] >= SKILL_COUNT
        assert all(i["kind"] == "skill" for i in data["items"])
        builtin = [i for i in data["items"] if i.get("builtin")]
        assert len(builtin) == SKILL_COUNT

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


# ═══════════════════════════════════════════════════════════════════════
#  LIBRARY (unified prompts / skills / memory / agents index)
# ═══════════════════════════════════════════════════════════════════════

class TestLibrary:
    def _lib(self, tmpdir, registries=False):
        from src.library.mod import Library
        if registries:
            return Library(skills=Skills(), agents=Agents(), dir=tmpdir)
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
        assert kinds["skill"] == SKILL_COUNT
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
        assert out["facets"]["kinds"]["skill"] == SKILL_COUNT

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


# ═══════════════════════════════════════════════════════════════════════
#  TOOLBOXES (snap-on skill bundles)
# ═══════════════════════════════════════════════════════════════════════

class TestToolboxes:
    @pytest.fixture
    def boxes(self, skills, tmpdir):
        from src.toolbox.mod import Toolboxes
        return Toolboxes(skills=skills, path=os.path.join(tmpdir, "toolboxes.json"))

    def test_builtins_present(self, boxes):
        for name in ("core", "explore", "code", "verify", "vcs", "web", "meta"):
            assert name in boxes.ls()
            assert boxes.get(name).builtin

    def test_builtin_tools_all_exist(self, boxes, skills):
        available = set(skills.ls())
        for name in boxes.ls():
            box = boxes.get(name)
            assert set(box.tools) <= available, f"{name} references missing skills"

    def test_resolve_union_dedupes(self, boxes):
        union = boxes.resolve(["core", "code"])
        assert "bash" in union and "patch" in union
        assert len(union) == len(set(union))
        # order preserved: core's tools come first
        assert union.index("bash") < union.index("patch")

    def test_custom_box_persists(self, boxes, skills, tmpdir):
        boxes.add("mybox", ["bash", "git"], "my custom loadout")
        assert "mybox" in boxes.ls()
        from src.toolbox.mod import Toolboxes
        reloaded = Toolboxes(skills=skills, path=os.path.join(tmpdir, "toolboxes.json"))
        assert reloaded.get("mybox").tools == ["bash", "git"]
        assert reloaded.rm("mybox")["existed"]

    def test_custom_box_validates_tools(self, boxes):
        with pytest.raises(ValueError):
            boxes.add("bad", ["not-a-skill"])

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
    """Toolboxes snap onto the agent and scope its live skill set."""

    @pytest.fixture
    def agent(self):
        from src.mod import Agent
        return Agent()

    def test_default_unfiltered(self, agent):
        assert agent.active_skills() is None
        assert len(agent.skill_schema()) == SKILL_COUNT

    def test_snap_scopes_schema(self, agent):
        agent.snap("vcs")
        state = agent.snapped()
        assert state["snapped"] == ["vcs"]
        assert state["filtered"]
        assert set(agent.skill_schema().keys()) == {"git", "diff"}

    def test_snap_union(self, agent):
        agent.snap("vcs")
        agent.snap("web")
        assert set(agent.active_skills()) == {"git", "diff", "fetch", "websurf"}

    def test_unsnap(self, agent):
        agent.snap("vcs")
        agent.snap("web")
        agent.unsnap("vcs")
        assert agent.snapped()["snapped"] == ["web"]
        agent.unsnap()
        assert agent.active_skills() is None
        assert len(agent.skill_schema()) == SKILL_COUNT

    def test_snap_unknown_raises(self, agent):
        with pytest.raises(KeyError):
            agent.snap("no-such-box")

    def test_explicit_skills_beat_snap(self, agent):
        agent.snap("vcs")
        agent._skill_names = ["bash"]
        assert agent.active_skills() == ["bash"]


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
#  DISCOVER — the internet-wide skill aggregator
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
            {"id": "gh:a/pdf-skill", "source": "github", "kind": "skill",
             "name": "pdf-skill", "description": "PDF things",
             "repo": "https://github.com/a/pdf-skill", "stars": 120, "tags": []},
            {"id": "gh:b/unrelated", "source": "github", "kind": "skill",
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
        assert out["facets"]["kinds"] == {"skill": 2}

    def test_kind_filter(self, tmpdir):
        d = self._d(tmpdir)
        d.src_mcp = lambda q, l: [
            {"id": "mcp:x", "source": "mcp", "kind": "mcp", "name": "x",
             "description": "", "repo": "", "tags": []}]
        assert d.search("", sources=["mcp"], kind="skill")["total"] == 0
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
        doc = d.skill_doc("mcp:io.example/thing")
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

    def test_skill_doc_from_mcp_record(self, tmpdir):
        """Non-SKILL.md sources still yield an installable reference card."""
        d = self._d(tmpdir)
        d.detail = lambda i: {
            "id": i, "source": "mcp", "kind": "mcp", "name": "postgres",
            "title": "io.example/postgres", "description": "Query Postgres",
            "url": "https://example.com", "repo": "", "tags": ["mcp"],
            "install": {"remote": "https://example.com/mcp", "tools": ["query"]},
        }
        doc = d.skill_doc("mcp:io.example/postgres")
        assert doc["name"] == "postgres" and doc["kind"] == "mcp"
        assert "https://example.com/mcp" in doc["body"]
        assert "query" in doc["body"]


class TestInstalledSkills:
    """Installing a scanned result adds a document to the library —
    never an executable — and it stays addressable by CID."""

    def _lib(self, tmpdir):
        from src.library.mod import Library
        return Library(skills=Skills(), dir=str(tmpdir))

    def test_install_upsert_and_index(self, tmpdir):
        lib = self._lib(tmpdir)
        assert lib.installed_skills() == []
        s = lib.skill_add("pdf", "# PDF\nsteps", "Handle PDFs", tags=["docs"],
                          source="github", url="https://github.com/a/b/SKILL.md",
                          origin_id="gh:a/b")
        # re-installing the same origin refreshes in place
        s2 = lib.skill_add("pdf", "# PDF\nnewer steps", origin_id="gh:a/b")
        assert s2["id"] == s["id"] and len(lib.installed_skills()) == 1
        assert s2["url"] == s["url"]                   # provenance survives a refresh
        item = [i for i in lib.items(kind="skill")["items"] if i["id"] == s["id"]][0]
        assert item["external"] is True
        assert item["tags"].count("github") == 1       # source tag isn't duplicated
        assert "installed" in item["tags"]

    def test_builtin_skills_are_untouched(self, tmpdir):
        """External installs never shadow or replace the code skill registry."""
        lib = self._lib(tmpdir)
        builtin = {i["name"] for i in lib.items(kind="skill")["items"] if i.get("builtin")}
        lib.skill_add("bash", "# not the real bash skill", origin_id="gh:evil/bash")
        after = lib.items(kind="skill")["items"]
        assert {i["name"] for i in after if i.get("builtin")} == builtin
        assert Skills().get("bash").description                # still the real one

    def test_uninstall(self, tmpdir):
        lib = self._lib(tmpdir)
        s = lib.skill_add("x", "body")
        lib.skill_rm(s["id"])
        assert lib.installed_skills() == []
        with pytest.raises(KeyError):
            lib.skill_rm(s["id"])

    def test_requires_name_and_body(self, tmpdir):
        lib = self._lib(tmpdir)
        with pytest.raises(ValueError):
            lib.skill_add("", "body")
        with pytest.raises(ValueError):
            lib.skill_add("name", "")

    def test_skill_docs_selects_for_run_context(self, tmpdir):
        lib = self._lib(tmpdir)
        a = lib.skill_add("a", "body a")
        lib.skill_add("b", "body b")
        assert [d["name"] for d in lib.skill_docs([a["id"]])] == ["a"]
        assert lib.skill_docs([]) == []
        assert lib.skill_docs(["nope"]) == []

    def test_body_is_clipped(self, tmpdir):
        lib = self._lib(tmpdir)
        s = lib.skill_add("big", "x" * 400_000)
        assert len(s["body"]) == lib.MAX_SKILL_CHARS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
