"""momentum — price momentum off the observed tape.

Keeps a rolling per-instrument price window across syncs (stateful memory,
documented deviation from strict signal() purity — same trade-off the
polymarket momentum mode makes with engine-fed history). Buys when the
last price is up rise_pct% over the window, exits a held position when it
is down drop_pct%.
"""

from collections import deque

from protocol import Order, OrderSide, Strat, SyncResult


class Momentum(Strat):
    venues = ["raydium", "uniswap", "hyperliquid", "bittensor", "polymarket"]

    def __init__(self, config):
        super().__init__(config)
        self._window: dict[str, deque] = {}

    def signal(self, sync: SyncResult) -> list[Order]:
        p = self.config.params
        rise = float(p.get("rise_pct", 2.0)) / 100.0
        drop = float(p.get("drop_pct", 2.0)) / 100.0
        n = int(p.get("window", 20))
        stake = float(p.get("stake", 25.0))

        for t in sorted(sync.trades, key=lambda x: x.timestamp):
            if t.price <= 0:
                continue
            self._window.setdefault(f"{t.venue}:{t.symbol}", deque(maxlen=n)).append(t.price)

        orders: list[Order] = []
        for key, prices in self._window.items():
            if len(prices) < 2:
                continue
            venue, symbol = key.split(":", 1)
            first, last = prices[0], prices[-1]
            held = sync.open_positions.get(key, 0.0)
            if last >= first * (1 + rise) and held <= 0:
                size = min(stake, self.config.max_order_size) / last
                orders.append(Order(venue=venue, symbol=symbol,
                                    side=OrderSide.BUY, size=size, price=last,
                                    tag="momentum-entry"))
            elif last <= first * (1 - drop) and held > 0:
                orders.append(Order(venue=venue, symbol=symbol,
                                    side=OrderSide.SELL, size=held, price=last,
                                    tag="momentum-exit"))
        return orders
