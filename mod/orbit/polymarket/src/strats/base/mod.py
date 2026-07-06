"""
Canonical Strat interface — the contract every Polymarket strategy
implements, analogous to ERC-20 for tokens. The live engine and the
backtest engine both speak to a strat *only* through these methods, so
custom strategies plug in without touching engine code.

Method contract
---------------
    setup()        one-time init when the strat is mounted (load state,
                   open positions, prime caches). MAY raise — engine halts.
    sync()         pull latest data from upstream (trades, prices, balance).
                   READ-ONLY, IDEMPOTENT. Returns a SyncResult snapshot.
    signal(sync)   pure function — given a SyncResult, decide which Orders
                   to place. No side-effects, no I/O. Deterministic for a
                   given sync snapshot + state.
    execute(os)    submit orders to the venue. Side-effecting. Returns one
                   ExecutionResult per Order. Failures bubble up but never
                   crash the engine — they get logged.
    tick()         convenience: sync → signal → execute. Most engines call
                   only this, in a loop. Subclasses RARELY override.
    backtest(h)    replay logic over historical trades. Pure-ish — must not
                   touch the live wallet. Returns a BacktestResult.
    teardown()     cleanup on stop (close positions, persist state).
    state()        snapshot of internal state for the UI / persistence.

Live-engine cycle (every `scan_minutes` minutes):
    1. engine calls strat.tick()
    2. tick → sync → signal → execute → return TickResult
    3. engine logs the result, schedules next cycle

Backtest cycle (one-shot over a window):
    1. engine collects historical trades for the watchlist
    2. engine calls strat.backtest(history)
    3. strat replays signal() over the trades and returns curve + stats
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    """A single intent to trade. The Strat emits these from `signal()`."""
    token_id: str           # Polymarket CLOB outcome token id (binary YES/NO)
    side: OrderSide
    size: float             # shares (NOT dollars). Convert from notional / price.
    price: float            # limit price 0.0 – 1.0
    order_type: str = "GTC"  # "GTC" (good-til-cancel) or "FOK" (fill-or-kill)
    # Provenance: which upstream trade triggered this, for log correlation.
    source_trader: Optional[str] = None
    source_trade_id: Optional[str] = None
    # Free-form tag for strat-specific routing (e.g. "rebalance", "stop-loss").
    tag: Optional[str] = None


@dataclass
class TraderTrade:
    """A single trade observed on an upstream trader the strat watches."""
    id: str
    trader: str
    timestamp: int          # ms epoch
    market: str             # market slug or title
    condition_id: str       # Polymarket conditionId (CTF asset id root)
    token_id: str           # outcome token (YES or NO branch)
    side: OrderSide
    size: float             # shares the upstream trader bought/sold
    price: float            # price they paid (0.0–1.0)
    outcome: Optional[str] = None  # "Yes" / "No" label if known


@dataclass
class SyncResult:
    """Snapshot of upstream state. Returned by `sync()`, consumed by `signal()`."""
    timestamp: int                          # when this sync was taken (ms)
    trader_trades: list[TraderTrade]        # new trades since last sync per watched trader
    wallet_usdc: float                      # current available USDC in proxy
    open_positions: dict[str, float]        # token_id → size held by the strat
    # Anything the strat wants to thread through to signal()/state(). The
    # base class doesn't read it — subclasses can stash mid-prices,
    # orderbook depth, sentiment scores, etc.
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """One result per Order returned from `execute()`."""
    order: Order
    success: bool
    order_id: Optional[str] = None     # CLOB-assigned id on success
    error: Optional[str] = None        # human-readable failure reason
    filled_size: float = 0.0           # may differ from order.size on partial fills
    filled_price: float = 0.0


@dataclass
class TickResult:
    """Output of one full `tick()` cycle. The engine logs this verbatim."""
    timestamp: int
    sync: SyncResult
    orders: list[Order]
    results: list[ExecutionResult]
    skipped: list[tuple[Order, str]]   # (intended_order, skip_reason)


@dataclass
class BacktestResult:
    """Output of `backtest()`. Powers the BACKTEST tab in the UI."""
    pnl_curve: list[tuple[int, float]]    # [(timestamp_ms, running_pnl_usd), ...]
    trades_simulated: int                  # count after the strat's filtering
    fees_total: float
    gas_total: float
    final_pnl: float                       # mark-to-market at window end
    roi_pct: float                         # final_pnl / capital × 100
    notes: list[str] = field(default_factory=list)  # warnings ("8045 trades, fees > pnl")


@dataclass
class StratConfig:
    """
    Everything a strat needs at construction. Engine passes this in.

    `data_fns` is the engine-supplied I/O surface — the strat never opens
    HTTP sockets directly. This keeps strats testable (pass in mocks) and
    lets the engine swap live vs. backtest data sources transparently.
    """
    name: str
    capital: float                          # USD allocated to this strat
    watchlist: list[dict[str, Any]]         # [{address, weight}, ...]
    scan_minutes: int = 1                   # how often the live engine ticks

    # Per-trade risk constraints
    min_order_size: float = 1.0             # USD — skip below this
    max_order_size: float = 100.0           # USD — clamp above this
    max_slippage_bps: int = 300

    # Engine-provided I/O (strat code uses these, NOT raw requests). Mock
    # them in tests and pass historical data in for backtest.
    fetch_trader_trades: Optional[Callable[[str, int], list[TraderTrade]]] = None
    fetch_wallet_usdc: Optional[Callable[[], float]] = None
    fetch_open_positions: Optional[Callable[[], dict[str, float]]] = None
    place_order: Optional[Callable[[Order], ExecutionResult]] = None

    # Free-form, persisted alongside the strat. Subclasses interpret as needed.
    params: dict[str, Any] = field(default_factory=dict)


class Strat(ABC):
    """
    Abstract base. Subclass + implement at minimum `signal()` and `backtest()`.
    `setup`/`sync`/`execute`/`teardown`/`state` have reasonable defaults that
    most copy-style strats can lean on.
    """

    def __init__(self, config: StratConfig) -> None:
        self.config = config
        # Tracks trade ids the strat has already acted on — prevents
        # double-firing when sync() re-fetches an overlapping window.
        self._handled_trade_ids: set[str] = set()
        # In-memory snapshot of strat-side positions, updated by execute().
        self._positions: dict[str, float] = {}

    # ── Lifecycle ──────────────────────────────────────────────────

    def setup(self) -> None:
        """Called once when the engine mounts the strat. Override to prime
        state (load positions, warm caches). Default: pull current positions."""
        if self.config.fetch_open_positions:
            self._positions = self.config.fetch_open_positions()

    def teardown(self) -> None:
        """Called once on stop. Override to persist state, cancel open orders, etc."""
        return None

    # ── Per-tick canonical surface ─────────────────────────────────

    def sync(self) -> SyncResult:
        """Pull latest data. Idempotent. Default reads each watched trader's
        trades since the last tick. Subclasses can fold in mid-prices or
        orderbook depth via `extras`."""
        cutoff = self._last_sync_ts()
        trades: list[TraderTrade] = []
        if self.config.fetch_trader_trades:
            for entry in self.config.watchlist:
                addr = entry["address"]
                trades.extend(self.config.fetch_trader_trades(addr, cutoff))
        usdc = (self.config.fetch_wallet_usdc() if self.config.fetch_wallet_usdc else 0.0)
        positions = (self.config.fetch_open_positions() if self.config.fetch_open_positions else self._positions)
        import time
        now_ms = int(time.time() * 1000)
        return SyncResult(timestamp=now_ms, trader_trades=trades,
                          wallet_usdc=usdc, open_positions=positions)

    @abstractmethod
    def signal(self, sync: SyncResult) -> list[Order]:
        """Pure function — given a sync snapshot, what orders should fire?
        MUST NOT do I/O. Determinism here is what makes backtest credible."""
        raise NotImplementedError

    def execute(self, orders: list[Order]) -> list[ExecutionResult]:
        """Submit orders to the venue. Default: call `place_order` for each,
        skip ones below `min_order_size`, accumulate results. Override to
        batch, route by venue, or wrap in retries."""
        results: list[ExecutionResult] = []
        if not self.config.place_order:
            for o in orders:
                results.append(ExecutionResult(order=o, success=False,
                                               error="no place_order configured"))
            return results
        for o in orders:
            notional = o.size * o.price
            if notional < self.config.min_order_size:
                results.append(ExecutionResult(order=o, success=False,
                                               error=f"below min_order_size ({notional:.2f} < {self.config.min_order_size})"))
                continue
            r = self.config.place_order(o)
            results.append(r)
            if r.success:
                self._handled_trade_ids.add(o.source_trade_id or "")
                # Optimistic position update — engine reconciles on next sync.
                delta = r.filled_size if o.side == OrderSide.BUY else -r.filled_size
                self._positions[o.token_id] = self._positions.get(o.token_id, 0.0) + delta
        return results

    def tick(self) -> TickResult:
        """One full cycle. Most engines call only this in a loop."""
        sync = self.sync()
        orders = self.signal(sync)
        results = self.execute(orders)
        skipped = [(r.order, r.error or "") for r in results if not r.success]
        return TickResult(timestamp=sync.timestamp, sync=sync,
                          orders=orders, results=results, skipped=skipped)

    # ── Backtest ────────────────────────────────────────────────────

    @abstractmethod
    def backtest(self, history: list[TraderTrade]) -> BacktestResult:
        """Replay `signal()` over historical trades and return a BacktestResult.
        Must NOT touch the live wallet — use the historical sequence only.
        The chart + feed in the UI render directly from this."""
        raise NotImplementedError

    # ── Introspection ──────────────────────────────────────────────

    def state(self) -> dict[str, Any]:
        """Snapshot of internal state for the UI / persistence."""
        return {
            "name": self.config.name,
            "capital": self.config.capital,
            "positions": dict(self._positions),
            "handled_trade_count": len(self._handled_trade_ids),
            "watchlist": [w["address"] for w in self.config.watchlist],
        }

    # ── Internal helpers ───────────────────────────────────────────

    def _last_sync_ts(self) -> int:
        """ms epoch of the last sync, or 0 if first call. Default: now - scan_minutes."""
        import time
        now_ms = int(time.time() * 1000)
        return now_ms - (self.config.scan_minutes * 60_000)
