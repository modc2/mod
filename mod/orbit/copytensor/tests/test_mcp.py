"""MCP surface — the dispatcher, the tool registry, and the routes each
ops tool hits. No chain, no bt, no running API: `tools._request` is
replaced by a fake that records the call and answers from a script.

What these pin:
  * the strat agent's scope is read-only (no ct_sync / ct_create_copy in it),
  * ct_sync maps to the portfolio pass (dry_run → preview, copy_id → that
    route, neither → live) with the long timeout,
  * JSON-RPC shape: initialize / tools/list / tools/call / batch /
    notification-is-silent / unknown-method error,
  * the API mounts POST /mcp and GET /mcp/schema on the same dispatcher.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent import mcp_server, tools  # noqa: E402

SS58 = "5GsbTgfvgCH4xdqSkiPb7EaBBFLHjWH5vfEALhJaewSFpZX9"


class FakeApi:
    """Answers `tools._request` from a route table and records every call."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def __call__(self, method, path, params=None, body=None, headers=None,
                 timeout=None):
        self.calls.append({"method": method, "path": path, "params": params,
                           "body": body, "headers": headers, "timeout": timeout})
        key = f"{method} {path}"
        if key in self.routes:
            ans = self.routes[key]
            if isinstance(ans, Exception):
                raise ans
            return json.loads(json.dumps(ans))
        raise RuntimeError(f"{method} {path} -> 404 no route in fake")


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi({
        "GET /status": {"running": True, "reads": "bt"},
        "GET /wallet/balance": {"ss58": SS58, "balance_tao": 12.5},
        "GET /copies": [{"id": "c1", "target_ss58": SS58, "label": "x",
                         "status": "active", "config": {"daily_limit_tao": 5}}],
        "GET /copy/c1": {"id": "c1", "target_ss58": SS58, "status": "active",
                         "alloc_tao": 5.0, "config": {"our_hotkey": SS58},
                         "target_info": {"total_stake_tao": 100,
                                         "top_allocations": list(range(10))}},
        "POST /copy": {"id": "c2", "target_ss58": SS58, "status": "active",
                       "alloc_tao": 7.0, "config": {}},
        "PUT /copy/c1": {"id": "c1", "target_ss58": SS58, "status": "active",
                         "alloc_tao": 9.0, "config": {}},
        "POST /copy/c1/pause": {"id": "c1", "status": "paused"},
        "DELETE /copy/c1": {"deleted": True, "id": "c1"},
        "POST /copy/c1/sync": {"synced": True, "scope": "portfolio", "trades": []},
        "POST /portfolio/sync": {"our_ss58": SS58, "staked_tao": 1, "free_tao": 2,
                                 "rows": [], "sleeves": [], "trades": 0,
                                 "executed": True, "results": [{"status": "ok"}]},
        "GET /portfolio": {"our_ss58": SS58, "rows": [{"netuid": 1}],
                           "sleeves": [], "trades": 1},
        "GET /portfolio/status": {"running": True, "sleeves": 1},
        "GET /trades": [{"id": "t1"}],
        "POST /watch": {"watched": SS58, "total": 7},
        "GET /strats": {"fingerprint": "abc", "strats": [{"id": "s1", "thesis": "long"}]},
        "POST /strats/backtest": {"stats": {"return_pct": 1.0}},
    })
    monkeypatch.setattr(tools, "_request", fake)
    return fake


# ── registry / scopes ────────────────────────────────────────────

def test_agent_scope_is_read_only():
    agent = {t["name"] for t in tools.list_tools("agent")}
    ops = {t["name"] for t in tools.list_tools("ops")}
    assert "propose_strat" in agent
    assert not (agent & ops)
    for writer in ("ct_sync", "ct_create_copy", "ct_delete_copy", "ct_watch"):
        assert writer in ops and writer not in agent
    assert {t["name"] for t in tools.list_tools("all")} == agent | ops
    assert len(tools.ALL_TOOLS) == len(tools.BY_NAME)  # no name collisions


def test_scope_blocks_ops_calls(api):
    with pytest.raises(ValueError, match="unknown tool"):
        tools.call_tool("ct_sync", {"dry_run": True}, scope="agent")
    assert api.calls == []


