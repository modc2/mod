"""mirror — one-to-one copy of every watched trade, scaled.

The simplest strat on the protocol and the reference for authoring one:
subclass Strat, implement signal(). Everything else (sync, execute, tick,
mark-to-market backtest) comes from the base.
"""

from protocol import Order, OrderSide, Strat, SyncResult


class Mirror(Strat):
    venues = ["raydium", "uniswap", "hyperliquid", "bittensor", "polymarket"]

    def signal(self, sync: SyncResult) -> list[Order]:
        scale = float(self.config.params.get("scale", 0.1))
        weights = {w["address"]: float(w.get("weight", 1.0))
                   for w in self.config.watchlist}
        orders: list[Order] = []
        for t in sync.trades:
            if t.id in self._handled_trade_ids or t.price <= 0:
                continue
            w = weights.get(t.trader, 1.0)
            size = t.size * scale * w
            notional = min(size * t.price, self.config.max_order_size)
            size = notional / t.price
            if t.side == OrderSide.SELL:
                held = sync.open_positions.get(f"{t.venue}:{t.symbol}", 0.0)
                size = min(size, held)
                if size <= 0:
                    continue
            orders.append(Order(
                venue=t.venue, symbol=t.symbol, side=t.side,
                size=size, price=t.price,
                source_trader=t.trader, source_trade_id=t.id, tag="mirror"))
        return orders
