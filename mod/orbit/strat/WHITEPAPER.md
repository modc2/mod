# One Strat, Many Venues

**A unified framework for the trading strategies of polymarket, hyperliquid and copytensor, using adapters instead of rewrites.**

*orbit/strat, protocol v1, bridge v1.1, October 2026*

---

## Abstract

Three modules in this fleet each grew their own strategy layer. Polymarket copies prediction-market traders. Hyperliquid copies perp traders. Copytensor copies Bittensor dTAO stakers. They converged on the same eight-method contract (`setup / sync / signal / execute / tick / backtest / teardown / state`) but kept separate, venue-specific dataclasses. As a result, a strategy written for one venue cannot be listed, compared, forked or run next to a strategy from another.

This paper describes `orbit/strat`'s answer: one venue-neutral protocol, plus one **adapter (bridge) per source module**. The adapters mount every strategy those modules already ship onto the protocol **without changing a line of their code**. Each bridged strategy keeps its native signal logic and its native backtest model. It gains the shared marketplace: one listing, verification, a board, forking, dry-run ticks, and a "plan" that emits the exact config the owning module's own live engine consumes. Schema drift between the modules and the framework is detected mechanically, not discovered in production.

The design is local-first. Strategy code is read from the local disk, peers are found in local `config.json` files, and the only network reads go to fleet modules on `localhost`, plus the public venue endpoints those modules read themselves. No keys are held and no credentials are minted.

---

## 1. The problem: three copies of one idea

| | polymarket | hyperliquid | copytensor |
|---|---|---|---|
| package | `polymarket/src/strats` | `hyperliquid/src/strats` | `copytensor/src/strats` |
| instrument | `Order.token_id` (CLOB outcome) | `Order.coin` (perp) | `Order.netuid` (subnet pool) |
| price unit | probability 0–1 | USD | τ per alpha |
| cash | `wallet_usdc` | `wallet_usdc` | `wallet_tao` |
| trade extras | `market, condition_id, outcome` | `closed_pnl, fee, dir` | `tao_value, block` |
| construction | `Strat(config)` | `Strat(**params, config=)` | `Strat(**params, config=)` |
| selection hook | none (watchlist given) | `pick_leaders(hl)` | `pick_leaders(ct)` |
| backtest model | FIFO cost basis, 2% fee + gas | realised `closed_pnl` × size ratio | mark-to-market alpha book |
| live engine | TS/Rust session engine, `/live/start` | Rust `live_engine.rs`, `build_config` | sleeve `CopyEngine`, `build_copies` |
| shipped strats | copytrader, example_ev_strat | copy_wallets, top_n, whales, high_win_rate, sharpe | copy_coldkeys, top_n, whales, steady |

The three modules already pin parity to each other through tests: hyperliquid and copytensor both import polymarket's `StratConfig` and assert equal field sets through declared renames. So the contract is agreed. What is missing is a single place where strategies from all three can live together.

Two obvious fixes are both wrong:

- **Move everything into one package.** Each module's live engine, UI and tests import its own package. A shared package would couple deploys across three products that ship independently. It would also violate the fleet rule that the module owning a chain owns its execution.
- **Rewrite the strategies on the new protocol.** That doubles the code, and the two copies drift apart within weeks. The native copy would stay authoritative, because it is the one the live engine runs.

## 2. Design: protocol + adapters

```
                         ┌────────────────────── orbit/strat ──────────────────────┐
  polymarket/src/strats ─┤ PolymarketSource ─┐                                     │
 hyperliquid/src/strats ─┤ HyperliquidSource ├─ codec ─► Bridged(Strat) ─► registry │─► list / verify / board
  copytensor/src/strats ─┤ CopytensorSource ─┘            ▲       │                 │   fork / backtest / plan
                         │                                │       ▼                 │
         strats/*, orbit strat mods ── Strat subclasses ──┘   venues.py ─► fleet module that owns the chain
                         └─────────────────────────────────────────────────────────┘
```

### 2.1 The protocol (`protocol.py`)

This is the venue-neutral superset of the canonical schema. It keeps the same eight methods. Instruments are `(venue, symbol)` pairs, and `VenueTrade.extras` carries venue-specific fields. The parity test pins `StratConfig` to polymarket's canon through three declared renames (`fetch_trader_trades→fetch_trades`, `fetch_wallet_usdc→fetch_cash`, `fetch_open_positions→fetch_positions`) and two declared additions (`venues`, `quote`).

### 2.2 Sources (`bridge.py`)

A `Source` is a small adapter with three jobs. Every one of them is read-only with respect to the source module.

1. **Load.** The module's package is loaded by file path under a private name (`strat_src_<mod>`) with `submodule_search_locations` set to its own directory. Its relative imports therefore resolve inside itself. The module is never imported under its real name, never put on `sys.path`, and never written to. Polymarket keeps one strategy per folder (`<id>/mod.py`, the same layout its user uploads use) and has no registry, so its source scans folders for concrete `Strat` subclasses. Hyperliquid and copytensor expose a `REGISTRY`.

