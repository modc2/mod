"""Offline unit tests for the copytensor strategy layer (src/strats).

Everything here runs without the API or the chain: `ct` is faked with a
canned leaderboard, so the tests pin selection, filtering, weighting and
sleeve composition — the pure logic the live engine trusts. The parity
tests at the bottom pin the schema to polymarket's canonical base (and
hyperliquid's port), so the cross-mod contract can't silently drift.

Run:  cd orbit/copytensor && python3 -m pytest tests/test_strats.py -q
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.strats import (  # noqa: E402
    REGISTRY, BacktestResult, CopyColdkeys, ExecutionResult, Leader, Order,
    OrderSide, Steady, Strat, StratConfig, SyncResult, TickResult, TopN,
    TraderTrade, Whales, flow_to_trade, list_strats, make,
)

# Realistic-looking (checksum-irrelevant offline) ss58s with mixed case —
# several tests assert case is PRESERVED end to end.
A = "5F3sa2TJAWMqDhXG6jhV4N8ko9SxwGy8TpaNS1repo5EYjQX"
B = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
C = "5HGjWAeFDfFCWPsjFQdVV2Msvz2XtMktvgocEZcCj68kUMaw"


class FakeCT:
    """Stands in for the copytensor mod client; serves a fixed board."""

    def __init__(self, rows=None, copies=None):
        self.rows = rows or []
        self.copies = copies or []
        self.created = []
        self.deleted = []
        self.calls = []

    def leaderboard(self, days=7, top=50):
        self.calls.append({"days": days, "top": top})
        return list(self.rows)

    def wallet_balance(self):
        return {"ss58": A, "balance_tao": 100.0}

    def create_copy(self, **row):
        self.created.append(row)
        return {"id": f"copy-{len(self.created)}", **row}

    def list_copies(self):
        return {"copies": list(self.copies) + [
            {"id": f"copy-{i+1}", **r} for i, r in enumerate(self.created)]}

    def delete_copy(self, copy_id):
        self.deleted.append(copy_id)
        return {"deleted": copy_id}

    def portfolio(self):
        return {"plan": []}


def R(ss58, pnl=0.0, book=100.0, subnets=3, baseline=True,
      market_pnl=0.0, market_pct=0.0):
    """A leaderboard row, LeaderboardEntryResponse-shaped."""
    return {"ss58": ss58, "label": None, "total_stake_tao": book,
            "pnl_tao": pnl, "pnl_pct": 0.0, "num_subnets": subnets,
            "top_subnet": None, "top_subnet_pnl": 0.0, "baseline": baseline,
            "window_days": 7.0, "market_pnl_tao": market_pnl,
            "market_pct": market_pct, "flow_tao": 0.0}


def F(id, trader, netuid=1, side=OrderSide.BUY, size=100.0, price=0.05,
      ts=1_000):
    return TraderTrade(id=id, trader=trader, timestamp=ts, netuid=netuid,
                       side=side, size=size, price=price,
                       tao_value=size * price)


def snap(trades, ts=2_000):
    return SyncResult(timestamp=ts, trader_trades=trades,
                      wallet_tao=0.0, open_positions={})


# ── Leader / StratParams / registry ─────────────────────────────────────

def test_leader_preserves_ss58_case():
    e = Leader(ss58=A, weight=0.5).to_engine()
    assert e == {"ss58": A, "weight": 0.5, "enabled": True}
    assert e["ss58"] != A.lower()   # the address really is mixed-case


def test_strat_kwargs_split_risk_vs_params():
    s = TopN(n=3, size_pct=7.5, max_per_trade_tao=2.5, netuids_deny=[64])
    assert s.risk.size_pct == 7.5
    assert s.risk.max_per_trade_tao == 2.5
    assert s.risk.netuids_deny == [64]
    assert s._params["n"] == 3               # strat knob stays out of risk
    assert s.risk.interval_ms == 300_000     # untouched default


def test_registry_and_make():
    assert set(REGISTRY) == {"copy_coldkeys", "top_n", "whales", "steady"}
    assert {r["name"] for r in list_strats()} == set(REGISTRY)
    assert isinstance(make("top_n", n=2), TopN)
    with pytest.raises(ValueError):
        make("nope")


# ── Selection strats ────────────────────────────────────────────────────

def test_top_n_pnl_weighted_and_skips_warming_rows():
    ct = FakeCT([R(A, pnl=75), R(B, pnl=25), R(C, pnl=999, baseline=False)])
    leaders = TopN(n=2).pick_leaders(ct)
    assert [l.ss58 for l in leaders] == [A, B]       # C never picked: warming
    assert leaders[0].weight == pytest.approx(0.75)
    assert leaders[1].weight == pytest.approx(0.25)

    equal = TopN(n=2, equal_weight=True).pick_leaders(ct)
    assert [l.weight for l in equal] == [1.0, 1.0]


def test_whales_sqrt_weighted_and_requires_spread():
    ct = FakeCT([R(A, book=400), R(B, book=100), R(C, book=9_999, subnets=1)])
    leaders = Whales(n=2, min_subnets=2).pick_leaders(ct)
    assert [l.ss58 for l in leaders] == [A, B]       # C is a one-subnet bet
    # √400 : √100 = 2 : 1
    assert leaders[0].weight == pytest.approx(2 / 3)
    assert leaders[1].weight == pytest.approx(1 / 3)


def test_steady_filters_flow_fiction_and_equal_weights():
    ct = FakeCT([
        R(A, market_pnl=10, market_pct=12.0),
        R(B, market_pnl=5, market_pct=8.0, book=10.0),   # thin book: out
        R(C, market_pnl=3, market_pct=900.0),            # emptied-wallet artefact: out
    ])
    leaders = Steady(n=5, min_book_tao=25).pick_leaders(ct)
    assert [l.ss58 for l in leaders] == [A]
    assert leaders[0].weight == 1.0


def test_copy_coldkeys_accepts_strings_and_dicts():
    s = CopyColdkeys([A, {"ss58": B, "weight": 3.0}])
    leaders = s.pick_leaders(ct=None)
    assert [(l.ss58, l.weight) for l in leaders] == [(A, 1.0), (B, 3.0)]


# ── Canonical schema surface (shared with polymarket/hyperliquid) ───────

def test_resolve_watchlist_bridges_pick_leaders_to_config():
    s = CopyColdkeys([A, B])
    wl = s.resolve_watchlist(ct=None)
    assert wl == [{"address": A, "weight": 1.0}, {"address": B, "weight": 1.0}]
    assert s.config.watchlist == wl


def test_default_signal_mirrors_sized_weighted_and_slippage_padded():
    s = CopyColdkeys([A], size_pct=10)
    s.config.watchlist = [{"address": A, "weight": 0.5}]
    orders = s.signal(snap([F("f1", A, side=OrderSide.BUY, size=1_000, price=0.05)]))
    assert len(orders) == 1
    o = orders[0]
    assert o.netuid == 1 and o.side == OrderSide.BUY
    assert o.size == pytest.approx(1_000 * 0.10 * 0.5)     # size_pct × weight
    assert o.price == pytest.approx(0.05 * 1.01)           # +100bps pad on buys
    assert o.source_trade_id == "f1" and o.tag == "mirror"

    sells = s.signal(snap([F("f2", A, side=OrderSide.SELL, size=1_000, price=0.05)]))
    assert sells[0].price == pytest.approx(0.05 * 0.99)    # −pad on sells


def test_default_signal_filters_deny_dust_and_strangers():
    s = CopyColdkeys([A], size_pct=10, netuids_deny=[64],
                     min_order_size_tao=1.0)
    s.resolve_watchlist(ct=None)
    trades = [
        F("deny", A, netuid=64),                       # denied subnet
        F("stranger", C),                              # not on watchlist
        F("dust", A, size=10, price=0.05),             # 0.05τ mirror
        F("ok", A, size=1_000, price=0.05),            # 5τ mirror
    ]
    orders = s.signal(snap(trades))
    assert [o.source_trade_id for o in orders] == ["ok"]


def test_default_signal_clamps_to_max_order_size():
    s = CopyColdkeys([A], size_pct=100, max_per_trade_tao=2.5)
    s.resolve_watchlist(ct=None)
    o = s.signal(snap([F("big", A, size=1_000, price=0.05)]))[0]
    assert o.size * 0.05 == pytest.approx(2.5)             # τ notional capped


def test_execute_without_place_order_reports_failures_not_crashes():
    s = CopyColdkeys([A])
    rs = s.execute([Order(netuid=1, side=OrderSide.BUY, size=10, price=0.05)])
    assert len(rs) == 1 and not rs[0].success
    assert "place_order" in (rs[0].error or "")


def test_tick_end_to_end_with_engine_supplied_io_and_dedupe():
    flows = [F("f1", A, size=1_000, price=0.05)]
    placed = []

    def place(o: Order) -> ExecutionResult:
        placed.append(o)
        return ExecutionResult(order=o, success=True, order_id="0xext",
                               filled_size=o.size, filled_price=o.price)

    s = CopyColdkeys([A], size_pct=10)
    s.resolve_watchlist(ct=None)
    s.config.fetch_trader_trades = lambda addr, since: list(flows)
    s.config.fetch_wallet_tao = lambda: 42.0
    s.config.fetch_open_positions = None
    s.config.place_order = place

    r = s.tick()
    assert isinstance(r, TickResult)
    assert r.sync.wallet_tao == 42.0
    assert len(r.orders) == 1 and len(placed) == 1 and not r.skipped
    assert s._positions[1] == pytest.approx(100.0)         # optimistic update
    assert s.state()["handled_trade_count"] == 1

    # Same flow re-observed on the next sync window must NOT fire again.
    r2 = s.tick()
    assert r2.orders == [] and len(placed) == 1


def test_backtest_marks_to_market_off_observed_prices():
    s = CopyColdkeys([A], size_pct=10, capital=100.0, max_slippage_bps=0)
    s.resolve_watchlist(ct=None)
    history = [
        # Buy 1000α @0.05 → mirror 100α, cash −5τ, mark 0.
        F("h1", A, size=1_000, price=0.05, ts=1_000),
        # Price observation only (stranger's flow): pool now 0.07 → book +2τ.
        F("h2", C, size=50, price=0.07, ts=2_000),
        # Leader sells 500α @0.08 → mirror sells 50α: cash +4−5=−1τ,
        # book 50α×0.08=4τ → mark 3τ.
        F("h3", A, side=OrderSide.SELL, size=500, price=0.08, ts=3_000),
    ]
    b = s.backtest(history)
    assert isinstance(b, BacktestResult)
    assert b.trades_simulated == 2                          # h2 is a stranger
    assert b.pnl_curve[0] == (1_000, pytest.approx(0.0))
    assert b.pnl_curve[1] == (2_000, pytest.approx(2.0))
    assert b.pnl_curve[2] == (3_000, pytest.approx(3.0))
    assert b.final_pnl == pytest.approx(3.0)
    assert b.roi_pct == pytest.approx(3.0)

    empty = s.backtest([])
    assert empty.trades_simulated == 0 and empty.notes


def test_config_and_risk_views_agree_on_shared_knobs():
    s = CopyColdkeys([A], size_pct=5, min_order_size_tao=0.25,
                     max_per_trade_tao=5, max_slippage_bps=40)
    assert s.config.min_order_size == 0.25
    assert s.config.max_order_size == 5
    assert s.config.max_slippage_bps == 40
    assert s.config.name == "copy_coldkeys"

    cfg = StratConfig(name="x", capital=20, watchlist=[], min_order_size=0.7)
    s2 = CopyColdkeys([A], config=cfg)
    assert s2.config is cfg
    assert s2.risk.min_order_size_tao == 0.7 and s2.risk.capital == 20


# ── Sleeve bridge ───────────────────────────────────────────────────────

def test_build_copies_sizes_sleeves_from_normalized_weights():
    ct = FakeCT([R(A, pnl=75), R(B, pnl=25)])
    rows = TopN(n=2, capital=40).build_copies(ct)
    assert [r["target_ss58"] for r in rows] == [A, B]
    assert rows[0]["alloc_tao"] == pytest.approx(30.0)      # 40 × 0.75
    assert rows[1]["alloc_tao"] == pytest.approx(10.0)
    assert all(r["label"] == "top_n" for r in rows)
    assert all(r["our_hotkey"] == A for r in rows)          # wallet prefill
    assert all(r["poll_interval_sec"] == 300 for r in rows)
    assert "max_tao_per_tx" not in rows[0]                  # 0 = unset

    capped = TopN(n=2, capital=40, max_per_trade_tao=1.5).build_copies(ct)
    assert capped[0]["max_tao_per_tx"] == 1.5


def test_build_copies_requires_capital_and_leaders():
    ct = FakeCT([R(A, pnl=75)])
    with pytest.raises(RuntimeError, match="capital"):
        TopN(n=1).build_copies(ct)
    with pytest.raises(RuntimeError, match="no leaders"):
        TopN(n=1, capital=10).build_copies(FakeCT([]))


def test_start_stop_round_trip_via_label():
    ct = FakeCT([R(A, pnl=75), R(B, pnl=25)],
                copies=[{"id": "other", "label": "someone_else"}])
    s = TopN(n=2, capital=40)
    created = s.start(ct)
    assert len(created) == 2 and len(ct.created) == 2
    st = s.status(ct)
    assert len(st["copies"]) == 2                           # other label excluded
    s.stop(ct)
    assert set(ct.deleted) == {"copy-1", "copy-2"}          # never "other"


# ── flow_to_trade ───────────────────────────────────────────────────────

def test_flow_to_trade_maps_bt_row_and_synthesizes_stable_id():
    flow = {"netuid": 19, "side": "sell", "alpha": 12.5, "price": 0.04,
            "tao_value": 0.5}
    t1 = flow_to_trade(A, flow, ts_ms=5_000, block=123)
    t2 = flow_to_trade(A, flow, ts_ms=5_000, block=123)
    assert t1.id == t2.id                                   # dedupe-stable
    assert t1.trader == A and t1.netuid == 19
    assert t1.side == OrderSide.SELL
    assert t1.size == 12.5 and t1.price == 0.04 and t1.block == 123


# ── Cross-mod parity: the schema must not drift from polymarket's ───────

_PM_BASE = Path("/root/mod/mod/orbit/polymarket/src/strats/base/mod.py")
_HL_BASE = Path("/root/mod/mod/orbit/hyperliquid/src/strats/base.py")

CANONICAL_METHODS = {"setup", "sync", "signal", "execute", "tick",
                     "backtest", "teardown", "state"}
CANONICAL_TYPES = {"Strat", "StratConfig", "Order", "OrderSide", "TraderTrade",
                   "SyncResult", "ExecutionResult", "TickResult", "BacktestResult"}

# The ONE declared venue rename: τ is the cash currency on Bittensor.
CASH_FIELD_RENAMES = {"fetch_wallet_usdc": "fetch_wallet_tao"}


def _load_by_path(path: Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # dataclass field resolution looks the module up in sys.modules.
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(name, None)
    return mod


def test_canonical_surface_present_here():
    assert CANONICAL_METHODS <= set(dir(Strat))
    import src.strats as m
    assert CANONICAL_TYPES <= set(dir(m))


@pytest.mark.skipif(not _PM_BASE.exists(), reason="polymarket checkout not present")
def test_schema_parity_with_polymarket():
    pm = _load_by_path(_PM_BASE, "pm_strat_base")
    # Same method contract on the base class…
    assert CANONICAL_METHODS <= set(dir(pm.Strat))
    # …same dataclass vocabulary…
    assert CANONICAL_TYPES <= set(dir(pm))
    # …and StratConfig agrees on every field through the declared rename.
    pm_fields = {CASH_FIELD_RENAMES.get(f, f)
                 for f in pm.StratConfig.__dataclass_fields__}
    ct_fields = set(StratConfig.__dataclass_fields__)
    assert pm_fields == ct_fields, f"StratConfig drift: {pm_fields ^ ct_fields}"


@pytest.mark.skipif(not _HL_BASE.exists(), reason="hyperliquid checkout not present")
def test_method_parity_with_hyperliquid():
    hl = _load_by_path(_HL_BASE, "hl_strat_base")
    assert CANONICAL_METHODS <= set(dir(hl.Strat))
    # The sleeve bridge mirrors HL's live-engine bridge verbs.
    for verb in ("start", "stop", "status"):
        assert hasattr(Strat, verb) and hasattr(hl.Strat, verb)
