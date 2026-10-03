"""The canonical leaderboard score and the score market built on it.

The score is defined ONCE, in Rust (traders.rs::leaderboard_score):

    score = roi × winRateLo/100 × sharpe

gated on evidence (fill-measured, ≥ MIN_CLOSES realised closes, ≥
MIN_SHARPE_DAYS days behind the Sharpe). The UI's ƒ SCORE preset and the
/traders/market response both quote that definition — these tests are what
keeps the three copies from drifting apart.

Two layers, like test_mcp.py: offline parity anywhere, live behaviour when
the API answers on HL_API_URL (and skipped, not failed, while a pre-market
binary is still serving).
"""

import json
import re
from pathlib import Path

import pytest
import requests

from test_mcp import API_URL, CONFIG, hyperliquid_class, live, rust_tools

ROOT = Path(__file__).resolve().parents[1]
TRADERS_RS = (ROOT / "src" / "api" / "src" / "traders.rs").read_text()
STATS_RS = (ROOT / "src" / "api" / "src" / "stats.rs").read_text()
SCORE_TS = (ROOT / "src" / "app" / "app" / "lib" / "scoreFormula.ts").read_text()

needs_api = pytest.mark.skipif(not live(), reason=f"no API at {API_URL}")


def rust_const(src, name):
    m = re.search(rf"pub const {name}: \w+ = (\d+)", src)
    assert m, f"{name} not found"
    return int(m.group(1))


# ── offline: one definition, three quotations ───────────────────────────

def test_score_market_is_declared_everywhere():
    assert "score_market" in CONFIG["fns"]
    assert callable(getattr(hyperliquid_class(), "score_market", None))
    assert ("hl_score_market", "score_market") in rust_tools()


def test_the_formula_is_the_same_in_rust_and_the_ui_preset():
    # Rust: the arithmetic itself, and the self-description string.
    assert "t.roi * (t.win_rate_lo / 100.0) * t.sharpe" in TRADERS_RS
    m = re.search(r'SCORE_FORMULA: &str = "([^"]+)"', TRADERS_RS)
    assert m and "roi" in m.group(1) and "winRateLo/100" in m.group(1) and "sharpe" in m.group(1)

    # UI preset: identical product, as a filtering JS function.
    preset = re.search(r'key: "score".*?formula: `(.*?)`', SCORE_TS, re.S)
    assert preset, "the ƒ SCORE preset is gone from scoreFormula.ts"
    assert re.search(r"return roi \* winRateLo / 100 \* sharpe", preset.group(1))


def test_the_evidence_gates_quote_the_stats_constants():
    min_closes = rust_const(STATS_RS, "MIN_CLOSES")
    min_days = rust_const(STATS_RS, "MIN_SHARPE_DAYS")
    # Rust score gates on the shared constants by NAME (not a copied number).
    assert "t.closes < crate::stats::MIN_CLOSES" in TRADERS_RS
    assert "t.sharpe_days < crate::stats::MIN_SHARPE_DAYS" in TRADERS_RS
    # The preset interpolates the client mirrors of the same constants — and
    # those mirrors still equal the Rust values.
    api_ts = (ROOT / "src" / "app" / "app" / "lib" / "api.ts").read_text()
    assert f"MIN_CLOSES = {min_closes}" in api_ts
    assert f"MIN_SHARPE_DAYS = {min_days}" in api_ts


# ── live: the market keeps its own promises ─────────────────────────────

def market(**params):
    r = requests.get(f"{API_URL}/traders/market", params=params, timeout=30)
    if r.status_code in (404, 405):
        pytest.skip("running API predates /traders/market")
    r.raise_for_status()
    return r.json()


@needs_api
def test_market_self_describes_score_and_gate():
    m = market(days=7)
    assert "winRateLo/100" in m["score"]
    assert m["gate"]["min_closes"] == rust_const(STATS_RS, "MIN_CLOSES")
    assert m["gate"]["min_sharpe_days"] == rust_const(STATS_RS, "MIN_SHARPE_DAYS")
    assert set(m["gate"]["factors_positive"]) == {"roi", "winRateLo", "sharpe"}
    # The funnel only narrows: priced ⊇ measured ⊇ matched.
    assert m["priced"] >= m["measured"] >= m["matched"] == len(m["rows"])


@needs_api
def test_every_admitted_row_is_evidence_backed_and_all_factors_positive():
    m = market(days=7)
    if m["warming"] or not m["rows"]:
        pytest.skip("no warm 7d board / empty market right now")
    min_closes = m["gate"]["min_closes"]
    min_days = m["gate"]["min_sharpe_days"]
    scores = [r["score"] for r in m["rows"]]
    assert scores == sorted(scores, reverse=True), "market is ranked by score desc"
    for r in m["rows"]:
        assert r["closes"] >= min_closes
        assert r["sharpe_days"] >= min_days
        assert r["roi"] > 0 and r["win_rate_lo"] > 0 and r["sharpe"] > 0
        # The score IS the stated product of the row's own factors.
        want = r["roi"] * (r["win_rate_lo"] / 100.0) * r["sharpe"]
        assert abs(r["score"] - want) < 1e-9 * max(1.0, abs(want))


@needs_api
def test_min_score_is_a_floor_and_limit_truncates_after_ranking():
    m = market(days=7, limit=400)
    if len(m["rows"]) < 2:
        pytest.skip("not enough admitted rows to exercise the floor")
    floor = m["rows"][1]["score"]
    floored = market(days=7, min_score=floor, limit=400)
    assert all(r["score"] >= floor for r in floored["rows"])
    top1 = market(days=7, limit=1)
    assert len(top1["rows"]) == 1
    # Same board generation ⇒ same leader; a refresh between calls is the
    # only legitimate difference, so compare addresses not scores.
    if top1["updated_at"] == m["updated_at"]:
        assert top1["rows"][0]["address"] == m["rows"][0]["address"]


@needs_api
def test_an_uncached_window_warms_instead_of_lying():
    # 83d is no prewarmed window; the market must say "warming" (or serve a
    # real board if some visitor built one) — never fabricate rows.
    m = market(days=83)
    assert m["warming"] == (m["updated_at"] == 0)
    if m["warming"]:
        assert m["rows"] == [] and m["priced"] == 0
