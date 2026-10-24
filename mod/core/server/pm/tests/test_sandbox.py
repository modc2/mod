"""
Tests for the mod protocol docker sandbox: the layer that runs ANY mod in one
container (PM.boot / PM.serve), decides whether a mod is runnable (PM.ready),
and hands the ones that are not to the build agent (PM.modify).

Every external call (docker, agents, the tree) is mocked — nothing here starts
a container or submits a job.

Lives apart from test_pm.py because that module imports pm.pypm, a backend that
is not in this tree, so it cannot be collected.
"""

import os
import json
import pytest
from unittest.mock import Mock, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Mod protocol sandbox (PM.ready / PM.modify / PM.serve) — fully mocked
# ---------------------------------------------------------------------------

class TestModSandbox:
    """The 'run any mod in one container, hand the broken ones to the agent' layer."""

    @pytest.fixture
    def pm(self, tmp_path):
        from pm.docker.docker import PM
        inst = PM.__new__(PM)
        inst.mod = "mod"
        inst.image = "mod"
        inst.network = "modnet"
        inst.registry = Mock(reg=Mock(), dereg=Mock())
        inst.store = Mock(get=Mock(return_value=[]), put=Mock())
        return inst

    @staticmethod
    def _mod_dir(tmp_path, name="demo", config=None, entry="mod.py"):
        d = tmp_path / name
        d.mkdir()
        if config is not None:
            (d / "config.json").write_text(json.dumps(config))
        if entry:
            (d / entry).write_text("class Mod:\n    pass\n")
        return str(d)

    @staticmethod
    def _broken_report():
        """What ready() actually returns for a mod missing its config.json."""
        return {
            "mod": "demo", "path": "/tree/demo", "ready": False,
            "checks": [{"name": "config", "ok": False,
                        "detail": "no config.json",
                        "fix": "add a config.json with name + fns"}],
            "notes": [], "missing": ["config"], "fix": "m docker/modify demo",
        }

    # --- ready ---

    def test_ready_happy_path(self, pm, tmp_path):
        path = self._mod_dir(tmp_path, config={"name": "demo", "fns": ["health"]})
        with patch.object(type(pm), "is_docker_daemon_on", return_value=True), \
             patch.object(type(pm), "sandbox_image_exists", return_value=True), \
             patch.object(type(pm), "sandbox_running", return_value=False), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.dirpath.return_value = path
            mock_m.get_text = lambda p: open(p).read()
            mock_m.mod.return_value = Mock()
            mock_m.print = print
            report = pm.ready("demo")
        assert report["ready"] is True
        assert report["missing"] == []
        assert report["fix"] is None

    def test_ready_unknown_mod_is_not_ready(self, pm):
        with patch.object(type(pm), "is_docker_daemon_on", return_value=True), \
             patch.object(type(pm), "sandbox_image_exists", return_value=True), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.dirpath.side_effect = AssertionError("Mod nope not found in tree")
            mock_m.print = print
            report = pm.ready("nope")
        assert report["ready"] is False
        assert "resolves" in report["missing"]
        assert report["fix"] == "m docker/modify nope"

    def test_ready_missing_config_is_a_failure(self, pm, tmp_path):
        path = self._mod_dir(tmp_path, config=None)
        with patch.object(type(pm), "is_docker_daemon_on", return_value=True), \
             patch.object(type(pm), "sandbox_image_exists", return_value=True), \
             patch.object(type(pm), "sandbox_running", return_value=False), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.dirpath.return_value = path
            mock_m.mod.return_value = Mock()
            mock_m.print = print
            report = pm.ready("demo")
        assert "config" in report["missing"]

    def test_ready_import_error_is_a_failure(self, pm, tmp_path):
        path = self._mod_dir(tmp_path, config={"name": "demo", "fns": []})
        with patch.object(type(pm), "is_docker_daemon_on", return_value=True), \
             patch.object(type(pm), "sandbox_image_exists", return_value=True), \
             patch.object(type(pm), "sandbox_running", return_value=False), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.dirpath.return_value = path
            mock_m.get_text = lambda p: open(p).read()
            mock_m.mod.side_effect = ImportError("no module named requests")
            mock_m.print = print
            report = pm.ready("demo")
        assert "importable" in report["missing"]

    def test_config_port_outside_band_is_a_note_not_a_failure(self, pm, tmp_path):
        """A mod keeping its own port is normal — serve() remaps it, so it must
        not be the thing that sends a working module to the build agent."""
        path = self._mod_dir(tmp_path, config={"name": "demo", "fns": [], "port": 50091})
        with patch.object(type(pm), "is_docker_daemon_on", return_value=True), \
             patch.object(type(pm), "sandbox_image_exists", return_value=True), \
             patch.object(type(pm), "sandbox_running", return_value=False), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.dirpath.return_value = path
            mock_m.get_text = lambda p: open(p).read()
            mock_m.mod.return_value = Mock()
            mock_m.print = print
            report = pm.ready("demo")
        assert report["ready"] is True
        assert [n["name"] for n in report["notes"]] == ["config_port_published"]

    # --- modify (the build-agent hand-off) ---

    def test_modify_prompt_names_every_failure(self, pm):
        report = {
            "mod": "demo", "path": "/tree/demo", "ready": False,
            "checks": [
                {"name": "config", "ok": False, "detail": "no config.json", "fix": "add one"},
                {"name": "importable", "ok": True, "detail": "ok"},
            ],
            "notes": [], "missing": ["config"], "fix": "m docker/modify demo",
        }
        prompt = pm.modify_prompt("demo", report)
        assert "/tree/demo" in prompt
        assert "config: no config.json" in prompt
        assert "add one" in prompt
        assert "m docker/ready demo" in prompt      # states what done means
        assert "importable" not in prompt           # passing checks are not noise

    def test_modify_short_circuits_when_ready(self, pm):
        with patch.object(type(pm), "ready", return_value={"ready": True, "missing": []}):
            r = pm.modify("demo")
        assert r["ready"] is True
        assert r.get("submitted") is not True

    def test_modify_dispatches_to_the_build_agent(self, pm):
        report = self._broken_report()
        build_agent = Mock()
        build_agent.edit_module.return_value = {"job_id": "job-1"}
        with patch.object(type(pm), "ready", return_value=report), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.mod.return_value = Mock(return_value=build_agent)
            mock_m.print = print
            r = pm.modify("demo")
        assert r["submitted"] is True
        assert r["agent"] == "build"
        assert r["job"] == {"job_id": "job-1"}
        assert r["status"] == "queued"        # a job is not a fix
        kwargs = build_agent.edit_module.call_args.kwargs
        assert kwargs["module_name"] == "demo"
        assert "config" in kwargs["prompt"]

    def test_modify_falls_back_to_the_modify_mod(self, pm):
        report = self._broken_report()
        build_agent = Mock()
        build_agent.edit_module.side_effect = RuntimeError("build api is down")
        modify_agent = Mock()
        modify_agent.forward.return_value = {"applied": True}

        def pick(name):
            return Mock(return_value=build_agent if name == "build" else modify_agent)

        with patch.object(type(pm), "ready", side_effect=[report, {"ready": True, "missing": []}]), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.mod.side_effect = pick
            mock_m.print = print
            r = pm.modify("demo")
        assert r["submitted"] is True
        assert r["agent"] == "modify"
        assert r["status"] == "fixed"
        assert "build" in r["skipped"]        # says why it wasn't the build agent

    def test_modify_does_not_trust_a_sync_agent_that_changed_nothing(self, pm):
        """The modify mod reports applied=True off a heuristic. Re-run the
        checks rather than repeating its optimism back to the caller."""
        report = self._broken_report()
        modify_agent = Mock()
        modify_agent.forward.return_value = {"applied": True}
        with patch.object(type(pm), "ready", side_effect=[report, report]), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.mod.return_value = Mock(return_value=modify_agent)
            mock_m.print = print
            r = pm.modify("demo", agent="modify")
        assert r["status"] == "incomplete"
        assert r["fixed"] is False
        assert r["still_missing"] == ["config"]
        assert r["fix"] == "m docker/modify demo"

    def test_modify_returns_the_brief_when_no_agent_answers(self, pm):
        report = self._broken_report()
        dead = Mock()
        dead.edit_module.side_effect = RuntimeError("down")
        dead.forward.side_effect = RuntimeError("down")
        with patch.object(type(pm), "ready", return_value=report), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.mod.return_value = Mock(return_value=dead)
            mock_m.print = print
            r = pm.modify("demo")
        assert r["submitted"] is False
        assert "demo" in r["prompt"]
        assert set(r["agent_errors"]) == {"build", "modify"}

    def test_modify_dry_run_submits_nothing(self, pm):
        report = self._broken_report()
        with patch.object(type(pm), "ready", return_value=report), \
             patch("pm.docker.docker.m") as mock_m:
            mock_m.mod.side_effect = AssertionError("dry_run must not touch an agent")
            mock_m.print = print
            r = pm.modify("demo", dry_run=True)
        assert r["submitted"] is False
        assert r["prompt"]

    # --- port band / serving ---

    def test_sandbox_serving_parses_ps_output(self, pm):
        out = "m serve polymarket port=50950\nm serve chain port=50951"
        with patch.object(type(pm), "sh", return_value=(0, out)):
            assert pm.sandbox_serving() == {"polymarket": 50950, "chain": 50951}

    def test_free_band_port_skips_taken(self, pm):
        with patch.object(type(pm), "sandbox_serving", return_value={"a": 50950, "b": 50951}):
            assert pm.free_band_port() == 50952

    def test_free_band_port_raises_when_band_is_full(self, pm):
        lo, hi = pm.sandbox_band
        full = {str(p): p for p in range(lo, hi + 1)}
        with patch.object(type(pm), "sandbox_serving", return_value=full):
            with pytest.raises(RuntimeError):
                pm.free_band_port()

    def test_serve_refuses_a_mod_that_is_not_ready(self, pm):
        """The point of the gate: you get the report, not a container that
        half-starts and a log to go dig through."""
        report = {"mod": "demo", "ready": False, "missing": ["config"],
                  "fix": "m docker/modify demo"}
        with patch.object(type(pm), "ready", return_value=report), \
             patch.object(type(pm), "boot", side_effect=AssertionError("must not boot")):
            r = pm.serve("demo")
        assert r["fix"] == "m docker/modify demo"

    def test_serve_reuses_a_mod_already_running_inside(self, pm):
        with patch.object(type(pm), "ready", return_value={"ready": True, "missing": []}), \
             patch.object(type(pm), "boot", return_value={"ok": True}), \
             patch.object(type(pm), "sandbox_serving", return_value={"demo": 50955}):
            r = pm.serve("demo")
        assert r == {"ok": True, "mod": "demo", "port": 50955,
                     "url": "http://localhost:50955", "status": "already_serving"}

    def test_serve_surfaces_boot_failure(self, pm):
        with patch.object(type(pm), "ready", return_value={"ready": True, "missing": []}), \
             patch.object(type(pm), "boot", return_value={"ok": False, "stage": "build"}):
            r = pm.serve("demo")
        assert r["ok"] is False
        assert r["stage"] == "build"

    # --- sh never raises ---

    def test_sh_returns_rc_and_output(self, pm):
        code, out = pm.sh("echo hello")
        assert code == 0 and "hello" in out

    def test_sh_reports_failure_instead_of_raising(self, pm):
        code, out = pm.sh("exit 7")
        assert code == 7

    def test_sh_times_out_cleanly(self, pm):
        code, out = pm.sh("sleep 5", timeout=1)
        assert code == 124 and "timeout" in out
