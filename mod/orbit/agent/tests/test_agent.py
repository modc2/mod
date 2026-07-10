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
        assert len(names) == AGENT_COUNT
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
        assert len(schema) == AGENT_COUNT
        for name, info in schema.items():
            assert "description" in info, f"{name} missing description"

    def test_forward_no_name_lists_all(self, agents):
        r = agents.forward()
        assert "agents" in r
        assert "total" in r
        assert r["total"] == AGENT_COUNT
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
        assert len(agent.agents.ls()) == AGENT_COUNT

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
        assert len(s["agents"]) == AGENT_COUNT

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
        assert len(data["agents"]) == AGENT_COUNT

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
        assert kinds.get("skill") == SKILL_COUNT
        assert kinds.get("agent", 0) >= AGENT_COUNT
        assert kinds.get("prompt", 0) >= 1  # seeded defaults

    def test_library_kind_filter(self):
        client = self._get_app()
        r = client.get("/library", params={"kind": "skill"})
        data = r.json()
        assert data["total"] == SKILL_COUNT
        assert all(i["kind"] == "skill" for i in data["items"])

    def test_prompt_roundtrip(self):
        client = self._get_app()
        r = client.post("/prompts", json={
            "name": "api test prompt", "text": "do the api test thing",
            "tags": ["apitest"]})
        entry = r.json()
        assert entry["id"]
        try:
            r = client.get("/library", params={"q": "api test prompt", "kind": "prompt"})
            assert any(i["id"] == entry["id"] for i in r.json()["items"])
            # tag facet includes the new tag
            assert "apitest" in r.json()["facets"]["tags"]
        finally:
            r = client.delete(f"/prompts/{entry['id']}")
            assert r.json().get("removed") == entry["id"]

    def test_memory_roundtrip(self):
        client = self._get_app()
        r = client.post("/memory", json={
            "name": "api test note", "content": "remember the api test",
            "tags": ["apitest"]})
        entry = r.json()
        assert entry["id"]
        try:
            r = client.get("/memory")
            assert any(n["id"] == entry["id"] for n in r.json()["memory"])
            r = client.get("/library", params={"kind": "memory", "tag": "apitest"})
            assert any(i["id"] == entry["id"] for i in r.json()["items"])
        finally:
            r = client.delete(f"/memory/{entry['id']}")
            assert r.json().get("removed") == entry["id"]

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
