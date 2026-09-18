"""The human in the loop.

What these pin:
  * which tools need an approval and which do not (a dry-run sync does not;
    a live one does; reads never do),
  * the store's lifecycle — park, decide, expire, and the fact that a second
    decision cannot overturn the first,
  * the MCP dispatcher does NOT execute a gated tool until the answer comes
    back, returns the decline as a tool RESULT (not an error), and fails
    CLOSED when the approval queue is unreachable,
  * the console stream's view: take_new hands each card out once.

No chain, no API, no claude: `tools._request` is faked and the approval
store is driven straight.
"""

import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent import approvals, mcp_server, tools  # noqa: E402

SS58 = "5GsbTgfvgCH4xdqSkiPb7EaBBFLHjWH5vfEALhJaewSFpZX9"


@pytest.fixture(autouse=True)
def clean_store():
    approvals.reset()
    yield
    approvals.reset()


# ── which calls need a human ─────────────────────────────────────

def test_reads_are_free():
    for name in ("ct_traders", "ct_trader", "ct_portfolio", "ct_copies",
                 "ct_trades", "ct_backtest", "ct_wallet", "propose_strat"):
        assert tools.needs_approval(name) is False


def test_every_write_is_gated():
    for name in ("ct_create_copy", "ct_resize_copy", "ct_delete_copy",
                 "ct_pause_copy", "ct_resume_copy", "ct_watch", "ct_unwatch"):
        assert tools.needs_approval(name, {}) is True


def test_dry_run_sync_is_free_but_a_live_one_is_not():
    assert tools.needs_approval("ct_sync", {"dry_run": True}) is False
    assert tools.needs_approval("ct_sync", {}) is True
    assert tools.needs_approval("ct_sync", {"dry_run": False}) is True


def test_describe_says_the_money_out_loud():
    line = tools.describe("ct_create_copy", {"target_ss58": SS58, "alloc_tao": 40})
    assert "40" in line and "5Gsb" in line
    assert "CHAIN" in tools.describe("ct_sync", {})


# ── the store ────────────────────────────────────────────────────

def test_park_then_approve():
    a = approvals.create("run1", "ct_watch", {"ss58": SS58}, "watch it", "low")
    assert a.state == approvals.PENDING
    assert [p.id for p in approvals.pending("run1")] == [a.id]
    approvals.decide(a.id, True, "go on")
    assert approvals.get(a.id).state == approvals.APPROVED
    assert approvals.pending("run1") == []


def test_decline_carries_the_reason_to_the_model():
    a = approvals.create("run1", "ct_sync", {})
    approvals.decide(a.id, False, "not while the board is cold")
    got = approvals.get(a.id)
    assert got.state == approvals.DECLINED
    assert got.note == "not while the board is cold"


def test_a_second_decision_cannot_overturn_the_first():
    a = approvals.create("run1", "ct_delete_copy", {"copy_id": "c1"})
    approvals.decide(a.id, False, "no")
    approvals.decide(a.id, True, "actually yes")
    assert approvals.get(a.id).state == approvals.DECLINED


def test_unanswered_requests_expire_into_a_decline():
    a = approvals.create("run1", "ct_sync", {}, ttl=0.05)
    time.sleep(0.08)
    got = approvals.get(a.id)
    assert got.state == approvals.DECLINED and "expired" in got.note
    assert approvals.waiting("run1") is False


def test_wait_returns_the_moment_a_human_answers():
    a = approvals.create("run1", "ct_create_copy", {"alloc_tao": 5})
    threading.Timer(0.1, approvals.decide, (a.id, True, "")).start()
    t0 = time.time()
    got = approvals.wait(a.id, timeout=5)
    assert got.state == approvals.APPROVED
    assert time.time() - t0 < 2          # not the full timeout


def test_wait_times_out_still_pending():
    a = approvals.create("run1", "ct_sync", {})
    got = approvals.wait(a.id, timeout=0.05)
    assert got.state == approvals.PENDING


def test_runs_do_not_see_each_others_cards():
    approvals.create("run1", "ct_watch", {})
    approvals.create("run2", "ct_watch", {})
    assert len(approvals.pending("run1")) == 1
    assert len(approvals.pending()) == 2