def test_every_schema_is_well_formed():
    for t in tools.list_tools("all"):
        assert t["name"].startswith(("ct_", "propose_"))
        assert t["inputSchema"]["type"] == "object"
        for pname, p in t["inputSchema"]["properties"].items():
            assert p["type"] and p["description"], (t["name"], pname)


# ── ops tools → routes ───────────────────────────────────────────

def test_sync_dry_run_previews_and_signs_nothing(api):
    out = tools.call_tool("ct_sync", {"dry_run": True})
    assert out["dry_run"] is True
    c = api.calls[-1]
    assert (c["method"], c["path"], c["params"]) == \
        ("POST", "/portfolio/sync", {"dry_run": "true"})


def test_sync_copy_hits_that_copy_with_long_timeout(api):
    out = tools.call_tool("ct_sync", {"copy_id": "c1"})
    assert out["synced"] and out["copy_id"] == "c1"
    c = api.calls[-1]
    assert c["path"] == "/copy/c1/sync"
    assert c["timeout"] == tools.SYNC_TIMEOUT_SEC > tools.TIMEOUT_SEC


def test_sync_live_runs_the_whole_portfolio(api):
    out = tools.call_tool("ct_sync", {})
    assert out["executed"] and out["dry_run"] is False
    assert out["results"] == [{"status": "ok"}]
    c = api.calls[-1]
    assert (c["method"], c["path"], c["params"]) == ("POST", "/portfolio/sync", None)
    assert c["timeout"] == tools.SYNC_TIMEOUT_SEC


def test_portfolio_is_plan_plus_loop(api):
    out = tools.call_tool("ct_portfolio", {})
    assert out["plan"]["rows"] == [{"netuid": 1}] and out["plan"]["executed"] is False
    assert out["loop"] == {"running": True, "sleeves": 1}
    assert [c["method"] for c in api.calls] == ["GET", "GET"]


def test_create_copy_defaults_hotkey_to_wallet(api):
    out = tools.call_tool("ct_create_copy", {"target_ss58": SS58, "alloc_tao": 7})
    assert out["id"] == "c2" and "ct_sync" in out["note"]
    post = [c for c in api.calls if c["method"] == "POST"][0]
    assert post["body"] == {"target_ss58": SS58, "our_hotkey": SS58, "alloc_tao": 7.0}


def test_create_copy_needs_a_wallet(api):
    api.routes["GET /wallet/balance"] = RuntimeError("GET /wallet/balance -> 400 wallet not set")
    with pytest.raises(ValueError, match="no wallet set"):
        tools.call_tool("ct_create_copy", {"target_ss58": SS58, "alloc_tao": 7})
    assert not any(c["method"] == "POST" for c in api.calls)


def test_create_copy_validates_inputs(api):
    with pytest.raises(ValueError, match="SS58"):
        tools.call_tool("ct_create_copy", {"target_ss58": "nope", "alloc_tao": 1})
    with pytest.raises(ValueError, match="alloc_tao"):
        tools.call_tool("ct_create_copy", {"target_ss58": SS58, "alloc_tao": 0})
    with pytest.raises(ValueError, match="unknown arguments"):
        tools.call_tool("ct_create_copy", {"target_ss58": SS58, "alloc_tao": 1,
                                           "mnemonic": "x"})
    assert api.calls == []


def test_wallet_not_set_is_an_answer_not_an_error(api):
    api.routes["GET /wallet/balance"] = RuntimeError("GET /wallet/balance -> 400 wallet not set")
    out = tools.call_tool("ct_wallet", {})
    assert out["wallet_set"] is False and "hint" in out
    api.routes["GET /wallet/balance"] = {"ss58": SS58, "balance_tao": 1.23456789}
    assert tools.call_tool("ct_wallet", {}) == {"wallet_set": True, "ss58": SS58,
                                                "balance_tao": 1.234568}


