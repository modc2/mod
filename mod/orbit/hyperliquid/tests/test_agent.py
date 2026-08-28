"""Tests for the agent that drives this module's MCP server.

The load-bearing property is the tool policy: a *question* must never be able
to reach a tool that signs, spends or mutates state. That split is derived
from the live MCP schema, so it is tested both offline (list building, command
construction) and live (the schema really does classify the write tools).

Run:  cd orbit/hyperliquid && python3 -m pytest tests/ -q
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text())
API_URL = os.environ.get("HL_API_URL", f"http://localhost:{CONFIG['ports']['api']}")
AGENT_RS = (ROOT / "src" / "api" / "src" / "agent.rs").read_text()


def agent_module():
    spec = importlib.util.spec_from_file_location(
        "hyperliquid_agent_test", ROOT / "src" / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = agent_module()


def live():
    try:
        return requests.get(f"{API_URL}/health", timeout=3).ok
    except requests.RequestException:
        return False


needs_api = pytest.mark.skipif(not live(), reason=f"no API at {API_URL}")


# ── offline: the agent is part of the protocol surface ──────────────────

def test_ask_is_a_declared_fn():
    assert "ask" in CONFIG["fns"] and "ask_status" in CONFIG["fns"]


def test_routes_are_wired_and_only_status_is_public():
    routes = (ROOT / "src" / "api" / "src" / "routes.rs").read_text()
    assert 'route("/ask", post(crate::agent::ask))' in routes
    assert 'route("/ask/status", get(crate::agent::ask_status))' in routes
    auth = (ROOT / "src" / "api" / "src" / "auth.rs").read_text()
    assert '| "/ask/status"' in auth, "status probe should be public"
    assert '"/ask"' not in auth.split("pub fn is_public")[1].split("}")[0], \
        "POST /ask must stay token-gated — it spends model credits"


def test_the_question_never_lands_in_a_process_listing():
    # The Rust route pipes it over stdin; agent.py reads stdin in --stream.
    assert 'arg("--stream")' in AGENT_RS and "stdin.write_all" in AGENT_RS


# ── offline: tool policy ────────────────────────────────────────────────

READS = ["mcp__hyperliquid__hl_mids", "mcp__hyperliquid__hl_top_traders"]
WRITES = ["mcp__hyperliquid__hl_trade", "mcp__hyperliquid__hl_withdraw"]


def test_read_only_runs_deny_every_write_tool():
    cmd = agent.build_cmd("q", READS, agent.LOCAL_TOOLS + WRITES, False, API_URL, "")
    allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
    denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
    assert set(allowed) == set(READS)
    assert set(WRITES) <= set(denied)


def test_local_host_tools_are_always_denied():
    # The agent reasons over Hyperliquid, not over this host.
    for tools, act in ((READS, False), (READS + WRITES, True)):
        cmd = agent.build_cmd("q", tools, agent.LOCAL_TOOLS, act, API_URL, "tok")
        denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
        assert {"Bash", "Write", "Read"} <= set(denied)


def test_act_mode_gets_the_action_briefing():
    plain = agent.build_cmd("q", READS, [], False, API_URL, "")
    acting = agent.build_cmd("q", READS + WRITES, [], True, API_URL, "tok")
    assert agent.ACT_PROMPT not in plain[plain.index("--append-system-prompt") + 1]
    assert agent.ACT_PROMPT in acting[acting.index("--append-system-prompt") + 1]


def test_act_without_a_token_is_refused_before_spending_anything():
    events = list(agent.ask("buy 1 BTC", api_url=API_URL, token="", act=True))
    assert [e["type"] for e in events] == ["error"]
    assert "sign" in events[0]["error"]


def test_empty_questions_are_refused():
    assert list(agent.ask("   "))[0]["type"] == "error"


def test_mcp_config_carries_the_callers_token_to_the_stdio_server():
    cfg = agent.mcp_config("http://x:1", "tok")["mcpServers"]["hyperliquid"]
    assert cfg["args"] == ["--stdio"]
    assert cfg["env"] == {"HL_API_URL": "http://x:1", "HYPERLIQUID_TOKEN": "tok"}
    # Signed out: no token key at all, rather than an empty one.
    assert "HYPERLIQUID_TOKEN" not in agent.mcp_config("http://x:1", "")[
        "mcpServers"]["hyperliquid"]["env"]


def test_stream_events_are_translated_for_the_console():
    ev = list(agent._events({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "name": "mcp__hyperliquid__hl_mids", "input": {}}]}}))
    assert [e["type"] for e in ev] == ["text", "tool"]
    assert ev[1]["name"] == "hl_mids", "the mcp__ prefix should be stripped"
    done = list(agent._events({"type": "result", "result": "42", "num_turns": 2}))
    assert done[0] == {"type": "done", "answer": "42", "turns": 2,
                       "ms": None, "cost_usd": None}


# ── live: the schema really does split reads from writes ────────────────

@needs_api
def test_tool_policy_classifies_the_dangerous_tools_as_writes():
    reads, writes = agent.tool_policy(API_URL)
    assert reads and writes
    assert set(reads).isdisjoint(writes)
    for t in ["hl_trade", "hl_withdraw", "hl_usd_send", "hl_vault_transfer",
              "hl_live_start", "hl_action", "hl_create_follow"]:
        assert f"mcp__hyperliquid__{t}" in writes, f"{t} must not be readable-mode"
    for t in ["hl_mids", "hl_top_traders", "hl_analyze_trader", "hl_user_state"]:
        assert f"mcp__hyperliquid__{t}" in reads


@needs_api
def test_status_reports_readiness_and_tool_counts():
    st = agent.status(API_URL)
    assert set(st) >= {"ready", "auth", "model", "read_tools", "write_tools"}
    assert st["read_tools"] > 10 and st["write_tools"] > 10
    if not st["ready"]:
        assert st["hint"], "not-ready status must say what is missing"


@needs_api
def test_ask_status_route_is_public():
    r = requests.get(f"{API_URL}/ask/status", timeout=30)
    assert r.status_code == 200 and "ready" in r.json()


@needs_api
def test_asking_requires_a_token():
    r = requests.post(f"{API_URL}/ask", json={"question": "hi"}, timeout=15)
    assert r.status_code == 401
