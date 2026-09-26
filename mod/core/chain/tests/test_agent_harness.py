"""The chain console's Claude Code harness (src/agent/mod.py), without the CLI.

Run from the module dir:  python3 -m pytest tests/test_agent_harness.py -q
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent / "src" / "agent" / "mod.py"


@pytest.fixture()
def runner(tmp_path, monkeypatch):
    """The runner over a throwaway build dir; the CLI is a stub."""
    monkeypatch.setenv("CHAIN_BUILD_DIR", str(tmp_path / "build"))
    spec = importlib.util.spec_from_file_location("chain_agent_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._running.clear()
    return mod


def _seed(mod, who, name, files):
    store = mod.BUILD_DIR / mod.PROJECTS
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({who: {name: {"files": files, "updated": 1}}}))


# ── layout + workspace ──────────────────────────────────────────────────────

def test_layout_puts_files_where_hardhat_looks(runner):
    out = runner.layout({"Foo.sol": "a", "Foo.test.js": "b", "contracts/Bar.sol": "c",
                         "test/x.ts": "d", "../evil.sol": "e", "README.md": "f"})
    assert out == {"contracts/Foo.sol": "a", "test/Foo.test.js": "b", "contracts/Bar.sol": "c",
                   "test/x.ts": "d", "README.md": "f"}


def test_workspace_is_a_hardhat_project(runner):
    files = {"Counter.sol": "contract Counter {}", "Counter.test.js": "it('x')"}
    root = runner.Mod().workspace("0xabc", "counter", files)
    assert (root / "contracts" / "Counter.sol").read_text() == "contract Counter {}"
    assert (root / "test" / "Counter.test.js").read_text() == "it('x')"
    assert (root / "hardhat.config.js").is_file()
    assert (root / "package.json").is_file()
    assert (root / "node_modules").is_symlink()
    note = (root / "CLAUDE.md").read_text()
    assert "contracts/Counter.sol" in note and "never deploy" in note.lower()


def test_workspace_drops_the_last_runs_files(runner):
    r = runner.Mod()
    root = r.workspace("0xabc", "p", {"A.sol": "a"})
    (root / "contracts" / "Stale.sol").write_text("old")
    (root / "notes.txt").write_text("old")
    root = r.workspace("0xabc", "p", {"B.sol": "b"})
    assert sorted(p.name for p in (root / "contracts").iterdir()) == ["B.sol"]
    assert not (root / "notes.txt").exists()
    assert (root / "node_modules").is_symlink()          # kept, not rebuilt


def test_sync_back_writes_the_diff_into_the_project(runner):
    r = runner.Mod()
    _seed(runner, "0xabc", "p", {"A.sol": "a", "test/A.test.js": "t"})
    before = r.project_files("0xabc", "p")
    root = r.workspace("0xabc", "p", before)
    (root / "contracts" / "A.sol").write_text("a2")
    (root / "test" / "B.test.js").write_text("new")
    (root / "test" / "A.test.js").unlink()
    after = r.collect(root)
    diff = r.sync_back("0xabc", "p", before, after)
    assert diff == {"added": ["test/B.test.js"], "removed": ["test/A.test.js"],
                    "edited": ["contracts/A.sol"],
                    "changed": ["contracts/A.sol", "test/A.test.js", "test/B.test.js"]}
    saved = r.project_files("0xabc", "p")
    assert saved == {"contracts/A.sol": "a2", "test/B.test.js": "new"}


def test_sync_back_untouched_project_writes_nothing(runner):
    r = runner.Mod()
    _seed(runner, "0xabc", "p", {"A.sol": "a"})
    before = r.project_files("0xabc", "p")
    root = r.workspace("0xabc", "p", before)
    diff = r.sync_back("0xabc", "p", before, r.collect(root))
    assert diff["changed"] == []
    assert json.loads((runner.BUILD_DIR / runner.PROJECTS).read_text())["0xabc"]["p"]["updated"] == 1


# ── the CLI's stream, as steps ──────────────────────────────────────────────

def _ev(**kw):
    return json.dumps(kw)


def test_trace_turns_stream_json_into_steps(runner):
    t = runner.Trace()
    steps = []
    steps += t.line(_ev(type="system", subtype="init", session_id="s1"))
    steps += t.line(_ev(type="assistant", message={"content": [
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "contracts/A.sol"}},
    ]}))
    # the narration flushed when the tool call followed it; the tool step is still open
    assert [s["tool"] for s in steps] == ["response"]
    steps += t.line(_ev(type="user", message={"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "contract A {}"},
    ]}))
    assert steps[-1] == {"tool": "read", "params": {"file_path": "contracts/A.sol"}, "result": "contract A {}"}
    steps += t.line(_ev(type="assistant", message={"content": [
        {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "npx hardhat test", "description": "run"}},
    ]}))
    steps += t.line(_ev(type="user", message={"content": [
        {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
         "content": [{"type": "text", "text": "1 failing"}]},
    ]}))
    assert steps[-1]["tool"] == "bash" and steps[-1]["error"] == "1 failing"
    steps += t.line(_ev(type="assistant", message={"content": [{"type": "text", "text": "Done: fixed it."}]}))
    steps += t.line(_ev(type="result", subtype="success", result="Done: fixed it.",
                        total_cost_usd=0.02, num_turns=3, session_id="s1"))
    assert t.done and t.final == "Done: fixed it." and t.cost == 0.02
    steps += t.close("completed", synced={"changed": ["contracts/A.sol"]})
    assert steps[-1] == {"tool": "finish", "params": {"summary": "Done: fixed it.",
                                                        "changed": ["contracts/A.sol"],
                                                        "cost_usd": 0.02, "turns": 3}}
    # the answer rides on finish, not as a duplicate response step
    assert [s["tool"] for s in steps].count("response") == 1


def test_trace_reports_unanswered_tools_and_failures(runner):
    t = runner.Trace()
    t.line(_ev(type="assistant", message={"content": [
        {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "x", "old_string": "a", "new_string": "b"}},
    ]}))
    steps = t.close("failed", error="claude exited 1")
    assert steps[0]["tool"] == "edit" and steps[0]["error"] == "claude exited 1"
    assert steps[-1] == {"tool": "error", "params": {}, "error": "claude exited 1"}


def test_trace_relativizes_workspace_paths(runner):
    t = runner.Trace("/ws/root")
    t.line(_ev(type="assistant", message={"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/ws/root/contracts/A.sol"}},
    ]}))
    steps = t.line(_ev(type="user", message={"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}))
    assert steps[0]["params"]["file_path"] == "contracts/A.sol"


def test_trace_ignores_noise(runner):
    t = runner.Trace()
    assert t.line("") == [] and t.line("not json") == [] and t.line('{"type":"weird"}') == []


# ── the command line ────────────────────────────────────────────────────────

def test_command_sandboxes_the_cli(runner):
    cmd = runner.Mod().command("fix it", model="opus", goal="be brief", note="project: p")
    assert cmd[:2] == ["--print", "--verbose"]
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
    assert all(a.startswith("Bash(npx hardhat") for a in allowed)
    assert "WebFetch" in cmd and "Task" in cmd
    assert cmd[cmd.index("--append-system-prompt") + 1] == "be brief\n\nproject: p"
    assert cmd[-2:] == ["-p", "fix it"]


def test_command_default_model(runner):
    cmd = runner.Mod().command("hi")
    assert cmd[cmd.index("--model") + 1] == runner.DEFAULT_MODEL
    assert "--append-system-prompt" not in cmd


# ── the run, with a stub CLI ────────────────────────────────────────────────

@pytest.fixture()
def fake_claude(tmp_path, monkeypatch):
    """A `claude` that edits a file, prints a stream, and exits."""
    script = tmp_path / "claude"
    script.write_text("""#!/usr/bin/env python3
