# Strats

Polymarket strategies as classes — like ERC-20 standardizes tokens, the `Strat` interface standardizes trading logic so the live engine and backtest engine can drive any strategy through the same methods, parameterized by the class.

Two authoring surfaces, same idea (history in, trade intents out):

- **TypeScript** — `src/app/app/lib/strats/base.ts`: `abstract class Strat<P>` with five hooks (`maxPerCycle` / `shouldMirror` / `scoreCandidate` / `sizeAndPrice` / `propose`), every hook receiving the full `StratHistory` (watchlist trade history, per-trader stats, open positions, balance). This is what the browser live engine and the BACKTEST tab execute. `copytrader.ts` is the mirror reference; `flowmomentum.ts` is the history-driven `propose()` reference. Register classes in `registry.ts`.
- **Python** — `base/mod.py` (this directory): `sync → signal → execute`, documented below.

## The canonical interface

```python
class Strat(ABC):
    def setup(self) -> None
    def sync() -> SyncResult                    # read-only, idempotent
    def signal(sync) -> list[Order]             # pure: decides what to trade
    def execute(orders) -> list[ExecutionResult] # side-effecting: places orders
    def tick() -> TickResult                    # sync → signal → execute
    def backtest(history) -> BacktestResult     # historical replay
    def teardown() -> None
    def state() -> dict
```

The engine only ever calls these methods. Your strategy is a subclass that fills in `signal()` and `backtest()` — the rest has working defaults.

## Live engine cycle

Every `scan_minutes` (configured per-strat in the LIVE tab):

1. Engine calls `strat.tick()`
2. `tick()` runs `sync()` → `signal()` → `execute()` and returns `TickResult`
3. Engine logs the result, schedules next cycle

## Backtest cycle

One-shot replay over a historical window:

1. Engine collects historical trades for the strat's watchlist
2. Engine calls `strat.backtest(history)`
3. Strat replays its `signal()` logic deterministically and returns a `BacktestResult` (curve, fees, gas, final PnL, ROI)

The UI's P&L curve, trade feed, and fee/gas/total/gross row all read directly from `BacktestResult`.

## Sharing a strat

Two ways to get a strat to someone else:

- **In-deploy gallery** — flip it `public` (`POST /user-strats/:id/publish`) and
  it appears in every trader's Community list, where they can `fork` it.
- **By CID (cross-system)** — `POST /user-strats/:id/share` bundles the strat's
  source + metadata into a self-describing JSON blob, stores those bytes in a
  content-addressable store, and returns an IPFS-compatible **CID**. Anyone
  imports it with `POST /user-strats/import {cid, owner}` — it lands as a
  private copy they own, with `forkedFrom` lineage back to the original.

The share backend is just an HTTP endpoint speaking a two-call contract
(`POST /put` → `{cid}`, `GET /get/{cid}`). The orbit `localfs` module
implements it on `:8860` and is the default. Point `POLYMARKET_SHARE_URL`
(or `LOCALFS_URL`) at any other service that speaks it — a remote/shared
localfs, the `store` module, or an adapter in front of a real IPFS pinning
service — and sharing works across systems unchanged. Because the bundle is
plain bytes and the CID is computed the IPFS way, the same CID re-pinned to
any IPFS-compatible store resolves to the same strat: the link is portable
even when the backend is not. See `src/api/src/share.rs`.

## Writing a custom strat

Two paths:

### Path A — Edit `copytrader.py` in place

The reference `CopyTrader` mirrors trades from a watchlist with weights. Edit:

- `_should_mirror(trade)` — filter trades (skip certain markets / outcomes / prices)
- `_per_trade_size_usd(trade)` — change sizing (e.g. fixed size, Kelly-fraction, vol-scaled)
- `_slippage_adjusted_price(trade)` — limit-price rule (aggressive vs. patient)

### Path B — Subclass `Strat`

For strategies that aren't pure mirroring (e.g. momentum, mean-reversion, market-making):

```python
from polymarket.strats import Strat, Order, OrderSide

class MeanReversion(Strat):
    def signal(self, sync):
        # ignore sync.trader_trades — generate orders from your own logic
        orders = []
        for token_id, qty in sync.open_positions.items():
            mid = sync.extras["mid_prices"][token_id]
            if mid < 0.4:   # cheap — accumulate
                orders.append(Order(token_id, OrderSide.BUY, size=10, price=mid))
            elif mid > 0.6: # rich — trim
                orders.append(Order(token_id, OrderSide.SELL, size=qty * 0.5, price=mid))
        return orders

    def backtest(self, history):
        # ... use the same signal() logic over a historical mid-price series
        ...
```

## I/O is engine-provided

Strats never open HTTP sockets directly. The engine passes `StratConfig.fetch_trader_trades`, `fetch_wallet_usdc`, `fetch_open_positions`, `place_order` — your strat code calls those. This is what lets the same `signal()` work for both live and backtest (engine swaps in historical-replay versions for backtest).

## Why this shape

- **`sync` separate from `signal`**: keeps the decision logic pure, so backtest can reuse it deterministically. If you mix I/O into `signal()`, you lose backtest credibility.
- **`backtest()` mandatory**: every strat has a self-contained replay. The UI's "what would this have done last 7 days?" requires it.
- **`signal()` returns `Order`, not raw API calls**: lets the engine batch, rate-limit, or route across venues without strat changes.
- **No throttle in the base class**: every detected trade flows through `signal()`. If you want throttling, do it explicitly in your override (`_should_mirror` returning False past N/hour). The default mirrors everything — copy engines are about being fast, not filtering.

## File layout

```
src/strats/
  __init__.py          # public exports: Strat, StratConfig, Order, ...
  base.py              # abstract Strat + dataclasses (the "ABI")
  copytrader.py        # reference implementation (editable template)
  README.md            # this file
```
