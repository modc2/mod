"""
bridge — mounts the strat classes that already ship INSIDE the polymarket,
hyperliquid and copytensor modules onto the unified strat protocol, without
touching a line of their code.

Each of those modules carries its own copy of the canonical Strat schema
(same eight methods, venue-specific dataclasses):

    polymarket/src/strats    Order.token_id   cash=wallet_usdc  FIFO replay
    hyperliquid/src/strats   Order.coin       cash=wallet_usdc  closed_pnl replay
    copytensor/src/strats    Order.netuid     cash=wallet_tao   mark-to-market

A `Source` adapter per module does three things, all read-only:

  1. LOAD   the module's strats package by path under a private name
            (strat_src_<mod>) — the module is never imported as itself,
            never put on sys.path, never written to.
  2. CODEC  translate the unified dataclasses <-> the native ones
            (VenueTrade <-> TraderTrade, Order <-> Order, SyncResult,
            BacktestResult, StratConfig). `drift()` lists every native
            field the codec does not know — the parity test pins it to
            empty, so a schema change upstream fails HERE, not in prod.
  3. SELECT give native selection hooks (`pick_leaders(hl|ct)`) a
            duck-typed read-only client of the owning module, so a
            bridged strat can resolve its own watchlist.

`bridged(name)` returns a protocol.Strat subclass whose signal()/backtest()
delegate to the native class — the native strategy logic and the native
backtest model run unchanged; sync/execute/tick go through the unified
venue layer (dry-run default, keys never held).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import json
import os
import sys
import urllib.parse
from typing import Any, Optional

from protocol import (BacktestResult, ExecutionResult, Order, OrderSide,
                      Strat, StratConfig, SyncResult, VenueTrade)

SELF = os.path.dirname(os.path.abspath(__file__))
ORBIT = os.path.dirname(SELF)


def _venues():
    """venues.py as mod.py loaded it (strat_venues), else load it ourselves."""
    v = sys.modules.get("strat_venues")
    if v is None:
        spec = importlib.util.spec_from_file_location(
            "strat_venues", os.path.join(SELF, "venues.py"))
        v = importlib.util.module_from_spec(spec)
        sys.modules["strat_venues"] = v
        spec.loader.exec_module(v)
    return v


class BridgeError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════
# Read-only clients handed to native selection hooks. They duck-type ONLY
# the slice of each module's Mod that the shipped strats call.
# ═══════════════════════════════════════════════════════════════════════


class _Reader:
    mod = ""

    def __init__(self):
        self.peer = _venues().Peer(self.mod, f"STRAT_{self.mod.upper()}_URL")

    def _get(self, path: str, timeout: int = 120, **params) -> Any:
        q = {k: v for k, v in params.items() if v is not None}
        url = self.peer.base() + path + ("?" + urllib.parse.urlencode(q) if q else "")
        return self.peer._req("GET", url, timeout=timeout)


class HLReader(_Reader):
    """The `hl` the hyperliquid strats' pick_leaders(hl) expects."""
    mod = "hyperliquid"

    def top_traders(self, days=7, min_per_day=1.0, pool=150, **kw) -> Any:
        return self._get("/traders/top", days=days, min_per_day=min_per_day,
                         pool=pool, **kw)


class CTReader(_Reader):
    """The `ct` the copytensor strats' pick_leaders(ct) expects."""
    mod = "copytensor"

    def leaderboard(self, days=7, top=50) -> Any:
        return self._get("/leaderboard", days=days, top=top)

    def wallet_balance(self) -> Any:
        # build_copies() asks for this only when no hotkey was given; this
        # module holds no wallet, so the caller must name one.
        raise BridgeError("pass hotkey= — strat never reads a wallet")

    def _post(self, path: str, body: Any) -> Any:
        # Only reached by backtest_remote(): a server-side REPLAY, read-only.
        return self.peer._req("POST", self.peer.base() + path, body, timeout=120)


class PMReader:
    """Polymarket has no native pick_leaders — its watchlist comes from the
    public leaderboard the polymarket module itself reads (data-api)."""
    DATA = os.environ.get("STRAT_POLYMARKET_DATA_URL",
                          "https://data-api.polymarket.com")
    WINDOWS = {1: "DAY", 7: "WEEK", 30: "MONTH"}

    def leaderboard(self, days=7, n=5) -> list:
        window = self.WINDOWS.get(int(days), "WEEK")
        url = (f"{self.DATA}/v1/leaderboard?timePeriod={window}"
               f"&orderBy=PNL&limit={int(n)}")
        rows = _venues().Peer("polymarket", "")._req("GET", url, timeout=20)
        return rows if isinstance(rows, list) else []