import json, sys, os
if '--version' in sys.argv:
    print('9.9.9 (Claude Code)'); sys.exit(0)
def ev(**kw): print(json.dumps(kw), flush=True)
ev(type='system', subtype='init', session_id='fake')
ev(type='assistant', message={'content': [{'type': 'tool_use', 'id': 'a', 'name': 'Read', 'input': {'file_path': 'contracts/A.sol'}}]})
ev(type='user', message={'content': [{'type': 'tool_result', 'tool_use_id': 'a', 'content': open('contracts/A.sol').read()}]})
open('contracts/A.sol', 'w').write('contract A { uint x; }')
os.makedirs('test', exist_ok=True)
open('test/A.test.js', 'w').write("it('works')")
ev(type='assistant', message={'content': [{'type': 'tool_use', 'id': 'b', 'name': 'Write', 'input': {'file_path': 'test/A.test.js', 'content': "it('works')"}}]})
ev(type='user', message={'content': [{'type': 'tool_result', 'tool_use_id': 'b', 'content': 'ok'}]})
ev(type='result', subtype='success', result='Added a test.', total_cost_usd=0.01, num_turns=2, session_id='fake')
""")
    script.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BIN", str(script))
    return script


def test_run_end_to_end_with_stub_cli(runner, fake_claude):
    r = runner.Mod()
    _seed(runner, "0xabc", "p", {"A.sol": "contract A {}"})
    seen = []
    steps = r.run("add a test", project="p", address="0xABC", network="ganache",
                  on_step=seen.append, timeout=60)
    tools = [s["tool"] for s in steps]
    assert tools == ["workspace", "read", "write", "project", "finish"]
    assert steps == seen
    assert all(s["run"] == steps[0]["run"] for s in steps)
    assert steps[-1]["params"]["summary"] == "Added a test."
    assert steps[-1]["params"]["changed"] == ["contracts/A.sol", "test/A.test.js"]
    # the project has the agent's edits
    assert r.project_files("0xabc", "p") == {"contracts/A.sol": "contract A { uint x; }",
                                             "test/A.test.js": "it('works')"}
    # and the run is on the ledger
    runs = r.runs("0xabc")
    assert runs[0]["status"] == "completed" and runs[0]["project"] == "p"
    assert runs[0]["changed"] == ["contracts/A.sol", "test/A.test.js"]
    assert runs[0]["cost_usd"] == 0.01
    assert r.harness()["available"] and r.harness()["version"].startswith("9.9.9")


def test_run_resolves_bare_address_and_default_project(runner, fake_claude):
    r = runner.Mod()
    steps = r.run("go", key="0x" + "1" * 40, timeout=60)
    assert steps[0]["params"]["project"] == runner.DEFAULT_PROJECT
    assert r.runs("0x" + "1" * 40)[0]["project"] == runner.DEFAULT_PROJECT


def test_run_without_cli_says_so(runner, monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", "/nonexistent/claude")
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    monkeypatch.setattr(runner.os.path, "isfile", lambda p: False)
    with pytest.raises(RuntimeError, match="not installed"):
        runner.Mod().run("hi", project="p", address="0xabc")


def test_run_timeout_kills_the_cli(runner, tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text("#!/usr/bin/env python3\nimport time\nprint('{\"type\":\"system\"}', flush=True)\ntime.sleep(60)\n")
    script.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BIN", str(script))
    _seed(runner, "0xabc", "p", {"A.sol": "x"})
    monkeypatch.setattr(runner.time, "time", (lambda real: (lambda: real() + 100))(runner.time.time))
    steps = runner.Mod().run("hang", project="p", address="0xabc", timeout=30)
    assert steps[-1]["tool"] == "error" and "exceeded" in steps[-1]["error"]
    assert runner._running == {}


def test_concurrency_is_capped(runner, fake_claude):
    r = runner.Mod()
    runner._running["x"] = {"who": "0xabc", "project": "p", "started": 0}
    with pytest.raises(RuntimeError, match="already working on p"):
        r.run("again", project="p", address="0xabc")
    runner._running["y"] = {"who": "0xdef", "project": "q", "started": 0}
    with pytest.raises(RuntimeError, match="already in flight"):
        r.run("more", project="z", address="0x999")
