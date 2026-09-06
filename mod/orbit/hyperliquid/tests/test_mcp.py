"""Tests for the MCP tool server and its binding to the mod protocol.

Two layers:

* offline — config.json, mod.py and the Rust tool registry must agree on the
  fn surface. These run anywhere and are what catches schema drift.
* live — if the API answers on HL_API_URL (default :8919), the JSON-RPC
  handshake, tool dispatch and the auth gate are exercised for real; skipped
  otherwise so the suite stays runnable offline.

Run:  cd orbit/hyperliquid && python3 -m pytest tests/ -q
"""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CONFIG = json.loads((ROOT / "config.json").read_text())
MCP_RS = (ROOT / "src" / "api" / "src" / "mcp.rs").read_text()
API_URL = os.environ.get("HL_API_URL", f"http://localhost:{CONFIG['ports']['api']}")


def hyperliquid_class():
    """Load src/mod.py under its own name.

    It does `import mod as m` (the framework package), and this suite puts
    src/ on sys.path for `strats` — so importing it as plain `mod` would make
    it shadow the framework and re-enter itself. Bind the framework to the
    name `mod` first, then load the file under a private name.
    """
    src = str(ROOT / "src")
    without_src = [p for p in sys.path if p != src]
    sys.path, saved = without_src, sys.path
    try:
        import mod  # noqa: F401  — the framework package, now pinned in sys.modules
    finally:
        sys.path = saved
    spec = importlib.util.spec_from_file_location("hyperliquid_modpy", ROOT / "src" / "mod.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Hyperliquid


def rust_tools():
    """(tool name, mod fn) for every tool declared in the Rust registry."""
    return re.findall(r'tool\(\s*"(hl_\w+)",\s*"(\w+)"', MCP_RS)


def rpc(method, params=None, token=None, timeout=60):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(f"{API_URL}/mcp", timeout=timeout, headers=headers,
                      json={"jsonrpc": "2.0", "id": 1, "method": method,
                            "params": params or {}})
    r.raise_for_status()
    return r.json()


def live():
    try:
        return requests.get(f"{API_URL}/health", timeout=3).ok
    except requests.RequestException:
        return False


needs_api = pytest.mark.skipif(not live(), reason=f"no API at {API_URL}")


# ── offline: schema ↔ mod protocol ──────────────────────────────────────

def test_config_declares_the_mcp_endpoint():
    mcp = CONFIG["mcp"]
    assert mcp["endpoint"] == "POST /mcp"
    assert mcp["schema"] == "GET /mcp/schema"
    assert mcp["tool_prefix"] == "hl_"
    # Every transport a client might arrive on is advertised, not just one.
    for transport in ("streamable-http", "/sse", "--stdio"):
        assert transport in mcp["transport"], f"{transport} is not advertised"


def test_mcp_config_covers_every_transport():
    """`mcp_config` is what a caller pastes into a client — an omitted
    transport is a client that cannot connect."""
    cfg = hyperliquid_class()(api_url="http://localhost:1").mcp_config()
    assert cfg["http"]["url"].endswith("/mcp")
    assert cfg["sse"]["url"].endswith("/sse")
    assert cfg["stdio"]["args"] == ["--stdio"]
    assert "--transport http" in cfg["add_cmd"]


def test_mod_py_and_config_declare_the_same_fns():
    fns = hyperliquid_class().fns
    assert set(fns) == set(CONFIG["fns"]), (
        "config.json fns and mod.py fns have drifted: "
        f"{set(fns) ^ set(CONFIG['fns'])}"
    )


def test_every_declared_fn_is_callable():
    cls = hyperliquid_class()
    missing = [f for f in cls.fns if not callable(getattr(cls, f, None))]
    assert not missing, f"declared but not implemented: {missing}"


def test_every_mcp_tool_fronts_a_mod_protocol_fn():
    tools = rust_tools()
    assert len(tools) > 40, "tool registry did not parse"
    unknown = [(name, fn) for name, fn in tools if fn not in CONFIG["fns"]]
    assert not unknown, f"tools front fns config.json does not declare: {unknown}"


def test_tool_names_are_unique():
    names = [n for n, _ in rust_tools()]
    assert len(names) == len(set(names))


# ── live: JSON-RPC over the running API ─────────────────────────────────

@needs_api
def test_initialize_echoes_a_supported_protocol_version():
    r = rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert r["protocolVersion"] == "2025-06-18"
    assert r["serverInfo"]["name"] == "hyperliquid"
    assert r["serverInfo"]["version"] == CONFIG["version"]
    assert "tools" in r["capabilities"]


@needs_api
def test_unknown_method_is_a_jsonrpc_error():
    assert rpc("does/not/exist")["error"]["code"] == -32601


@needs_api
def test_tools_list_matches_the_registry():
    tools = rpc("tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == {n for n, _ in rust_tools()}
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


@needs_api
def test_schema_route_publishes_the_fn_mapping():
    doc = requests.get(f"{API_URL}/mcp/schema", timeout=10).json()
    assert doc["mcp"]["endpoint"] == "/mcp"
    by_name = {t["name"]: t for t in doc["tools"]}
    assert by_name["hl_trade"]["fn"] == "trade"
    assert by_name["hl_trade"]["public"] is False
    assert by_name["hl_mids"]["public"] is True
    # Public flags must agree with what the info route advertises.
    info = requests.get(f"{API_URL}/", timeout=10).json()
    assert info["mcp"]["tools"] == len(doc["tools"])


@needs_api
def test_public_tool_needs_no_token():
    r = rpc("tools/call", {"name": "hl_status", "arguments": {}})["result"]
    assert r["isError"] is False
    assert r["structuredContent"]["ok"] is True


def skip_if_rate_limited(result):
    """Hyperliquid 429s aggressive /info callers; that is upstream, not us."""
    if result["isError"] and "429" in result["content"][0]["text"]:
        pytest.skip("Hyperliquid upstream rate limit")


@needs_api
def test_path_and_query_arguments_reach_the_route():
    r = rpc("tools/call", {"name": "hl_candles",
                           "arguments": {"coin": "ETH", "interval": "1h", "hours": 3}})["result"]
    skip_if_rate_limited(r)
    assert r["isError"] is False
    assert all(c["s"] == "ETH" for c in r["structuredContent"]["result"])


@needs_api
def test_market_data_tool_returns_live_prices():
    r = rpc("tools/call", {"name": "hl_mids", "arguments": {}})["result"]
    skip_if_rate_limited(r)
    assert r["isError"] is False
    assert float(r["structuredContent"]["BTC"]) > 0


@needs_api
def test_missing_required_argument_fails_before_the_call():
    r = rpc("tools/call", {"name": "hl_orderbook", "arguments": {}})["result"]
    assert r["isError"] is True
    assert "requires `coin`" in r["content"][0]["text"]


@needs_api
def test_unknown_tool_is_reported_as_a_tool_error():
    r = rpc("tools/call", {"name": "hl_nope", "arguments": {}})["result"]
    assert r["isError"] is True
    assert "unknown tool" in r["content"][0]["text"]


@needs_api
def test_a_batch_answers_every_request_in_it():
    """A client that pipelines two calls into one POST must get two responses
    back — answering only the first leaves it waiting on an id forever."""
    r = requests.post(f"{API_URL}/mcp", timeout=30, json=[
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    r.raise_for_status()
    out = r.json()
    assert [m["id"] for m in out] == [1, 2], "notifications must draw no response"
    assert len(out[1]["result"]["tools"]) == len(rust_tools())


@needs_api
def test_a_client_that_only_takes_a_stream_gets_one():
    r = requests.post(f"{API_URL}/mcp", timeout=30,
                      headers={"Accept": "text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    r.raise_for_status()
    assert r.headers["content-type"].startswith("text/event-stream")
    assert '"id":1' in r.text


# ── live: the HTTP+SSE transport (GET /sse → POST /messages) ────────────

def sse_session(timeout=30):
    """Open GET /sse and return (response, line iterator, endpoint URL) once
    the stream has named where to post.

    The iterator is handed back rather than re-derived per read on purpose:
    abandoning a half-consumed `iter_lines` generator makes requests release
    the connection, which closes the stream and reaps the session.
    """
    r = requests.get(f"{API_URL}/sse", stream=True, timeout=timeout,
                     headers={"Accept": "text/event-stream"})
    r.raise_for_status()
    lines = r.iter_lines(decode_unicode=True)
    event = None
    for raw in lines:
        if not raw:
            continue
        if raw.startswith("event: "):
            event = raw[7:]
        elif raw.startswith("data: "):
            assert event == "endpoint", f"first event was {event}, not endpoint"
            # Relative by design, so it resolves behind the gateway prefix too.
            assert not raw[6:].startswith("/"), "endpoint must be relative"
            return r, lines, f"{API_URL}/{raw[6:].lstrip('/')}"
    raise AssertionError("stream closed before naming an endpoint")


def sse_next(lines):
    """Next `message` event's payload off an open stream."""
    event = None
    for raw in lines:
        if not raw:
            continue
        if raw.startswith("event: "):
            event = raw[7:]
        elif raw.startswith("data: ") and event == "message":
            return json.loads(raw[6:])
    raise AssertionError("stream closed before answering")


@needs_api
def test_sse_transport_handshakes_and_calls_a_tool():
    stream, lines, endpoint = sse_session()
    try:
        post = requests.post(endpoint, timeout=30, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "pytest", "version": "1"}}})
        # Responses ride the stream, so the POST itself just gets accepted.
        assert post.status_code == 202
        hello = sse_next(lines)["result"]
        assert hello["serverInfo"]["name"] == "hyperliquid"
        assert hello["protocolVersion"] == "2024-11-05"

        requests.post(endpoint, timeout=30,
                      json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "hl_status", "arguments": {}}})
        called = sse_next(lines)
        assert called["id"] == 2
        assert called["result"]["isError"] is False
    finally:
        stream.close()


@needs_api
def test_sse_messages_without_a_live_session_are_refused():
    """Posting to a dead session must fail loudly — a 202 would strand the
    client waiting on a stream that will never carry its answer."""
    r = requests.post(f"{API_URL}/messages?sessionId=not-a-session", timeout=15,
                      json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 404
    r = requests.post(f"{API_URL}/messages", timeout=15,
                      json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 400


@needs_api
def test_schema_advertises_all_three_transports():
    doc = requests.get(f"{API_URL}/mcp/schema", timeout=10).json()
    kinds = {t["type"] for t in doc["mcp"]["transports"]}
    assert kinds == {"streamable-http", "sse", "stdio"}


@needs_api
def test_gated_tool_without_a_token_is_refused():
    """MCP must not be a way around the auth gate."""
    if os.environ.get("HYPERLIQUID_ACCESS_OPEN") == "1":
        pytest.skip("auth guard disabled")
    r = rpc("tools/call", {"name": "hl_list_follows",
                           "arguments": {"follower": "0x" + "de" * 20}})["result"]
    assert r["isError"] is True
    assert "401" in r["content"][0]["text"]


# ── coin requirement on the top-traders board ──

def test_top_traders_tool_declares_the_coin_requirement():
    doc = requests.get(f"{API_URL}/mcp/schema", timeout=10).json()
    tool = next(t for t in doc["tools"] if t["name"] == "hl_top_traders")
    props = tool["inputSchema"]["properties"]
    assert props["coins"]["type"] == "array"


def test_unknown_coin_is_rejected_before_the_scan():
    # A coin nobody trades would otherwise walk the whole scan budget through
    # HL's throttled fills endpoint to find nothing.
    r = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": 3, "coins": "NOPE_COIN"}, timeout=30)
    assert r.status_code == 400
    assert "unknown coin" in r.json()["error"]
    assert r.json()["coins"] == ["NOPE_COIN"]


def test_plain_board_reports_how_it_was_found():
    r = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": 3}, timeout=30).json()
    assert r["coins"] == []
    assert r["depth"] == len(r["traders"]) <= 3


# ── the shape behind a row's number ─────────────────────────────────────
#
# The board hovers this endpoint once per row, so its contract is unusual:
# it is not allowed to fail. Everything below is about that.

@needs_api
def test_curve_is_the_window_rebased_to_zero():
    row = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": 3}, timeout=60).json()["traders"][0]
    c = requests.get(f"{API_URL}/trader/{row['address']}/curve", params={"days": 7}, timeout=60).json()
    if not c["available"]:
        # Upstream, not us — the endpoint still answered in the right shape.
        assert c["points"] == [] and c["note"]
        pytest.skip(f"no curve right now: {c['note']}")

    pts = c["points"]
    assert len(pts) >= 2 and len(pts) <= 64, len(pts)
    assert [t for t, _ in pts] == sorted(t for t, _ in pts), "oldest first"
    assert pts[0][1] == 0.0, "the window opens at zero, whatever HL's lifetime figure is"
    assert pts[-1][1] == c["pnl"], "`pnl` is the last point of the drawn line"
    assert (c["start_ms"], c["end_ms"]) == (pts[0][0], pts[-1][0])
    # HL's own "week" runs ~7.9 days; the answer is trimmed to the window asked for.
    assert c["period"] == "week"
    assert c["end_ms"] - c["start_ms"] <= 7 * 86_400_000

    # Drawdown is a fall, so it is never negative, never smaller than the drop
    # from the high to the low, and never larger than the whole range.
    assert c["max_drawdown"] >= 0
    assert c["max_drawdown"] >= round(c["high"] - c["low"], 2) - 0.01 or c["high"] <= c["low"]
    assert c["low"] <= c["pnl"] <= c["high"]


@needs_api
def test_curve_windows_map_to_hyperliquid_periods():
    addr = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": 3}, timeout=60).json()["traders"][0]["address"]
    for days, period in ((1, "day"), (7, "week"), (30, "month")):
        c = requests.get(f"{API_URL}/trader/{addr}/curve", params={"days": days}, timeout=60).json()
        assert c["period"] == period and c["days"] == days
        if c["available"]:
            assert c["end_ms"] - c["start_ms"] <= days * 86_400_000


@needs_api
def test_curve_never_answers_a_hover_with_an_error():
    # A wallet that has never traded, and an address that is not one at all:
    # both are 200s carrying `available: false` and a sentence, because this
    # endpoint decorates a row that already has its numbers.
    for addr in ("0x0000000000000000000000000000000000000000", "not-an-address"):
        r = requests.get(f"{API_URL}/trader/{addr}/curve", params={"days": 7}, timeout=60)
        assert r.status_code == 200, r.text[:200]
        c = r.json()
        assert c["available"] is False
        assert c["points"] == [] and isinstance(c["note"], str) and c["note"]


@needs_api
def test_curve_tool_is_public_and_bound_to_the_fn():
    doc = requests.get(f"{API_URL}/mcp/schema", timeout=10).json()
    tool = next(t for t in doc["tools"] if t["name"] == "hl_trader_curve")
    assert tool["mod_fn"] == "trader_curve" and tool["public"] is True
    assert tool["inputSchema"]["required"] == ["address"]


# ── the whole board, filtered by score; fill stats rationed to the top ──

def test_top_traders_tool_declares_all_pool_enrich_and_score_floors():
    doc = requests.get(f"{API_URL}/mcp/schema", timeout=10).json()
    tool = next(t for t in doc["tools"] if t["name"] == "hl_top_traders")
    props = tool["inputSchema"]["properties"]
    assert "string" in props["pool"]["type"] and "integer" in props["pool"]["type"]
    for k in ["enrich", "sort", "min_roi", "min_sharpe", "min_win", "min_equity", "min_volume",
              "min_trades", "with_stats"]:
        assert k in props, k


def test_pool_all_is_the_whole_gated_leaderboard_with_stats_only_on_top():
    r = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": "all", "enrich": 120}, timeout=120).json()
    assert r["all"] is True and r["pool"] == "all"
    rows = r["traders"]
    # thousands of wallets clear the gates on any given day; a 150-row board was never "all"
    assert len(rows) > 600, len(rows)
    assert r["candidates"] == len(rows) == r["priced"] == r["matched"]
    # every row is priced from the leaderboard ...
    assert all(t["account_value"] >= 1000 for t in rows)
    # ... but fill stats were only spent on a top slice by rank — the request
    # asks for 120, and the background deepener may have measured further down
    # by now, so the bound is "a slice", not the requested number.
    assert 0 < r["enriched"] < len(rows)
    assert r["enriched"] == sum(1 for t in rows if t["win_rate"] >= 0)
    # the board is ordered by the rank metric
    rois = [t["roi"] for t in rows]
    assert rois == sorted(rois, reverse=True)


def test_score_floors_and_sort_apply_server_side():
    base = {"days": 7, "pool": "all"}
    every = requests.get(f"{API_URL}/traders/top", params=base, timeout=120).json()
    r = requests.get(f"{API_URL}/traders/top", params={**base, "min_roi": 5, "min_equity": 10000}, timeout=120).json()
    assert r["filtered"] is True and r["priced"] == every["priced"]
    assert r["matched"] == len(r["traders"]) < every["matched"]
    assert all(t["roi"] >= 5 and t["account_value"] >= 10000 for t in r["traders"])
    # a sharpe floor can only be met by measured rows, and sorting by sharpe
    # never lets an unmeasured 0 outrank a measured value
    r = requests.get(f"{API_URL}/traders/top", params={**base, "min_sharpe": 0, "sort": "sharpe"}, timeout=120).json()
    assert r["sort"] == "sharpe"
    assert r["matched"] == r["enriched"] == len(r["traders"]) < every["matched"]
    sharpes = [t["sharpe"] for t in r["traders"]]
    assert sharpes == sorted(sharpes, reverse=True) and all(s >= 0 for s in sharpes)
    # rows without stats sink to the bottom on a stat sort of the unfiltered board
    r = requests.get(f"{API_URL}/traders/top", params={**base, "sort": "win_rate"}, timeout=120).json()
    measured = [t["win_rate"] >= 0 for t in r["traders"]]
    assert measured.index(False) == r["enriched"] and not any(measured[r["enriched"]:])


def test_cold_board_answers_scanning_instead_of_timing_out():
    # A board nobody has asked for yet is a multi-minute walk — longer than any
    # proxy holds a connection. `wait` bounds how long the request blocks; past
    # it the walk continues in the background and the answer says so.
    r = requests.get(f"{API_URL}/traders/top",
                     params={"days": 30, "pool": 7, "rank": "volume", "active": "window",
                             "enrich": 5, "wait": 0}, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "scanning" in d and isinstance(d["traders"], list)
    if d["scanning"]:
        assert d["progress"] is not None
    # a cached board is never "scanning" — the fast path answers outright
    warm = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": 5}, timeout=120).json()
    assert warm["scanning"] is False and warm["progress"] is None and len(warm["traders"]) == 5


def test_bad_pool_and_sort_are_400s():
    r = requests.get(f"{API_URL}/traders/top", params={"pool": "lots"}, timeout=30)
    assert r.status_code == 400 and "pool" in r.json()["error"]
    r = requests.get(f"{API_URL}/traders/top", params={"sort": "luck"}, timeout=30)
    assert r.status_code == 400 and "sort" in r.json()["error"]


def test_top_n_boards_still_work_and_are_sliced_from_the_whole():
    r = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": 25}, timeout=60).json()
    assert r["all"] is False and r["pool"] == 25 and len(r["traders"]) == 25
    whole = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": "all"}, timeout=60).json()
    assert [t["address"] for t in r["traders"]] == [t["address"] for t in whole["traders"][:25]]
