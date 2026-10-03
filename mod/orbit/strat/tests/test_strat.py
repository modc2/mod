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



# ── Bridge: module strats on the unified protocol (offline) ────────

bridge = sys.modules["bridge"]
SRC_PRESENT = {m: s.available() for m, s in bridge.SOURCES.items()}


def _bridged(name, watchlist, params=None, capital=1000.0):
    cls = MOD._class(name)
    return cls(proto.StratConfig(name=name, capital=capital,
                                 params=dict(params or {}), watchlist=watchlist,
                                 max_order_size=1e9))


def test_bridge_sources_load_without_drift():
    """Every shipped strat package loads by path and the codec maps every
    native dataclass field — a schema change in polymarket / hyperliquid /
    copytensor fails HERE, before a bridged strat silently drops data."""
    for mod, present in SRC_PRESENT.items():
        if not present:
            continue
        info = bridge.SOURCES[mod].info()
        assert "error" not in info, info
        assert info["strats"], f"{mod} shipped no strats"
        assert info["drift"] == [], f"{mod} drift: {info['drift']}"


def test_bridge_detects_drift():
    """The drift check is real: a codec that names the wrong instrument
    field must report the native one as unmapped."""
    if not SRC_PRESENT.get("hyperliquid"):
        return
    class Broken(bridge.HyperliquidSource):
        symbol_field = "ticker"
    src = Broken()
    src._pkg = bridge.SOURCES["hyperliquid"].package()
    src.pkg_name = bridge.SOURCES["hyperliquid"].pkg_name
    assert any("coin" in d for d in src.drift())


def test_bridged_strats_register_and_verify():
    reg = MOD._registry()
    for mod, present in SRC_PRESENT.items():
        if not present:
            continue
        names = [n for n, e in reg.items() if e["origin"] == "bridge"
                 and e["strat"]["source"] == mod]
        assert names, f"no bridged strats from {mod}"
        for n in names:
            v = MOD.verify(n)
            assert v["ok"], (n, v)
            assert issubclass(MOD._class(n), proto.Strat)
            assert MOD.code(n).strip()      # the module's own source, read


def test_bridge_codec_roundtrip():
    S = proto.OrderSide
    cases = {
        "polymarket": proto.VenueTrade(id="p1", venue="polymarket", trader="0xa",
            timestamp=1, symbol="12345", side=S.BUY, size=10, price=0.4,
            extras={"market": "m", "condition_id": "0xc", "outcome": "Yes"}),
        "hyperliquid": proto.VenueTrade(id="h1", venue="hyperliquid", trader="0xa",
            timestamp=1, symbol="BTC", side=S.SELL, size=1, price=100,
            extras={"closed_pnl": 5.0, "fee": 0.1, "dir": "Close Long"}),
        "copytensor": proto.VenueTrade(id="c1", venue="bittensor", trader="5Fa",
            timestamp=1, symbol="SN8", side=S.BUY, size=3, price=0.2,
            extras={"tao_value": 0.6, "block": 9}),
    }
    for mod, vt in cases.items():
        if not SRC_PRESENT.get(mod):
            continue
        src = bridge.SOURCES[mod]
        nt = src.trade_in(vt)
        assert getattr(nt, src.symbol_field) == src.sym_in(vt.symbol)
        for native_f, extra_k in src.trade_extras.items():
            assert getattr(nt, native_f) == vt.extras[extra_k], (mod, native_f)
        o = proto.Order(venue=src.venue, symbol=vt.symbol, side=vt.side,
                        size=vt.size, price=vt.price, source_trade_id=vt.id)
        back = src.order_out(src.order_in(o))
        assert (back.venue, back.symbol, back.side, back.source_trade_id) == \
               (o.venue, o.symbol, o.side, o.source_trade_id), mod


def test_bridged_hyperliquid_native_signal_and_backtest():
    """The NATIVE hyperliquid mirror runs (size_pct, closed_pnl model)."""
    if not SRC_PRESENT.get("hyperliquid"):
        return
    S = proto.OrderSide
    s = _bridged("hyperliquid.copy_wallets",
                 [{"venue": "hyperliquid", "address": "0xabc", "weight": 1.0}],
                 params={"size_pct": 50, "min_order_size": 1})
    tape = [
        proto.VenueTrade(id="a", venue="hyperliquid", trader="0xabc", timestamp=1,
                         symbol="ETH", side=S.BUY, size=2, price=100),
        proto.VenueTrade(id="b", venue="hyperliquid", trader="0xabc", timestamp=2,
                         symbol="ETH", side=S.SELL, size=2, price=110,
                         extras={"closed_pnl": 20.0, "fee": 1.0}),
        proto.VenueTrade(id="x", venue="bittensor", trader="5F", timestamp=3,
                         symbol="SN1", side=S.BUY, size=9, price=1),  # other venue
    ]
    orders = s.signal(proto.SyncResult(timestamp=3, trades=tape, cash=1e6))
    assert [o.symbol for o in orders] == ["ETH", "ETH"]
    assert all(o.venue == "hyperliquid" and abs(o.size - 1.0) < 1e-9 for o in orders)
    bt = s.backtest(tape)
    # half-size mirror of a +20 close with 1 fee -> +10 - 0.5
    assert abs(bt.final_pnl - 9.5) < 1e-9, bt
    assert bt.notes[0].startswith("native hyperliquid model")


