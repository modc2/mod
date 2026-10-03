"""
Base abstractions for copytensor strategies.

This is the SAME canonical Strat schema the polymarket module defines
(polymarket/src/strats/base/mod.py) and the hyperliquid module ports
(hyperliquid/src/strats/base.py) — like ERC-20 for tokens, the interface
standardizes trading logic so an engine can drive any strat through the
same methods:

    setup()        one-time init when the strat is mounted.
    sync()         pull latest data from upstream. READ-ONLY, IDEMPOTENT.
    signal(sync)   pure function — given a SyncResult, decide which Orders
                   to place. No side-effects, no I/O.
    execute(os)    submit orders to the venue. Side-effecting.
    tick()         convenience: sync → signal → execute.
    backtest(h)    replay logic over historical flows. Must not touch the
                   live wallet. Returns a BacktestResult.
    teardown()     cleanup on stop.
    state()        snapshot of internal state for the UI / persistence.

The dTAO-venue differences live in the dataclasses, not the contract:
orders name a `netuid` instead of an outcome token or perp coin, sizes
are alpha units, prices are τ per alpha (the subnet pool price), and the
cash currency is τ — so `wallet_usdc`/`fetch_wallet_usdc` become
`wallet_tao`/`fetch_wallet_tao` (the ONE declared StratConfig rename;
tests/test_strats.py pins parity with polymarket's field set through it).

Copytensor additionally has a server-side hot path: the CopyEngine
(src/engine/copier.py) blends per-trader τ sleeves into one on-chain
book. A strat here therefore has TWO run surfaces:

  * the canonical tick loop (`tick()`), for Python-side engines,
    backtests and tests — identical to polymarket's/hyperliquid's; and
  * the sleeve bridge (`build_copies`/`start`/`stop`/`status`), which
    compiles the same selection + risk knobs into `POST /copy` sleeves
    the live engine consumes. `pick_leaders(ct)` is the one required
    hook: it is what both surfaces derive the watchlist from.

One divergence worth naming: SS58 addresses are case-sensitive base58 —
they are NEVER lowercased anywhere in this layer (hyperliquid lowercases
its 0x addresses; doing that here would corrupt every coldkey).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

# Below this τ notional a "trade" is emission drift, not intent — same
# floor the server backtest uses (src/engine/backtest.py DUST_TAO).
MIN_ORDER_TAO = 0.5


# ═══════════════════════════════════════════════════════════════════════
# Canonical dataclasses — field-for-field the polymarket shapes, with
# dTAO substitutions (netuid / τ-per-alpha price / alpha size).
# ═══════════════════════════════════════════════════════════════════════


class OrderSide(str, Enum):
    BUY = "BUY"      # stake τ into the subnet pool (acquire alpha)
    SELL = "SELL"    # unstake alpha back to τ


@dataclass
class Order:
    """A single intent to trade. The Strat emits these from `signal()`."""
    netuid: int             # subnet id — the dTAO "asset"
    side: OrderSide
    size: float             # alpha units (NOT τ). Convert from notional / price.
    price: float            # τ per alpha, slippage-padded reference price
    order_type: str = "SWAP"  # dTAO trades are pool swaps; no resting orders
    # Provenance: which upstream flow triggered this, for log correlation.
    source_trader: Optional[str] = None
    source_trade_id: Optional[str] = None
    # Free-form tag for strat-specific routing (e.g. "rebalance", "stop-loss").
    tag: Optional[str] = None


@dataclass
class TraderTrade:
    """A single flow observed on an upstream coldkey the strat watches.

    Flows are inferred from per-subnet alpha deltas between snapshots
    (bt's `_diff`), so there is no exchange-assigned id — synthesize one
    (see `flow_to_trade`) and no realised-PnL field exists at this layer."""
    id: str
    trader: str             # coldkey ss58 (case-sensitive — never lowercase)
    timestamp: int          # ms epoch
    netuid: int
    side: OrderSide
    size: float             # alpha units the upstream trader staked/unstaked
    price: float            # τ per alpha at observation
    tao_value: float = 0.0  # |size| × price, as bt reported it
    block: int = 0          # chain block of the snapshot that revealed it


@dataclass
class SyncResult:
    """Snapshot of upstream state. Returned by `sync()`, consumed by `signal()`."""
    timestamp: int                          # when this sync was taken (ms)
    trader_trades: List[TraderTrade]        # new flows since last sync per watched trader
    wallet_tao: float                       # free (unstaked) τ balance
    open_positions: Dict[int, float]        # netuid → alpha held by the strat
    # Anything the strat wants to thread through to signal()/state().
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """One result per Order returned from `execute()`."""
    order: Order
    success: bool
    order_id: Optional[str] = None     # extrinsic hash on success
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
    pnl_curve: List[Tuple[int, float]]    # [(timestamp_ms, running_pnl_tao), ...]
    trades_simulated: int                  # count after the strat's filtering
    fees_total: float                      # τ
    gas_total: float                       # extrinsic fees, when modelled
    final_pnl: float                       # τ, mark-to-market at window end
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

    Field set is the canonical polymarket StratConfig with exactly one
    venue rename: `fetch_wallet_usdc` → `fetch_wallet_tao` (τ is the cash
    currency here). The parity test declares and enforces that mapping.
    """
    name: str
    capital: float                          # τ allocated to this strat
    watchlist: List[Dict[str, Any]]         # [{address, weight}, ...] — address is an ss58
    scan_minutes: float = 5.0               # chain cadence; sleeves poll every 5 min

    # Per-trade risk constraints (τ notional)
    min_order_size: float = MIN_ORDER_TAO   # skip below this
    max_order_size: float = 0.0             # clamp above this; 0 = uncapped
    max_slippage_bps: int = 100

    # Engine-provided I/O (strat code uses these, NOT raw requests).
    fetch_trader_trades: Optional[Callable[[str, int], List[TraderTrade]]] = None
    fetch_wallet_tao: Optional[Callable[[], float]] = None
    fetch_open_positions: Optional[Callable[[], Dict[int, float]]] = None
    place_order: Optional[Callable[[Order], ExecutionResult]] = None

    # Free-form, persisted alongside the strat. Subclasses interpret as needed.
    params: Dict[str, Any] = field(default_factory=dict)


def flow_to_trade(ss58: str, flow: Dict[str, Any], ts_ms: int,
                  block: int = 0) -> TraderTrade:
    """Map one bt flow row ({netuid, side, alpha, price, tao_value}) onto the
    canonical TraderTrade. Flows carry no upstream id, so one is synthesized
    from (trader, ts, netuid, side) — stable across re-fetches of the same
    window, which is what the dedupe in `execute()` needs."""
    side = OrderSide.BUY if str(flow.get("side", "")).lower() == "buy" else OrderSide.SELL
    netuid = int(flow.get("netuid", 0))
    return TraderTrade(
        id=f"{ss58}:{ts_ms}:{netuid}:{side.value}",
        trader=ss58,
        timestamp=ts_ms,
        netuid=netuid,
        side=side,
        size=float(flow.get("alpha", 0.0)),
        price=float(flow.get("price", 0.0)),
        tao_value=float(flow.get("tao_value", 0.0)),
        block=int(block or flow.get("block", 0)),
    )


# ═══════════════════════════════════════════════════════════════════════
# Copytensor-specific shapes — the bridge to the sleeve-based CopyEngine.
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class Leader:
    """One coldkey to mirror. In the canonical tick loop `weight` multiplies
    on top of `size_pct`; on the sleeve bridge the normalized weight decides
    the τ sleeve behind this trader (`capital × weight / Σweights`)."""
    ss58: str
    weight: float = 1.0
    enabled: bool = True

    def to_engine(self) -> Dict[str, Any]:
        # ss58 passes through verbatim — case-sensitive base58.
        return {"ss58": self.ss58, "weight": self.weight, "enabled": self.enabled}


@dataclass
class StratParams:
    """Risk + execution knobs every strat exposes. Mirrors the CopyConfig /
    POST /copy fields the live CopyEngine consumes — see engine/copier.py."""
    size_pct: float = 10.0              # mirror leader.size × this % (tick loop)
    max_per_trade_tao: float = 0.0      # 0 = unlimited
    min_order_size_tao: float = MIN_ORDER_TAO
    max_slippage_bps: int = 100         # one-sided slippage padding
    interval_ms: int = 300_000          # engine cycle period (5 min chain cadence)
    netuids_allow: List[int] = field(default_factory=list)
    netuids_deny: List[int] = field(default_factory=list)
    capital: float = 0.0                # τ; the sleeve bridge REQUIRES this


class Strat(ABC):
    """Abstract base for copytensor copy strategies.

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
        # _params for the subclass (e.g. n, days, ss58s).
        known = {f for f in StratParams.__dataclass_fields__}
        risk_kwargs = {k: params.pop(k) for k in list(params) if k in known}
        if config is not None:
            risk_kwargs.setdefault("min_order_size_tao", config.min_order_size)
            risk_kwargs.setdefault("max_per_trade_tao", config.max_order_size)
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
                min_order_size=self.risk.min_order_size_tao,
                max_order_size=self.risk.max_per_trade_tao,
                max_slippage_bps=self.risk.max_slippage_bps,
                params=self._params,
            )

        # Tracks flow ids the strat has already acted on — prevents
        # double-firing when sync() re-fetches an overlapping window.
        self._handled_trade_ids: set = set()
        # In-memory snapshot of strat-side positions, updated by execute().
        self._positions: Dict[int, float] = {}

    # ── Required ──

    @abstractmethod
    def pick_leaders(self, ct) -> List[Leader]:
        """Return the list of coldkeys the strat wants to mirror. `ct` is
        the copytensor mod instance."""
        raise NotImplementedError

    def resolve_watchlist(self, ct) -> List[Dict[str, Any]]:
        """Run `pick_leaders` and store the result on `config.watchlist`
        (canonical `{address, weight}` entries — `address` holds an ss58) —
        the bridge from the selection hook to the canonical tick surface."""
        leaders = [l for l in self.pick_leaders(ct) if l.enabled]
        self.config.watchlist = [
            {"address": l.ss58, "weight": l.weight} for l in leaders
        ]
        return self.config.watchlist

    # ── Canonical lifecycle ──

    def setup(self) -> None:
        """Called once when the engine mounts the strat. Override to prime
        state (load positions, warm caches). Default: pull current positions."""
        if self.config.fetch_open_positions:
            self._positions = self.config.fetch_open_positions()

    def teardown(self) -> None:
        """Called once on stop. Override to persist state, unwind sleeves, etc."""
        return None

    # ── Canonical per-tick surface ──

    def sync(self) -> SyncResult:
        """Pull latest data. Idempotent. Default reads each watched trader's
        flows since the last tick. Subclasses can fold in pool prices or
        subnet stats via `extras`."""
        cutoff = self._last_sync_ts()
        trades: List[TraderTrade] = []
        if self.config.fetch_trader_trades:
            for entry in self.config.watchlist:
                trades.extend(self.config.fetch_trader_trades(entry["address"], cutoff))
        tao = self.config.fetch_wallet_tao() if self.config.fetch_wallet_tao else 0.0
        positions = (self.config.fetch_open_positions()
                     if self.config.fetch_open_positions else dict(self._positions))
        return SyncResult(timestamp=int(time.time() * 1000), trader_trades=trades,
                          wallet_tao=tao, open_positions=positions)

    def signal(self, sync: SyncResult) -> List[Order]:
        """Pure function — given a sync snapshot, what orders should fire?
        MUST NOT do I/O. Determinism here is what makes backtest credible.

        Default is the copy-trade mirror: each unhandled leader flow becomes
        an order sized `leader_size × size_pct% × leader_weight`, run through
        the netuid allow/deny lists and τ notional clamps, with the reference
        price padded `max_slippage_bps` in the trade's direction."""
        weights = {e["address"]: float(e.get("weight", 1.0))
                   for e in self.config.watchlist}
        allow = {int(u) for u in self.risk.netuids_allow}
        deny = {int(u) for u in self.risk.netuids_deny}
        pad = self.risk.max_slippage_bps / 10_000.0

        orders: List[Order] = []
        for t in sorted(sync.trader_trades, key=lambda t: t.timestamp):
            if t.id in self._handled_trade_ids:
                continue
            if allow and t.netuid not in allow:
                continue
            if t.netuid in deny:
                continue
            w = weights.get(t.trader, 0.0)
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
                netuid=t.netuid, side=t.side, size=size, price=price,
                source_trader=t.trader, source_trade_id=t.id, tag="mirror",
            ))
        return orders

    def execute(self, orders: List[Order]) -> List[ExecutionResult]:
        """Submit orders to the venue. Default: call `place_order` for each,
        accumulate results. Override to batch or wrap in retries."""
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
                self._positions[o.netuid] = self._positions.get(o.netuid, 0.0) + delta
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
        """Replay `signal()` over historical flows and return a BacktestResult.
        Must NOT touch the live wallet — use the historical sequence only.

        Flows carry no realised PnL (unlike hyperliquid fills), so the default
        model is mark-to-market: mirrored buys/sells move τ cash and an alpha
        book, and the book is re-marked at the LAST pool price each flow in
        the window revealed — even unmirrored flows contribute price
        observations. Honest about what it can't know: pool impact and swap
        fees aren't modelled (use `backtest_remote()` for the server's
        rebalanced-return model), and a netuid seen once marks at its entry."""
        history = sorted(history, key=lambda t: t.timestamp)
        snapshot = SyncResult(
            timestamp=history[-1].timestamp if history else int(time.time() * 1000),
            trader_trades=list(history), wallet_tao=0.0, open_positions={},
        )
        fills = {o.source_trade_id: o for o in self.signal(snapshot)}

        cash = 0.0                      # τ spent/received on mirrors
        book: Dict[int, float] = {}     # netuid → alpha held
        last_price: Dict[int, float] = {}
        curve: List[Tuple[int, float]] = []
        n = 0
        for t in history:
            if t.price > 0:
                last_price[t.netuid] = t.price
            o = fills.get(t.id)
            if o is not None:
                # Fill at the padded price — the pad IS the slippage model.
                if o.side == OrderSide.BUY:
                    cash -= o.size * o.price
                    book[o.netuid] = book.get(o.netuid, 0.0) + o.size
                else:
                    cash += o.size * o.price
                    book[o.netuid] = book.get(o.netuid, 0.0) - o.size
                n += 1
            mark = cash + sum(a * last_price.get(uid, 0.0) for uid, a in book.items())
            curve.append((t.timestamp, mark))

        capital = self.config.capital or self.risk.capital
        final = curve[-1][1] if curve else 0.0
        notes: List[str] = []
        if not history:
            notes.append("no history supplied — empty replay")
        elif n == 0:
            notes.append("no flows survived the strat's filters")
        else:
            notes.append("mark-to-market off observed pool prices; "
                         "pool impact/fees not modelled")
        return BacktestResult(
            pnl_curve=curve, trades_simulated=n, fees_total=0.0, gas_total=0.0,
            final_pnl=final,
            roi_pct=(final / capital * 100.0) if capital > 0 else 0.0,
            notes=notes,
        )

    def backtest_remote(self, ct, days: int = 7) -> Dict[str, Any]:
        """The server's rebalanced-return replay (POST /strats/backtest) over
        this strat's basket — dust-guarded, contribution-attributed, the model
        the strat picker UI runs. Complements the pure local `backtest()`.
        (mod.py has no client fn for this route yet, hence the private call.)"""
        leaders = [l for l in self.pick_leaders(ct) if l.enabled]
        capital = self.risk.capital or self.config.capital or 100.0
        total_w = sum(l.weight for l in leaders) or 1.0
        traders = [{"ss58": l.ss58, "weight": l.weight,
                    "alloc_tao": capital * l.weight / total_w} for l in leaders]
        return ct._post("/strats/backtest",
                        {"traders": traders, "days": days, "capital_tao": capital})

    # ── Sleeve bridge (copytensor-specific live engine) ──

    def build_copies(self, ct, our_hotkey: Optional[str] = None) -> List[Dict[str, Any]]:
        """Compose the POST /copy sleeve rows the CopyEngine consumes — one
        per leader, sized `capital × weight / Σweights` so the sleeves sum to
        `risk.capital`. The engine floors each live sleeve at 1τ/day, so a
        wide basket on thin capital overspends — keep baskets ≤ capital in τ."""
        leaders = [l for l in self.pick_leaders(ct) if l.enabled]
        if not leaders:
            raise RuntimeError(f"strat {self.name!r} returned no leaders")
        if self.risk.capital <= 0:
            raise RuntimeError(f"strat {self.name!r} needs capital= (τ) to size sleeves")
        if our_hotkey is None:
            our_hotkey = ct.wallet_balance()["ss58"]
        total_w = sum(l.weight for l in leaders) or 1.0
        rows = []
        for l in leaders:
            row: Dict[str, Any] = {
                "target_ss58": l.ss58,
                "our_hotkey": our_hotkey,
                "alloc_tao": self.risk.capital * l.weight / total_w,
                "label": self.name,
                "poll_interval_sec": max(1, self.risk.interval_ms // 1000),
            }
            if self.risk.max_per_trade_tao > 0:
                row["max_tao_per_tx"] = self.risk.max_per_trade_tao
            rows.append(row)
        return rows

    def start(self, ct, our_hotkey: Optional[str] = None) -> List[Dict[str, Any]]:
        """Create one live sleeve per leader. Idempotent-ish: sleeves are
        labelled with the strat name so `stop()` can find them; run `stop()`
        first when re-sizing, or the sets compound."""
        return [ct.create_copy(**row) for row in self.build_copies(ct, our_hotkey)]

    def stop(self, ct) -> List[Dict[str, Any]]:
        """Delete every sleeve labelled with this strat's name."""
        return [ct.delete_copy(c["id"]) for c in self._own_copies(ct)]

    def status(self, ct) -> Dict[str, Any]:
        """This strat's live sleeves + the blended portfolio plan."""
        return {"copies": self._own_copies(ct), "portfolio": ct.portfolio()}

    def _own_copies(self, ct) -> List[Dict[str, Any]]:
        copies = ct.list_copies()
        rows = copies.get("copies", copies) if isinstance(copies, dict) else copies
        return [c for c in rows if c.get("label") == self.name]

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
        """ms epoch cutoff for `sync()`'s flow fetch — one scan window back."""
        return int(time.time() * 1000) - int(self.config.scan_minutes * 60_000)
