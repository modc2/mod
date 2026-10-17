"""Tests for the "$N on this trader" backtest and its data checks.

The replay math itself is unit-tested in Rust (src/api/src/backtest.rs) where
it lives — scaling, drawdown, wipeout flooring, fills truncation. What this
suite pins is the contract the rest of the fleet sees:

* offline — the fn is declared everywhere it must be (config.json, mod.py,
  the Rust tool registry), because a backtest nobody can reach is a backtest
  that does not exist.
* live — the route's shape holds on a real wallet: every result carries its
  checks, `ok` is derived from them and not asserted independently, and bad
  input degrades to `available: false` with a reason instead of a 4xx/5xx
  that a UI would render as a broken page.

Run:  cd orbit/hyperliquid && python3 -m pytest tests/test_backtest.py -q
"""

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
ROUTES_RS = (ROOT / "src" / "api" / "src" / "routes.rs").read_text()
API_URL = os.environ.get("HL_API_URL", f"http://localhost:{CONFIG['ports']['api']}")

# The burn address is a REAL, busy HL account (see test_mcp notes) — the
# right "never traded perps as this exact wallet" probe is a fresh-looking
# address, and the right bad-input probe is not an address at all.
GOOD_PROBE = "0x0000000000000000000000000000000000000000"

VALID_STATUSES = {"pass", "warn", "fail"}
EXPECTED_CHECKS = {"history", "coverage", "freshness", "basis", "scale", "fills", "agreement"}


def live():
    try:
        return requests.get(f"{API_URL}/health", timeout=3).ok
    except requests.RequestException:
        return False


needs_api = pytest.mark.skipif(not live(), reason=f"no API at {API_URL}")


def backtest(addr, **params):
    r = requests.get(f"{API_URL}/trader/{addr}/backtest", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


# ── offline: the fn exists on every surface ─────────────────────────────

def test_backtest_trader_is_declared_everywhere():
    assert "backtest_trader" in CONFIG["fns"], "config.json fns"
    tools = dict(re.findall(r'tool\(\s*"(hl_\w+)",\s*"(\w+)"', MCP_RS))
    assert tools.get("hl_backtest_trader") == "backtest_trader", "mcp.rs registry"
    assert '"/trader/:addr/backtest"' in ROUTES_RS, "routes.rs route"
    # The info() endpoint map is the protocol's public promise.
    assert "/trader/:addr/backtest" in ROUTES_RS.split('"public"')[1].split("]")[0]


def test_mod_py_wrapper_hits_the_route(monkeypatch):
    from test_mcp import hyperliquid_class
    hl = hyperliquid_class()(api_url="http://localhost:1")
    seen = {}

    def fake_get(path, _timeout=30, **params):
        seen["path"], seen["params"] = path, params
        return {"ok": True}

    monkeypatch.setattr(hl, "_get", fake_get)
    hl.backtest_trader("0xabc", capital=2500, days=14)
    assert seen["path"] == "/trader/0xabc/backtest"
    assert seen["params"] == {"capital": 2500, "days": 14}


# ── live: the contract on real data ─────────────────────────────────────

@needs_api
def test_checks_ride_every_result_and_ok_is_derived():
    b = backtest(GOOD_PROBE, capital=1000, days=7)
    assert isinstance(b["checks"], list) and b["checks"], "no checks attached"
    for c in b["checks"]:
        assert c["status"] in VALID_STATUSES, c
        assert c["detail"], f"check {c['name']} has no detail sentence"
    has_fail = any(c["status"] == "fail" for c in b["checks"])
    assert b["ok"] == (not has_fail), "`ok` must be exactly `no check failed`"


@needs_api
def test_a_real_trader_backtests_at_two_capitals_linearly():
    # Pull one live wallet off the cached board rather than hardcoding an
    # address that will one day go quiet.
    board = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": 5},
                         timeout=60).json()
    traders = board.get("traders") or []
    if not traders:
        pytest.skip("board cache is cold")
    addr = traders[0]["address"]

    b1 = backtest(addr, capital=1000, days=7)
    if not b1["available"]:
        pytest.skip(f"no history for {addr}: {b1.get('note')}")
    assert set(c["name"] for c in b1["checks"]) >= EXPECTED_CHECKS - {"wipeout", "source", "input"}
    assert b1["capital"] == 1000
    assert b1["basis_equity"] > 0
    assert len(b1["points"]) >= 2
    assert b1["points"][0][1] == pytest.approx(1000, abs=0.02), "curve must start at the deposit"
    assert b1["final_value"] == pytest.approx(1000 + b1["pnl"], abs=0.02)

    # Double the money, double the pnl — the model is proportional and says
    # so (a `scale` warn may appear, but the arithmetic must not bend).
    b2 = backtest(addr, capital=2000, days=7)
    if b2["available"] and not b1["wiped"] and not b2["wiped"]:
        assert b2["pnl"] == pytest.approx(2 * b1["pnl"], abs=max(0.05, abs(b1["pnl"]) * 0.001))


@needs_api
def test_bad_input_degrades_never_errors():
    b = backtest("not-a-wallet", capital=1000, days=7)
    assert b["available"] is False and b["ok"] is False
    assert any(c["status"] == "fail" for c in b["checks"])
    assert "wallet" in b["note"]

    b = backtest(GOOD_PROBE, capital=-50, days=7)
    assert b["available"] is False
    assert "positive" in b["note"]
