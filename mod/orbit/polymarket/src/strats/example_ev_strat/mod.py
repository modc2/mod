"""
Example strat: EV-gated copy trader.

This is the minimal-yet-useful template for writing your own Polymarket
strategy. Fork this file, rename the class, edit `signal()` / `backtest()`
/ the hooks below. Upload via the LIVE → PARAMS → CUSTOM STRATS panel.

What this example does on top of the basic CopyTrader:

  * Tracks each watched trader's *historical* win rate + average P&L
    per trade. We refresh once on `setup()` from the data-api the
    engine hands us via `fetch_trader_trades`.

  * Computes an **Expected Value** for every prospective mirror order:

        EV = win_rate × mean_realized_pnl × scale_factor

    where `scale_factor` is the size-of-our-mirror / size-of-leader's-trade.
    Negative-EV trades are SKIPPED (logged + dropped) instead of fired.

  * Caps per-trade exposure at `max_order_size` and scales by trader weight
    inside the user-configured capital allocation.

Hook reference (override these for custom behavior):

  _should_mirror(trade)        — filter trades (market / outcome / price)
  _per_trade_size_usd(trade)   — custom sizing (Kelly, vol-scaled, fixed)
  _ev_per_trade(trade)         — replace the EV formula with your own
  _ev_threshold()              — minimum EV to fire (default $0.00)

See `src/strats/base.py` for the full `Strat` contract.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..base.mod import (
    Strat,
    StratConfig,
    Order,
    OrderSide,
    SyncResult,
    TraderTrade,
    BacktestResult,
)


class EVCopyTrader(Strat):
    """Copy trader that gates each mirror on the leader's historical EV.

    Subclass + override `_ev_per_trade` for a smarter signal (e.g. factor
    in time of day, market category, leader-specific win-rate by outcome).
    """

    # ── Lifecycle ──────────────────────────────────────────────────

    def setup(self) -> None:
        """Pull each watched trader's recent trade history so we can
        compute EV stats. Called once when the engine mounts the strat."""
        super().setup()
        # Per-trader stats: address (lowercase) → (win_rate, mean_pnl_per_trade).
        self._stats: dict[str, tuple[float, float]] = {}

        if not self.config.fetch_trader_trades:
            return

        for entry in self.config.watchlist:
            addr = entry["address"].lower()
            # `0` cutoff = "everything you've got"; engine paginates internally.
            history = self.config.fetch_trader_trades(addr, 0)
            self._stats[addr] = _compute_stats(history)

    # ── Live: per-tick signal ──────────────────────────────────────

    def signal(self, sync: SyncResult) -> list[Order]:
        orders: list[Order] = []
        for trade in sync.trader_trades:
            if trade.id in self._handled_trade_ids:
                continue
            if not self._should_mirror(trade):
                continue

            size_usd = self._per_trade_size_usd(trade)
            if size_usd <= 0:
                continue
            size_usd = min(size_usd, self.config.max_order_size, sync.wallet_usdc)
            if size_usd < self.config.min_order_size:
                continue

            # ── EV gate ──
            ev = self._ev_per_trade(trade, size_usd)
            if ev < self._ev_threshold():
                # Engine logs SKIPs separately if it sees them; we just
                # drop the order from the list and move on.
                continue

            price = self._slippage_adjusted_price(trade)
            shares = size_usd / max(price, 0.01)
            orders.append(Order(
                token_id=trade.token_id,
                side=trade.side,
                size=shares,
                price=price,
                order_type="GTC",
                source_trader=trade.trader,
                source_trade_id=trade.id,
                # Carry the EV through to the log entry for visibility.
                tag=f"ev=${ev:.2f}",
            ))
        return orders

    # ── Backtest: FIFO replay, same EV gate ────────────────────────

    def backtest(self, history: list[TraderTrade]) -> BacktestResult:
        history_sorted = sorted(history, key=lambda t: t.timestamp)
        capital = self.config.capital
        weights = {w["address"].lower(): w.get("weight", 0.0)
                   for w in self.config.watchlist}
        total_w = sum(weights.values()) or 1.0

        per_trader_vol: dict[str, float] = defaultdict(float)
        for t in history_sorted:
            per_trader_vol[t.trader.lower()] += t.price * t.size

        # Recompute stats on the historical window so the backtest's EV
        # gate uses the same numbers it would have known at-decision-time.
        # (Strictly speaking we'd need point-in-time recomputation, but
        # most strats are stable enough that a single pass is fine; pin
        # this if you care about look-ahead bias.)
        by_trader: dict[str, list[TraderTrade]] = defaultdict(list)
        for t in history_sorted:
            by_trader[t.trader.lower()].append(t)
        self._stats = {a: _compute_stats(ts) for a, ts in by_trader.items()}

        fee_rate = 0.02
        gas_per_trade = 0.005
        fees_total = 0.0
        gas_total = 0.0
        running_pnl = 0.0
        curve: list[tuple[int, float]] = []
        trades_simulated = 0
        notes: list[str] = []

        fifo: dict[str, list[tuple[float, float]]] = defaultdict(list)

        for trade in history_sorted:
            addr = trade.trader.lower()
            wf = weights.get(addr, 0.0) / total_w if total_w > 0 else 0.0
            tvol = per_trader_vol.get(addr, 0.0) or 1.0
            scale = (capital * wf) / tvol
            shares = trade.size * scale
            notional = trade.price * shares
            if notional < self.config.min_order_size:
                continue
            notional = min(notional, self.config.max_order_size)
            shares = notional / max(trade.price, 0.01)

            ev = self._ev_per_trade(trade, notional)
            if ev < self._ev_threshold():
                continue

            trades_simulated += 1
            gas_total += gas_per_trade
            fees_total += shares * min(trade.price, 1 - trade.price) * fee_rate

            if trade.side == OrderSide.BUY:
                fifo[trade.token_id].append((trade.price, shares))
            else:
                remaining = shares
                while remaining > 0 and fifo[trade.token_id]:
                    buy_price, buy_size = fifo[trade.token_id][0]
                    consumed = min(buy_size, remaining)
                    running_pnl += (trade.price - buy_price) * consumed
                    remaining -= consumed
                    if consumed >= buy_size:
                        fifo[trade.token_id].pop(0)
                    else:
                        fifo[trade.token_id][0] = (buy_price, buy_size - consumed)

            net_pnl = running_pnl - fees_total - gas_total
            curve.append((trade.timestamp, net_pnl))

        final_pnl = curve[-1][1] if curve else 0.0
        roi = (final_pnl / capital * 100.0) if capital > 0 else 0.0

        if fees_total + gas_total > running_pnl and running_pnl > 0:
            notes.append(
                f"FEES (${fees_total + gas_total:.2f}) exceed gross P&L "
                f"(${running_pnl:.2f}) — strat is unprofitable after costs"
            )

        return BacktestResult(
            pnl_curve=curve,
            trades_simulated=trades_simulated,
            fees_total=fees_total,
            gas_total=gas_total,
            final_pnl=final_pnl,
            roi_pct=roi,
            notes=notes,
        )

    # ── Hooks (override for custom behavior) ──────────────────────

    def _should_mirror(self, trade: TraderTrade) -> bool:
        """Filter — return False to skip a trade. Default mirrors all."""
        return True

    def _per_trade_size_usd(self, trade: TraderTrade) -> float:
        """USD size for the mirror order. Default = weight-share of capital
        × leader's notional, ×10 amplifier, capped by remaining wallet."""
        addr = trade.trader.lower()
        weights = {w["address"].lower(): w.get("weight", 0.0)
                   for w in self.config.watchlist}
        total_w = sum(weights.values()) or 1.0
        wf = weights.get(addr, 0.0) / total_w
        return min(self.config.capital * wf, trade.price * trade.size * wf * 10)

    def _slippage_adjusted_price(self, trade: TraderTrade) -> float:
        bps = self.config.max_slippage_bps / 10_000
        if trade.side == OrderSide.BUY:
            return min(trade.price * (1 + bps), 0.99)
        return max(trade.price * (1 - bps), 0.01)

    # ── EV ────────────────────────────────────────────────────────

    def _ev_per_trade(self, trade: TraderTrade, mirror_notional_usd: float) -> float:
        """Expected $ profit for *our* mirror order, given the leader's
        historical edge.

            EV = win_rate × mean_pnl_per_trade × scale

        where `scale` rescales the leader's avg trade P&L down to OUR
        mirror size (our $X exposure vs. the leader's avg $Y exposure).
        Returns 0 when no history exists for the trader.
        """
        addr = trade.trader.lower()
        win_rate, mean_pnl = self._stats.get(addr, (0.0, 0.0))
        if mean_pnl == 0.0 or win_rate == 0.0:
            return 0.0
        leader_notional = max(trade.price * trade.size, 1.0)
        scale = mirror_notional_usd / leader_notional
        return win_rate * mean_pnl * scale

    def _ev_threshold(self) -> float:
        """Minimum acceptable EV (USD) to fire a mirror. Default = $0
        (fire any non-negative). Raise for stricter selection."""
        return 0.0


