"""
Offline tests for the strat protocol + marketplace. No network, no wallets.

Run:  python3 tests/test_strat.py   (or pytest tests/)
"""

import dataclasses
import importlib.util
import json
import os
import shutil
import sys

SELF = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SELF)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


host = _load(os.path.join(SELF, "mod.py"), "strat_host_test")
proto = sys.modules["protocol"]
MOD = host.Mod()


def fake_trades():
    T = proto.VenueTrade
    S = proto.OrderSide
    return [
        T(id="t1", venue="hyperliquid", trader="0xabc", timestamp=1_000,
          symbol="BTC", side=S.BUY, size=2.0, price=100.0),
        T(id="t2", venue="hyperliquid", trader="0xabc", timestamp=2_000,
          symbol="BTC", side=S.BUY, size=0.001, price=100.0),   # dust
        T(id="t3", venue="bittensor", trader="5Fabc", timestamp=3_000,
          symbol="SN8", side=S.BUY, size=50.0, price=2.0),
        T(id="t4", venue="hyperliquid", trader="0xabc", timestamp=4_000,
          symbol="BTC", side=S.SELL, size=1.0, price=150.0),
    ]


# ── Protocol contract ──────────────────────────────────────────────

def test_method_surface():
    for m in proto.METHODS:
        assert callable(getattr(proto.Strat, m, None)), f"Strat lacks {m}()"
    assert set(proto.METHODS) == {"setup", "sync", "signal", "execute",
                                  "tick", "backtest", "teardown", "state"}


def test_parity_with_polymarket_canon():
    """The venue-neutral schema tracks the canonical polymarket Strat schema
    through DECLARED renames/additions only — any other drift fails here,
    the same coupling hyperliquid and copytensor enforce."""
    pm_path = os.path.join(os.path.dirname(SELF),
                           "polymarket", "src", "strats", "base", "mod.py")
    if not os.path.isfile(pm_path):
        return  # canon not present on this node — nothing to pin against
    pm = _load(pm_path, "pm_strat_canon")
    # Same method contract.
    for m in proto.METHODS:
        assert callable(getattr(pm.Strat, m, None)), f"canon lacks {m}()"
    # StratConfig fields: canon -> unified through declared renames,
    # plus declared venue-neutral additions.
    renames = {"fetch_trader_trades": "fetch_trades",
               "fetch_wallet_usdc": "fetch_cash",
               "fetch_open_positions": "fetch_positions"}
    additions = {"venues", "quote"}
    canon = {renames.get(f.name, f.name)
             for f in dataclasses.fields(pm.StratConfig)}
    ours = {f.name for f in dataclasses.fields(proto.StratConfig)}
    assert ours == canon | additions, (
        f"StratConfig drift: extra={ours - (canon | additions)} "
        f"missing={(canon | additions) - ours}")


def test_schema_fn():
    s = MOD.schema()
    assert s["version"] == proto.PROTOCOL_VERSION
    assert s["venues"] == ["raydium", "uniswap", "hyperliquid",
                           "bittensor", "polymarket"]
    assert "Order" in s["types"] and "venue" in s["types"]["Order"]


# ── Registry: mods are strats ──────────────────────────────────────

def test_registry_finds_builtins():
    names = {s["name"] for s in MOD.strats()}
    assert {"mirror", "whale", "momentum"} <= names


def test_verify_builtins():
    for s in MOD.strats():
        v = MOD.verify(s["name"])
        assert v["ok"], f"{s['name']}: {v['issues']}"


def test_venue_filter():
    assert all("bittensor" in s["venues"] for s in MOD.strats(venue="bittensor"))


def test_code_is_class_defined():
    assert "class Mirror(Strat)" in MOD.code("mirror")


# ── Strat behavior (fake data, no I/O) ─────────────────────────────

def _mk(name, capital=1000.0, params=None, watchlist=None):
    cls = MOD._class(name)
    e = MOD._entry(name)
    p = dict(e["strat"].get("params", {}))
    p.update(params or {})
    return cls(proto.StratConfig(
        name=name, capital=capital, params=p,
        watchlist=watchlist or [{"venue": "hyperliquid", "address": "0xabc",
                                 "weight": 1.0}]))