# ═══════════════════════════════════════════════════════════════════════
# Sources — one adapter per strat-shipping module.
# ═══════════════════════════════════════════════════════════════════════


class Source:
    mod = ""            # module that owns the strats package
    venue = ""          # protocol venue its orders land on
    currency = "USD"
    symbol_field = ""   # native Order/TraderTrade instrument field
    cash_field = ""     # native SyncResult cash field
    fetch_cash_field = ""
    # native TraderTrade fields carried in VenueTrade.extras
    trade_extras: dict[str, str] = {}
    model = ""          # one line: the native backtest model

    def __init__(self):
        self.root = os.path.join(ORBIT, self.mod, "src", "strats")
        self.pkg_name = f"strat_src_{self.mod}"
        self._pkg = None
        self._error: Optional[str] = None

    # ── load ─────────────────────────────────────────────────────

    def available(self) -> bool:
        return os.path.isfile(os.path.join(self.root, "__init__.py"))

    def package(self):
        if self._pkg is not None:
            return self._pkg
        if not self.available():
            raise BridgeError(f"{self.mod}: no strats package at {self.root}")
        spec = importlib.util.spec_from_file_location(
            self.pkg_name, os.path.join(self.root, "__init__.py"),
            submodule_search_locations=[self.root])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[self.pkg_name] = pkg
        try:
            spec.loader.exec_module(pkg)
        except Exception:
            sys.modules.pop(self.pkg_name, None)
            raise
        self._pkg = pkg
        return pkg

    def base(self):
        self.package()
        return importlib.import_module(f"{self.pkg_name}.base")

    def natives(self) -> dict[str, type]:
        """native name -> concrete native Strat class."""
        return dict(getattr(self.package(), "REGISTRY", {}))

    def native_file(self, cls: type) -> str:
        return inspect.getsourcefile(cls) or ""

    # ── codec ────────────────────────────────────────────────────

    def sym_out(self, native_sym: Any) -> str:
        return str(native_sym)

    def sym_in(self, symbol: str) -> Any:
        return symbol

    def side_in(self, side: OrderSide):
        return self.base().OrderSide(side.value)

    def trade_in(self, t: VenueTrade):
        b = self.base()
        kw = {"id": t.id, "trader": t.trader, "timestamp": t.timestamp,
              self.symbol_field: self.sym_in(t.symbol),
              "side": self.side_in(t.side), "size": t.size, "price": t.price}
        for native_f, extra_k in self.trade_extras.items():
            v = (t.extras or {}).get(extra_k)
            if v is not None:
                kw[native_f] = v
        fields = {f.name: f for f in dataclasses.fields(b.TraderTrade)}
        for k, f in fields.items():   # required native fields with no data
            if k not in kw and f.default is dataclasses.MISSING \
                    and f.default_factory is dataclasses.MISSING:
                kw[k] = "" if f.type in ("str", str) else None
        return b.TraderTrade(**kw)

    def order_out(self, o) -> Order:
        tag = o.tag
        if getattr(o, "reduce_only", False):
            tag = f"{tag or ''}+reduce_only"
        return Order(venue=self.venue, symbol=self.sym_out(getattr(o, self.symbol_field)),
                     side=OrderSide(o.side.value), size=float(o.size),
                     price=float(o.price), order_type=o.order_type,
                     source_trader=o.source_trader,
                     source_trade_id=o.source_trade_id, tag=tag)

    def order_in(self, o: Order):
        return self.base().Order(**{
            self.symbol_field: self.sym_in(o.symbol), "side": self.side_in(o.side),
            "size": o.size, "price": o.price, "source_trader": o.source_trader,
            "source_trade_id": o.source_trade_id, "tag": o.tag})

    def result_in(self, r: ExecutionResult, native_order):
        return self.base().ExecutionResult(
            order=native_order, success=r.success, order_id=r.order_id,
            error=r.error, filled_size=r.filled_size, filled_price=r.filled_price)

    def positions_in(self, positions: dict[str, float]) -> dict:
        out = {}
        for key, size in (positions or {}).items():
            venue, _, sym = key.partition(":")
            if venue == self.venue and sym:
                try:
                    out[self.sym_in(sym)] = size
                except Exception:
                    continue
        return out

    def sync_in(self, sync: SyncResult):
        return self.base().SyncResult(**{
            "timestamp": sync.timestamp,
            "trader_trades": [self.trade_in(t) for t in sync.trades
                              if t.venue == self.venue],
            self.cash_field: sync.cash,
            "open_positions": self.positions_in(sync.open_positions),
            "extras": dict(sync.extras or {}),
        })

    @staticmethod
    def backtest_out(r) -> BacktestResult:
        return BacktestResult(
            pnl_curve=[(int(ts), float(v)) for ts, v in r.pnl_curve],
            trades_simulated=int(r.trades_simulated),
            fees_total=float(r.fees_total), gas_total=float(r.gas_total),
            final_pnl=float(r.final_pnl), roi_pct=float(r.roi_pct),
            notes=list(r.notes or []))

    def native_config(self, config: StratConfig):
        """Build the native StratConfig from the unified one. The engine I/O
        callables are wrapped so native code that does its own fetching
        (e.g. a setup() that primes stats) reads through the venue layer."""
        b = self.base()
        venue = self.venue

        def fetch(addr, since):
            if not config.fetch_trades:
                return []
            return [self.trade_in(t) for t in config.fetch_trades(venue, addr, since)
                    if t.venue == venue]

        def positions():
            return self.positions_in(config.fetch_positions()) \
                if config.fetch_positions else {}

        def place(o):
            if not config.place_order:
                return b.ExecutionResult(order=o, success=False,
                                         error="no place_order configured")
            return self.result_in(config.place_order(self.order_out(o)), o)

        values = {
            "name": config.name, "capital": config.capital,
            "watchlist": [dict(w) for w in config.watchlist
                          if w.get("venue", venue) in ("", venue)],
            "scan_minutes": config.scan_minutes,
            "min_order_size": config.min_order_size,
            "max_order_size": config.max_order_size,
            "max_slippage_bps": config.max_slippage_bps,
            "fetch_trader_trades": fetch,
            self.fetch_cash_field: config.fetch_cash,
            "fetch_open_positions": positions,
            "place_order": place,
            "params": dict(config.params),
        }
        fields = [f.name for f in dataclasses.fields(b.StratConfig)]
        missing = [f for f in fields if f not in values]
        if missing:
            raise BridgeError(f"{self.mod}: StratConfig drift, unmapped {missing}")
        return b.StratConfig(**{f: values[f] for f in fields})

    # ── drift: what the codec does not cover ─────────────────────

    def drift(self) -> list[str]:
        """Every native dataclass field the codec doesn't know. Empty = the
        bridge is lossless against the module's current schema."""
        b = self.base()
        known = {
            "Order": {self.symbol_field, "side", "size", "price", "order_type",
                      "source_trader", "source_trade_id", "tag", "reduce_only"},
            "TraderTrade": {"id", "trader", "timestamp", self.symbol_field,
                            "side", "size", "price", *self.trade_extras},
            "SyncResult": {"timestamp", "trader_trades", self.cash_field,
                           "open_positions", "extras"},
            "ExecutionResult": {"order", "success", "order_id", "error",
                                "filled_size", "filled_price"},
            "BacktestResult": {"pnl_curve", "trades_simulated", "fees_total",
                               "gas_total", "final_pnl", "roi_pct", "notes"},
            "StratConfig": {"name", "capital", "watchlist", "scan_minutes",
                            "min_order_size", "max_order_size",
                            "max_slippage_bps", "fetch_trader_trades",
                            self.fetch_cash_field, "fetch_open_positions",
                            "place_order", "params"},
        }
        issues = []
        for cls_name, ok in known.items():
            cls = getattr(b, cls_name, None)
            if cls is None:
                issues.append(f"{cls_name} missing from {self.mod} base")
                continue
            extra = [f.name for f in dataclasses.fields(cls) if f.name not in ok]
            if extra:
                issues.append(f"{cls_name}: unmapped {extra}")
        return issues

    # ── instantiate / select / plan ──────────────────────────────

    def instantiate(self, cls: type, config: StratConfig):
        return cls(self.native_config(config))

    def defaults(self, cls: type) -> dict:
        """The native constructor's tunable params with their defaults."""
        out = {}
        try:
            for p in inspect.signature(cls.__init__).parameters.values():
                if p.name in ("self", "config") or p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
                    continue
                out[p.name] = None if p.default is p.empty else p.default
        except (TypeError, ValueError):
            pass
        return out

    def reader(self):
        return None

    def selects(self, cls: type) -> bool:
        """Can this native strat pick its own leaders? Not when it takes a
        required leader list (copy_wallets / copy_coldkeys)."""
        return not any(v is None for v in self.defaults(cls).values())

    def resolve(self, native, max_leaders: int = 5) -> list[dict]:
        """Run the native selection hook; return unified watchlist entries."""
        return []

    def plan(self, native, **kw) -> dict:
        return {}

    def info(self) -> dict:
        d = {"module": self.mod, "venue": self.venue, "currency": self.currency,
             "package": self.root, "available": self.available(),
             "model": self.model}
        if d["available"]:
            try:
                d["strats"] = sorted(self.natives())
                d["drift"] = self.drift()
            except Exception as e:
                d["error"] = f"{type(e).__name__}: {e}"
        return d


