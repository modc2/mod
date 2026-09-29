"""whale — mirror only the trades big enough to mean conviction."""

from protocol import Order, OrderSide, Strat, SyncResult


class Whale(Strat):
    venues = ["raydium", "uniswap", "hyperliquid", "bittensor", "polymarket"]

    def signal(self, sync: SyncResult) -> list[Order]:
        floor = float(self.config.params.get("min_notional", 1000.0))
        scale = float(self.config.params.get("scale", 0.05))
        orders: list[Order] = []
        for t in sync.trades:
            if t.id in self._handled_trade_ids or t.price <= 0:
                continue
            if t.size * t.price < floor:
                continue
            size = t.size * scale
            notional = min(size * t.price, self.config.max_order_size)
            size = notional / t.price
            if t.side == OrderSide.SELL:
                held = sync.open_positions.get(f"{t.venue}:{t.symbol}", 0.0)
                size = min(size, held)
                if size <= 0:
                    continue
            orders.append(Order(
                venue=t.venue, symbol=t.symbol, side=t.side, size=size,
                price=t.price, source_trader=t.trader, source_trade_id=t.id,
                tag="whale"))
        return orders
