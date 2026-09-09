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


# ── Canonical schema surface (shared with polymarket) ───────────────────
#
# The methods below are the cross-mod contract: sync → signal → execute,
# tick, backtest, state. Copy-family defaults are driven entirely by
# config.watchlist + risk knobs, so CopyWallets exercises them all.

from strats import (  # noqa: E402
    BacktestResult, ExecutionResult, Order, OrderSide, StratConfig,
    SyncResult, TickResult, TraderTrade,
)


def F(id, trader, coin="BTC", side=OrderSide.BUY, size=100.0, price=10.0,
      ts=1_000, closed_pnl=0.0, fee=0.0):
    return TraderTrade(id=id, trader=trader, timestamp=ts, coin=coin,
                       side=side, size=size, price=price,
                       closed_pnl=closed_pnl, fee=fee)


def snap(trades, ts=2_000):
    return SyncResult(timestamp=ts, trader_trades=trades,
                      wallet_usdc=0.0, open_positions={})


def test_resolve_watchlist_bridges_pick_leaders_to_config():
    s = CopyWallets(["0xAAA", "0xBBB"])
    wl = s.resolve_watchlist(hl=None)
    assert wl == [{"address": "0xaaa", "weight": 1.0},
                  {"address": "0xbbb", "weight": 1.0}]
    assert s.config.watchlist is wl or s.config.watchlist == wl


def test_default_signal_mirrors_sized_weighted_and_slippage_padded():
    s = CopyWallets(["0xaaa"], size_pct=10)
    s.config.watchlist = [{"address": "0xaaa", "weight": 0.5}]
    orders = s.signal(snap([F("f1", "0xAAA", side=OrderSide.BUY, size=100, price=10)]))
    assert len(orders) == 1
    o = orders[0]
    assert o.coin == "BTC" and o.side == OrderSide.BUY
    assert o.size == pytest.approx(100 * 0.10 * 0.5)          # size_pct × weight
    assert o.price == pytest.approx(10 * 1.01)                 # +100bps pad on buys
    assert o.source_trade_id == "f1" and o.tag == "mirror"

    sells = s.signal(snap([F("f2", "0xaaa", side=OrderSide.SELL, size=100, price=10)]))
    assert sells[0].price == pytest.approx(10 * 0.99)          # −pad on sells


def test_default_signal_filters_deny_dust_and_strangers():
    s = CopyWallets(["0xaaa"], size_pct=10, coins_deny=["DOGE"],
                    min_order_size_usd=100.0)
    s.resolve_watchlist(hl=None)
    trades = [
        F("deny", "0xaaa", coin="DOGE"),                       # denied coin
        F("stranger", "0xzzz"),                                # not on watchlist
        F("dust", "0xaaa", size=5, price=1.0),                 # 0.5 USD mirror
        F("ok", "0xaaa", size=1_000, price=10.0),              # 1000 USD mirror
    ]
    orders = s.signal(snap(trades))
    assert [o.source_trade_id for o in orders] == ["ok"]


def test_default_signal_clamps_to_max_order_size():
    s = CopyWallets(["0xaaa"], size_pct=100, max_per_trade_usd=250.0)
    s.resolve_watchlist(hl=None)
    o = s.signal(snap([F("big", "0xaaa", size=100, price=10)]))[0]
    assert o.size * 10 == pytest.approx(250.0)                 # notional capped


def test_execute_without_place_order_reports_failures_not_crashes():
    s = CopyWallets(["0xaaa"])
    rs = s.execute([Order(coin="BTC", side=OrderSide.BUY, size=1, price=10)])
    assert len(rs) == 1 and not rs[0].success
    assert "place_order" in (rs[0].error or "")


