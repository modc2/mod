# polymarket

**Copy Polymarket traders, with an amount against each name — as many of them as you like, filtered in plain language, with a board that says what actually landed in your wallet.**

You pick a trader. You put dollars behind them. The engine mirrors their fills with that money, and the ledger tells you what that name — not "the portfolio" — actually made. That list of names and amounts is the **copy book**, and the screen that edits it is the **COPY DESK** at `/polymarket/copy`, the console's front door.

Everything else in this module is machinery under that desk: a strategy engine, a backtest worker, a live copy engine, a Rust API and an MCP server. It is documented below because when a leader isn't being copied, the answer is always in there.

```
   a trader you want to copy
            │
            ▼
   ┌──────────────────┐     the IDENTITY TEMPLATE      ┌────────────────────┐
   │  allocation      │ ─────────────────────────────► │  identity strat    │
   │  0xab… → $250    │   one leader, weight 1,        │  id: copy-<addr>   │
   └──────────────────┘   this row's capital+gates     └─────────┬──────────┘
            ▲                                                    │
            │                                        ┌───────────┴───────────┐
   ┌────────┴─────────┐                              ▼                       ▼
   │ COPY DESK  (UI)  │                     ┌─────────────────┐    ┌──────────────────┐
   │ pm_copy_* (MCP)  │                     │ backtest worker │    │  live engine     │
   └──────────────────┘                     │ walk-forward    │    │  DRY RUN / LIVE  │
        one book, on the server             └─────────────────┘    └──────────────────┘
```

### The copy book

`~/.mod/polymarket/copy/book.json` — server-owned, plaintext, owner-gated (`api/src/copy.rs`). One line per leader:

```json
{ "address": "0xab…", "label": "SPORTS SHARP", "allocationUsd": 250,
  "enabled": true, "params": { "maxTrade": 40 }, "notes": "why I'm copying them" }
```

**The amount is the whole position-sizing model.** The live engine budgets against it; the backtest replays with it. Change it and you change both.

It lives on the server rather than in the browser for one reason: saved strats sync **encrypted** with a key the browser never uploads (`api/src/strats.rs`), so nothing outside that one tab can read them — and an agent that cannot see what it is copying cannot reason about it. The book holds no keys and no wallet state, only addresses, dollars and a few tunables, and every route to it is already behind the owner-only access gate.

### The copy book, in the sidebar

The book is a right-hand column on **every page** (`app/components/UserSidebar.tsx` → `CopyPanel.tsx`), opened from the sidebar handle or the wallet chip in the top-right. It is built around a ROSTER, not a row — a desk is a set of traders with different amounts behind them, and every control here acts on as many of them as you check:

1. **Select** — paste one address, or paste a whole list: every `0x…` in the blob is added, each with the amount beside it, so "copy these five" is one gesture rather than five. `▦ FIND` goes to the full desk and its market search.
2. **Fund** — the `$` on each row *is* the sizing model. Check any number of rows and the bulk bar appears: `SET EACH $N`, `▶`, `■`, `PAUSE`, `RESUME`, `DROP`, or hand the selection to the `BASKET` screen. Desk-wide there is `BANKROLL` + `SPLIT EVEN`, and `▶ START ALL` / `■ STOP ALL` behind one TEST·LIVE switch.
3. **MEASURE** — `$N` over `1D · 3D · 7D · 14D · 30D`, per row, through the same `identityStrat → runBacktest` pipeline the desk cards and the live engine use.
4. **MY COPY TRADES** — the join (below): what the leaders did, what landed in my wallet, and the coverage number over both. The sentence box filters it, and `ARM AS GATE` turns the same sentence into the rule the checked traders copy under.

Three deliberate restraints:

- The block only mounts while it is **expanded**, and MEASURE / MY COPY TRADES are separately collapsible and separately mounted. A docked column is open on every page; a book poll, a replay sweep and a wallet walk on every mount would be a request storm for something nobody is looking at.
- The sim amount starts **blank**, meaning "each row at its own allocation" — the signature the background worker already replayed, so the default view paints from its cache. Typing an amount is what asks this browser to re-replay, an explicit act with a cost.
- Dense controls carry `btn-xs` / `input-xs` (`globals.css`). That file loads *after* `@tailwind utilities`, so `.pixel-btn` (13px, 8×14 padding) beats a `text-[9px] px-1.5` on the same element — the two-class variants are how a 340px column gets buttons its own size instead of desk-sized ones that wrap.

Everything it renders is server state (`GET /copy/book`, `POST /copy/*`), so an allocation an agent moved over MCP appears here on the next poll and vice versa.

### MY COPY TRADES — did any of it actually land?

`/copy/trades` (and the compact copy of the same component in the sidebar) answers the one question a backtest card and a green RUNNING dot both dodge: **of the trades the leaders made, how many are in my wallet?**

Nothing upstream links the two halves. A fill is a row in my wallet's activity and carries no leader tag, so the join is inferred, in `app/lib/copyTrades.ts` — same market (conditionId, else the title), same side, my fill at or *after* theirs within `matchMinutes` (30 by default), nearest wins, and one leader trade can be claimed only once. What comes out:

| | |
|---|---|
| `COVERAGE` | copied ÷ their trades. The headline. A desk that looks busy and copies 3 of 60 is the failure mode this module keeps re-finding. |
| `LAG` | median seconds between their fill and mine. |
| `SLIP` | what the lag cost, in cents signed against their price (positive = I paid up for being late). |
| `UNATTRIBUTED` | fills of mine with **no** leader behind them — the engine's own stop-loss/take-profit exits, or hand trades from the same wallet. Reported, never quietly credited to a leader. |