def test_copy_lifecycle_routes(api):
    assert tools.call_tool("ct_copy", {"copy_id": "c1"})["target"]["top_allocations"] == list(range(5))
    assert tools.call_tool("ct_resize_copy", {"copy_id": "c1", "alloc_tao": 9})["alloc_tao"] == 9.0
    assert api.calls[-1]["body"] == {"alloc_tao": 9}
    with pytest.raises(ValueError, match="nothing to change"):
        tools.call_tool("ct_resize_copy", {"copy_id": "c1"})
    assert tools.call_tool("ct_pause_copy", {"copy_id": "c1"})["status"] == "paused"
    out = tools.call_tool("ct_delete_copy", {"copy_id": "c1"})
    assert out["deleted"] and "unwound on the next sync" in out["note"]
    assert api.calls[-1]["method"] == "DELETE"


def test_watch_and_strats_and_backtest(api):
    assert tools.call_tool("ct_watch", {"ss58": SS58, "label": "L"})["watched"] == SS58
    assert api.calls[-1]["body"] == {"ss58": SS58, "label": "L"}
    out = tools.call_tool("ct_strats", {"owner_key": "k"})
    assert api.calls[-1]["headers"] == {"X-Owner-Key": "k"}
    assert "thesis" not in out["strats"][0]
    tools.call_tool("ct_backtest", {"traders": [{"ss58": SS58, "alloc_tao": 5}], "days": 30})
    assert api.calls[-1]["body"] == {"traders": [{"ss58": SS58, "weight": 1.0, "alloc_tao": 5}],
                                     "days": 30, "capital_tao": 100.0}
    assert tools.call_tool("ct_trades", {"limit": 9999})["count"] == 1
    assert api.calls[-1]["params"] == {"limit": 500}


# ── JSON-RPC dispatcher ──────────────────────────────────────────

def rpc(method, id_=1, **params):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


def test_initialize_and_list(api):
    r = mcp_server.handle_message(rpc("initialize", protocolVersion="2024-11-05"))
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "copytensor"
    assert "ct_sync" in r["result"]["instructions"]
    names = {t["name"] for t in mcp_server.handle_message(rpc("tools/list"))["result"]["tools"]}
    assert {"ct_sync", "ct_status", "propose_strat"} <= names
    agent_names = {t["name"] for t in
                   mcp_server.handle_message(rpc("tools/list"), scope_="agent")["result"]["tools"]}
    assert "ct_sync" not in agent_names


def test_tools_call_ok_and_error(api):
    r = mcp_server.handle_message(rpc("tools/call", name="ct_status", arguments={}))
    assert r["result"]["isError"] is False
    assert json.loads(r["result"]["content"][0]["text"]) == {"running": True, "reads": "bt"}
    r = mcp_server.handle_message(rpc("tools/call", name="ct_nope"))
    assert r["result"]["isError"] is True and "unknown tool" in r["result"]["content"][0]["text"]


def test_notifications_batches_and_unknown_methods(api):
    assert mcp_server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert mcp_server.handle_batch([{"jsonrpc": "2.0", "method": "notifications/initialized"}]) is None
    replies = mcp_server.handle_batch([rpc("ping", 1), rpc("nope", 2),
                                       {"jsonrpc": "2.0", "method": "x"}])
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[0]["result"] == {} and replies[1]["error"]["code"] == -32601
    assert mcp_server.handle_message("garbage")["error"]["code"] == -32600


def test_schema_lists_transports(api):
    s = mcp_server.schema()
    assert "POST /mcp" in s["transports"]["http"]
    assert len(s["tools"]) == len(tools.ALL_TOOLS)


# ── HTTP mount (FastAPI, in-process, no lifespan) ────────────────

@pytest.fixture
def client(api, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    try:
        from src.api import app as app_mod
    except Exception as e:  # bittensor et al. missing on a bare box
        pytest.skip(f"api app not importable here: {e}")
    return TestClient(app_mod.app)


def test_http_mcp_routes(client):
    r = client.post("/mcp", json=rpc("tools/call", name="ct_status", arguments={}))
    assert r.status_code == 200 and r.json()["result"]["isError"] is False
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    r = client.post("/mcp", json=[rpc("ping", 1), rpc("ping", 2)])
    assert [x["id"] for x in r.json()] == [1, 2]
    r = client.post("/mcp", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == -32700
    assert client.get("/mcp").status_code == 405
    s = client.get("/mcp/schema").json()
    assert s["connect"]["http"].endswith("/mcp")
    assert {t["name"] for t in s["tools"]} >= {"ct_sync", "ct_status"}