def test_tick_end_to_end_with_engine_supplied_io_and_dedupe():
    fills = [F("f1", "0xaaa", size=100, price=10)]
    placed = []

    def place(o: Order) -> ExecutionResult:
        placed.append(o)
        return ExecutionResult(order=o, success=True, order_id="oid",
                               filled_size=o.size, filled_price=o.price)

    s = CopyWallets(["0xaaa"], size_pct=10)
    s.resolve_watchlist(hl=None)
    s.config.fetch_trader_trades = lambda addr, since: list(fills)
    s.config.fetch_wallet_usdc = lambda: 500.0
    s.config.fetch_open_positions = None
    s.config.place_order = place

    r = s.tick()
    assert isinstance(r, TickResult)
    assert r.sync.wallet_usdc == 500.0
    assert len(r.orders) == 1 and len(placed) == 1 and not r.skipped
    assert s._positions["BTC"] == pytest.approx(10.0)          # optimistic update
    assert s.state()["handled_trade_count"] == 1

    # Same fill re-observed on the next sync window must NOT fire again.
    r2 = s.tick()
    assert r2.orders == [] and len(placed) == 1


def test_backtest_scales_closed_pnl_by_mirror_ratio():
    s = CopyWallets(["0xaaa"], size_pct=10, capital=1_000.0)
    s.resolve_watchlist(hl=None)
    history = [
        F("h1", "0xaaa", size=100, price=10, ts=1_000, closed_pnl=50.0, fee=2.0),
        F("h2", "0xaaa", size=100, price=10, ts=2_000, closed_pnl=50.0, fee=2.0),
    ]
    b = s.backtest(history)
    assert isinstance(b, BacktestResult)
    assert b.trades_simulated == 2
    assert b.pnl_curve == [(1_000, pytest.approx(5.0)), (2_000, pytest.approx(10.0))]
    assert b.fees_total == pytest.approx(0.4)
    assert b.final_pnl == pytest.approx(9.6)
    assert b.roi_pct == pytest.approx(0.96)

    empty = s.backtest([])
    assert empty.trades_simulated == 0 and empty.notes


def test_config_and_risk_views_agree_on_shared_knobs():
    s = CopyWallets(["0xaaa"], size_pct=5, min_order_size_usd=25,
                    max_per_trade_usd=500, max_slippage_bps=40)
    assert s.config.min_order_size == 25
    assert s.config.max_order_size == 500
    assert s.config.max_slippage_bps == 40
    assert s.config.name == "copy_wallets"

    cfg = StratConfig(name="x", capital=2_000, watchlist=[], min_order_size=7)
    s2 = CopyWallets(["0xaaa"], config=cfg)
    assert s2.config is cfg
    assert s2.risk.min_order_size_usd == 7 and s2.risk.capital == 2_000


# ── Cross-mod parity: the schema must not drift from polymarket's ───────

_PM_BASE = Path("/root/mod/mod/orbit/polymarket/src/strats/base/mod.py")

CANONICAL_METHODS = {"setup", "sync", "signal", "execute", "tick",
                     "backtest", "teardown", "state"}
CANONICAL_TYPES = {"Strat", "StratConfig", "Order", "OrderSide", "TraderTrade",
                   "SyncResult", "ExecutionResult", "TickResult", "BacktestResult"}


def test_canonical_surface_present_here():
    assert CANONICAL_METHODS <= set(dir(Strat))
    import strats as m
    assert CANONICAL_TYPES <= set(dir(m))


@pytest.mark.skipif(not _PM_BASE.exists(), reason="polymarket checkout not present")
def test_schema_parity_with_polymarket():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pm_strat_base", _PM_BASE)
    pm = importlib.util.module_from_spec(spec)
    # dataclass field resolution looks the module up in sys.modules.
    sys.modules["pm_strat_base"] = pm
    try:
        spec.loader.exec_module(pm)
    finally:
        sys.modules.pop("pm_strat_base", None)

    # Same method contract on the base class…
    assert CANONICAL_METHODS <= set(dir(pm.Strat))
    # …same dataclass vocabulary…
    assert CANONICAL_TYPES <= set(dir(pm))
    # …and StratConfig agrees on every venue-neutral field.
    pm_fields = set(pm.StratConfig.__dataclass_fields__)
    hl_fields = set(StratConfig.__dataclass_fields__)
    assert pm_fields == hl_fields, f"StratConfig drift: {pm_fields ^ hl_fields}"