def test_mirror_signal_and_backtest():
    s = _mk("mirror", params={"scale": 0.5})
    snap = proto.SyncResult(timestamp=1, trades=fake_trades(), cash=1000.0)
    orders = s.signal(snap)
    # dust trade filtered later by execute/backtest min size; SELL with no
    # position held is dropped at signal time.
    assert all(o.side == proto.OrderSide.BUY for o in orders)
    bt = s.backtest(fake_trades())
    assert bt.trades_simulated > 0
    # bought BTC at 100, tape re-marks at 150 -> positive pnl
    assert bt.final_pnl > 0
    assert bt.roi_pct == bt.final_pnl / 1000.0 * 100.0


def test_whale_floor():
    s = _mk("whale", params={"min_notional": 150.0, "scale": 1.0})
    snap = proto.SyncResult(timestamp=1, trades=fake_trades(), cash=1000.0)
    orders = s.signal(snap)
    # t2 (0.1 notional) and t3 (100 notional) fall under the floor
    assert {o.source_trade_id for o in orders} == {"t1"}


def test_momentum_entry_exit():
    s = _mk("momentum", params={"rise_pct": 10.0, "drop_pct": 10.0,
                                "window": 10, "stake": 50.0})
    T, S = proto.VenueTrade, proto.OrderSide
    rising = [T(id=f"r{i}", venue="raydium", trader="w", timestamp=i,
                symbol="SOL/USDC", side=S.BUY, size=1, price=100 + i * 10)
              for i in range(3)]
    orders = s.signal(proto.SyncResult(timestamp=3, trades=rising, cash=1000.0))
    assert len(orders) == 1 and orders[0].side == S.BUY
    falling = [T(id=f"f{i}", venue="raydium", trader="w", timestamp=10 + i,
                 symbol="SOL/USDC", side=S.SELL, size=1, price=120 - i * 30)
               for i in range(3)]
    orders = s.signal(proto.SyncResult(
        timestamp=13, trades=falling, cash=1000.0,
        open_positions={"raydium:SOL/USDC": 2.0}))
    assert any(o.side == S.SELL and o.size == 2.0 for o in orders)


def test_execute_without_place_order_is_graceful():
    s = _mk("mirror")
    res = s.execute([proto.Order(venue="hyperliquid", symbol="BTC",
                                 side=proto.OrderSide.BUY, size=1, price=10)])
    assert res and not res[0].success and "place_order" in res[0].error


# ── Authoring: new / fork ──────────────────────────────────────────

def test_new_and_fork_roundtrip():
    for n in ("tmptest", "tmptest-fork"):
        shutil.rmtree(os.path.join(SELF, "strats", n), ignore_errors=True)
    try:
        r = MOD.new("tmptest", venues=["hyperliquid"], description="scratch")
        assert r["verify"]["ok"], r["verify"]
        assert MOD._entry("tmptest")["strat"]["venues"] == ["hyperliquid"]
        f = MOD.fork("tmptest", "tmptest-fork")
        assert f["verify"]["ok"]
        with open(os.path.join(f["dir"], "config.json")) as fh:
            assert json.load(fh)["forked_from"] == "tmptest"
        # the scaffolded class actually runs
        s = _mk("tmptest")
        snap = proto.SyncResult(timestamp=1, trades=fake_trades(), cash=100.0)
        assert s.signal(snap)
    finally:
        for n in ("tmptest", "tmptest-fork"):
            shutil.rmtree(os.path.join(SELF, "strats", n), ignore_errors=True)


def test_new_rejects_duplicates_and_bad_venues():
    for bad in (lambda: MOD.new("mirror"),
                lambda: MOD.new("xyzzy", venues=["nasdaq"])):
        try:
            bad()
            assert False, "should have raised"
        except ValueError:
            pass


# ── Marketplace surfaces ───────────────────────────────────────────

def test_info_and_board_offline():
    info = MOD.info()
    assert info["protocol_version"] == 1 and info["count"] >= 3
    board = MOD.board()   # no traders -> listing without perf, no network
    assert len(board["strats"]) >= 3
    assert all("ok" in row for row in board["strats"])


def test_venues_registry_shape():
    vs = MOD.venues(check=False)   # no network
    assert set(vs) == set(proto.VENUES)
    assert vs["raydium"]["module"] == "solana"
    assert vs["uniswap"]["module"] == "defi"
    assert vs["bittensor"]["module"] == "bt"


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok  {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {name}: {e!r}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
