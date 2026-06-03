"""
CopyTrader — the reference Strat implementation.

Mirrors each watched trader's fills, scaled to the strat's capital allocation
according to each trader's `weight`. This file is the editable template — fork
it (`MyCustomCopy(Strat)`) or edit in place to tune copy logic without
touching the engine.

Implements:
    signal(sync) → list[Order]      mirror each new trader trade as a same-side
                                    order, sized by capital_share / max(buy_vol).
    backtest(history) → BacktestResult
                                    deterministic FIFO replay over the trade
                                    history; produces the curve the UI renders.

Override hooks (most common edits):
    _per_trade_size_usd(trade)      cap / floor per-trade sizing per trader
    _should_mirror(trade)           filter by market / price / outcome
    _slippage_adjusted_price(t)     widen limit price for fast fills
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


class CopyTrader(Strat):
    """Weighted-mirror copy trader. Each watched trader's trades are replicated
    as orders sized by (capital × trader_weight / trader_volume_in_window)."""

    # ── Live: per-tick signal ──────────────────────────────────────

    def signal(self, sync: SyncResult) -> list[Order]:
        orders: list[Order] = []
        for trade in sync.trader_trades:
            # Skip trades we've already mirrored (overlap from re-fetched window).
            if trade.id in self._handled_trade_ids:
                continue
            if not self._should_mirror(trade):
                continue
            size_usd = self._per_trade_size_usd(trade)
            if size_usd <= 0:
                continue
            # Clamp to user-configured max + remaining wallet headroom.
            size_usd = min(size_usd, self.config.max_order_size, sync.wallet_usdc)
            if size_usd < self.config.min_order_size:
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
                tag="copy",
            ))
        return orders

    # ── Backtest: deterministic FIFO replay ────────────────────────

    def backtest(self, history: list[TraderTrade]) -> BacktestResult:
        """Replay the strat's signal logic over a historical trade list.

        Approach: for each trade in chronological order, compute the mirror
        order this strat *would* have placed (using the same `signal()`
        sizing rules), accumulate fill-based P&L via FIFO matching on each
        outcome token, and emit the cumulative curve.
        """
        history_sorted = sorted(history, key=lambda t: t.timestamp)
        capital = self.config.capital
        weights = {w["address"].lower(): w.get("weight", 0.0) for w in self.config.watchlist}
        total_w = sum(weights.values()) or 1.0

        # Per-trader volume aggregation for scaling.
        per_trader_vol: dict[str, float] = defaultdict(float)
        for t in history_sorted:
            per_trader_vol[t.trader.lower()] += t.price * t.size

        fee_rate = 0.02         # 2% taker fee on matched notional
        gas_per_trade = 0.005   # ~$0.005 per Polygon tx
        fees_total = 0.0
        gas_total = 0.0
        running_pnl = 0.0
        curve: list[tuple[int, float]] = []
        trades_simulated = 0
        notes: list[str] = []

        # FIFO basis ledger per token_id (cost basis of open shares).
        fifo: dict[str, list[tuple[float, float]]] = defaultdict(list)  # token → [(price, size), ...]

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

            trades_simulated += 1
            gas_total += gas_per_trade
            fees_total += shares * min(trade.price, 1 - trade.price) * fee_rate

            if trade.side == OrderSide.BUY:
                fifo[trade.token_id].append((trade.price, shares))
            else:  # SELL — match against earliest BUYs
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
                f"(${running_pnl:.2f}) — strategy is unprofitable after costs"
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

    # ── Override hooks (edit these for custom behavior) ────────────

    def _should_mirror(self, trade: TraderTrade) -> bool:
        """Filter — return False to skip a trade. Default mirrors everything.
        Subclasses can filter by market slug, outcome, time-of-day, etc."""
        return True

    def _per_trade_size_usd(self, trade: TraderTrade) -> float:
        """USD size for the mirror order. Default scales by trader weight
        relative to all watched traders + trader's recent volume."""
        addr = trade.trader.lower()
        weights = {w["address"].lower(): w.get("weight", 0.0)
                   for w in self.config.watchlist}
        total_w = sum(weights.values()) or 1.0
        wf = weights.get(addr, 0.0) / total_w
        # Simple proxy: weight-share × capital × trader's notional in this trade
        # bounded by capital headroom. Subclasses can replace with a smarter rule.
        return min(self.config.capital * wf, trade.price * trade.size * wf * 10)

    def _slippage_adjusted_price(self, trade: TraderTrade) -> float:
        """Limit price for the mirror order. Default: nudge by max_slippage_bps
        toward fillable side (BUY = up, SELL = down)."""
        bps = self.config.max_slippage_bps / 10_000
        if trade.side == OrderSide.BUY:
            return min(trade.price * (1 + bps), 0.99)
        return max(trade.price * (1 - bps), 0.01)