# ── Helpers ────────────────────────────────────────────────────────

def _compute_stats(history: list[TraderTrade]) -> tuple[float, float]:
    """Reduce a trader's recent trade history to (win_rate, mean_pnl).

    Approximation: realized P&L on each SELL is `(sell_price - buy_price)
    × consumed_shares`, FIFO-matched within the token. Trades that never
    close (still-open BUY positions) contribute 0 to the sample.

    Returns (0, 0) when the sample is too small (< 5 closed trades) so
    the EV gate degrades gracefully to "fire everything" for new traders
    instead of blocking them outright.
    """
    if not history:
        return (0.0, 0.0)
    closes: list[float] = []
    fifo: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for t in sorted(history, key=lambda x: x.timestamp):
        if t.side == OrderSide.BUY:
            fifo[t.token_id].append((t.price, t.size))
        else:
            remaining = t.size
            realized = 0.0
            while remaining > 0 and fifo[t.token_id]:
                buy_price, buy_size = fifo[t.token_id][0]
                consumed = min(buy_size, remaining)
                realized += (t.price - buy_price) * consumed
                remaining -= consumed
                if consumed >= buy_size:
                    fifo[t.token_id].pop(0)
                else:
                    fifo[t.token_id][0] = (buy_price, buy_size - consumed)
            if realized != 0.0:
                closes.append(realized)
    if len(closes) < 5:
        return (0.0, 0.0)
    wins = sum(1 for c in closes if c > 0)
    win_rate = wins / len(closes)
    mean_pnl = sum(closes) / len(closes)
    return (win_rate, mean_pnl)
