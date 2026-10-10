"""Tests for expected profit per day and the scout agent.

The math (expect.rs: edge per close at the Wilson-lower win rate; scout.rs:
min(model, backtest), gates, ranking) is unit-tested in Rust. This suite pins
the contract around it:

* offline — `scout` is declared on every surface (config.json, mod.py, the
  MCP registry, the router, the public allowlist) and the ƒ score exposes
  expDay / expDay1k.
* live — `/scout` answers publicly with its method and a report shape whose
  ranking honours the min(model, backtest) rule; `/traders/top` rows carry
  an `expected` block whose numbers are internally consistent.

Run:  cd orbit/hyperliquid && python3 -m pytest tests/test_scout.py -q
"""

import json
import os
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text())
SRC = ROOT / "src" / "api" / "src"
API_URL = os.environ.get("HL_API_URL", f"http://localhost:{CONFIG['ports']['api']}")


def live():
    try:
        return requests.get(f"{API_URL}/health", timeout=3).ok
    except requests.RequestException:
        return False


needs_api = pytest.mark.skipif(not live(), reason=f"no API at {API_URL}")


# ── offline ─────────────────────────────────────────────────────────────

def test_scout_fn_is_on_every_surface():
    assert "scout" in CONFIG["fns"]
    assert '"scout"' in (ROOT / "src" / "mod.py").read_text()
    assert 'tool("hl_scout", "scout", "GET", "/scout", true' in (SRC / "mcp.rs").read_text()
    assert '.route("/scout", get(scout))' in (SRC / "routes.rs").read_text()
    assert '| "/scout"' in (SRC / "auth.rs").read_text()


def test_score_box_exposes_expected_per_day():
    f = (ROOT / "src" / "app" / "app" / "lib" / "scoreFormula.ts").read_text()
    assert '"expDay", "expDay1k"' in f
    assert 'formula: "expDay1k"' in f


# ── live ────────────────────────────────────────────────────────────────

@needs_api
def test_scout_is_public_and_self_describing():
    r = requests.get(f"{API_URL}/scout", timeout=15)
    assert r.status_code == 200, r.text[:200]
    s = r.json()
    assert "min(model, backtest)" in s["method"]
    for k in ("running", "next_run_ms", "every_ms", "report", "best", "history"):
        assert k in s
    rep = s["report"]
    if rep is None:
        pytest.skip("scout hasn't finished its first run yet")
    assert rep["windows"] == [1, 7, 30] and rep["capital"] == 1000
    assert rep["eligible"] <= rep["scanned"]
    verified = [p for p in rep["picks"] if p["expected_per_day"] is not None]
    # verified picks lead, best first
    assert rep["picks"][: len(verified)] == verified
    vals = [p["expected_per_day"] for p in verified]
    assert vals == sorted(vals, reverse=True)
    for p in verified:
        assert p["expected_per_day"] <= p["model"]["per_1k"] + 0.01
        assert p["expected_per_day"] <= p["backtest_per_day"] + 0.01
        assert p["verdict"] in {"consistent", "mixed", "losing"}


@needs_api
def test_board_rows_carry_a_consistent_expected_block():
    r = requests.get(f"{API_URL}/traders/top", params={"days": 7, "pool": 150, "wait": 5}, timeout=30)
    r.raise_for_status()
    rows = r.json()["traders"]
    measured = [t for t in rows if t["win_rate"] >= 0 and t["closes"] > 0]
    if not measured:
        pytest.skip("board not measured yet")
    with_e = [t for t in measured if t.get("expected")]
    assert with_e, "measured rows must carry an expected block"
    for t in with_e:
        e = t["expected"]
        assert abs(e["pace_per_day"] - t["pnl"] / 7) < 0.02
        assert 0 <= e["win_lo"] <= 1
        if e["per_1k"] is not None:
            assert e["basis"] >= 1000
            assert abs(e["per_1k"] - e["per_day"] * 1000 / e["basis"]) < 0.02
    for t in rows:
        if t["win_rate"] < 0:
            assert t.get("expected") is None
