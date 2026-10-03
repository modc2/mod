"""
strat protocol v1 — the unified, venue-neutral Strat contract.

One class contract for a trading strategy across every venue this node can
reach: raydium (Solana), uniswap (Ethereum/Base), hyperliquid, bittensor
(dTAO pools) and polymarket. It is the venue-neutral superset of the
canonical per-venue schema that already ships in polymarket/src/strats/base,
hyperliquid/src/strats/base.py and copytensor/src/strats/base.py — same
method contract, generalized instruments.

Method contract (identical to the per-venue canon):
    setup()        one-time init when the strat is mounted. MAY raise.
    sync()         pull latest upstream data. READ-ONLY, IDEMPOTENT.
    signal(sync)   pure function SyncResult -> [Order]. No I/O.
    execute(os)    submit orders to venues. Side-effecting.
    tick()         sync -> signal -> execute. Engines call only this.
    backtest(h)    replay signal() over history. Never touches a wallet.
    teardown()     cleanup on stop.
    state()        snapshot for UI / persistence.

Instruments are addressed as (venue, symbol):
    raydium      symbol = "<input_mint>/<output_mint>" or "SOL/USDC"
    uniswap      symbol = "<sell>/<buy>" token names or 0x addresses
    hyperliquid  symbol = coin, e.g. "BTC"
    bittensor    symbol = "SN<netuid>" — the subnet alpha pool
    polymarket   symbol = CLOB outcome token id

A strat is a MOD: a directory with a config.json declaring a `strat` block
and a strat.py defining exactly one subclass of Strat. The strat module's
registry (mod.py) discovers, verifies, forks and boards them.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

PROTOCOL_VERSION = 1

VENUES = ["raydium", "uniswap", "hyperliquid", "bittensor", "polymarket"]

# The canonical method surface. Per-venue strat layers and the parity test
# in tests/test_strat.py both pin this list.
METHODS = ["setup", "sync", "signal", "execute", "tick",
           "backtest", "teardown", "state"]


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    """A single intent to trade. The Strat emits these from `signal()`."""
    venue: str              # one of VENUES
    symbol: str             # venue-scoped instrument (see module docstring)
    side: OrderSide
    size: float             # base units of the instrument (NOT dollars)
    price: float            # limit price in the venue's quote currency
    order_type: str = "GTC"
    # Provenance: which upstream trade triggered this, for log correlation.
    source_trader: Optional[str] = None
    source_trade_id: Optional[str] = None
    tag: Optional[str] = None


@dataclass
class VenueTrade:
    """One trade observed on an upstream trader the strat watches."""
    id: str
    venue: str
    trader: str
    timestamp: int          # ms epoch
    symbol: str
    side: OrderSide
    size: float
    price: float
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncResult:
    """Snapshot of upstream state. Returned by `sync()`, read by `signal()`."""
    timestamp: int                      # when this sync was taken (ms)
    trades: list[VenueTrade]            # new upstream trades since last sync
    cash: float                         # available quote-currency balance
    currency: str = "USD"
    open_positions: dict[str, float] = field(default_factory=dict)  # "venue:symbol" -> size
    extras: dict[str, Any] = field(default_factory=dict)


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
    """Output of one full `tick()` cycle."""
    timestamp: int
    sync: SyncResult
    orders: list[Order]
    results: list[ExecutionResult]
    skipped: list[tuple[Order, str]]


@dataclass
class BacktestResult:
    """Output of `backtest()`. Powers the marketplace board."""
    pnl_curve: list[tuple[int, float]]     # [(timestamp_ms, running_pnl), ...]
    trades_simulated: int
    fees_total: float
    gas_total: float
    final_pnl: float
    roi_pct: float
    notes: list[str] = field(default_factory=list)


@dataclass
class StratConfig:
    """
    Everything a strat needs at construction. The engine passes this in.

    The fetch_*/place_order callables are the engine-supplied I/O surface —
    a strat never opens sockets itself. mod.py wires them to the venue
    adapters for live runs and to recorded history for backtests; tests
    pass in fakes. This is what keeps every strat runnable offline.
    """
    name: str
    capital: float                          # quote currency allocated
    venues: list[str] = field(default_factory=lambda: list(VENUES))
    watchlist: list[dict[str, Any]] = field(default_factory=list)  # [{venue, address, weight}]
    scan_minutes: int = 5

    # Per-trade risk constraints (quote currency)
    min_order_size: float = 1.0
    max_order_size: float = 100.0
    max_slippage_bps: int = 300

    # Engine-provided I/O
    fetch_trades: Optional[Callable[[str, str, int], list[VenueTrade]]] = None  # (venue, trader, since_ms)
    fetch_cash: Optional[Callable[[], float]] = None
    fetch_positions: Optional[Callable[[], dict[str, float]]] = None
    place_order: Optional[Callable[[Order], ExecutionResult]] = None
    quote: Optional[Callable[[str, str, OrderSide, float], float]] = None  # (venue, symbol, side, size) -> price

    # Free-form, persisted alongside the strat.
    params: dict[str, Any] = field(default_factory=dict)


class Strat(ABC):
    """
    Abstract base. A strat mod subclasses this and implements `signal()`;
    everything else has defaults a copy-style strat can lean on, including
    a mark-to-market `backtest()`.
    """

    # Strat mods may narrow this to the venues their logic understands.
    venues: list[str] = list(VENUES)

    def __init__(self, config: StratConfig) -> None:
        self.config = config
        self._handled_trade_ids: set[str] = set()
        self._positions: dict[str, float] = {}

    # ── Lifecycle ──────────────────────────────────────────────────

    def setup(self) -> None:
        if self.config.fetch_positions:
            self._positions = self.config.fetch_positions()

    def teardown(self) -> None:
        return None

    # ── Per-tick canonical surface ─────────────────────────────────

    def sync(self) -> SyncResult:
        cutoff = self._last_sync_ts()
        trades: list[VenueTrade] = []
        if self.config.fetch_trades:
            for entry in self.config.watchlist:
                venue = entry.get("venue", "")
                if venue and venue not in self.config.venues:
                    continue
                trades.extend(self.config.fetch_trades(venue, entry["address"], cutoff))
        cash = self.config.fetch_cash() if self.config.fetch_cash else 0.0
        positions = (self.config.fetch_positions() if self.config.fetch_positions
                     else self._positions)
        return SyncResult(timestamp=_now_ms(), trades=trades,
                          cash=cash, open_positions=positions)

    @abstractmethod
    def signal(self, sync: SyncResult) -> list[Order]:
        """Pure function — no I/O. Determinism is what makes backtests honest."""
        raise NotImplementedError

    def execute(self, orders: list[Order]) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        if not self.config.place_order:
            return [ExecutionResult(order=o, success=False,
                                    error="no place_order configured") for o in orders]
        for o in orders:
            notional = o.size * o.price
            if notional < self.config.min_order_size:
                results.append(ExecutionResult(
                    order=o, success=False,
                    error=f"below min_order_size ({notional:.2f} < {self.config.min_order_size})"))
                continue
            r = self.config.place_order(o)
            results.append(r)
            if r.success:
                self._handled_trade_ids.add(o.source_trade_id or "")
                delta = r.filled_size if o.side == OrderSide.BUY else -r.filled_size
                key = f"{o.venue}:{o.symbol}"
                self._positions[key] = self._positions.get(key, 0.0) + delta
        return results

    def tick(self) -> TickResult:
        sync = self.sync()
        orders = self.signal(sync)
        results = self.execute(orders)
        skipped = [(r.order, r.error or "") for r in results if not r.success]
        return TickResult(timestamp=sync.timestamp, sync=sync,
                          orders=orders, results=results, skipped=skipped)

    # ── Backtest ────────────────────────────────────────────────────

    def backtest(self, history: list[VenueTrade]) -> BacktestResult:
        """
        Default mark-to-market replay (same model as copytensor's base):
        feed history chronologically through `signal()`, fill every emitted
        order at its price, and re-mark the book at the LAST price any trade
        in the window revealed per instrument. Pool impact and fees are not
        modelled — notes say so.
        """
        cash = self.config.capital
        book: dict[str, float] = {}
        last_price: dict[str, float] = {}
        curve: list[tuple[int, float]] = []
        simulated = 0

        for t in sorted(history, key=lambda x: x.timestamp):
            key = f"{t.venue}:{t.symbol}"
            last_price[key] = t.price
            snap = SyncResult(timestamp=t.timestamp, trades=[t], cash=cash,
                              open_positions=dict(book))
            for o in self.signal(snap):
                notional = o.size * o.price
                if notional < self.config.min_order_size:
                    continue
                if o.side == OrderSide.BUY:
                    if notional > cash:
                        continue
                    cash -= notional
                    book[f"{o.venue}:{o.symbol}"] = book.get(f"{o.venue}:{o.symbol}", 0.0) + o.size
                else:
                    held = book.get(f"{o.venue}:{o.symbol}", 0.0)
                    size = min(o.size, held)
                    if size <= 0:
                        continue
                    cash += size * o.price
                    book[f"{o.venue}:{o.symbol}"] = held - size
                last_price[f"{o.venue}:{o.symbol}"] = o.price
                simulated += 1
            marked = cash + sum(sz * last_price.get(k, 0.0) for k, sz in book.items())
            curve.append((t.timestamp, marked - self.config.capital))

        final = curve[-1][1] if curve else 0.0
        roi = (final / self.config.capital * 100.0) if self.config.capital else 0.0
        return BacktestResult(
            pnl_curve=curve, trades_simulated=simulated,
            fees_total=0.0, gas_total=0.0, final_pnl=final, roi_pct=roi,
            notes=["mark-to-market; pool impact and fees not modelled"])

    # ── Introspection ──────────────────────────────────────────────

    def state(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "capital": self.config.capital,
            "venues": list(self.config.venues),
            "positions": dict(self._positions),
            "handled_trade_count": len(self._handled_trade_ids),
            "watchlist": [w.get("address") for w in self.config.watchlist],
        }

    # ── Internal helpers ───────────────────────────────────────────

    def _last_sync_ts(self) -> int:
        return _now_ms() - (self.config.scan_minutes * 60_000)


def _now_ms() -> int:
    return int(time.time() * 1000)


def schema() -> dict[str, Any]:
    """Machine-readable description of the contract, served by the mod."""
    import dataclasses
    out: dict[str, Any] = {
        "protocol": "strat", "version": PROTOCOL_VERSION,
        "venues": list(VENUES), "methods": list(METHODS), "types": {},
    }
    for cls in (Order, VenueTrade, SyncResult, ExecutionResult,
                TickResult, BacktestResult, StratConfig):
        out["types"][cls.__name__] = [f.name for f in dataclasses.fields(cls)]
    return out
