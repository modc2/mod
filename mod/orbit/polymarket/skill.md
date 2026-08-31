---
name: polymarket
description: Copy Polymarket traders — put a dollar amount against a trader's address and the engine mirrors their fills with it. Copy many at once, backtest one before committing (walk-forward verdict + entry funnel), size a BASKET with a different amount against each, gate a leader to one slice of their flow in plain language ("big buys on crypto under 30c" compiles to marketQuery + tradeFilters), and check what actually landed with pm_copy_trades (coverage, lag, slippage, every missed trade). Driven from the COPY DESK console or over MCP (pm_copy_*). Use when asked to copy a trader, allocate money across traders, filter which of their trades to copy, ask whether copying someone would have worked, or find out why a copy session isn't trading.
type: orbit-module
---

# polymarket — the copy desk

The unit is an **allocation**: one trader, one dollar amount.

```
0xab… → $250      the engine mirrors their fills with $250
0xcd… → $100      and the ledger says what each name made
```

That list is the **copy book**, it lives on the server at
`~/.mod/polymarket/copy/book.json`, and it is the same book whether you edit it
in the browser (`/polymarket/copy`) or over MCP (`pm_copy_*`). There is no
second, client-side copy — that is the point.

## Orient yourself

```
pm_copy_book                      what is being copied, with how much, and how it's doing
pm_trader address=0x…             is this leader copyable at all (see below)
pm_copy_backtest address=0x… days=7   would copying THEM have worked
pm_copy_basket legs=[{address,allocationUsd},…] days=7 compare=true floors=true
                                  several traders, a different amount each — how to SPLIT money
pm_copy_trades days=7 q="missed longshots"
                                  what they traded vs what I actually got: coverage, lag, slip
pm_copy_allocate address=0x… allocationUsd=250
pm_copy_start address=0x…         DRY RUN unless autoExecute=true
pm_live_gates                     why a running session isn't filling
```

Same thing over HTTP (owner Bearer required — the whole API is owner-only):

```
GET    /copy/book?eoa=0x…  rows + totals, plus `sessions`: sessions RUNNING on this wallet
                           that are not rows (older strat screens) — the desk shows and can stop them
POST   /copy/allocations   {address, allocationUsd, label?, enabled?, params?}
DELETE /copy/allocations/{address}?eoa=0x…
POST   /copy/rebalance     {bankroll, mode: "equal"|"weighted"}
POST   /copy/start         {eoa, address?, autoExecute?}
POST   /copy/stop          {eoa, address?}
POST   /live/stop          {eoa, strategyId}   stop one of those non-book sessions
GET    /copy/strats        the book as identity strats — what the worker replays

POST   /polymarket/api/basket  {legs:[{address, allocationUsd}], days, compare?, floors?}
                           the BASKET replay — Next app port, not the Rust API
GET    /polymarket/api/copytrades?days=7&q=…
                           their trades joined to my fills — also Next app port
```

## Copying many, and copying only part of what they do

Two things one leader's row cannot express, both first-class:

- **Many leaders.** The sidebar book takes a pasted LIST of addresses (every
  `0x…` in the blob, one amount each) and a checkbox per row drives a bulk bar:
  fund each, start, stop, pause, drop, or send the selection to `/copy/basket`
  to size them against each other.
- **Part of one leader.** `params.marketQuery` picks the MARKETS (title match,
  commas OR, spaces AND) and `params.tradeFilters`
  (`{sides,minPrice,maxPrice,minNotional,maxNotional}`) picks the TRADES inside
  them. `app/lib/semanticFilter.ts` compiles one typed sentence into exactly
  that pair — "big buys on crypto under 30c" → `marketQuery` over the expanded
  crypto lexicon + `{sides:"buy", maxPrice:0.3, minNotional:500}` — and
  anything it cannot express (a time window, `not candles`, `missed`) is
  returned as `viewOnly` and never armed. `pm_copy_trades q=…` returns the same
  compiled gate for an agent to write with `pm_copy_allocate`.

## Did the copying work?

`pm_copy_trades` / `/copy/trades` joins MY on-chain fills to the leader trades
they mirror (same market, same side, mine within 30 min after theirs, nearest
wins, one leader trade claimed once — a fill carries no leader tag, so nothing
upstream links them). It reports `coverage` (copied ÷ their trades — the number
that matters), `medianLagSec`, `avgSlipCents`, and `unattributed`: fills of mine
with no leader behind them, which are the engine's own stop-loss/take-profit
exits or hand trades, reported rather than credited to somebody.

## Layout

