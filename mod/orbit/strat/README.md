# strat — a marketplace of strategies, where strats are mods

One unified, class-defined strategy protocol across five venues, and a
marketplace built out of the mod system itself: **a strategy is a mod** — a
directory with a `config.json` declaring a `strat` block and a `strat.py`
defining one subclass of the canonical `Strat` class. Anything that honors
the contract shows up on the board, can be forked, backtested and run.

## The protocol (`protocol.py`)

The venue-neutral superset of the canonical strat schema already shipped in
the polymarket, hyperliquid and copytensor modules — same eight-method
contract, pinned by a parity test:

```
setup / sync / signal / execute / tick / backtest / teardown / state
```

`signal()` is the only method a strat must implement: a pure function from a
`SyncResult` snapshot to a list of `Order`s. The base class supplies a
watchlist-driven `sync()`, a guarded `execute()`, and a mark-to-market
`backtest()`.

Instruments are `(venue, symbol)` pairs:

| venue | executed by | symbol |
|---|---|---|
| raydium | solana module (`:50710`, Jupiter routes / Raydium pools) | `SOL/USDC` mint pair |
| uniswap | defi module DEX desk (`:50500` → eth module) | `WETH/USDC` token pair |
| hyperliquid | hyperliquid module (`:8919`, MCP) | coin, e.g. `BTC` |
| bittensor | bt module (`:50280`, dTAO pools) | `SN<netuid>` |
| polymarket | polymarket module (`:50091`, live-engine sessions) | CLOB token id |

## Bridged strats: polymarket, hyperliquid and copytensor on one framework (`bridge.py`)

The strats that already ship inside three modules are mounted onto this
protocol by adapters. **No code in those modules is changed.** Each module's
`src/strats` package is loaded read-only by path, its native `signal()` and
`backtest()` run unchanged, and a per-source codec translates the types
(`token_id` / `coin` / `netuid`, `wallet_usdc` / `wallet_tao`, trade extras).

| source | venue | strats (named `<module>.<strat>`) | native backtest |
|---|---|---|---|
| polymarket | polymarket | copytrader, example_ev_strat | FIFO, 2% fee + gas |
| hyperliquid | hyperliquid | copy_wallets, top_n, whales, high_win_rate, sharpe | exchange `closed_pnl` |
| copytensor | bittensor | copy_coldkeys, top_n, whales, steady | mark-to-market τ |

```
m strat/sources                                  # what's bridged + schema drift (should be [])
m strat/strats origin=bridge
m strat/backtest name=hyperliquid.sharpe         # selects its own leaders (max_leaders=5)
m strat/plan name=hyperliquid.top_n eoa=0x…      # the body hl's live engine takes
m strat/plan name=copytensor.steady capital=20 hotkey=5…   # copytensor sleeve rows
m strat/fork name=copytensor.top_n new_name=my-ct-top   # subclass, edit freely
m strat/catalog                                  # each module's server-side strat boards
m strat/whitepaper                               # the design, in full
```

`drift()` lists any native dataclass field the codec doesn't map. The tests
pin it to empty, so a schema change upstream fails here first. Full design,
trust model and backtest-model caveats are in **WHITEPAPER.md**.

Live board snapshot (2026-10-03, 7d, 3 self-selected leaders, capital 1000
in venue currency; each row uses its native model, so ROIs are comparable
in rank, not in method):

| strat | ROI | trades simulated | currency |
|---|---|---|---|
| hyperliquid.top_n | +97.6% | 3355 / 4089 fills | USDC |
| copytensor.steady | −0.05% | 1 | TAO |
| copytensor.top_n | −0.19% | 5 | TAO |
| polymarket.copytrader | −1.16% | 168 / 6000 trades | USDC |
| polymarket.example_ev_strat | −1.16% | 168 (EV gate passed all) | USDC |

Hyperliquid's native model counts realised `closed_pnl` scaled by
`size_pct` against a nominal capital, so its ROI is not cash-bounded. See
whitepaper §5.

## Trust model (the defi-desk rule)

This module is a **client, never a signer**. It holds no keys and mints no
credential: a caller's bearer token is forwarded verbatim to the peer module
that owns the chain, so a compromise of strat cannot sign anything. Every
run is a **dry run by default**; a live order needs `dry_run=false` AND
`confirm=true` AND a token the peer itself accepts — and the peer's own gate
still applies underneath. Polymarket orders are never placed directly at
all: that venue's own live engine (autoExecute off by default, exits never
gated) is the execution surface.

Peers are discovered **locally** from their `orbit/<mod>/config.json` —
no external registry, overridable per venue with `STRAT_<VENUE>_URL`.

## The marketplace (`mod.py`)

```
m strat/strats                  # the listing (builtin + orbit + bridged strats)
m strat/board                   # verified board, cached backtest perf
m strat/strat name=mirror       # one card
m strat/code name=mirror        # the class is the strategy
m strat/verify name=mirror      # contract check
m strat/new name=mystrat venues='["hyperliquid"]'      # scaffold a strat mod
m strat/new name=mystrat orbit=true                    # ...as an orbit module
m strat/fork name=mirror new_name=mymirror             # remix
m strat/publish name=mymirror                          # builtin -> orbit module
m strat/backtest name=mirror traders='["hyperliquid:0x..."]' days=7
m strat/tick name=mirror traders='["hyperliquid:0x..."]'     # DRY RUN
```

Built-ins (each one a complete example strat mod under `strats/`):

- **mirror** — copy every watched trade, scaled to your capital, any venue.
- **whale** — copy only conviction-sized trades (notional floor).
- **momentum** — buy what is rising, exit what is falling, off the tape.

## Authoring a strat mod

```
mystrat/
  config.json   { "name": "mystrat",
                  "strat": { "protocol": 1, "class": "strat.py:Mystrat",
                             "venues": ["hyperliquid"], "params": {...} } }
  strat.py      class Mystrat(Strat): def signal(self, sync): ...
```

That's the whole contract. `verify` tells you if you've honored it; the
board picks it up automatically.

## Tests

```
python3 tests/test_strat.py     # 24 offline checks, no network, no wallets
```

Includes the fleet-style parity test pinning this protocol to the canonical
polymarket strat schema through declared renames only.
