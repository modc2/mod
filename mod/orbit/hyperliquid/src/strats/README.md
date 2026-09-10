# Strats

Hyperliquid strategies as classes — the **same canonical `Strat` schema the
polymarket module defines** (`polymarket/src/strats/base/mod.py`). Like ERC-20
standardizes tokens, the interface standardizes trading logic so any engine
(live loop, backtest, tests) drives any strategy through the same methods.

## The canonical interface

```python
class Strat(ABC):
    def setup(self) -> None
    def sync() -> SyncResult                     # read-only, idempotent
    def signal(sync) -> list[Order]              # pure: decides what to trade
    def execute(orders) -> list[ExecutionResult] # side-effecting: places orders
    def tick() -> TickResult                     # sync → signal → execute
    def backtest(history) -> BacktestResult      # historical replay
    def teardown() -> None
    def state() -> dict
```

Identical method contract and dataclass names as polymarket
(`Order`, `TraderTrade`, `SyncResult`, `ExecutionResult`, `TickResult`,
`BacktestResult`, `StratConfig`). The venue differences live in the fields,
not the contract:

| polymarket (prediction markets) | hyperliquid (perps) |
|---|---|
| `Order.token_id` (outcome token) | `Order.coin` (perp coin) |
| price 0.0–1.0 (probability) | price in USD |
| — | `Order.reduce_only`, `TraderTrade.closed_pnl` / `fee` / `dir` |
| `gas_total` = polygon gas | `gas_total` = funding / venue costs |

`tests/test_strats.py` carries a parity test that imports polymarket's base
and asserts the method surface matches — the schema can't silently drift.

## The Hyperliquid extra: a Rust hot path

Unlike polymarket, the production copy loop here is **Rust**
(`api/src/live_engine.rs`), mirroring leader fills server-side with
agent-signed orders. So a strat has two run surfaces off one definition:

- **Canonical tick loop** — `strat.tick()` in Python: `sync()` pulls watched
  traders' fills through engine-provided I/O callables, the default
  `signal()` mirrors them (size × `size_pct`% × leader weight, coin
  allow/deny, USD notional clamps, slippage-padded limit price), `execute()`
  places through `config.place_order`. `backtest(history)` replays the same
  `signal()` over historical fills, scaling each leader fill's
  exchange-reported `closed_pnl` by the mirrored size ratio.
- **Rust bridge** — `build_config(hl, eoa)` compiles the same selection +
  risk knobs into the live engine's config; `start/stop/status` drive the
  venue-side session.

Both derive the watchlist from the one required hook:

```python
def pick_leaders(self, hl) -> list[Leader]   # who to mirror, with weights
```

`resolve_watchlist(hl)` runs it and lands `{address, weight}` entries on
`config.watchlist` for the canonical surface.

## Writing a custom strat

```python
from strats import Strat, Leader, Order, OrderSide

class MyStrat(Strat):
    name = "my_strat"
    description = "Mirror wallets I like, but only majors."

    def __init__(self, **params):
        super().__init__(coins_allow=["BTC", "ETH"], **params)

    def pick_leaders(self, hl):
        r = hl.top_traders(days=7, pool=100)
        return [Leader(t["address"], weight=1.0) for t in r["traders"][:5]]
```

Selection-only strats are complete with `pick_leaders` — the copy-family
defaults do the rest. For logic that isn't mirroring (momentum,
mean-reversion), override `signal(sync)`: it must stay **pure** (no I/O) —
determinism there is what makes `backtest()` credible.

Register in `__init__.py`'s `REGISTRY`; then `hl.strat("my_strat", ...)`,
`hl.run_strat("my_strat", eoa=...)` and `list_strats()` all see it.

## I/O is engine-provided

Strats never open HTTP sockets directly. `StratConfig` carries
`fetch_trader_trades`, `fetch_wallet_usdc`, `fetch_open_positions`,
`place_order` — mock them in tests, hand in historical-replay versions for
backtests, wire them to the mod client for a live Python loop.

## File layout

```
src/strats/
  __init__.py       # exports + REGISTRY + make()
  base.py           # canonical Strat + dataclasses (the "ABI") + Rust bridge
  copy_wallets.py   # mirror a fixed list of wallets
  top_n.py          # top N by window PnL, PnL-weighted
  whales.py         # highest volume, volume-weighted
  high_win_rate.py  # Wilson-lower-bound win rate, edge-weighted
  sharpe.py         # risk-adjusted, Sharpe-weighted
```