class PolymarketSource(Source):
    mod = "polymarket"
    venue = "polymarket"
    currency = "USDC"
    symbol_field = "token_id"
    cash_field = "wallet_usdc"
    fetch_cash_field = "fetch_wallet_usdc"
    trade_extras = {"market": "market", "condition_id": "condition_id",
                    "outcome": "outcome"}
    model = "FIFO cost basis per outcome token, 2% taker fee + Polygon gas"

    def base(self):
        self.package()
        return importlib.import_module(f"{self.pkg_name}.base.mod")

    def natives(self) -> dict[str, type]:
        """Polymarket keeps one strat per folder (<id>/mod.py, the same
        layout user uploads use) and has no REGISTRY — so scan the folders
        for concrete Strat subclasses."""
        self.package()
        base = self.base()
        out = {}
        for entry in sorted(os.listdir(self.root)):
            if entry in ("base", "__pycache__") or \
                    not os.path.isfile(os.path.join(self.root, entry, "mod.py")):
                continue
            try:
                m = importlib.import_module(f"{self.pkg_name}.{entry}.mod")
            except Exception as e:
                self._error = f"{entry}: {e}"
                continue
            for obj in vars(m).values():
                if isinstance(obj, type) and issubclass(obj, base.Strat) \
                        and obj is not base.Strat and obj.__module__ == m.__name__ \
                        and not inspect.isabstract(obj):
                    out[entry] = obj
                    break
        return out

    def reader(self):
        return PMReader()

    def resolve(self, native, max_leaders=5):
        p = native.config.params
        rows = self.reader().leaderboard(days=p.get("days", 7),
                                         n=min(int(p.get("n", max_leaders)), max_leaders))
        pnls = [max(float(r.get("pnl") or 0), 0.0) for r in rows]
        total = sum(pnls) or 1.0
        return [{"venue": self.venue, "address": str(r["proxyWallet"]).lower(),
                 "weight": pnl / total} for r, pnl in zip(rows, pnls)
                if r.get("proxyWallet")]

    def plan(self, native, **kw):
        # The body polymarket's own live engine takes (POST /live/start).
        # autoExecute stays FALSE — that venue's rule, not ours to flip.
        return {"module": self.mod, "endpoint": "POST /live/start",
                "body": {"traders": [{"address": w["address"],
                                      "weight": w.get("weight", 1.0)}
                                     for w in native.config.watchlist],
                         "bankroll": native.config.capital,
                         "sizing": "bankroll", "autoExecute": False}}


