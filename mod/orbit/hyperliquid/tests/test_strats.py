"""Offline unit tests for the hyperliquid strategy layer (src/strats).

Everything here runs without the API: `hl` is faked with a canned
`top_traders` payload, so the tests pin selection, filtering, weighting
and engine-config composition — the pure logic the live engine trusts.

Run:  cd orbit/hyperliquid && python -m pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strats import (  # noqa: E402
    REGISTRY, CopyWallets, HighWinRate, Leader, Sharpe, Strat, StratParams,
    TopN, Whales, list_strats, make,
)


class FakeHL:
    """Stands in for the Hyperliquid mod client; serves a fixed roster."""

    def __init__(self, traders):
        self.traders = traders
        self.calls = []

    def top_traders(self, **kw):
        self.calls.append(kw)
        return {"traders": self.traders}


def T(addr, pnl=0.0, volume=0.0, sharpe=0.0, win_rate=0.0, trades=0,
      closes=None, win_rate_lo=None, sharpe_days=30):
    """A board row.

    `closes` defaults to `trades` and `win_rate_lo` to `win_rate`, which is the
    degenerate case the real API never produces — every fill closing, and a
    ratio with no sampling error. Tests that care about sample size pass them
    explicitly; `sharpe_days` defaults high so Sharpe rows clear the evidence
    gate unless a test is specifically probing it.
    """
    return {"address": addr, "pnl": pnl, "volume": volume,
            "sharpe": sharpe, "win_rate": win_rate, "trades": trades,
            "closes": trades if closes is None else closes,
            "win_rate_lo": win_rate if win_rate_lo is None else win_rate_lo,
            "sharpe_days": sharpe_days}


# ── Leader / StratParams ────────────────────────────────────────────────

def test_leader_to_engine_lowercases_address():
    e = Leader(address="0xABCdef0000000000000000000000000000000001", weight=0.5).to_engine()
    assert e == {"address": "0xabcdef0000000000000000000000000000000001",
                 "weight": 0.5, "enabled": True}


def test_strat_kwargs_split_risk_vs_params():
    s = TopN(n=3, size_pct=7.5, max_per_trade_usd=250, coins_deny=["DOGE"])
    assert s.risk.size_pct == 7.5
    assert s.risk.max_per_trade_usd == 250
    assert s.risk.coins_deny == ["DOGE"]
    assert s._params["n"] == 3          # strat knob stays out of risk
    assert s.risk.interval_ms == 15_000  # untouched default


# ── build_config ────────────────────────────────────────────────────────

def test_build_config_shape_and_lowercased_eoa():
    hl = FakeHL([T("0xAA", pnl=100), T("0xBB", pnl=50)])
    cfg = TopN(n=2).build_config(hl, "0xEOA00000000000000000000000000000000000AA")
    assert cfg["eoa"] == "0xeoa00000000000000000000000000000000000aa"
    assert cfg["strategy_id"] == "top_n"
    assert len(cfg["traders"]) == 2
    assert {"interval_ms", "size_pct", "max_per_trade_usd", "min_order_size_usd",
            "max_slippage_bps", "coins_allow", "coins_deny", "vault_address",
            "capital"} <= set(cfg)


def test_build_config_raises_on_no_leaders():
    hl = FakeHL([])
    with pytest.raises(RuntimeError, match="no leaders"):
        TopN().build_config(hl, "0xeoa")


# ── TopN ────────────────────────────────────────────────────────────────

def test_top_n_sorts_by_pnl_and_truncates():
    hl = FakeHL([T("0xlow", pnl=10), T("0xhigh", pnl=1000), T("0xmid", pnl=100)])
    leaders = TopN(n=2).pick_leaders(hl)
    assert [l.address for l in leaders] == ["0xhigh", "0xmid"]


def test_top_n_pnl_weights_normalized():
    hl = FakeHL([T("0xa", pnl=300), T("0xb", pnl=100)])
    leaders = TopN(n=2).pick_leaders(hl)
    weights = {l.address: l.weight for l in leaders}
    assert weights["0xa"] == pytest.approx(0.75)
    assert weights["0xb"] == pytest.approx(0.25)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_top_n_equal_weight():
    hl = FakeHL([T("0xa", pnl=300), T("0xb", pnl=100)])
    leaders = TopN(n=2, equal_weight=True).pick_leaders(hl)
    assert all(l.weight == 1.0 for l in leaders)


def test_top_n_min_pnl_filter_and_empty_result():
    hl = FakeHL([T("0xa", pnl=5), T("0xb", pnl=-10)])
    assert TopN(min_pnl_usd=50).pick_leaders(hl) == []


def test_top_n_negative_pnl_leader_gets_zero_weight_not_negative():
    # min_pnl_usd default 0 admits pnl=0; weights clamp at 0, never negative.
    hl = FakeHL([T("0xa", pnl=100), T("0xb", pnl=0)])
    leaders = TopN(n=2).pick_leaders(hl)
    weights = {l.address: l.weight for l in leaders}
    assert weights["0xb"] == 0.0
    assert weights["0xa"] == pytest.approx(1.0)


# ── Whales ──────────────────────────────────────────────────────────────

def test_whales_filters_by_volume_and_weights_by_volume():
    hl = FakeHL([
        T("0xshrimp", volume=1_000),
        T("0xwhale", volume=2_000_000),
        T("0xorca", volume=1_000_000),
    ])
    leaders = Whales(n=5, min_volume_usd=500_000).pick_leaders(hl)
    assert [l.address for l in leaders] == ["0xwhale", "0xorca"]
    assert leaders[0].weight == pytest.approx(2 / 3)
    assert leaders[1].weight == pytest.approx(1 / 3)


# ── HighWinRate ─────────────────────────────────────────────────────────

def test_high_win_rate_gates_on_trades_and_rate():
    hl = FakeHL([
        T("0xlucky", win_rate=90, trades=3),     # too few trades
        T("0xcoin", win_rate=51, trades=100),    # below min rate
        T("0xgood", win_rate=70, trades=100),
        T("0xbest", win_rate=80, trades=100),
    ])
    leaders = HighWinRate(min_trades=30, min_win_rate=55, min_closes=0).pick_leaders(hl)
    assert [l.address for l in leaders] == ["0xbest", "0xgood"]
    # Edge-over-coinflip weights: (80-50):(70-50) = 30:20.
    assert leaders[0].weight == pytest.approx(0.6)
    assert leaders[1].weight == pytest.approx(0.4)


def test_high_win_rate_is_not_fooled_by_a_perfect_tiny_sample():
    """The bug this strat was built to avoid, and used to walk straight into.

    `0xstreak` has 40 fills — clearing any `min_trades` gate — but only 4 of
    them closed, and all 4 were green. Its headline win rate is 100%. On the
    old ranking it sorted above every seasoned book on the board and took the
    largest allocation.
    """
    hl = FakeHL([
        T("0xstreak", win_rate=100, win_rate_lo=51.0, trades=40, closes=4),
        T("0xreal", win_rate=90, win_rate_lo=85.0, trades=400, closes=200),
    ])
    leaders = HighWinRate(min_trades=30, min_win_rate=55, min_closes=20).pick_leaders(hl)
    assert [l.address for l in leaders] == ["0xreal"], "4 closes is not a track record"
    assert leaders[0].weight == pytest.approx(1.0)

    # Even with the closes gate switched off, the lower bound must still rank
    # the measured book first — the gate and the ranking are two defences.
    both = HighWinRate(min_trades=30, min_win_rate=0, min_closes=0).pick_leaders(hl)
    assert [l.address for l in both] == ["0xreal", "0xstreak"]


def test_sharpe_strat_ignores_a_two_day_wonder():
    """A ratio computed from two green days is not a Sharpe ratio."""
    hl = FakeHL([
        T("0xwonder", sharpe=13.37, volume=100_000, sharpe_days=2),
        T("0xsteady", sharpe=2.0, volume=100_000, sharpe_days=30),
    ])
    leaders = Sharpe(min_sharpe=1.0, min_volume_usd=1_000, min_days=7).pick_leaders(hl)
    assert [l.address for l in leaders] == ["0xsteady"]


# ── Sharpe ──────────────────────────────────────────────────────────────

def test_sharpe_filters_and_weights():
    hl = FakeHL([
        T("0xnoise", sharpe=3.0, volume=100),          # dust volume
        T("0xsteady", sharpe=2.0, volume=100_000),
        T("0xok", sharpe=1.0, volume=100_000),
        T("0xmeh", sharpe=0.5, volume=100_000),        # below min_sharpe
    ])
    leaders = Sharpe(n=5, min_sharpe=1.0, min_volume_usd=25_000).pick_leaders(hl)
    assert [l.address for l in leaders] == ["0xsteady", "0xok"]
    assert leaders[0].weight == pytest.approx(2 / 3)


# ── CopyWallets ─────────────────────────────────────────────────────────

def test_copy_wallets_fixed_list_lowercased_equal_weight():
    s = CopyWallets(["0xAAA", "0xBBB"])
    leaders = s.pick_leaders(hl=None)
    assert [l.address for l in leaders] == ["0xaaa", "0xbbb"]
    assert all(l.weight == 1.0 for l in leaders)


def test_copy_wallets_requires_addresses():
    with pytest.raises(ValueError):
        CopyWallets([])


# ── Registry / factory ──────────────────────────────────────────────────

def test_registry_and_factory():
    assert set(REGISTRY) == {"copy_wallets", "top_n", "whales", "high_win_rate", "sharpe"}
    assert {s["name"] for s in list_strats()} == set(REGISTRY)
    s = make("top_n", n=4, size_pct=5)
    assert isinstance(s, TopN) and isinstance(s, Strat)
    assert s.risk.size_pct == 5


def test_make_unknown_raises():
    with pytest.raises(ValueError, match="unknown strat"):
        make("moonshot")


def test_describe_reports_params_and_risk():
    d = Sharpe(n=8, days=14, size_pct=3).describe()
    assert d["name"] == "sharpe"
    assert d["params"]["n"] == 8
    assert d["risk"]["size_pct"] == 3
    assert isinstance(StratParams(), StratParams)
