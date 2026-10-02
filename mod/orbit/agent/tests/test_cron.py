"""
cron — scheduled agent runs: the engine against a fake host, the scheduler
thread, the Mod gates (owner / 'cron' grantee / stranger), the run adapter
and the compute probe. No model is called.

    python3 -m pytest tests/test_cron.py -q
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cron import compute
from src.cron.mod import Cron, ModHost, Scheduler, Standing, MIN_EVERY
from src.mod import Mod

OWNER, FRIEND, STRANGER = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40


class FakeHost:
    def __init__(self, agents=("scout",), allowed=(OWNER,), fail=False):
        self._agents, self.allowed, self.fail = set(agents), set(allowed), fail
        self.ran = []

    def agents(self): return self._agents
    def standing(self, addr): return None if addr in self.allowed else "revoked"

    def run(self, job):
        self.ran.append(job["id"])
        if self.fail:
            raise RuntimeError("model down")
        return {"status": "done", "summary": f"ran {job['agent']}",
                "compute": {"hostname": "box"}}


def _add(c, **kw):
    return c.add(OWNER, True, **{"agent": "scout", "prompt": "check", "every": 5, **kw})


class TestEngine:
    def test_add_validates(self, tmp_path):
        c = Cron(str(tmp_path))
        with pytest.raises(ValueError):
            c.add(OWNER, True, agent="scout", prompt="", every=5)
        with pytest.raises(ValueError):
            c.add(OWNER, True, agent="scout", prompt="x")          # no every
        with pytest.raises(ValueError):
            _add(c, bogus=1)
        j = _add(c, every=0)
        assert j["every"] == MIN_EVERY and j["enabled"] and j["owner"] == OWNER
        assert j["next_at"] > time.time()

    def test_persists(self, tmp_path):
        j = _add(Cron(str(tmp_path)))
        assert Cron(str(tmp_path)).get(j["id"])["prompt"] == "check"

    def test_due_and_run_records(self, tmp_path):
        c, h = Cron(str(tmp_path)), FakeHost()
        j = _add(c)
        assert c.due() == []
        assert [x["id"] for x in c.due(time.time() + 301)] == [j["id"]]
        res = c.run(h, j["id"])
        assert res["status"] == "done" and res["compute"]["hostname"] == "box"
        got = c.get(j["id"])
        assert got["last"]["summary"] == "ran scout" and got["runs_total"] == 1
        assert got["next_at"] >= res["t"] + 300 - 1

    def test_failures_pause(self, tmp_path):
        c, h = Cron(str(tmp_path)), FakeHost(fail=True)
        j = _add(c, max_fails=2)
        c.run(h, j["id"]); assert c.get(j["id"])["enabled"]
        c.run(h, j["id"])
        got = c.get(j["id"])
        assert not got["enabled"] and "2 failures" in got["paused_reason"]
        # resuming forgives
        got = c.update(j["id"], enabled=True)
        assert got["enabled"] and got["fails"] == 0 and got["paused_reason"] is None

    def test_revoked_standing_pauses_without_running(self, tmp_path):
        c, h = Cron(str(tmp_path)), FakeHost(allowed=())
        j = _add(c)
        res = c.run(h, j["id"])
        assert res["status"] == "paused" and h.ran == []
        assert not c.get(j["id"])["enabled"]

    def test_missing_agent_pauses(self, tmp_path):
        c, h = Cron(str(tmp_path)), FakeHost(agents=())
        j = _add(c)
        assert c.run(h, j["id"])["status"] == "paused" and h.ran == []

    def test_daily_cap(self, tmp_path):
        c, h = Cron(str(tmp_path)), FakeHost()
        j = _add(c, daily_cap=1)
        c.run(h, j["id"])
        assert c.run(h, j["id"])["status"] == "skipped" and len(h.ran) == 1
        c.run(h, j["id"], manual=True)          # by hand goes past the cap
        assert len(h.ran) == 2

    def test_job_limit(self, tmp_path):
        c = Cron(str(tmp_path))
        for _ in range(20):
            c.add(FRIEND, False, agent="scout", prompt="p", every=5)
        with pytest.raises(ValueError):
            c.add(FRIEND, False, agent="scout", prompt="p", every=5)

    def test_remove(self, tmp_path):
        c = Cron(str(tmp_path))
        j = _add(c)
        c.remove(j["id"])
        with pytest.raises(KeyError):
            c.get(j["id"])


class TestScheduler:
    def test_starts_due_jobs(self, tmp_path):
        c, h = Cron(str(tmp_path)), FakeHost()
        j = _add(c)
        c._state["jobs"][j["id"]]["next_at"] = 0
        s = Scheduler(c, h)
        assert s.check() == 1
        for _ in range(50):
            if c.get(j["id"])["last"]:
                break
            time.sleep(0.02)
        assert h.ran == [j["id"]] and c.get(j["id"])["next_at"] > time.time()
        assert s.check() == 0                   # not due again yet

    def test_disabled_is_not_due(self, tmp_path):
        c = Cron(str(tmp_path))
        j = _add(c, enabled=False)
        c._state["jobs"][j["id"]]["next_at"] = 0
        assert c.due() == []


def _mod(tmp_path, acl=None):
    """A Mod with no auth verifier: an address string is its own identity."""
    mod = Mod.__new__(Mod)
    mod._owner = OWNER
    mod._co_owners = ()
    mod.auth = None
    mod.key = None
    mod._acl = acl or {}
    mod.agents = type("A", (), {"ls": lambda self: ["scout"],
                                "get": lambda self, n: {"provider": "liquidai", "model": "lfm"}})()
    mod._cron_inst = Cron(str(tmp_path))
    from src.cron.mod import ModHost as H, Scheduler as S
    mod._cron_host = H(mod)
    mod._cron_sched = S(mod._cron_inst, mod._cron_host)
    return mod


class TestModGates:
    def test_owner_schedules_and_sees_all(self, tmp_path):
        m = _mod(tmp_path, {FRIEND: {"actions": ["cron"]}})
        a = m.cron_add(key=OWNER, agent="scout", prompt="hi", every=10)
        b = m.cron_add(key=FRIEND, agent="scout", prompt="yo", every=10)
        st = m.cron_status(key=OWNER)
        assert {j["id"] for j in st["jobs"]} == {a["id"], b["id"]}
        assert st["you"]["owner"] and st["compute"]["host"]["cores"] >= 1
        # owner manages the friend's job
        assert m.cron_update(b["id"], key=OWNER, every=30)["every"] == 30

    def test_grantee_sees_only_own(self, tmp_path):
        m = _mod(tmp_path, {FRIEND: {"actions": ["cron"]}})
        a = m.cron_add(key=OWNER, agent="scout", prompt="hi", every=10)
        b = m.cron_add(key=FRIEND, agent="scout", prompt="yo", every=10)
        st = m.cron_status(key=FRIEND)
        assert [j["id"] for j in st["jobs"]] == [b["id"]] and st["total"] == 2
        with pytest.raises(PermissionError):
            m.cron_rm(a["id"], key=FRIEND)
        m.cron_rm(b["id"], key=FRIEND)

    def test_star_grant_counts_run_grant_does_not(self, tmp_path):
        m = _mod(tmp_path, {FRIEND: {"actions": ["*"]},
                            STRANGER: {"actions": ["run", "tool_run"]}})
        m.cron_add(key=FRIEND, agent="scout", prompt="x", every=5)
        with pytest.raises(PermissionError):
            m.cron_add(key=STRANGER, agent="scout", prompt="x", every=5)

    def test_stranger_sees_counts_only(self, tmp_path):
        m = _mod(tmp_path)
        m.cron_add(key=OWNER, agent="scout", prompt="secret", every=5)
        for k in (STRANGER, None):
            st = m.cron_status(key=k)
            assert st["jobs"] == [] and st["total"] == 1
            assert not st["you"]["can_schedule"]
        with pytest.raises(PermissionError):
            m.cron_add(key=None, agent="scout", prompt="x", every=5)

    def test_unknown_agent(self, tmp_path):
        with pytest.raises(ValueError):
            _mod(tmp_path).cron_add(key=OWNER, agent="nope", prompt="x", every=5)

    def test_revoke_pauses_on_next_run(self, tmp_path):
        m = _mod(tmp_path, {FRIEND: {"actions": ["cron"]}})
        j = m.cron_add(key=FRIEND, agent="scout", prompt="x", every=5)
        m._acl.pop(FRIEND)
        res = m._cron_inst.run(m._cron_host, j["id"])
        assert res["status"] == "paused" and "cron" in res["summary"]

    def test_scheduler_is_owner_only(self, tmp_path):
        m = _mod(tmp_path, {FRIEND: {"actions": ["*"]}})
        with pytest.raises(PermissionError):
            m.cron_scheduler(False, key=FRIEND)
        assert m.cron_scheduler(False, key=OWNER)["running"] is False

    def test_agent_compute(self, tmp_path):
        m = _mod(tmp_path)
        c = m.compute_info(agent="scout")
        assert c["inference"]["kind"] == "local" and c["inference"]["model"] == "lfm"
        assert c["host"]["hostname"]


class TestRunAdapter:
    def test_owner_run_goes_through_run_with_standing(self, tmp_path):
        m = _mod(tmp_path)
        seen = {}

        def fake_run(**kw):
            seen.update(kw)
            kw["on_step"]({"tool": "finish", "params": {"summary": "all good"}})
            return []
        m._run = fake_run
        m.meter = type("M", (), {"take": lambda self: {}})()
        m.is_free_provider = lambda p=None: True
        m.graph_answer = Mod.graph_answer
        j = m.cron_add(key=OWNER, agent="scout", prompt="go", every=5)
        res = m._cron_inst.run(m._cron_host, j["id"])
        assert res["status"] == "done" and res["summary"] == "all good"
        assert isinstance(seen["key"], Standing) and seen["key"].address == OWNER
        assert seen["agent_type"] == "scout" and seen["query"] == "go"
        assert res["compute"]["inference"]["kind"] == "local"

    def test_grantee_without_credits_is_refused(self, tmp_path):
        m = _mod(tmp_path, {FRIEND: {"actions": ["cron"]}})
        m._run = lambda **kw: pytest.fail("must not run")
        m.meter = type("M", (), {"take": lambda self: {}})()
        m.is_free_provider = lambda p=None: False
        m.credits = type("C", (), {"balance": lambda self, a: 0, "fee_rate": 0.1})()
        j = m.cron_add(key=FRIEND, agent="scout", prompt="go", every=5)
        res = m._cron_inst.run(m._cron_host, j["id"])
        assert res["status"] == "error" and "credits" in res["summary"]


def test_compute_probe():
    h = compute.host()
    assert h["cores"] >= 1 and h["hostname"] and "mem" in h
    assert compute.inference("openrouter")["kind"] == "remote"
    assert compute.inference("browser")["kind"] == "tab"
    assert compute.inference("mod:chutes")["kind"] == "fleet"
    assert compute.inference(harness="claude")["kind"] == "cli"