class _CopyFamily(Source):
    """hyperliquid + copytensor share a shape: kwargs constructor with
    config=, a REGISTRY, and pick_leaders(client) selection."""
    leader_key = "address"

    def instantiate(self, cls, config):
        sig = inspect.signature(cls.__init__).parameters
        risk = {f.name for f in dataclasses.fields(self.base().StratParams)}
        accepts_any = any(p.kind == p.VAR_KEYWORD for p in sig.values())
        # None = "unset" (a required arg's placeholder in the listing).
        kw = {k: v for k, v in config.params.items() if v is not None
              and (k in sig or (accepts_any and k in risk))}
        # Fixed-list strats (copy_wallets / copy_coldkeys) take their list
        # positionally — feed it from the watchlist when params don't.
        for p in sig.values():
            if p.name in ("self", "config") or p.kind != p.POSITIONAL_OR_KEYWORD:
                continue
            if p.default is p.empty and p.name not in kw:
                kw[p.name] = [w["address"] for w in config.watchlist
                              if w.get("venue", self.venue) in ("", self.venue)]
        return cls(config=self.native_config(config), **kw)

    def resolve(self, native, max_leaders=5):
        leaders = [l for l in native.pick_leaders(self.reader()) if l.enabled]
        leaders.sort(key=lambda l: -l.weight)
        return [{"venue": self.venue, "address": getattr(l, self.leader_key),
                 "weight": l.weight} for l in leaders[:max_leaders]]