```
src/api/src/copy.rs            the copy book: store, identity template, /copy/* routes
src/app/app/lib/identityStrat.ts   the SAME template in TypeScript
src/app/app/lib/strats/identity.fixture.json   pins the two against each other
src/app/app/components/CopyDesk.tsx  the desk — reads/writes only /copy/*
src/app/app/lib/semanticFilter.ts   one English sentence → filter, and → the engine's own gate
src/app/app/lib/copyTrades.ts  the join: my fills ↔ the leader trades they mirror
src/app/app/api/copytrades/route.ts  that join, for the screen and for pm_copy_trades
src/app/app/components/CopyTradesPanel.tsx  /copy/trades + its compact twin in the sidebar
src/app/app/components/CopyPanel.tsx  the sidebar book (WHO I COPY): roster, bulk bar, BACKTEST, RESULTS
src/app/app/lib/basketSim.ts   the BASKET replay: one sleeve per leg, on its own capital
src/app/app/components/BasketSim.tsx  /copy/basket — the roster IS the results table
src/app/app/api/basket/route.ts  the same replay for agents (pm_copy_basket)
src/app/app/lib/copyLadder.ts   one leader replayed at several $ — feed/bankroll/resolutions once, a rung per size
src/app/app/components/DeskAllocationChart.tsx  WHERE THE MONEY IS: $ per trader as bars with the backtest at that $, click → replay at $N + ladder
src/app/app/lib/copyBook.ts    its client; one function per route, no local state
src/app/app/lib/server/hubWorker.ts  the backtest worker; reads /copy/strats each pass
src/mcp.py                     pm_copy_* — the same routes, for agents
src/api/src/live_engine.rs     what actually mirrors a fill (6.5k lines)
```

## The identity template

An allocation becomes a strategy through **one function that exists twice** —
`identity_strat` in `copy.rs` (live engine) and `identityStrat` in
`identityStrat.ts` (browser + worker). Both produce a strat whose watchlist is
that one trader at weight 1. `identity.fixture.json` asserts both against the
same expected output, so a default changed on one side turns the other side's
suite red. Add a knob to one and you must add it to the fixture and the other.

Strat ids are **derived**: `copy-<address without 0x>`. Session key, ledger
bucket, persisted config and backtest card all agree from the address alone.
Never allocate one.

The same template is the **COPY TRADES** card on the STRAT HUB's recommended
shelf (`defaultStrats.ts`), which *imports* those constants rather than
retyping them — plain copy trading over the week's 5 best traders, buys and
sells both, no trade filters. Forking it gives the desk's strategy as an
ordinary strat; the browser's `$ DEPOSIT` screen funds several strats (and
copy-book leaders) in one pass, over `/live/start` and `/copy/start`
respectively. No new routes — if you are funding from an agent, keep using
`pm_copy_allocate` + `pm_copy_start`.

## Things that will bite you

**"It isn't trading" is DRY RUN, until proven otherwise.** Starting defaults to
dry run — every mirror computed, none placed. Check `autoExecute` on the row
before any other theory. `pm_copy_book` puts it on every trader.

**A leader's own P&L does not mean they are copyable.** Read the `funnel` on
their backtest: entries observed → entries copied, and which gate blocked the
rest. Most high-frequency leaders trade sub-hour Up/Down candles, which resolve
before a poller can react; `minMinutesToClose: 60` refuses them by default, and
that refusal is a measured result, not a limitation to tune away. `pm_trader`
reports the share of a leader's flow that is candles — check it *first*.

**Rank on `walkForward.verdict`, not on pnl.** `held` is the only pass:
profitable in the window before the card's *and* in the card's own. `faded`
made money once.

**A pnl with a large `unsettled` is a hypothesis.** Those legs were valued at
the last observed price. Leaders sell winners and let losers expire, so an
unresolved mark reads high.

**`sizing: "flow"` is the default for a reason.** `bankroll` copies the
leader's fraction-of-net-worth, which for a whale is a number below every order
floor there is — a small book copying them under `bankroll` places nothing at
all and looks broken.

**An underfunded leg takes NO position, not a small one.** Below its floor
every proportional mirror lands under the CLOB minimum and the upscale clamp
refuses it (`SUB_SCALE`), so the money sits in cash while the session shows
"running". A basket reports this as `legsTrading`/`idleUsd`; `floors: true`
names the smallest amount each dead leg would need. Never read `$0.00 · 0 TXS`
as break-even.

**A basket is sleeves, not a pool.** Each leg replays on its own capital
because that is what the desk runs — one allocation, one session, one budget.
Do not "improve" it into a shared wallet: the per-leg numbers would then depend
on the order the other leaders filled in.

**Explicit `0` is a value.** `stopLoss: 0` means no stop-loss. Use `??`, never
`||`, anywhere these knobs are defaulted.

**Real execution over MCP is off unless opted in.** `pm_copy_start` with
`autoExecute: true` is refused unless `POLYMARKET_MCP_ALLOW_LIVE=1`. Don't work
around it — dry-run, then tell the human to flip it on the desk.

**The app and API sleep.** The fleet activator stops both after ~60s idle;
requests through `localhost:9000/polymarket` wake them, direct calls to
`:50091` / `:3091` do not. "Connection refused" usually means asleep, not
broken.

## Building

```
cd src/api && cargo test && cargo build --release   # then pm2 restart polymarket-api
cd src/app && npx tsx app/lib/strats/__test__.ts    # parity, incl. the identity fixture
cd src/app && npx tsx app/lib/__test__.ts          # replay engine + the basket's sleeves/splits/floors
cd src/app && bash build.sh                         # NEVER bare `next build`
```

`build.sh` builds into `.next-staging` and swaps atomically — building in place
under a live `next start` strands open tabs on chunk hashes that no longer
exist (that is what "the buttons stopped working" means here).