2. **Codec.** A codec translates between the unified and native dataclasses:
   - `VenueTrade → TraderTrade`: `symbol` goes to the native instrument field, and declared `extras` go to native fields (`closed_pnl`, `tao_value`, …).
   - `native Order → Order`: the instrument goes back to `symbol`. `netuid 8 ↔ "SN8"`. `reduce_only` is preserved in `tag`.
   - `SyncResult`: cash maps to `wallet_usdc` / `wallet_tao`. Positions keyed `venue:symbol` map to native keys (token id, coin, or int netuid). Trades from other venues are filtered out.
   - `StratConfig`: field by field. The engine I/O callables are wrapped, so native code that fetches for itself (such as a `setup()` that primes statistics) still reads through the unified venue layer.
   - `BacktestResult`: identical field sets, copied straight across.

3. **Select.** Hyperliquid and copytensor strategies choose their own leaders through `pick_leaders(hl|ct)`. The bridge passes them **duck-typed read-only clients** (`HLReader.top_traders`, `CTReader.leaderboard`) that implement only the slice of each module's `Mod` the shipped strategies call, over that module's local HTTP API. Polymarket has no native selector, so its source supplies one from the same public leaderboard the polymarket module reads.

### 2.3 The bridged strat

`bridged("<module>.<strat>")` generates a `protocol.Strat` subclass (cached per name) whose instance holds a native instance:

| method | implementation |
|---|---|
| `signal(sync)` | `native.signal(codec.sync_in(sync))`, then `codec.order_out` on each order. **Native logic.** |
| `backtest(h)` | `native.backtest(codec.trade_in(h))`, then `backtest_out`. **Native model**, labelled in `notes[0]`. |
| `sync / execute / tick` | the unified base class, which routes through `venues.py` |
| `execute` | the base class, then pushes handled trade ids into the native dedupe set, so the native `signal()` never fires twice on one trade |
| `setup / teardown` | both layers |
| `state()` | unified state + `native.state()` + bridge provenance |
| `resolve_watchlist()` | the native selection hook, capped at `max_leaders` |
| `plan()` | the native live-engine bridge (`build_config` / `build_copies` / `/live/start` body), pinned to the resolved leaders |

The choice of what to delegate is deliberate. **Decisions** (signal, backtest, selection) belong to the strategy author and stay native. **Plumbing** (I/O, guards, dry-run, dedupe) belongs to the framework and is unified.

### 2.4 Naming and the registry