class HyperliquidSource(_CopyFamily):
    mod = "hyperliquid"
    venue = "hyperliquid"
    currency = "USDC"
    symbol_field = "coin"
    cash_field = "wallet_usdc"
    fetch_cash_field = "fetch_wallet_usdc"
    trade_extras = {"closed_pnl": "closed_pnl", "fee": "fee", "dir": "dir"}
    model = "exchange-reported closed_pnl × size ratio, fees scaled the same"

    def reader(self):
        return HLReader()

    def plan(self, native, eoa: Optional[str] = None, **kw):
        if not eoa:
            raise BridgeError("hyperliquid plan needs eoa= (the master wallet)")
        return {"module": self.mod, "endpoint": "hl_live_start (MCP) / POST /live/start",
                "body": native.build_config(self.reader(), eoa)}


class CopytensorSource(_CopyFamily):
    mod = "copytensor"
    venue = "bittensor"
    currency = "TAO"
    symbol_field = "netuid"
    cash_field = "wallet_tao"
    fetch_cash_field = "fetch_wallet_tao"
    trade_extras = {"tao_value": "tao_value", "block": "block"}
    leader_key = "ss58"
    model = "mark-to-market alpha book at last observed pool price"

    def sym_out(self, netuid):
        return f"SN{int(netuid)}"

    def sym_in(self, symbol):
        s = str(symbol).upper()
        return int(s[2:] if s.startswith("SN") else s)

    def reader(self):
        return CTReader()

    def plan(self, native, hotkey: Optional[str] = None, **kw):
        if not hotkey:
            raise BridgeError("copytensor plan needs hotkey= (our hotkey ss58)")
        return {"module": self.mod,
                "endpoint": "ct_create_copy per row (human-gated in the console)",
                "body": native.build_copies(self.reader(), hotkey)}

    def remote_backtest(self, native, days=7):
        """copytensor's server-side rebalanced-return replay of the basket."""
        return native.backtest_remote(self.reader(), days=days)


SOURCES: dict[str, Source] = {s.mod: s for s in (
    PolymarketSource(), HyperliquidSource(), CopytensorSource())}


# ═══════════════════════════════════════════════════════════════════════
# The bridged strat — a protocol.Strat that delegates to a native one.
# ═══════════════════════════════════════════════════════════════════════


class Bridged(Strat):
    """protocol.Strat over a native module strat. signal() and backtest()
    are the NATIVE ones (translated in/out); sync/execute/tick are the
    unified venue layer's."""

    source: Source = None       # set on generated subclasses
    native_cls: type = None
    native_name: str = ""

    def __init__(self, config: StratConfig) -> None:
        config.venues = [self.source.venue]
        super().__init__(config)
        self.native = self.source.instantiate(self.native_cls, config)

    # ── selection ────────────────────────────────────────────────

    def resolve_watchlist(self, max_leaders: int = 5) -> list[dict]:
        """Let the native strat pick its own leaders (read-only)."""
        wl = self.source.resolve(self.native, max_leaders=max_leaders)
        self.config.watchlist = wl
        self.native.config.watchlist = [{"address": w["address"],
                                         "weight": w["weight"]} for w in wl]
        return wl

    def plan(self, **kw) -> dict:
        """The config the owning module's own live engine would consume.
        Native build_config/build_copies re-run pick_leaders; pin it (on
        this instance only) to the watchlist already resolved/capped, so
        the plan names exactly the leaders the backtest saw."""
        wl = self.config.watchlist
        leader_cls = getattr(self.source.base(), "Leader", None)
        if wl and leader_cls is not None:
            key = getattr(self.source, "leader_key", "address")
            pinned = [leader_cls(**{key: w["address"], "weight": w.get("weight", 1.0)})
                      for w in wl]
            self.native.pick_leaders = lambda client: list(pinned)
        return self.source.plan(self.native, **kw)

    # ── canonical surface ────────────────────────────────────────

    def setup(self) -> None:
        super().setup()
        self.native.setup()

    def teardown(self) -> None:
        self.native.teardown()

    def signal(self, sync: SyncResult) -> list[Order]:
        native_orders = self.native.signal(self.source.sync_in(sync))
        return [self.source.order_out(o) for o in native_orders]

    def execute(self, orders: list[Order]) -> list[ExecutionResult]:
        results = super().execute(orders)
        # The native signal() dedupes on ITS handled set — keep it in step.
        self.native._handled_trade_ids |= self._handled_trade_ids
        return results

    def backtest(self, history: list[VenueTrade]) -> BacktestResult:
        native_hist = [self.source.trade_in(t) for t in history
                       if t.venue == self.source.venue]
        r = self.source.backtest_out(self.native.backtest(native_hist))
        r.notes.insert(0, f"native {self.source.mod} model: {self.source.model} "
                          f"(currency {self.source.currency})")
        return r

    def state(self) -> dict[str, Any]:
        s = super().state()
        s["bridge"] = {"source": self.source.mod, "native": self.native_name,
                       "currency": self.source.currency}
        try:
            s["native"] = self.native.state()
        except Exception as e:
            s["native"] = {"error": str(e)}
        return s


