"""
Base abstractions for hyperliquid strategies.

This is the SAME canonical Strat schema the polymarket module uses
(polymarket/src/strats/base/mod.py) — like ERC-20 for tokens, the
interface standardizes trading logic so an engine can drive any strat
through the same methods:

    setup()        one-time init when the strat is mounted.
    sync()         pull latest data from upstream. READ-ONLY, IDEMPOTENT.
    signal(sync)   pure function — given a SyncResult, decide which Orders
                   to place. No side-effects, no I/O.
    execute(os)    submit orders to the venue. Side-effecting.
    tick()         convenience: sync → signal → execute.
    backtest(h)    replay logic over historical fills. Must not touch the
                   live wallet. Returns a BacktestResult.
    teardown()     cleanup on stop.
    state()        snapshot of internal state for the UI / persistence.

The perp-venue differences live in the dataclasses, not the contract:
orders name a `coin` instead of an outcome token, prices are USD instead
of 0–1 probabilities, and fills carry `closed_pnl`/`fee` because the
exchange reports realised PnL per fill.

Hyperliquid additionally has a Rust hot path (api/src/live_engine.rs)
that mirrors leader fills server-side. A strat here therefore has TWO
run surfaces:

  * the canonical tick loop (`tick()`), for Python-side engines,
    backtests and tests — identical to polymarket's; and
  * the Rust bridge (`build_config`/`start`/`stop`/`status`), which
    compiles the same selection + risk knobs into the config the live
    engine consumes. `pick_leaders(hl)` is the one required hook: it is
    what both surfaces derive the watchlist from.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════
# Canonical dataclasses — field-for-field the polymarket shapes, with
# perp-market substitutions (coin / USD price / closed_pnl).
# ═══════════════════════════════════════════════════════════════════════


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    """A single intent to trade. The Strat emits these from `signal()`."""
    coin: str               # perp coin, e.g. "BTC"
    side: OrderSide
    size: float             # base units (NOT dollars)
    price: float            # limit price in USD, slippage-padded
    order_type: str = "GTC"  # "GTC" or "IOC"
    reduce_only: bool = False
    # Provenance: which upstream fill triggered this, for log correlation.
    source_trader: Optional[str] = None
    source_trade_id: Optional[str] = None
    # Free-form tag for strat-specific routing (e.g. "rebalance", "stop-loss").
    tag: Optional[str] = None


@dataclass
class TraderTrade:
    """A single fill observed on an upstream trader the strat watches."""
    id: str
    trader: str
    timestamp: int          # ms epoch
    coin: str
    side: OrderSide
    size: float             # base units the upstream trader bought/sold
    price: float            # USD fill price
    closed_pnl: float = 0.0  # realised PnL the exchange reported on this fill
    fee: float = 0.0
    dir: str = ""           # HL fill direction label, e.g. "Open Long"


@dataclass
class SyncResult:
    """Snapshot of upstream state. Returned by `sync()`, consumed by `signal()`."""
    timestamp: int                          # when this sync was taken (ms)
    trader_trades: List[TraderTrade]        # new fills since last sync per watched trader
    wallet_usdc: float                      # available margin (perps are USDC-margined)
    open_positions: Dict[str, float]        # coin → signed size held by the strat
    # Anything the strat wants to thread through to signal()/state().
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """One result per Order returned from `execute()`."""
    order: Order
    success: bool
    order_id: Optional[str] = None
    error: Optional[str] = None
    filled_size: float = 0.0
    filled_price: float = 0.0


@dataclass
class TickResult:
    """Output of one full `tick()` cycle. The engine logs this verbatim."""
    timestamp: int
    sync: SyncResult
    orders: List[Order]
    results: List[ExecutionResult]
    skipped: List[Tuple[Order, str]]   # (intended_order, skip_reason)


@dataclass
class BacktestResult:
    """Output of `backtest()`. Powers strat perf views in the UI."""
    pnl_curve: List[Tuple[int, float]]    # [(timestamp_ms, running_pnl_usd), ...]
    trades_simulated: int                  # count after the strat's filtering
    fees_total: float
    gas_total: float                       # venue costs beyond fees (funding, on perps)
    final_pnl: float
    roi_pct: float                         # final_pnl / capital × 100
    notes: List[str] = field(default_factory=list)


@dataclass
class StratConfig:
    """
    Everything a strat needs at construction. Engine passes this in.

    The four `fetch_*`/`place_order` callables are the engine-supplied I/O
    surface — the strat never opens HTTP sockets directly. This keeps strats
    testable (pass in mocks) and lets the engine swap live vs. backtest data
    sources transparently.
    """
    name: str
    capital: float                          # USD allocated to this strat
    watchlist: List[Dict[str, Any]]         # [{address, weight}, ...]
    scan_minutes: float = 1.0               # how often the live engine ticks

    # Per-trade risk constraints (USD notional)
    min_order_size: float = 10.0            # skip below this
    max_order_size: float = 0.0             # clamp above this; 0 = uncapped
    max_slippage_bps: int = 100

    # Engine-provided I/O (strat code uses these, NOT raw requests).
    fetch_trader_trades: Optional[Callable[[str, int], List[TraderTrade]]] = None
    fetch_wallet_usdc: Optional[Callable[[], float]] = None
    fetch_open_positions: Optional[Callable[[], Dict[str, float]]] = None
    place_order: Optional[Callable[[Order], ExecutionResult]] = None

    # Free-form, persisted alongside the strat. Subclasses interpret as needed.
    params: Dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# Hyperliquid-specific shapes — the bridge to the Rust live engine.
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class Leader:
    """One trader to mirror. `weight` multiplies on top of the engine's
    `size_pct`, so effective mirror size is
    `leader_fill_size × (size_pct / 100) × weight`."""
    address: str
    weight: float = 1.0
    enabled: bool = True

    def to_engine(self) -> Dict[str, Any]:
        return {"address": self.address.lower(), "weight": self.weight, "enabled": self.enabled}


@dataclass
class StratParams:
    """Risk + execution knobs every strat exposes. Mirrors the EngineConfig
    fields the Rust live engine consumes — see api/src/live_engine.rs."""
    size_pct: float = 10.0              # mirror leader.size × this %
    max_per_trade_usd: float = 0.0      # 0 = unlimited
    min_order_size_usd: float = 10.0    # skip dust mirrors
    max_slippage_bps: int = 100         # one-sided slippage padding
    interval_ms: int = 15_000           # engine cycle period
    coins_allow: List[str] = field(default_factory=list)
    coins_deny: List[str] = field(default_factory=list)
    vault_address: Optional[str] = None # route orders through a vault if set
    capital: float = 0.0                # informational; engine sizes %-based


class Strat(ABC):
    """Abstract base for hyperliquid copy strategies.

    Subclasses MUST set `name` + `description` and implement
    `pick_leaders`. The canonical surface (`sync`/`signal`/`execute`/
    `tick`/`backtest`) has copy-trade defaults driven by the watchlist,
    so a selection-only strat is complete with just `pick_leaders`.
    Override `signal()` (pure!) for logic that isn't mirroring."""

    #: Identifier used by the registry / CLI.
    name: str = "abstract"

    #: One-line description shown by `list_strats()`.
    description: str = ""

    def __init__(self, **params: Any) -> None:
        # An engine may hand us a ready StratConfig; interactive use hands
        # loose kwargs instead. Either way both views (risk / config) exist
        # and agree on the shared knobs.
        config: Optional[StratConfig] = params.pop("config", None)

        # Split kwargs: risk knobs go to StratParams, the rest stay in
        # _params for the subclass (e.g. n, days, addresses).
        known = {f for f in StratParams.__dataclass_fields__}
        risk_kwargs = {k: params.pop(k) for k in list(params) if k in known}
        if config is not None:
            risk_kwargs.setdefault("min_order_size_usd", config.min_order_size)
            risk_kwargs.setdefault("max_per_trade_usd", config.max_order_size)
            risk_kwargs.setdefault("max_slippage_bps", config.max_slippage_bps)
            risk_kwargs.setdefault("capital", config.capital)
            risk_kwargs.setdefault("interval_ms", int(config.scan_minutes * 60_000))
        self.risk = StratParams(**risk_kwargs)
        self._params = params
        if config is not None:
            config.params = {**config.params, **self._params}
            self.config = config
        else:
            self.config = StratConfig(
                name=self.name,
                capital=self.risk.capital,
                watchlist=[],
                scan_minutes=self.risk.interval_ms / 60_000,
                min_order_size=self.risk.min_order_size_usd,
                max_order_size=self.risk.max_per_trade_usd,
                max_slippage_bps=self.risk.max_slippage_bps,
                params=self._params,
            )

        # Tracks fill ids the strat has already acted on — prevents
        # double-firing when sync() re-fetches an overlapping window.
        self._handled_trade_ids: set = set()
        # In-memory snapshot of strat-side positions, updated by execute().
        self._positions: Dict[str, float] = {}

    # ── Required ──

    @abstractmethod
    def pick_leaders(self, hl) -> List[Leader]:
        """Return the list of leaders the strat wants to mirror. `hl` is
        the Hyperliquid mod instance."""
        raise NotImplementedError

    def resolve_watchlist(self, hl) -> List[Dict[str, Any]]:
        """Run `pick_leaders` and store the result on `config.watchlist`
        (canonical `{address, weight}` entries) — the bridge from the
        selection hook to the canonical tick surface."""
        leaders = [l for l in self.pick_leaders(hl) if l.enabled]
        self.config.watchlist = [
            {"address": l.address.lower(), "weight": l.weight} for l in leaders
        ]
        return self.config.watchlist

    # ── Canonical lifecycle ──

    def setup(self) -> None:
        """Called once when the engine mounts the strat. Override to prime
        state (load positions, warm caches). Default: pull current positions."""
        if self.config.fetch_open_positions:
            self._positions = self.config.fetch_open_positions()

    def teardown(self) -> None:
        """Called once on stop. Override to persist state, flatten positions, etc."""
        return None

    # ── Canonical per-tick surface ──

    def sync(self) -> SyncResult:
        """Pull latest data. Idempotent. Default reads each watched trader's
        fills since the last tick. Subclasses can fold in mids or
        orderbook depth via `extras`."""
        cutoff = self._last_sync_ts()
        trades: List[TraderTrade] = []
        if self.config.fetch_trader_trades:
            for entry in self.config.watchlist:
                trades.extend(self.config.fetch_trader_trades(entry["address"], cutoff))
        usdc = self.config.fetch_wallet_usdc() if self.config.fetch_wallet_usdc else 0.0
        positions = (self.config.fetch_open_positions()
                     if self.config.fetch_open_positions else dict(self._positions))
        return SyncResult(timestamp=int(time.time() * 1000), trader_trades=trades,
                          wallet_usdc=usdc, open_positions=positions)

    def signal(self, sync: SyncResult) -> List[Order]:
        """Pure function — given a sync snapshot, what orders should fire?
        MUST NOT do I/O. Determinism here is what makes backtest credible.

        Default is the copy-trade mirror: each unhandled leader fill becomes
        an order sized `leader_size × size_pct% × leader_weight`, run through
        the coin allow/deny lists and USD notional clamps, with the limit
        price padded `max_slippage_bps` in the trade's direction."""
        weights = {e["address"].lower(): float(e.get("weight", 1.0))
                   for e in self.config.watchlist}
        allow = {c.upper() for c in self.risk.coins_allow}
        deny = {c.upper() for c in self.risk.coins_deny}
        pad = self.risk.max_slippage_bps / 10_000.0

        orders: List[Order] = []
        for t in sorted(sync.trader_trades, key=lambda t: t.timestamp):
            if t.id in self._handled_trade_ids:
                continue
            coin = t.coin.upper()
            if allow and coin not in allow:
                continue
            if coin in deny:
                continue
            w = weights.get(t.trader.lower(), 0.0)
            if w <= 0:
                continue
            size = t.size * (self.risk.size_pct / 100.0) * w
            if t.price <= 0 or size <= 0:
                continue
            cap = self.config.max_order_size
            if cap > 0 and size * t.price > cap:
                size = cap / t.price
            if size * t.price < self.config.min_order_size:
                continue
            price = t.price * (1 + pad) if t.side == OrderSide.BUY else t.price * (1 - pad)
            orders.append(Order(
                coin=t.coin, side=t.side, size=size, price=price,
                source_trader=t.trader, source_trade_id=t.id, tag="mirror",
            ))
        return orders

    def execute(self, orders: List[Order]) -> List[ExecutionResult]:
        """Submit orders to the venue. Default: call `place_order` for each,
        accumulate results. Override to batch, route through a vault, or
        wrap in retries."""
        results: List[ExecutionResult] = []
        if not self.config.place_order:
            return [ExecutionResult(order=o, success=False,
                                    error="no place_order configured") for o in orders]
        for o in orders:
            r = self.config.place_order(o)
            results.append(r)
            if r.success:
                self._handled_trade_ids.add(o.source_trade_id or "")
                # Optimistic position update — engine reconciles on next sync.
                delta = r.filled_size if o.side == OrderSide.BUY else -r.filled_size
                self._positions[o.coin] = self._positions.get(o.coin, 0.0) + delta
        return results

    def tick(self) -> TickResult:
        """One full cycle. Most engines call only this in a loop."""
        sync = self.sync()
        orders = self.signal(sync)
        results = self.execute(orders)
        skipped = [(r.order, r.error or "") for r in results if not r.success]
        return TickResult(timestamp=sync.timestamp, sync=sync,
                          orders=orders, results=results, skipped=skipped)

    # ── Backtest ──

    def backtest(self, history: List[TraderTrade]) -> BacktestResult:
        """Replay `signal()` over historical fills and return a BacktestResult.
        Must NOT touch the live wallet — use the historical sequence only.

        Default model for mirrors: our realised PnL on a leader fill is the
        exchange-reported `closed_pnl` scaled by our size ratio, and our fee
        scales the same way. Honest about what it can't know: unrealised PnL
        on positions the window never closed isn't invented."""
        snapshot = SyncResult(
            timestamp=history[-1].timestamp if history else int(time.time() * 1000),
            trader_trades=list(history), wallet_usdc=0.0, open_positions={},
        )
        by_id = {t.id: t for t in history}
        curve: List[Tuple[int, float]] = []
        running = 0.0
        fees = 0.0
        n = 0
        for o in self.signal(snapshot):
            src = by_id.get(o.source_trade_id or "")
            if src is None or src.size <= 0:
                continue
            ratio = o.size / src.size
            running += src.closed_pnl * ratio
            fees += src.fee * ratio
            n += 1
            curve.append((src.timestamp, running))
        capital = self.config.capital or self.risk.capital
        final = running - fees
        notes: List[str] = []
        if not history:
            notes.append("no history supplied — empty replay")
        elif n == 0:
            notes.append("no fills survived the strat's filters")
        if fees > abs(running) and n:
            notes.append(f"fees (${fees:,.0f}) exceed gross pnl — mirror is over-trading")
        return BacktestResult(
            pnl_curve=curve, trades_simulated=n, fees_total=fees, gas_total=0.0,
            final_pnl=final,
            roi_pct=(final / capital * 100.0) if capital > 0 else 0.0,
            notes=notes,
        )

    # ── Rust live-engine bridge (hyperliquid-specific) ──

    def build_config(self, hl, eoa: str) -> Dict[str, Any]:
        """Compose the live-engine config dict for api/src/live_engine.rs."""
        leaders = self.pick_leaders(hl)
        if not leaders:
            raise RuntimeError(f"strat {self.name!r} returned no leaders")
        return {
            "eoa": eoa.lower(),
            "strategy_id": self.name,
            "traders": [l.to_engine() for l in leaders],
            "interval_ms": self.risk.interval_ms,
            "size_pct": self.risk.size_pct,
            "max_per_trade_usd": self.risk.max_per_trade_usd,
            "min_order_size_usd": self.risk.min_order_size_usd,
            "max_slippage_bps": self.risk.max_slippage_bps,
            "coins_allow": self.risk.coins_allow,
            "coins_deny": self.risk.coins_deny,
            "vault_address": self.risk.vault_address,
            "capital": self.risk.capital,
        }

    def start(self, hl, eoa: str) -> Dict[str, Any]:
        """Start the strat on the venue-side live engine. Idempotent —
        replaces any existing session for `eoa`."""
        cfg = self.build_config(hl, eoa)
        return hl.live_start(**{k: v for k, v in cfg.items() if v is not None})

    def stop(self, hl, eoa: str) -> Dict[str, Any]:
        return hl.live_stop(eoa)

    def status(self, hl, eoa: str) -> Dict[str, Any]:
        return hl.live_status(eoa)

    # ── Introspection ──

    def state(self) -> Dict[str, Any]:
        """Snapshot of internal state for the UI / persistence."""
        return {
            "name": self.name,
            "capital": self.config.capital or self.risk.capital,
            "positions": dict(self._positions),
            "handled_trade_count": len(self._handled_trade_ids),
            "watchlist": [w["address"] for w in self.config.watchlist],
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params": dict(self._params),
            "risk": self.risk.__dict__,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self._params}>"

    # ── Internal helpers ──

    def _last_sync_ts(self) -> int:
        """ms epoch cutoff for `sync()`'s fill fetch — one scan window back."""
        return int(time.time() * 1000) - int(self.config.scan_minutes * 60_000)