Bridged strategies are named `<module>.<strat>`, for example `hyperliquid.top_n` and `copytensor.top_n`. The dot never appears in a scaffolded mod name, so bridged names cannot collide with builtin or orbit strategy mods. The registry merges three origins: `builtin` (this module's `strats/`), `orbit` (any module with a `strat` block) and `bridge`.

## 3. Drift is a test failure, not a production bug

The danger with adapters is silent data loss. If hyperliquid adds a field to `TraderTrade` tomorrow, a careless adapter keeps working and quietly drops it.

`Source.drift()` lists every field of the six native dataclasses (`Order`, `TraderTrade`, `SyncResult`, `ExecutionResult`, `BacktestResult`, `StratConfig`) that the codec does not explicitly map. Three mechanisms enforce it:

- `verify(name)` reports drift as issues, so a drifted source shows up as not-ok on the board.
- `test_bridge_sources_load_without_drift` asserts that drift is empty for every present source.
- `test_bridge_detects_drift` proves the detector works by building a codec with a wrong instrument field.

This follows the coupling style the fleet already uses (hyperliquid's and copytensor's parity tests against polymarket), with one difference: the failure lands in **this** module's suite. The source modules carry no new obligation, so they can keep evolving at their own pace.

## 4. Trust model

The defi-desk rule is unchanged:

- **No keys.** The framework holds no keys and mints no credentials. A caller's bearer token is forwarded verbatim to the module that owns the chain, and that module's own gate still applies.
- **Dry-run by default.** A live order needs `dry_run=false` **and** `confirm=true` **and** a token the peer accepts.
- **`plan()` is data, not action.** It returns the body each module's own engine would take. Starting it stays with that module and its gates: hyperliquid's agent approval, copytensor's console-approved writes, and polymarket's sessions with `autoExecute: false` and exits that are never gated.
- **Read-only selection.** Bridged selection clients implement reads only. `CTReader.wallet_balance` refuses outright, because this module never reads a wallet.

## 5. Backtests: comparable, not identical

Every bridged strategy keeps its native backtest model. This is intentional: the native model is what the module's own UI shows and what its authors tuned against. Replacing it would make the board disagree with the source module. The cost is that ROIs measure different things:

| model | measures | blind spot |
|---|---|---|
| polymarket FIFO | realised P&L on closed outcome tokens, net of 2% fee + gas | open positions at window end |
| hyperliquid `closed_pnl` | exchange-reported realised P&L scaled by mirror size (`size_pct × weight`) | unrealised P&L; capital is informational, so ROI is P&L ÷ nominal capital, not ÷ capital actually deployed |
| copytensor mark-to-market | τ cash + alpha book re-marked at the last observed pool price | pool impact, swap fees |
| builtin (protocol default) | cash-bounded mark-to-market across venues | fees, impact |

The board therefore reports `currency` with every row, and every result carries the model in `notes[0]`. ROI is unit-free, so rows can be ranked side by side. Read them as *"what this strategy's own authors would show you"*, not as one shared risk measure. A shared cash-bounded model across all strategies is future work (§8). The data path for it already exists, because all history arrives as `VenueTrade`.

## 6. Data sources (local-first)

| venue | trade tape | leaders |
|---|---|---|
| hyperliquid | hyperliquid module MCP `hl_user_fills` (`:8919`), now including `closed_pnl`/`fee` | hyperliquid module `/traders/top` |
| bittensor | bt module MCP `bt_trader_flows(address, hours)`; fallback: copytensor REST `/traders/{ss58}/flows` (the same bt index) | copytensor `/leaderboard` |
| polymarket | public `data-api.polymarket.com/trades`, the same upstream the polymarket engines read, because the module's own API is owner-gated end to end | public `data-api …/v1/leaderboard` |

Every peer URL is discovered from the peer's local `config.json` and can be overridden with `STRAT_<VENUE>_URL` (and `STRAT_POLYMARKET_DATA_URL`). Flow ids follow copytensor's `flow_to_trade` convention (`trader:ts_ms:netuid:SIDE`), so the bt path and the copytensor path dedupe against each other.

Building the bridge exposed two latent bugs in the v1.0 venue layer:

- the bittensor read called `bt_trader_flows` with a non-existent `coldkey` argument;
- the polymarket read targeted a `/trader/{addr}/trades` route that does not exist.

Both silently returned `[]`, so every bittensor and polymarket backtest was empty. Both are fixed. The silence itself is a lesson: an adapter whose failure mode is "empty list" needs a live smoke check. The board's `trades_seen` serves as that check.

## 7. Usage

```
m strat/sources                                   # what's bridged, models, drift
m strat/strats origin=bridge                      # the 11 module strats
m strat/code name=hyperliquid.sharpe              # the module's own source (read)
m strat/backtest name=copytensor.steady days=7    # picks its own leaders
m strat/backtest name=polymarket.copytrader traders='["polymarket:0x…"]'
m strat/tick name=hyperliquid.top_n               # DRY RUN through venues.py
m strat/plan name=hyperliquid.whales eoa=0x…      # hl live_start body
m strat/plan name=copytensor.top_n capital=20 hotkey=5…   # sleeve rows
m strat/fork name=hyperliquid.top_n new_name=my-top-n     # subclass, yours to edit
m strat/board refresh=true                        # every strat, every venue, ranked
m strat/catalog                                   # each module's server-side boards
```

A fork of a bridged strategy cannot copy the native file, because its relative imports and schema live in the source module. Instead the fork is a normal strategy mod whose class **subclasses** the bridged one:

```python
from bridge import bridged
Base = bridged("hyperliquid.top_n")
class MyTopN(Base):
    pass        # override signal()/backtest() — native by default
```

## 8. Limits and future work

- **Python layers only.** Polymarket's production strategy is a TypeScript class (`strats/strat.ts`) running in its session engine, and user-uploaded strategies live in its data directory (the wallet, which is never touched). Those are reachable only through `catalog(token=…)`, not as bridged classes.
- **No live start from here.** This is by design (§4). A future `start` would be a thin forwarder to each module's start call, behind the same confirm + token gates.
- **A common backtest model.** An optional `model="unified"` could replay every strategy's orders through one cash-bounded, fee-aware ledger, so cross-venue ROIs measure the same thing.
- **Cross-venue strategies.** The protocol already allows a strategy over several venues (the builtin `mirror` does this). Bridged strategies are single-venue because their native schemas are. A composite strategy that blends bridged sleeves by weight is the natural next primitive.
- **Currency normalisation.** τ and USDC capital are not converted. A τ/USD quote from the bt module would let the board show notional in one unit.

## 9. Verification

- `python3 tests/test_strat.py`: 24 offline checks, no network. They cover protocol parity, the 11 bridged strategies verifying, codec round-trips per venue, native signal and backtest per source (hyperliquid `closed_pnl` arithmetic exact; copytensor ss58 case preserved; polymarket FIFO profitable round trip), native dedupe kept in step after execute, bridged fork, drift detection.
- The source modules' own suites are unaffected (hyperliquid `tests/test_strats.py` 29/29, copytensor `tests/test_strats.py` 22/22), and `git status` shows no change under `polymarket/`, `hyperliquid/` or `copytensor/`.
- Live, against the running modules (7-day window, 3 self-selected leaders): see the board snapshot in the README.