_CLASSES: dict[str, type] = {}


def split(name: str) -> tuple[str, str]:
    mod, _, native = str(name).partition(".")
    if mod not in SOURCES or not native:
        raise KeyError(f"not a bridged strat name: {name!r} (want <module>.<strat>)")
    return mod, native


def bridged(name: str) -> type:
    """The protocol.Strat subclass for '<module>.<native strat>'."""
    if name in _CLASSES:
        return _CLASSES[name]
    mod, native = split(name)
    src = SOURCES[mod]
    natives = src.natives()
    if native not in natives:
        raise KeyError(f"{mod} has no strat {native!r} — have {sorted(natives)}")
    cls_name = "".join(p.capitalize() for p in f"{mod}_{native}".split("_"))
    cls = type(cls_name, (Bridged,), {
        "source": src, "native_cls": natives[native], "native_name": native,
        "venues": [src.venue], "__module__": __name__,
        "__doc__": (natives[native].__doc__ or "").strip() or None,
    })
    _CLASSES[name] = cls
    return cls


def entries() -> dict[str, dict]:
    """Registry rows for every native strat across every available source
    — the shape mod.py's _registry() returns."""
    out = {}
    for mod, src in SOURCES.items():
        if not src.available():
            continue
        try:
            natives = src.natives()
        except Exception:
            continue
        for native, cls in natives.items():
            name = f"{mod}.{native}"
            doc = getattr(cls, "description", "") or \
                ((cls.__doc__ or "").strip().splitlines() or [""])[0]
            out[name] = {
                "dir": os.path.dirname(src.native_file(cls)),
                "file": src.native_file(cls),
                "origin": "bridge",
                "config": {"name": name, "description": doc or name,
                           "version": "native", "source": mod},
                "strat": {"protocol": 1, "class": f"bridge:{name}",
                          "venues": [src.venue], "source": mod,
                          "native": native, "currency": src.currency,
                          "selects": src.selects(cls),
                          "params": src.defaults(cls)},
            }
    return out


def drift() -> dict[str, list[str]]:
    out = {}
    for mod, src in SOURCES.items():
        if src.available():
            try:
                out[mod] = src.drift()
            except Exception as e:
                out[mod] = [f"load failed: {e}"]
    return out


def catalog(token: Optional[str] = None, limit: int = 20) -> dict:
    """The strat catalogs each module SERVES (its own server-side boards),
    read through the module's API. Read-only; the token, if any, is
    forwarded verbatim (polymarket's API is owner-gated)."""
    Peer = _venues().Peer
    reads = {
        "hyperliquid": ("/strats/board?traders=%d&vaults=%d" % (limit, limit), "rows"),
        "copytensor": ("/strats", "strats"),
        "polymarket": ("/strats", "strats"),
    }
    out = {}
    for mod, (path, key) in reads.items():
        peer = Peer(mod, f"STRAT_{mod.upper()}_URL")
        try:
            r = peer.get(path, token=token)
            rows = r.get(key, r) if isinstance(r, dict) else r
            rows = rows if isinstance(rows, list) else []
            out[mod] = {"count": len(rows), "rows": rows[:limit]}
        except Exception as e:
            code = getattr(e, "code", None)
            out[mod] = {"count": 0, "rows": [],
                        "error": "gated — pass token=" if code in (401, 403)
                        else f"{type(e).__name__}: {e}"}
    return out


FORK_TEMPLATE = '''"""{name} — forked from {src} (a {mod} strat, bridged onto the strat protocol).

The base class runs {src}'s NATIVE signal() and backtest() unchanged —
override either here to change the strategy; tune its params in config.json.
"""

from bridge import bridged

Base = bridged("{src}")


class {cls}(Base):
    pass
'''