def test_bridged_copytensor_native_mark_to_market():
    if not SRC_PRESENT.get("copytensor"):
        return
    S = proto.OrderSide
    ss58 = "5FCaseSensitiveKey"
    s = _bridged("copytensor.copy_coldkeys",
                 [{"venue": "bittensor", "address": ss58, "weight": 1.0}],
                 params={"size_pct": 100, "min_order_size": 0.01})
    tape = [
        proto.VenueTrade(id="1", venue="bittensor", trader=ss58, timestamp=1,
                         symbol="SN8", side=S.BUY, size=10, price=1.0),
        proto.VenueTrade(id="2", venue="bittensor", trader="5Other", timestamp=2,
                         symbol="SN8", side=S.BUY, size=1, price=2.0),  # price obs
    ]
    orders = s.signal(proto.SyncResult(timestamp=2, trades=tape, cash=100))
    assert len(orders) == 1 and orders[0].symbol == "SN8"
    assert orders[0].source_trader == ss58          # never lowercased
    bt = s.backtest(tape)
    assert bt.final_pnl > 0                          # re-marked at 2.0


def test_bridged_polymarket_native_fifo():
    if not SRC_PRESENT.get("polymarket"):
        return
    S = proto.OrderSide
    s = _bridged("polymarket.copytrader",
                 [{"venue": "polymarket", "address": "0xabc", "weight": 1.0}],
                 capital=100.0)
    tape = [
        proto.VenueTrade(id="1", venue="polymarket", trader="0xabc", timestamp=1,
                         symbol="777", side=S.BUY, size=100, price=0.30),
        proto.VenueTrade(id="2", venue="polymarket", trader="0xabc", timestamp=2,
                         symbol="777", side=S.SELL, size=100, price=0.60),
    ]
    orders = s.signal(proto.SyncResult(timestamp=2, trades=tape, cash=1000))
    assert orders and all(o.venue == "polymarket" and o.symbol == "777" for o in orders)
    bt = s.backtest(tape)
    assert bt.trades_simulated == 2 and bt.final_pnl > 0, bt


def test_bridged_execute_keeps_native_dedupe_in_step():
    if not SRC_PRESENT.get("hyperliquid"):
        return
    S = proto.OrderSide
    s = _bridged("hyperliquid.copy_wallets",
                 [{"venue": "hyperliquid", "address": "0xabc", "weight": 1.0}],
                 params={"size_pct": 100, "min_order_size": 1})
    s.config.place_order = lambda o: proto.ExecutionResult(
        order=o, success=True, filled_size=o.size, filled_price=o.price)
    t = proto.VenueTrade(id="z", venue="hyperliquid", trader="0xabc", timestamp=1,
                         symbol="SOL", side=S.BUY, size=1, price=100)
    snap = proto.SyncResult(timestamp=1, trades=[t], cash=1e6)
    s.execute(s.signal(snap))
    assert s.signal(snap) == []                     # native won't re-fire it


def test_fork_bridged_strat():
    if not SRC_PRESENT.get("hyperliquid"):
        return
    d = os.path.join(SELF, "strats", "tmpbridgefork")
    shutil.rmtree(d, ignore_errors=True)
    try:
        f = MOD.fork("hyperliquid.copy_wallets", "tmpbridgefork")
        assert f["verify"]["ok"], f
        e = MOD._entry("tmpbridgefork")
        assert e["origin"] == "builtin" and e["strat"]["source"] == "hyperliquid"
        cls = MOD._class("tmpbridgefork")
        assert issubclass(cls, bridge.Bridged)
        s = _mk("tmpbridgefork", params={"size_pct": 100, "min_order_size": 1})
        t = proto.VenueTrade(id="q", venue="hyperliquid", trader="0xabc",
                             timestamp=1, symbol="BTC", side=proto.OrderSide.BUY,
                             size=1, price=100)
        assert s.signal(proto.SyncResult(timestamp=1, trades=[t], cash=1e6))
    finally:
        shutil.rmtree(d, ignore_errors=True)

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
