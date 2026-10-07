# Strats

Copytensor strategies as classes — the **same canonical `Strat` schema the
polymarket module defines** (`polymarket/src/strats/base/mod.py`) and the
hyperliquid module ports (`hyperliquid/src/strats/base.py`). Like ERC-20
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

Identical method contract and dataclass names as polymarket and hyperliquid
(`Order`, `TraderTrade`, `SyncResult`, `ExecutionResult`, `TickResult`,
`BacktestResult`, `StratConfig`). The venue differences live in the fields,
not the contract:

| polymarket (prediction markets) | hyperliquid (perps) | copytensor (dTAO) |
|---|---|---|
| `Order.token_id` (outcome token) | `Order.coin` (perp coin) | `Order.netuid` (subnet id) |
| price 0.0–1.0 (probability) | price in USD | price in τ per alpha |
| size in shares | size in base units | size in alpha units |
| cash = USDC | cash = USDC margin | cash = τ (`wallet_tao` / `fetch_wallet_tao`) |
| `TraderTrade` from CLOB fills | + `closed_pnl` / `fee` / `dir` | + `tao_value` / `block` (flows are inferred, no realised PnL) |
| addresses lowercased 0x | addresses lowercased 0x | **ss58 never lowercased** (case-sensitive base58) |
| `gas_total` = polygon gas | `gas_total` = funding | `gas_total` = extrinsic fees |

`tests/test_strats.py` carries a parity test that imports polymarket's base
and asserts the method surface matches and `StratConfig`'s field set is equal
through the ONE declared rename (`fetch_wallet_usdc` → `fetch_wallet_tao`) —
the schema can't silently drift. When hyperliquid's checkout is present its
surface is checked too.

## The copytensor extra: a sleeve engine

Unlike polymarket, the production copy loop here is the server-side
**CopyEngine** (`src/engine/copier.py`): each copied trader is a τ *sleeve*
(`alloc_tao` behind a `target_ss58`) and the engine blends every active
sleeve into one on-chain book. So a strat has two run surfaces off one
definition:

- **Canonical tick loop** — `strat.tick()` in Python: `sync()` pulls watched
  coldkeys' flows through engine-provided I/O callables, the default
  `signal()` mirrors them (size × `size_pct`% × leader weight, netuid
  allow/deny, τ notional clamps, slippage-padded reference price),
  `execute()` places through `config.place_order`. `backtest(history)`
  replays the same `signal()` mark-to-market off the pool prices the flow
  stream itself revealed (flows carry no realised PnL — honesty over
  invention; pool impact/fees aren't modelled).
- **Sleeve bridge** — `build_copies(ct)` compiles the same selection +
  risk knobs into `POST /copy` rows (`capital × weight / Σweights` per
  leader, labelled with the strat name); `start/stop/status` drive the
  live sleeves. `backtest_remote(ct, days)` runs the server's
  rebalanced-return replay (`POST /strats/backtest`) — the model the strat
  picker UI uses, dust-guarded and contribution-attributed.

Both derive the watchlist from the one required hook:

```python
def pick_leaders(self, ct) -> list[Leader]   # who to mirror, with weights
```

`resolve_watchlist(ct)` runs it and lands `{address, weight}` entries on
`config.watchlist` for the canonical surface (`address` holds an ss58).

## Writing a custom strat

```python
from src.strats import Strat, Leader

class MyStrat(Strat):
    name = "my_strat"
    description = "Mirror steady boards, root subnet only."

    def __init__(self, **params):
        super().__init__(netuids_allow=[0], **params)

    def pick_leaders(self, ct):
        rows = ct.leaderboard(days=7, top=100)
        return [Leader(ss58=r["ss58"], weight=1.0) for r in rows[:5]]
```

Selection-only strats are complete with `pick_leaders` — the copy-family
defaults do the rest. For logic that isn't mirroring, override
`signal(sync)`: it must stay **pure** (no I/O) — determinism there is what
makes `backtest()` credible. Register in `__init__.py`'s `REGISTRY`.

Board-row fields available to `pick_leaders` (from `ct.leaderboard`):
`ss58, label, total_stake_tao, pnl_tao, pnl_pct, num_subnets, top_subnet,
baseline, window_days, market_pnl_tao, market_pct, flow_tao`. Respect
`baseline: false` — that row's PnL reads 0 because the history is still
warming, not because the trader is flat.

## I/O is engine-provided

Strats never open HTTP sockets directly on the tick surface. `StratConfig`
carries `fetch_trader_trades`, `fetch_wallet_tao`, `fetch_open_positions`,
`place_order` — mock them in tests, hand in historical-replay versions for
backtests, wire them to the mod client for a live Python loop.
`flow_to_trade()` maps a bt flow row (`{netuid, side, alpha, price,
tao_value}`) onto the canonical `TraderTrade` with a synthesized stable id.

## File layout

```
src/strats/
  __init__.py       # exports + REGISTRY + make()
  base.py           # canonical Strat + dataclasses (the "ABI") + sleeve bridge
  copy_coldkeys.py  # mirror a fixed list of coldkeys
  top_n.py          # top N by window PnL, PnL-weighted
  whales.py         # biggest books by staked τ, √value-weighted
  steady.py         # market-PnL earners (deposit-driven "returns" filtered)
```