def test_take_new_hands_each_card_out_once():
    approvals.create("run1", "ct_watch", {})
    assert len(approvals.take_new("run1")) == 1
    assert approvals.take_new("run1") == []


def test_resolved_since_is_the_settle_signal():
    a = approvals.create("run1", "ct_watch", {})
    assert approvals.resolved_since("run1", 0) == []
    approvals.decide(a.id, True)
    assert [x.id for x in approvals.resolved_since("run1", 0)] == [a.id]
    assert approvals.resolved_since("run1", time.time() + 1) == []


# ── the dispatcher refuses to act before you answer ──────────────

class GateApi:
    """Stands in for the running API: parks into the real store, waits on
    it, and records whether the underlying tool route was ever hit."""

    def __init__(self, decision=None, note="", ttl=5.0):
        self.decision, self.note, self.ttl = decision, note, ttl
        self.ran = []

    def __call__(self, method, path, params=None, body=None, headers=None,
                 timeout=None):
        if method == "POST" and path == "/agent/approvals":
            a = approvals.create(body["run_id"], body["tool"], body["args"],
                                 body.get("summary", ""), body.get("risk", ""),
                                 ttl=self.ttl)
            if self.decision is not None:
                approvals.decide(a.id, self.decision, self.note)
            return a.public()
        if method == "GET" and path.endswith("/wait"):
            aid = path.split("/")[-2]
            got = approvals.wait(aid, min(params.get("timeout", 1), 0.2))
            return got.public()
        self.ran.append(f"{method} {path}")
        return {"ok": True}


def _call(name, args):
    return mcp_server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": args}})


def _text(reply):
    return reply["result"]["content"][0]["text"]


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setenv("COPYTENSOR_MCP_APPROVAL", "run1")
    monkeypatch.setenv("COPYTENSOR_MCP_SCOPE", "all")


def test_approved_write_reaches_the_api(monkeypatch, gated):
    api = GateApi(decision=True)
    monkeypatch.setattr(tools, "_request", api)
    reply = _call("ct_watch", {"ss58": SS58})
    assert reply["result"]["isError"] is False
    assert api.ran == ["POST /watch"]


def test_declined_write_never_runs_and_reads_as_a_result(monkeypatch, gated):
    api = GateApi(decision=False, note="too much TAO")
    monkeypatch.setattr(tools, "_request", api)
    reply = _call("ct_create_copy", {"target_ss58": SS58, "alloc_tao": 999})
    assert api.ran == []                       # nothing was created
    # A decline is a turn of the conversation, not a crash — isError would
    # make the model retry or apologise instead of asking what to change.
    assert reply["result"]["isError"] is False
    assert "DECLINED" in _text(reply) and "too much TAO" in _text(reply)


def test_an_expired_request_is_a_decline_not_a_run(monkeypatch, gated):
    api = GateApi(decision=None, ttl=0.05)     # nobody ever answers
    monkeypatch.setattr(tools, "_request", api)
    reply = _call("ct_sync", {})
    assert api.ran == []
    assert "DECLINED" in _text(reply)


def test_reads_skip_the_gate_entirely(monkeypatch, gated):
    api = GateApi(decision=False)
    monkeypatch.setattr(tools, "_request", api)
    _call("ct_sync", {"dry_run": True})
    assert api.ran == ["POST /portfolio/sync"]


def test_gate_fails_closed_when_the_queue_is_unreachable(monkeypatch, gated):
    def dead(method, path, **kw):
        if path.startswith("/agent/approvals"):
            raise RuntimeError("connection refused")
        pytest.fail("the tool ran without an approval")
    monkeypatch.setattr(tools, "_request", dead)
    reply = _call("ct_delete_copy", {"copy_id": "c1"})
    assert "DECLINED" in _text(reply)


def test_approval_can_be_turned_off_for_a_client(monkeypatch):
    monkeypatch.setenv("COPYTENSOR_MCP_APPROVAL", "0")
    api = GateApi(decision=False)
    monkeypatch.setattr(tools, "_request", api)
    _call("ct_watch", {"ss58": SS58})
    assert api.ran == ["POST /watch"]


def test_unset_means_gated(monkeypatch):
    monkeypatch.delenv("COPYTENSOR_MCP_APPROVAL", raising=False)
    assert mcp_server.approval_run() == "mcp"