The full board adds a per-leader table (who I keep up with and who I don't) and `MISSED`, which is the useful half: every leader trade with no fill behind it, next to the `⊘` that says so.

Cost note: the leaders' side is read out of the **worker's feed store** through `app/api/copytrades/route.ts` — the same bytes `/api/hub` and `/api/basket` replay over — so a ten-name desk costs one wallet walk, not eleven paginated `/activity` walks (see the offset ceiling). A leader nobody has fetched yet comes back in `warming`, which the screen prints as a fact about the *cache*, not about the trader.

### Filtering trades the way you'd say it

`app/lib/semanticFilter.ts` turns one line of English into a filter — and then back into a gate the live engine already enforces.

```
big buys on crypto under 30c        →  BUY · ≥$500 · ≤30¢ · CRYPTO
politics, not candles               →  POLITICS · NOT CANDLES (screen-only)
missed longshots last 3 days        →  MISSED · ≤15¢ · LAST 3D
sports coin flips over $200         →  SPORTS · 40–60¢ · ≥$200
```

Why it exists: the console's older `marketQuery` matches a market **title** literally, and almost no Polymarket title contains the word "crypto" — the flow is "Bitcoin above $110,000", "ETH up or down", "Will SOL…". So the parser expands a concept lexicon (crypto → btc/eth/sol/…, sports → nba/nfl/lakers/…, candles → "up or down"/5m/…) and pulls the attribute clauses out of the same sentence: a side, a price band (including `longshots`, `favorites`, `coin flips`), a notional band (`whales`, `dust`, `over $500`), a window, an outcome leg, a leader, and a `copied`/`missed` status.

Two properties make it worth trusting:

- **Every clause it read is a chip**, with the terms it expanded to in the tooltip. A filter that silently drops 90% of a feed is indistinguishable from a broken feed.
- **`compileGate()` emits the engine's own dialect** — a `marketQuery` in the exact comma-OR / space-AND form `lib/marketQuery.ts` and its Rust mirror `market_matches_query` parse, plus a `TradeFilters` for the attribute half. `ARM AS GATE` writes that pair onto the checked allocations (`params.marketQuery` + `params.tradeFilters`), so the sentence you filtered your history with becomes the rule the session runs under, with no second matcher invented for the browser. A test pins that the two agree title for title.

Anything that **cannot** be expressed that way — the time window, an exclusion (`not candles`), `missed`, a leader filter — comes back in `viewOnly` and is shown dashed and dimmed. The screen never implies the engine is enforcing something it isn't.

### The identity template

An allocation is not a special kind of thing. It is materialized into an **identity strat** — an ordinary strategy whose watchlist is exactly one trader at weight 1, with `identity` set to their address — and from there it runs on the same engine, the same backtest and the same ledger as everything else in this module.

Its id is **derived**, not allocated: `copy-<address without 0x>`. So the engine session key, the ledger bucket, the persisted config on disk and the backtest card all agree from the address alone, with no lookup table to fall out of sync — and "copy this trader" twice updates one allocation instead of starting a second session.

The template exists in two languages, `api/src/copy.rs::identity_strat` (live engine) and `app/lib/identityStrat.ts` (browser + worker), pinned against each other by `app/lib/strats/identity.fixture.json`. Change a default on one side and the other side's tests go red. That is deliberate — a backtest card promising something the live session doesn't do is the bug class this arrangement exists to close.

| default | value | why |
|---|---|---|
| `sizing` | `flow` | Copy the leader's **conviction** (your allocation across the capital they deployed), not their bankroll fraction. A $250 book copying a $2M whale places real orders under `flow` and *nothing at all* under `bankroll`: their 0.1%-of-net-worth bet is $2,000 of theirs and 25¢ of yours, below every floor there is. |
| `minMinutesToClose` | `60` | Refuse markets resolving inside the hour. Sub-hour Up/Down candles resolve before a poller can react — measured, not assumed. This gate blocks most high-frequency leaders, and the funnel says so when it fires. |
| `maxTradeAgeSec` | `300` | Never mirror a stale fill: after a fetch outage the backlog enters at prices the leader never paid. |
| `stopLoss` / `takeProfit` | `0.75` / `0.99` | Sell at 75% of entry rather than riding to zero; liquidate anything that runs to the top tick instead of leaving capital dead until resolution. Explicit `0` turns either off — it is a value, not an absence. |
| `pollMinutes` | `0.5` | 30s. Fast enough to reach a fill near the leader's price, slow enough not to draw 429s. The backtest aggregates at the same cadence, so it can't promise fills the engine never sees. |
| `minTrade` / `maxTrade` | `$1` / `$100` | Order floor (the CLOB's own hard floor) and per-order ceiling. |

Per-trader `params` override any of these and are a **patch** — an omitted knob keeps its current value, so an agent nudging `maxTrade` can't silently reset a stop-loss set in the browser.

### Backtesting one trader

Every leader on the desk gets their own card: the identity strat replayed over the window you pick, by the same background worker that replays everything else (`app/lib/server/hubWorker.ts`). The worker reads the copy book **from the API itself** each pass, so a leader added over MCP with no browser open still gets replayed on the next pass.

Three numbers, in the order they matter:

1. **`verdict`** — the walk-forward result: the same trader replayed over the window *before* the card's, with no knowledge of what came after. `held` is the only pass — profitable then, and profitable since. `faded` made money once. A leader ranked on P&L alone is ranked on one window.
2. **`funnel`** — entries observed → entries copied, with the gate that blocked each of the rest. "Flat" is usually "blocked": a leader whose whole flow is gated cannot be copied whatever their own P&L says.
3. **`pnl`** — and how much of it is `unsettled`: still open at the window's end, valued at the last observed price rather than a real resolution. A large unsettled figure makes the result a hypothesis. (Unresolved marks read *high* — leaders sell winners and let losers expire.)

### The basket — several traders, different amounts

`/polymarket/copy/basket`. One trader with one amount is a row; **a set of traders with a different amount against each** is the thing you actually have when you have money and a shortlist, and it is not the sum of N per-trader backtests.

Each leg is replayed on **its own capital** and the results are summed. That is not a modelling shortcut — it is what the deployment runs: one allocation is one live session with its own budget, its own ledger bucket and its own `maxOpenPositions`. A pooled replay would make every leg's number depend on the order the other leaders happened to fill in.

What the basket adds over reading N cards is the arithmetic of the **split**:

- **LEGS TRADING / IDLE CAPITAL** — an underfunded leg does not take a small position, it takes **no** position. Its proportional mirror lands under the CLOB's order floor, the upscale clamp refuses it (`SUB_SCALE`), and the money sits in cash for the whole window while the desk shows a green "running" pill. `$0.00 · 0 TXS` reads as break-even; it is not.
- **FIND FLOORS** — the smallest amount at which each idle leg would trade **at all**, walked over `10 · 25 · 50 · 100 · 250 · 500 · 1000 · 2500`. `null` means that leg never trades at any size on the ladder, which is a fact about the gate, not the money.
- **DID THE SPLIT PAY?** — the same names, the same total, the same window, divided **evenly**. If your conviction weights don't beat that, the panel says so instead of letting a good total launder a bad split.
- **SIZE THE BASKET** — the same split at $100 … $10,000. Copying is not linear in the money on one leader and it is not on five.
- **OVERLAP / CONCENTRATION** — two legs on one market are two real positions (the desk would place both orders), and a Herfindahl over the legs' P&L says when "the basket made money" is really "one name made money".

The roster **is** the results table: the row you type an amount into is the row that tells you what that amount did. Nothing is committed until **APPLY TO DESK**, which writes each leg through the same `/copy/allocations` route the MCP tools call and starts nothing.

The draft roster is browser-local (`lib/basketDraft.ts`) on purpose — it is a shopping list, not a position. `+ BASKET` on the FIND TRADERS board and on any trader's profile adds to it, carrying the amount and the gate you were looking at.

```bash
# the same replay, over HTTP (Next app port, owner Bearer)
POST /polymarket/api/basket {"legs":[{"address":"0xab…","allocationUsd":700},
                                     {"address":"0xcd…","allocationUsd":300}],
                             "days":7,"compare":true,"floors":true}
# …or replay what the desk already holds
POST /polymarket/api/basket {"fromDesk":true,"total":2000,"days":14}
```

### Going live

Starting **defaults to DRY RUN**: the engine computes every mirror it would place and places none. That default is load-bearing — the single most common "it isn't trading" report is a session that is dry-running exactly as asked. The desk labels each row `DRY RUN` / `LIVE` / `PAUSED` / `STOPPED` and totals them (`RUNNING` vs `EXECUTING`) so the distinction is never inferred.

```bash
# the desk, over HTTP (owner Bearer required — see AUTH)
GET    /copy/book?eoa=0x…
POST   /copy/allocations         {address, allocationUsd, label?, enabled?, params?}
DELETE /copy/allocations/{addr}?eoa=0x…
POST   /copy/rebalance           {bankroll, mode: "equal"|"weighted"}
POST   /copy/start               {eoa, address?, autoExecute?}   # autoExecute omitted ⇒ DRY RUN
POST   /copy/stop                {eoa, address?}
GET    /copy/strats              # the book as identity strats — what the worker replays

# the BASKET (Next app port): several traders, a different amount each, one replay
POST   /polymarket/api/basket    {legs:[{address, allocationUsd}], days, compare?, floors?}

# MY COPY TRADES (Next app port): their trades joined to my fills
GET    /polymarket/api/copytrades?days=7&q=big+buys+on+crypto+under+30c
```

`rebalance` with `mode: "weighted"` rescales the amounts you already set rather than flattening them, so conviction survives a deposit.

## Capabilities

- **Market Data**: Search, list, filter, and sort prediction markets by volume/liquidity/end date
- **Live Price Ticker**: Slim auto-scrolling tape above every page — top 24 markets, polls every 8s, Δ since last poll with up/down arrows, paused while tab is hidden
- **Trading**: Place limit and market orders via Polymarket CLOB (requires wallet + API credentials)
- **Copy Trading**: Track top traders by PNL/volume, view their positions and activity
- **Many leaders at once**: the sidebar book takes a pasted LIST of addresses (every `0x…` in the blob, one amount each), and a checkbox on every row drives a bulk bar — fund each, start, stop, pause, drop, or hand the selection to the basket sizer
- **MY COPY TRADES** (`/copy/trades`): their trades joined to my on-chain fills by market+side+time, with `COVERAGE` (what share of their flow I actually got), median `LAG`, signed `SLIP` in cents, per-leader roll-up, and every `⊘ MISSED` trade next to the reason it reads as missed
- **Plain-language trade filter**: "big buys on crypto under 30c", "missed longshots", "politics, not candles" — a concept lexicon so `crypto` reaches a title that only ever says *Bitcoin*, chips for every clause it read, and `ARM AS GATE` to compile the enforceable half onto real allocations (`marketQuery` + `tradeFilters`)
- **Proportional copy sizing**: mirrors are sized as `leader$ × (accountValue × weightFraction) / leaderBankroll` — the fraction of net worth the leader risked, applied to yours. `accountValue` (free cash + mark value of the strat's positions) and each leader's bankroll (their positions + free USDC) are re-read every cycle, so sizes track the account as it grows or draws down, and a $10k conviction entry copies 100× larger than a $100 punt. Guardrails, all defaulted on: `maxUpscale` 2× (a mirror that could only be placed by inflating it past the order floor is refused as `SUB_SCALE`, not silently placed at the minimum), proportional exits (leader sells 40% of their shares → the strat sells 40% of its own; leader flat → strat flat), `minMinutesToClose` 60m (sub-hour Up/Down candles resolve before a poller can react), `maxTradeAgeSec` 300s, and a BUY budget bounded by real wallet cash rather than the `capital` config. The ratio and clamps are pinned across TypeScript and Rust by `parity.fixture.json`, so the BACKTEST tab previews the sizes live will place
- **Strategy Index** (`/strats`): Build/edit a basket of traders, set capital + rebalance cadence, then go live. A pre-flight `CHECKLIST` sits at the top of the page — wallet, CLOB auth, strategy, traders, rebalance, capital — and goes from `4/6 complete` → `6/6 · ready to go live` as the user fills each gap
- **CLOB refresh-from-UI**: When the checklist's `CLOB AUTHENTICATED` row is unchecked, an amber `refresh` pill fires `authenticate()` (single MetaMask sig → derived API key) inline — no page hop
- **Wallet Funding Panel**: Source picker (network ▾) + asset chips that each show their **live balance** so you can see what you'd be spending before clicking. Polls every 30s + manual refresh; chips wrap onto their own row in narrow sidebar mounts so they're always visible
- **Trading-ready dot** on the wallet chip: 🟢 connected + CLOB authed · 🟡 connected · ⚪ disconnected
- **Portfolio**: View positions, P&L, open orders
- **Scraping**: Background price/trade history scraper with SQLite storage
- **Backtesting**: Run threshold-based backtests on stored historical data
- **Trader FILTER + freshness gate**: A strat ranks its own watchlist every scan (`score` = P(win)×ROI, `sharpe`, `roi`, `winRate`) and copies only the top N. `maxStaleHours` adds the other half of roster rot — a trader who simply *stopped* keeps excellent 30d numbers, so freshness is a separate cut. See [The freshness gate](#the-freshness-gate-maxstalehours)
- **Categories**: politics, sports, crypto, **btc**, pop-culture, business, science, tech, ai — `btc` is a bitcoin-only sub-slice of `crypto` (dated price markets *and* the 5-minute Up/Down candles), so a BTC strat ranks traders on BTC flow alone instead of every altcoin book

## UI / Theme

Moved away from the original Mario `Press Start 2P` pixel theme to a vibey modern dark stack:

| Slot | Font | Used for |
|---|---|---|
| Body | **Inter** (`font-pixel`, `font-sans`) | UI text, labels, buttons |
| Mono | **JetBrains Mono** (`font-mono`) — tabular nums | prices, balances, addresses, timestamps |
| Display | **Space Grotesk** (`font-display`) | headlines, branding accents |

Global tokens in `globals.css`:

- `--radius-sm` 6px, `--radius` 10px, `--radius-lg` 14px, `--radius-xl` 18px — every bordered panel/button/input/chip rounds to these
- Native `button`, `input`, `select`, `textarea` get `border-radius: var(--radius)` automatically — no per-component className edits needed
- Pixel-era inset 3px box-shadows replaced with soft `0 8px 24px rgba(0,0,0,0.45)` panel shadows + `linear-gradient(180deg, #141414 0%, #0e0e0e 100%)` backgrounds
- Heavy Game Boy CRT scanlines replaced with a subtle radial ambient vignette

Top bar simplified from a five-chip cluster (wallet · CLOB · token · split · panel) down to **wallet chip + profile menu**. Trading readiness is communicated by the wallet chip's dot color, not by separate chips. The dropped chips (`ClobChip`, `TokenChip`, `SplitButton`) still live on disk for re-mounting inside the profile menu later.

**Theming**: ten themes, picked from the swatch chip in the top bar and stored in `localStorage.poly_theme`.

| | | |
|---|---|---|
| `dark` **MIDNIGHT** (default) | `light` **DAYLIGHT** | `matrix` **MATRIX** |
| `neon` **NEON** | `ember` **EMBER** | `abyss` **ABYSS** |
| `warp` **WARP** | `paper` **PAPER** | `win95` **WIN95** |
| `mario` **MARIO** | | |

`ThemeContext` stamps two attributes on `<html>`, and `THEMES` there is the single source of truth (picker + boot script + classification):

- `data-theme` — the palette. Each id has one `[data-theme="id"]` token block in `globals.css` setting only what differs from its base.
- `data-base` — `dark` or `light`. All the generic light-mode legibility rules key on this, so a new light theme inherits them for free.

Every color flows through CSS vars (`--bg`, `--fg`, `--panel-from/to`, `--border`, `--grid-line`, …). Two indirections make a theme switch reach the whole UI without touching components:

- channel-style `--pixel-*-rgb` vars back the `pixel.*` Tailwind palette, so opacity modifiers like `text-pixel-white/60` keep working;
- the `green` / `red` / `amber` 300–500 shades are re-pointed in `tailwind.config.js` at `--up-rgb` / `--danger` / `--warn`, so the ~700 existing `text-green-400`-style classes mean *gain / loss / warning* in every theme rather than a pinned emerald.

Adding a theme = one entry in `THEMES` + one token block in `globals.css`. WIN95 (bevels), MARIO and WARP (`Press Start 2P` on buttons/badges) also carry a handful of shape rules under `── Per-theme chrome ──`.

Component-size sweep: every `text-[8–14px]` across all `app/components/*.tsx` and `app/**/page.tsx` was bumped one step up (8→11, 9→12, 10→12, 11→13, 12→14, 13→15, 14→16, 18→26) so Inter has room to breathe.

Alignment + legibility pass:

- `.pixel-table td` nowraps and ellipsises every cell — right for the dense leaderboards, wrong for reference tables, where it cut every description mid-sentence. Docs tables opt into `.pixel-table.wrap-prose` (wrap, `vertical-align: top`) and the docs `FieldTable` gives its prose column half the width
- Market cards reserve 3 question lines and always lay out the conviction row, so price bars, chips and footers land on one baseline across a grid row instead of drifting per card
- `formatVolume` / `formatPnl` bucket on the *rounded* value (`>= 999.5`, not `>= 1_000`) — a volume of 999.6 used to print `$1000` in a column of `$10.0K`s
- The `BuildBadge` CID chip rests at 45% opacity (full on hover / while publishing) and `<main>` carries `pb-14`, so build provenance stops sitting on top of the last table row
- Empty states are the CTA: STRAT's "NO TRADERS YET" is a dashed-icon panel with one **BROWSE TRADERS** button, replacing a line of copy that pointed at the panel it sat under

## Usage

### Python
```python
import mod as m
p = m.mod('polymarket')()

# Read-only
p.search("election")
p.trending(limit=20)
p.markets(limit=100, order="volume")
p.market("0x...")                   # single market by condition_id
p.by_liquidity(limit=20)
p.ending_soon(limit=20)
p.events(limit=50, tag="crypto")
p.orderbook("0x...")                # order book by token_id
p.midpoint("0x...")                 # midpoint price
p.tags()                            # all categories

# Trading (requires private_key)
p = m.mod('polymarket')(private_key="0x...")
p.auth()                            # derive CLOB API credentials
p.buy("0x...", price=0.5, size=10)
p.sell("0x...", price=0.7, size=10)
p.market_buy("0x...", size=10)
p.market_sell("0x...", size=10)
p.positions()
p.open_orders()
p.cancel(order_id)
p.cancel_all()

# Scraping
p.discover(count=50)                # auto-track top markets
p.scrape(interval=60)               # start background scraper
p.scrape_status()
p.stored_prices("0x...", start=0, end=9999999999)
p.stored_trades("0x...")
p.store_stats()

# Backtesting
p.backtest(start=0, end=9999999999, strategy="threshold",
           buy_threshold=0.3, sell_threshold=0.7,
           initial_capital=1000, position_size_pct=10)

# Server
p.serve()                           # start API + Next.js app
p.serve(api_only=True)              # API only
p.kill()                            # stop all services
p.status()                          # check service status
```

### CLI
```bash
m polymarket/search query=election
m polymarket/markets limit=20
m polymarket/trending limit=10
m polymarket/by_liquidity limit=10
m polymarket/ending_soon limit=10
m polymarket/orderbook token_id=0x...
m polymarket/buy token_id=0x... price=0.5 size=10
m polymarket/sell token_id=0x... price=0.7 size=10
m polymarket/positions
m polymarket/open_orders
m polymarket/backtest start=0 end=9999999999 strategy=threshold
m polymarket/scrape interval=60
m polymarket/scrape_stop
m polymarket/sync hours=6
m polymarket/serve
m polymarket/kill
m polymarket/status
m polymarket/test
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /health | Health check |
| GET | /markets | List markets (params: _limit, order, active, end_date_min, end_date_max) |
| GET | /markets/{condition_id} | Get single market |
| GET | /search | Search markets (params: q, _limit) |
| GET | /trending | Trending by volume |
| GET | /orderbook/{token_id} | Get order book |
| GET | /positions | Get positions (params: user) |
| GET | /trades | Get trade history (params: user) |
| POST | /order | Place limit order (auth required) |
| POST | /market-order | Place market order (auth required) |
| POST | /backtest | Run backtest |

## Structure

```
polymarket/
├── config.json              # mod config (ports 50091/3091)
├── skill.md                 # this file
├── polymarket/
│   └── mod.py               # Python module (Polymarket class)
└── app/                     # Next.js 14 app (Mario-themed terminal)
    ├── app/
    │   ├── page.tsx          # main page (markets, copy trading, portfolio tabs)
    │   ├── docs/page.tsx     # API documentation page
    │   ├── layout.tsx        # root layout with CRT overlay
    │   ├── globals.css       # pixel/Mario theme CSS
    │   ├── components/
    │   │   ├── TopBar.tsx                # logo + nav + search + wallet chip + profile menu
    │   │   ├── MarketTicker.tsx          # live-updating price tape (8s poll, marquee, Δ chips)
    │   │   ├── MarketCard.tsx            # market row (question, YES/NO bars, price-flash on change)
    │   │   ├── MarketsGrid.tsx           # paginated market list (silent 15s re-poll feeds flashes)
    │   │   ├── TradePanel.tsx            # order placement (limit/market, YES/NO)
    │   │   ├── CopyTrading.tsx           # top trader leaderboard
    │   │   ├── CopyIndex.tsx             # strategy basket editor (right sidebar host)
    │   │   ├── PreconditionChecklist.tsx # /strats top-of-page checklist + CLOB refresh button
    │   │   ├── LivePanel.tsx             # go-live engine controls + per-cycle log
    │   │   ├── WalletChip.tsx            # connect/disconnect + trading-ready dot
    │   │   ├── WalletFundingPanel.tsx    # network ▾ + asset chips (each with live balance)
    │   │   ├── PositionsTable.tsx        # portfolio positions
    │   │   ├── PnlChart.tsx              # cumulative PnL chart
    │   │   ├── ProfileMenu.tsx           # right-sidebar toggle ("PANEL ▶")
    │   │   └── AuthPanel.tsx             # SIWE / CLOB auth panel
    │   ├── context/
    │   │   └── AuthContext.tsx # wallet + CLOB auth state
    │   ├── lib/
    │   │   ├── types.ts            # TypeScript interfaces
    │   │   ├── polymarket.ts       # API helpers, categories, normalization
    │   │   ├── useLiveMarkets.ts   # hook: interval poll + prev-price diffs (powers ticker + flashes)
    │   │   ├── networks.ts         # multi-chain network configs + RPC fallback
    │   │   ├── lifi.ts             # LiFi bridge quote / execute
    │   │   ├── clobClient.ts       # L2 HMAC-signed CLOB calls (balance, orders, cancel)
    │   │   ├── copyEngine.ts       # live trading engine (rebalance loop, fills)
    │   │   ├── stratSync.ts        # persisted strategy CRUD
    │   │   └── auth.ts             # EIP-712 signing, credential derivation
    │   └── api/
    │       ├── polymarket/route.ts # proxy to Gamma/Data API
    │       └── clob/route.ts      # proxy to CLOB API (auth forwarding)
    ├── tailwind.config.ts
    ├── next.config.mjs
    └── package.json
```

## Search

Search operates across two domains — **markets** and **traders** — through a three-layer architecture: Next.js frontend, Rust proxy with caching, and upstream Polymarket APIs.

### Market Search

1. User types a query in the `TopBar` search input
2. Query updates global `FiltersContext` state and syncs to the URL as `?q=<query>`
3. `MarketsGrid` calls `searchMarkets()` which hits the Rust proxy at `/?endpoint=public-search&q=<query>`
4. The proxy routes `public-search` to `gamma-api.polymarket.com/public-search`
5. Gamma returns `{events: [{..., markets: [...]}]}` — the frontend flattens this into a market list
6. Results are normalized and cached client-side keyed by `search_<query>_<limit>`

### Trader Search

1. Same `TopBar` input, same `FiltersContext` — the `search` value applies to whichever page is active
2. On `/traders`, `CopyTrading` passes the search param to `fetchTradersPage()` for server-side filtering
3. `matchTraderSearch()` matches against both wallet **address** and **market titles** the trader has positions in
4. The Rust backend's `/active-traders` endpoint serves trader data from a two-phase pipeline: leaderboard fetch from the Data API, then enrichment with per-trader activity scraping

#### Market-topic filter (`marketQuery` / URL `mq`)

A free-text topic filter, finer than the fixed `category` keyword buckets and — unlike `search` — never matched against the wallet address. Typing e.g. `bitcoin` or `price of bitcoin` into the **MARKET QUERY** box (traders → FILTERS) keeps only traders active in markets whose title matches *every* query token (stopwords like "of/the" dropped), and **recomputes each trader's P&L / volume / win-rate from only those markets** — so you see a trader's bitcoin-specific track record, not their overall numbers. Traders heaviest in the topic sort first. Shared matcher: `app/lib/marketQuery.ts` ↔ `src/api/src/categories.rs::market_matches_query` (keep in sync).

The same `marketQuery` lives on a **strat** (`SavedIndex.marketQuery`, edited via the STRAT panel's **MARKET** box): the backtest preview, the in-browser copy engine (`CopyTrader.shouldMirror`), and the backend live engine (`EngineConfig.marketQuery`) all only act on matching markets, so a strat stays focused on one theme instead of mirroring every fill a watched trader makes. Non-matching trades are still *observed* (visible in the log/rail) but never mirrored.

#### The activity lens (`maxLastTradeHrs` — default 6h)

Every leaderboard read lands on traders who have **traded in the last 6 hours**. A dormant wallet's 7-day P&L, Sharpe and win rate all stay excellent — they describe trades that already closed — so an unfiltered board ranks people you cannot copy above people you can, and an allocation against one of them fills nothing. `DEFAULT_ACTIVE_HOURS` (`app/lib/polymarket.ts`) is the one place that number lives; the strat-side twin is `maxStaleHours` (see [the freshness gate](#the-freshness-gate-maxstalehours)).

- **It runs on the server, over the cached aggregate.** `maxLastTradeHrs` and `minTrades24h` are `/active-traders?paged=1` parameters applied in `apply_pagination` before the sort and the page slice, so the whole board is filtered, `total` counts the rows you can actually see, and it is still a **cache read** — no re-aggregation. The response carries `activityDropped` so an empty board can say *why* it is empty.
- **It used to be a per-page filter.** The console applied both floors to the 50 rows the server had already handed it: a full page rendered as five rows, the pager still counted the unfiltered board and offered pages that were entirely empty, and the warm-cache path disagreed with the post-sync streamed one.
- **Where it shows up.** `LAST TRADE ≤ HRS` in traders → FILTERS (blank or `0` = the whole board); the `ACTIVE 6H` toggle in the COPY desk's FIND TRADERS panel; the seed roster a new strat forks with (`fetchTopTraderAddresses`, which falls back to the unfiltered top N rather than seed nothing); `pm_top_traders` over MCP and `m polymarket/active_traders` on the CLI, both with `active_hours=0` to switch it off.
- **Unknown recency is not recent.** A row with no `lastTradeTs` (a pre-`lastTradeTs` disk payload) is dropped while the lens is on — the opposite of the strat-side gate, where unknown passes. Here a wrong drop costs one row on a board of thousands; there it would pause a running strat.
- **A snapshot older than the window empties the board**, and no amount of relaxing the other filters fixes that. The console says so — it names the age and points at ↻ SYNC — but the real guard is the background warmup, which re-aggregates every 5 min by default (30 min on this deployment).

#### Trader profile — TRADES / P&L / INFO

A trader page (`/traders/<address>`) is three tabs over one filtered flow:

| Tab | What |
|---|---|
| **TRADES** | the fill tape, with an ALL / OPEN / CLOSED / POSITIONS view switch |
| **P&L** | MTM curve, daily activity, biggest win/loss, per-market closed results |
| **INFO** | address + links, window/sync provenance, buy-sell split, exposure, market mix, and exactly which filters are on |

The **FILTERS** button in the tab bar opens the same bar the TRADES tape uses (`app/components/TradeFilterBar.tsx` — side / entry-price band / USD size band / keyword chips / category buckets). It narrows *everything* on the page: the stat grid, the P&L curve, and every table, so the tabs can never disagree with each other. The side/price/size dimensions are the exact gate a strat copies flow through (`app/lib/tradeFilters.ts`); keywords are a UI-only OR-match on market title + outcome. The bar's gate is skipped entirely when nothing is set — an all-defaults `TradeFilters` would otherwise impose the strat-side 60¢ BUY floor and silently hide half the tape.

### Caching

Markets/search hit the Polymarket API live (short TTL). Trader data and historical data are **persisted to disk** on first fetch and never re-requested — survives server restarts, no risk of rate limits.

| Layer | What | TTL | Storage |
|-------|------|-----|---------|
| **Frontend** (localStorage) | Market search, price history, market trades, positions, wallet trades | Hourly (same-hour = no refetch) | Browser |
| **Rust proxy** (in-memory) | Markets, events, search | 5 min | Memory only |
| **Rust proxy** (memory + disk) | Trader activity, positions, price history, market trades, leaderboard | 24h memory / **indefinite on disk** | `/tmp/polymarket-proxy-cache/` |
| **Pipeline** (memory + disk) | Aggregated active-trader data | 1 hour ceiling, re-warmed every 5 min in background | `/tmp/polymarket-active-traders-cache/` |

**Persistent endpoints** (disk-cached on first fetch): `activity`, `positions`, `users/`, `trades`, `v1/` (leaderboard), `holders`, `value`, `prices-history`, `market-trades`

**Ephemeral endpoints** (memory-only, fine to re-hit API): `markets`, `events`, `public-search`, `book`, `midpoint`, `price`

The proxy serves stale cache on upstream errors and sets `x-cache: HIT|MISS|STALE` headers.

### Background sync

The API re-pulls the 1/7/14/30-day trader leaderboards on a timer of its own — **every 5 minutes by default**, running whether or not the console is open (`src/api/src/sync.rs`). Each cycle only re-fetches windows that are actually stale, and a panicking cycle is caught so the schedule survives a bad upstream payload.

5 minutes is the floor and a *start-to-start target*, not a promise: a full sweep of ~6k traders takes 8–10 minutes, so at the default the scheduler effectively never idles and cycles queue back-to-back. That is the intent — maximum freshness — but it keeps steady pressure on the Polymarket data-api, and a trader that gets 429'd mid-sweep drops out of that window until the next pass. Raise the cadence if you'd rather have a quieter upstream than the freshest possible leaderboard; copy trading is unaffected either way, since the live engine polls tracked traders on its own 60s loop.

The **owner** can change that cadence — from the AUTO chip in the TRADERS header, or over the API. It persists to `~/.mod/polymarket/sync.json` (off-tree, per-deployment) and applies immediately: a sleeping scheduler is woken and re-times against the new interval instead of finishing its old sleep. Range: 5 minutes – 7 days.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /sync/status | Cadence, last run + duration, next run, last error |
| POST | /sync/config | `{enabled?, intervalSecs \| intervalMinutes \| intervalHours}` |
| POST | /sync/run | Run one cycle now (bypasses the freshness skip) |

```bash
m polymarket/sync                # current schedule
m polymarket/sync hours=6        # every 6 hours
m polymarket/sync minutes=30     # every 30 minutes
m polymarket/sync enabled=false  # pause
m polymarket/sync now=true       # run one cycle now
```

`POLYMARKET_SYNC_INTERVAL_SECS` seeds the cadence on a deployment that has never been configured; after the first owner change, `sync.json` wins.

### Reading the live engine's heartbeat

Every cycle logs one line, and it now says what actually happened:

```
polled 1 traders · 4 new trades observed · 4 BUY(s) gated (4 price<60¢)
```

- **`N new trades observed`** counts the trades this cycle pulled *past each trader's cursor*. It used to count observed trades stamped after the cycle began — but a leader trade is timestamped when *they* traded, always before we polled for it, so that read `0` on virtually every cycle and a perfectly healthy engine looked asleep.
- **`N BUY(s) gated (…)`** names the gate that dropped this cycle's entries: `price<60¢` (the implicit favorites-only floor when a strat sets no price band), `price`, `side`, `size`, `category`, `market query`. A strat whose filters exclude 100% of its leaders' flow is a *filter decision*, not a fault — but silently dropping those candidates made it indistinguishable from a broken engine.

Candidates that clear the gates and are then skipped downstream still log their own `SKIP` line (`TOO_SOON`, `SUB_SCALE`, `REBALANCE_SKIP`, …).

### Warmed candidate pool (a trap)

The background sync aggregates each window at **`pool=2000`**, and the pipeline cache is keyed `days:minPerDay:pool`. A *paged* request for any other pool is a different key, and a cold paged key returns `{"cold": true, "traders": []}` instead of computing — by design, so a page load can't block on a 10-minute sweep.

So a leaderboard read that omits `pool` (server default 1000) gets an empty list **forever**, no matter how warm the cache is. That's what made forking a recommended strat seed zero traders. Every console read now asks for `WARMED_CANDIDATE_POOL` (`app/lib/polymarket.ts`); if you add another leaderboard call site, pass that constant.

### The STRAT HUB (`/strats`)

The front door to `/strats` is a card grid, and each card's headline is its **N-day backtest** — the same replay engine (`app/lib/backtest.ts`) the BACKTEST tab and the live engine share, run over the same window for every card, so the numbers are comparable. Cards used to print `lastPnl`, a leftover from whatever window that strat was last opened with.

- **The card face IS the equity curve.** Each card renders its replay's equity path full-bleed behind the numbers, so a wall of strats reads as a wall of shapes first: which one ran up, which one bled, which one never traded. While a replay is in flight the face is a shimmer instead — "computing" and "did nothing" must never look the same.
- **Window pills** (1D / 3D / 7D / 14D / 30D) re-measure the whole grid. Runs are cached per card *and* per window (`poly_hub_backtest_v1`), so switching back is instant; `↻ RERUN` forces a fresh pass in the browser *and* kicks the background worker. 30 days is the ceiling — see `MAX_LOOKBACK_DAYS`.
- **Every card carries its funnel**: `144/2630 entries copied · 2003× MAX/CYCLE cap (3)`. That one line is the answer to "why is this strat so quiet?" — see [Where the flow went](#where-the-flow-went-the-entry-funnel).
- **Search** filters saved strats and recommendations together, over names, descriptions and filter chips (`"sports"`, `"buys only"`, `"top 5"`). Every term must match.
- **RECOMMENDED strats are backtested too.** A template is materialized into exactly the `SavedIndex` forking it would create — seeded from today's leaderboard via `templateIndex` / `templateRoster` in `app/lib/defaultStrats.ts` — and *that* is what gets replayed, from the same cached roster the fork will use. The number on the card is the strat you actually get.
- Those rosters are picked *by* trailing P&L over the window they're then scored on, so a recommendation's number is survivorship-biased by construction. The section header says so: **upper bound, not a forecast**. A saved strat carries no such bias — its traders were chosen before the window it's measured over.
- A strat with nothing to copy reports the reason (`no traders to copy`, `all 1279 entries blocked · time-to-close`, `no price tape for this window`) instead of a `$0` that reads as breaking even.

### Funding several strats at once (`$ DEPOSIT`)

Funding a strat is arming it: the amount becomes the strat's `capital` and its engine session is started to size against that number. Done one strat at a time through the LIVE tab — which is how it worked — nothing ever showed you the **sum** you were committing next to the money you actually have, so eight strats could each claim $1000 of a $223 wallet without a word.

`$ DEPOSIT` (STRAT HUB header, and the strat sidebar's STRATS row) is that screen: tick the strats, give each an amount, and every one is allocated and armed in one pass. It is a budget screen first —

- **WALLET** is free USDC in the deposit wallet, and the panel says outright that every strat trades through that one wallet: allocations are budgets against it, not transfers. Nothing moves between strats.
- The **budget** you may commit is that free cash *plus the cost basis the selected strats already hold* (`fundingBudget` in `app/lib/multiFund.ts`) — re-arming a funded strat at its current size is not over-allocating, because that money is already its own. Every *other* running strat's money is deliberately excluded: it's committed to them.
- Over the budget, DEPOSIT is **refused**. When the balance can't be read it **warns** instead — unknown is not zero, the same rule the sidebar's cash readout follows.
- **SPLIT ▸ EVENLY / ALL IN** divide a number across the ticked rows to the cent (remainder cents to the earliest row, the same rule `equalWeightTraders` uses for weights).
- **COPY DESK leaders are rows here too.** Their dollars live server-side keyed by address rather than by strat id (`api/src/copy.rs`), so funding one is a different pair of calls (`/copy/allocations` then `/copy/start` for that leader) — but it is the same decision, so it is the same list.
- An amount above $0 means **real orders**, matching what GO LIVE does on a funded wallet. Sessions are started one after the other on purpose: ten engines launched at once each walk their watchlist's history and earn the wallet a 429 before a single mirror is placed. A row that's refused reports the engine's own words (`3 armed · 2 refused — give them dollars before starting`) instead of taking the successes down with it.

### COPY TRADES — the desk, as a template

The first card on the RECOMMENDED shelf is plain copy trading: mirror the 5 best traders of the last week trade for trade — their buys **and** their sells, so their exits are your exits — sized to the conviction behind each trade (`sizing: "flow"`) rather than to their net worth. It deliberately carries **no trade filters**: a side or price band would drop the leaders' SELLs and leave the strat holding positions its leaders have already closed. Every other card on the shelf is this idea with a gate on it.

Its parameters are *imported* from `app/lib/identityStrat.ts`, not retyped — the same template the COPY DESK turns each per-trader allocation into, and the one `copy.rs::identity_strat` runs live. Copy trading therefore means one thing in both places, and forking the card gives you the desk's strategy as an ordinary strat: fundable from `$ DEPOSIT` alongside the others, backtested on the same wall, editable.

### Strats that originate their own trades are backtested too

A momentum strat has no watchlist by design: it reads a market's own price tape and buys the outcome whose odds are rising. The copy replay walks leader fills, so for years it had nothing to walk — those strats printed `originates its own trades — no copied flow to replay` and got **no backtest at all**, while their live sessions traded every cycle. That was the largest gap between backtest and live in this console, and it covered both shipped BTC templates.

`app/lib/originationBacktest.ts` closes it by replaying the **engine cycle loop** instead of a fill feed. For every cycle between the window's start and its end, at the strat's own poll cadence, it:

1. redeems anything that resolved since the last cycle (live: `auto_redeem`),
2. checks take-profit then stop-loss on every hold (live: `check_stop_losses`),
3. shows the strat **only the markets live at that instant**, with points only up to that instant (live: `fetch_candle_series` resolves one candle by its slug off the clock),
4. calls the same `strat.propose()` hook the live engine calls,
5. executes exits before entries, under the same cooldown / order-cap / free-capital / share-rounding rules.

The tape comes from `app/lib/momentumTape.ts`. Candle strats are the honest case: `btc-updown-5m-<start>` names its markets deterministically, so a past window's candles are *enumerable* — batch them 20 slugs per gamma request, pull each candle's 1-minute price history for exactly its own lifetime, and settle on gamma's published outcome rather than a last mark. Search-mode tapes can only find markets that still exist today and say so (`survivorship-biased`).

Two things it deliberately does not model, stated on the panel rather than buried: the **bid/ask spread** (the copy replay skips it too — fills book at the observed price, with the strat's limit deciding only whether the order was marketable) and **sub-minute price action** (1-minute bars are the CLOB's finest; a live session polls a 5-minute candle every 30s and also reads the midpoint). `PriceTape.fidelityMs` and the `120/289 CANDLES` chip carry both.

The first honest run of BTC 5-MIN DELTA over a day: **−70% on $100 across 245 trades, `no-edge` on the walk-forward.** That result existed all along; there was just nothing measuring it.

### Strat chat — say what you want, apply the patch

Every strat row has an **ASK** button. You describe the change in words ("stop buying longshots", "why did this only trade four times?") and an agent answers in the strat's own terms, proposing a patch when the ask implies one:

```
PROPOSED CHANGE
maxTrade: 10 → 3
momentum.minRiseCents: 5 → 8
stopLoss: 0.8 → 0.9
[APPLY] [DISMISS]
```

Three things bound it. The route (`app/api/strat-chat/route.ts`) is **owner-gated** with the same Bearer token `/api/hub` uses. The patch is validated against `app/lib/stratPatch.ts` — a whitelist of paths, types and ranges — on the server *and* again in the browser, and anything rejected is shown (`stopLoss: 5 is above the 1 maximum`) rather than swallowed, because an agent confidently inventing a setting is the thing you want to see. And **nothing is written until you press APPLY**: the agent has no tools, cannot touch the watchlist, cannot place an order and cannot start a session.

The agent is briefed from the same specs the validator enforces (so it can't be told about a field that would then be rejected) plus the strat's current settings, its latest worker backtest — funnel, walk-forward verdict, tape coverage — and its live session. That's what lets "why so few trades?" be answered with the strat's own numbers. It runs through the `claude` CLI already on the box (`POLYMARKET_CHAT_MODEL`, default `claude-opus-5`); when the CLI is missing the panel says so instead of inventing an answer.

### Is it still profitable? — the walk-forward badge

A backtest over one window answers the wrong question. Over *any* single window some strat printed a great number, and a wall of cards sorted by P&L sorts exactly those to the top. So **every card is backtested twice**: once over its own window, and once over the equal-length window immediately before it — then the two are put side by side.

```
1D BACKTEST                    12 TR · ⟳ 1m ago
+$133.29  +13.33%
↗ TURNED UP  prior day −$15.79 → +$133.29
7/2022 entries copied · 1945× trade filters
```

The prior window is replayed with the clock wound back. `BacktestInput.asOf` (`app/lib/backtest.ts`) moves the window end, and **everything** derives from it: the flow the sim copies, the 30-day trader stats it scores that flow with, the `StratHistory.now` its gates date markets against. A trade one second after `asOf` is invisible to it. The `asOf: the wound-back replay cannot score on future results` case in `app/lib/__test__.ts` pins that — a leader whose only closed round trip happens *after* the window executes nothing, with `no scoreable edge` as the stated reason.

The one thing that *is* taken from today is how the markets **resolved**. Those value the past window's inventory; they don't inform its decisions — which is the point of a walk-forward: yesterday's choices, scored by what actually happened.

Seven verdicts, one pass (`forwardVerdict` in `app/lib/hubReplay.ts`):

| verdict | prior window | this window | reading |
|---|---|---|---|
| `held` ✓ | profit | profit | the only pass — the headline survived out of sample |
| `faded` ✗ | profit | loss | **the expensive one** — what a strat fitted to one good window looks like |
| `recovered` ↗ | loss | profit | one good window after a bad one; needs a second confirmation |
| `no-edge` ✗ | loss | loss | nothing to deploy |
| `stalled` ⏸ | profit | no trades | didn't lose — went quiet. Read the funnel line, it's a gate |
| `untested` ? | no trades | any | no edge to confirm; this card rests on one window |
| `idle` · | no trades | no trades | nothing to judge yet |

- **`✓ HELD ONLY`** (next to the window pills) hides every card that isn't `held`. A card with no walk-forward yet does *not* pass the filter — "we haven't checked" must never render as "confirmed".
- The header tallies the shelf: `✓ 1/9 HELD`. It is usually a small number. That is the finding, not a bug — copying a leader at a lag is structurally hard, and this is the first surface in the console that makes it impossible to miss.
- The second replay costs **CPU only** — both windows read the same already-fetched 30-day feed, and one resolution lookup covers both. `POLYMARKET_HUB_FORWARD=0` turns it off in the worker.
- Over MCP, `pm_backtests` returns `forward: {verdict, confirmed, prior_pnl, prior_roi, prior_trades, prior_window}` on every row. Ranking by `pnl` alone ranks by a single window.

### The background backtest worker

Backtests are **cached, and refreshed by a worker that runs whether or not the console is open** (`app/lib/server/hubWorker.ts`, started from `app/instrumentation.ts` when the Next server boots). Opening `/strats` then paints real numbers on the first frame instead of firing a dozen paginated `/activity` walks and watching cards trickle in.

It is **two loops, and the split is the whole point**: replaying is cheap and local, fetching is expensive and rate-limited, so they run on different clocks.

```
console ──POST /polymarket/api/hub {strats}──► manifest.json     (which strats to replay)

FETCH  loop ──every 10m──► stalest traders only, 1 page each ──► feeds/<addr>.json
REPLAY loop ──every 30m──► hubReplay → backtest, over those ──► backtests.json
                                                        (~/.mod/polymarket/hub/)
console ──GET  /polymarket/api/hub?days=1─────────────────────► paints instantly
```

- **The replay never fetches.** It reads `~/.mod/polymarket/feeds/<addr>.json` — the same 30-day trade window the browser keeps in localStorage, but on disk and surviving restarts (`app/lib/server/feedStore.ts`). In the steady state a full pass costs **zero** upstream requests, which is why it can run every 30 minutes instead of every 2 hours.
- **The fetch loop is the only thing that talks to data-api** (`app/lib/server/feedFetcher.ts`). It syncs the *stalest* traders first, at most 40 per cycle, ≤2 concurrent with a 400ms gap, and incrementally: `fetchWalletTradesIncremental` pages `/activity` until it hits a trade already in the store — one page per trader per cycle, not the sixty a cold 30-day walk takes. Failures back off 1m → 4m → 16m → 1h.
- **This is what fixed the 429s.** The old single loop fetched *and* replayed every 2 hours, and it missed every cache by construction: the Rust proxy holds `/activity` for one hour (`api/src/cache.rs`), and `app/lib/cache.ts` is localStorage — a no-op in Node. So every pass re-walked a paginated 30-day feed for every trader of every strat.
- **A half-warm cache says so.** A card replayed while some of its traders had no cached history carries `warming: N` and a `partial — N/M traders still warming` note, and the hub header shows `⧗ warming 12/40`. A number derived from data we never fetched is a floor, and it must not be printed as a flat result.
- **The worker runs the app's own engine.** `hubReplay.ts` → `backtest.ts` → `strats/strat.ts` — the exact modules the console and the BACKTEST tab run. That's why it lives inside the Next server rather than as a separate service: a second build of the engine is how backtest and live drifted apart last time (`strats/parity.fixture.json`).
- **It authenticates as the owner.** Every API route is behind the access gate, so the worker mints the same `pma1.…` token the console's sign-in issues, from `~/.mod/polymarket/server.secret` (`app/lib/server/ownerToken.ts`). No gate, no worker — it fails closed and records why.
- **The window is 1 day** (`HUB_BACKTEST_DAYS`), which is what "which of these is working *right now*" wants. Other windows are replayed in the browser on demand — and cost nothing extra upstream, because the store already holds 30 days.
- Template rosters are cached to `hub/rosters.json` for 3h; feeds for traders that leave every roster are deleted after a week of not being read.
- The browser still fills gaps: a strat edited since the last pass, or a window the worker doesn't cover, is replayed locally and merged newest-wins.
- `POLYMARKET_HUB_WORKER=0` disables both loops. `POLYMARKET_HUB_BACKTEST_MINUTES` / `POLYMARKET_HUB_REFRESH_MINUTES` retune the cadences. `PUT /polymarket/api/hub` runs a replay synchronously (what the MCP tool uses); `POST …?run=1` queues one; `POST …?refresh=1` queues a fetch cycle — the only one of the three that spends upstream budget.

### What the replay is allowed to claim (legs + settlement)

Two things decided how much a backtest was worth believing, and both of them were wrong.

**1. A market is two tokens, not one.** `conditionId` names a market; the tradable assets are its outcome tokens (Yes / No), which have separate books and opposite payoffs. The live engine has always keyed `EngineState.positions` by `token_id`. The backtest and the FIFO P&L engine keyed their books by `conditionId`, so both legs collapsed into one position: a 6¢ No hold got marked at the 94¢ the Yes leg last printed, and a Yes exit closed No shares and booked the difference as profit. **19% of the markets in the cached leader feeds have both legs traded.** Everything that books inventory now keys on `legKey(conditionId, outcome)` (`app/lib/leg.ts`).

**2. A position nobody sold has to be valued — and the last observed price is a biased guess.** A copy replay only sees the leaders' fills. Leaders trade their winners on the way up and simply *let their losers expire*, so a loser's last print is its entry price: mark inventory there and every winner books while no loser ever does. This is the backtest twin of the live bug where the ledger read +$96 against a wallet holding $0.70.

`app/lib/server/resolutionStore.ts` fixes it with ground truth — gamma's resolved `outcomePrices`, cached forever (a resolution is immutable), refreshed on a backoff for anything still open, and never written as a negative when a lookup merely *failed*. The sim settles a dead leg at what it actually paid ($1 or $0) and reports the split:

```
settlement: { resolved, resolvedUsd, marked, markedUsd }
```

`marked` is the part it had to guess. The BACKTEST tab shows `39/39 SETTLED`, amber when anything is unverified; `pm_backtests` returns `unverified_usd`; the hub card carries it on every result. **A P&L with a large `markedUsd` is a hypothesis, not a measurement.**

What this changed on this deployment, replaying the same 3-day window over the same cached feeds:

| strat | before | after | settlement |
|---|---|---|---|
| `mrjg86gf` (live) | **+$3,762 · +1,687%** | **−$222 · −100%** | 39 resolved / 0 marked |
| `tpl:weather-edge` | +$9,363 · +936% | +$22 · +2% | 0 / 8 ($25 unverified) |
| `tpl:top-allstars` | +$1,009 · +101% | −$230 · −23% | 39 / 0 |
| `tpl:crypto-majors` | $0 | −$743 · −74% | 13 / 0 |

The corrected `mrjg86gf` figure is the interesting one: $223 of capital, 257 buys totalling $2,234 of exposure over three days, $1,381 back from sells, $630 from redemptions, **24 positions expired worthless**, ending cash $0.56. That matches what actually happened to the real wallet — the old number did not.

### Where the flow went (the entry funnel)

Every backtest now reports an **`EntryFunnel`**: each in-window leader BUY lands in exactly one bucket, so a quiet strat can always say *which* gate it was quiet because of.

```
observed → gated (strat filters) → outranked (per-cycle race) → skipped (unplaceable) → executed
```

with a per-reason tally: `time-to-close`, `trade filters`, `keyword filter`, `trader FILTER`, `MAX/CYCLE cap (N)`, `MAX POS cap (N)`, `SUB_SCALE`, `LEADER_DUST`, `out of cash`, `no scoreable edge`. It's on every hub card, in the tooltip in full, and over MCP as `pm_backtests`.

Measured examples from this deployment (1-day window):

| strat | observed | copied | dominant blocker |
|---|---|---|---|
| BTC 5-min candle bot, $100 | 1279 | 0 | `time-to-close` 1279 |
| same, gate off | 1287 | 0 | `SUB_SCALE` 1005 — $100 can't copy that leader in proportion |
| same, gate off, $1000 | 1287 | 271 | `MAX/CYCLE cap (3)` 346 |
| top-7d leaderboard roster, $1000 | 2630 | 144 | `MAX/CYCLE cap (3)` 2003 |

The last row is the useful lesson: raising `MAX/CYCLE` from 3 → 25 moved executions only 144 → 150, because the freed candidates immediately hit `SUB_SCALE`. Account size, not the cap, is the binding constraint.

### The time-to-close gate

The live engine refuses to mirror a BUY in a market resolving within **`minMinutesToClose`** (default **60**), because sub-hour Up/Down candles resolve before a poller can react — mirroring them late realized **−$253 across 1064 copies** on this console.

- It is now **a per-strat setting** (`MIN CLOSE` in the strat params, `0` = off), and the LIVE gate warning that reports it offers `15M / 5M / OFF` inline — the warning used to name a setting the console had no field for.
- **The backtest models it too.** `Strat.shouldMirror` dates the market from the trade itself: candle slugs (`btc-updown-5m-<start>`) give an exact end, an intraday title window (`… 5:45PM-5:50PM ET`) gives one to the minute, anything else is undatable and — exactly like live's unknown-end-date case — allowed through. Before this, hub cards happily counted fills a live session would refuse one-for-one.

### The freshness gate (`maxStaleHours`)

The trader FILTER drops leaders who start losing. It could not see the other way a watchlist rots: a leader who **stops**. A dormant trader's 30d ROI, Sharpe and win rate all stay excellent — they're a record of trades that already closed — so an unfiltered strat keeps holding a top-N slot open for someone who last traded seventeen days ago.

`filter.maxStaleHours` (**FRESH ≤ Nh** in the TRADER FILTER card, presets 6h / 24h) cuts them, and it behaves differently from the other two thresholds on purpose:

- **It runs before the ranking, not after.** `minScore` and `minSamples` cut a row where it stands, so a failing trader still occupies its rank. Stale traders instead sort *below every active one* — otherwise a roster whose four best scores went quiet last week would park all its top-N slots and copy nobody. A dormant #1 hands the slot to the best trader still trading.
- **"Stale" means stale at what THIS strat trades.** `lastTradeAt` is computed from the same `marketQuery`-filtered slice as the returns, in both languages. On a `bitcoin` strat, a leader posting size in politics all morning is still stale.
- **Any side counts.** A trader who is buying but hasn't closed anything is active; freshness is read off their most recent fill, not their last realized return.
- **Unknown ≠ stale.** A pre-upgrade `poly_roi_stats_*` cache has no `lastTradeAt` at all; that reads as "not computed yet" and passes, because pausing a live strat over missing data is worse than one cycle of a dormant leader. An explicit `0` (computed, nothing in the window) *is* stale.

Both languages are pinned by `parity.fixture.json` (`traderFilterNowMs` + per-trader `lastTradeMinutesAgo`, cases `stale-6h-promotes-fresh` / `stale-24h-with-sharpe-and-samples`), so the card's "copying 3 of 20 · 5 stale" preview is what the Rust engine enforces — it reports the same cut in its `FILTER ·` heartbeat line.

Alongside it, **PRUNE N** in the watchlist toolbar is the manual twin: it *disables* (never deletes) every enabled trader past the same cutoff, so a roster that has quietly become a list of retirees is one click to clean, and one click on a dot to undo. The `LAST` column turns red at exactly that cutoff, so the column and the gate never disagree.

The shipped **FILTER** template sets `maxStaleHours: 24`; **BTC SHARPS** — bitcoin markets only, ranked by Sharpe, top 3 of 20, `≤6h` — is the recipe that leans on all of it.

## MCP server

**The MCP server is the desk's other front end, not a mirror of it.** The `pm_copy_*` tools call the same `/copy/*` routes the browser calls — one book, two clients. Ask an agent to "put $50 on 0xab…" and it shows up on the screen at the next poll; change an amount on the screen and that is what the agent reads next.

```bash
python3 src/mcp.py                      # stdio
python3 src/mcp.py --http --port 50092  # Streamable HTTP: POST /mcp
m polymarket/mcp                        # same, via the module fn
```

**The copy desk** — what an agent runs the desk with:

| tool | writes | what it does |
|---|---|---|
| `pm_copy_book` | — | the desk: who is copied, with how much, running or not, **DRY RUN or real**, orders placed, realized P&L |
| `pm_copy_backtest` | — | replay copying **one** trader: pnl + `walkForward.verdict` + `funnel` |
| `pm_copy_basket` | — | size a **set** of traders against each other — a different amount per name, one portfolio, with `legsTrading`/`idleUsd` (how much of the money never traded), `floors` and the equal-split `comparison` |
| `pm_copy_allocate` | yes | copy a trader with N dollars (or change the amount). Adds intent, places nothing |
| `pm_copy_remove` | yes | stop copying them and drop them from the book |
| `pm_copy_rebalance` | yes | split a bankroll across the enabled traders (`equal` \| `weighted`) |
| `pm_copy_start` | yes | start copying — **DRY RUN by default** |
| `pm_copy_stop` | yes | stop a session, or the whole desk |

**Research and diagnosis** — the rest:

| tool | what it answers |
|---|---|
| `pm_health` | is the module up, when did the worker last run |
| `pm_markets` | busiest open markets, or a text search |
| `pm_top_traders` | the leaderboard, for finding someone to copy |
| `pm_trader` | one leader's flow + **what share of it is sub-hour candle games** |
| `pm_live_gates` | **why a running session isn't filling** — the per-gate tally |
| `pm_live_sessions` | what the engine is running, executing vs dry |
| `pm_strats` / `pm_backtests` / `pm_backtest_run` | the older multi-trader index strategies |

The loop an agent is told to follow: `pm_top_traders` / `pm_trader` to find a leader and check they aren't a candle bot → `pm_copy_backtest` to replay copying *them* → `pm_copy_basket` when there is more than one name and the question is how to split the money → `pm_copy_allocate` → `pm_copy_start` → `pm_live_gates` when it runs but doesn't fill.

**Safety.** There is no order-placing tool and there won't be: the console signs real money through the deposit wallet and a mis-prompted agent must not reach it. The one thing that *can* spend money is `pm_copy_start` with `autoExecute: true`, and it is **refused unless the deployment sets `POLYMARKET_MCP_ALLOW_LIVE=1`** — without it an agent can research, allocate, backtest and dry-run, and a human flips the last switch in the browser. Stopping is always allowed; it only reduces exposure. Auth is the owner token minted from `server.secret` — the server works exactly when the local owner's console works, and never accepts a caller-supplied token.

Register it with Claude Code:

```bash
claude mcp add polymarket -- python3 /root/mod/mod/orbit/polymarket/src/mcp.py
```

### URL Sync

All filter state is serialized to URL params via `FiltersContext` so search results are shareable:

```
/traders?q=election&days=7&cat=politics&minvol=100
```

Parameter mapping: `search→q`, `daysAgo→days`, `category→cat`, `marketQuery→mq`, `minTrades→mint`, `minPerDay→minpd`, `minVolume→minvol`, `minBuyVolume→minbuy`, `minSellVolume→minsell`, `minPnl→minpnl`

## Environment Variables

| Variable | Description |
|----------|-------------|
| NEXT_PUBLIC_API_URL | Backend API URL (default http://localhost:50091) |
| NEXT_PUBLIC_BASE_PATH | Base path for app routing (default /polymarket) |
| POLYMARKET_PRIVATE_KEY | Wallet private key for trading (Python only) |
| POLYMARKET_SYNC_INTERVAL_SECS | Initial background-sync cadence (default 300; owner setting in `~/.mod/polymarket/sync.json` overrides) |
| POLYMARKET_MCP_ALLOW_LIVE | `1` lets `pm_copy_start` place **real orders** over MCP. Unset (the default) ⇒ agents can only DRY RUN, and a human turns on execution from the COPY DESK |

## Mod Protocol

- **Module**: `polymarket`
- **Ports**: API 50091, App 3091
- **Serve**: `m polymarket/serve` (FastAPI + Next.js)
- **Kill**: `m polymarket/kill`
- **Config**: `config.json` with endpoints, fns, ports
- **Logs**: `/tmp/polymarket/api.log`, `/tmp/polymarket/app.log`
