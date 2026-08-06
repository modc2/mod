# polymarket

Polymarket prediction market interface with trading, data, scraping, and backtesting. Rust-powered engine with Python CLI and a modern dark Next.js terminal app (Inter / JetBrains Mono, rounded panels, live market ticker).

## Capabilities

- **Market Data**: Search, list, filter, and sort prediction markets by volume/liquidity/end date
- **Live Price Ticker**: Slim auto-scrolling tape above every page — top 24 markets, polls every 8s, Δ since last poll with up/down arrows, paused while tab is hidden
- **Trading**: Place limit and market orders via Polymarket CLOB (requires wallet + API credentials)
- **Copy Trading**: Track top traders by PNL/volume, view their positions and activity
- **Proportional copy sizing**: mirrors are sized as `leader$ × (accountValue × weightFraction) / leaderBankroll` — the fraction of net worth the leader risked, applied to yours. `accountValue` (free cash + mark value of the strat's positions) and each leader's bankroll (their positions + free USDC) are re-read every cycle, so sizes track the account as it grows or draws down, and a $10k conviction entry copies 100× larger than a $100 punt. Guardrails, all defaulted on: `maxUpscale` 2× (a mirror that could only be placed by inflating it past the order floor is refused as `SUB_SCALE`, not silently placed at the minimum), proportional exits (leader sells 40% of their shares → the strat sells 40% of its own; leader flat → strat flat), `minMinutesToClose` 60m (sub-hour Up/Down candles resolve before a poller can react), `maxTradeAgeSec` 300s, and a BUY budget bounded by real wallet cash rather than the `capital` config. The ratio and clamps are pinned across TypeScript and Rust by `parity.fixture.json`, so the BACKTEST tab previews the sizes live will place
- **Strategy Index** (`/strats`): Build/edit a basket of traders, set capital + rebalance cadence, then go live. A pre-flight `CHECKLIST` sits at the top of the page — wallet, CLOB auth, strategy, traders, rebalance, capital — and goes from `4/6 complete` → `6/6 · ready to go live` as the user fills each gap
- **CLOB refresh-from-UI**: When the checklist's `CLOB AUTHENTICATED` row is unchecked, an amber `refresh` pill fires `authenticate()` (single MetaMask sig → derived API key) inline — no page hop
- **Wallet Funding Panel**: Source picker (network ▾) + asset chips that each show their **live balance** so you can see what you'd be spending before clicking. Polls every 30s + manual refresh; chips wrap onto their own row in narrow sidebar mounts so they're always visible
- **Trading-ready dot** on the wallet chip: 🟢 connected + CLOB authed · 🟡 connected · ⚪ disconnected
- **Portfolio**: View positions, P&L, open orders
- **Scraping**: Background price/trade history scraper with SQLite storage
- **Backtesting**: Run threshold-based backtests on stored historical data
- **Categories**: politics, sports, crypto, pop-culture, business, science, tech, ai

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
- A strat with nothing to copy reports the reason (`no traders to copy`, `originates its own trades`, `all 1279 entries blocked · time-to-close`) instead of a `$0` that reads as breaking even.

### The background backtest worker

Backtests are **cached, and refreshed on a 2-hour cadence by a worker that runs whether or not the console is open** (`app/lib/server/hubWorker.ts`, started from `app/instrumentation.ts` when the Next server boots). Opening `/strats` then paints real numbers on the first frame instead of firing a dozen paginated `/activity` walks and watching cards trickle in.

```
console ──POST /polymarket/api/hub {strats}──► manifest.json   (which strats to replay)
worker  ──every 2h──► hubReplay → backtest → backtests.json    (~/.mod/polymarket/hub/)
console ──GET  /polymarket/api/hub?days=1───► paints instantly
```

- **The worker runs the app's own engine.** `hubReplay.ts` → `backtest.ts` → `strats/strat.ts` — the exact modules the console and the BACKTEST tab run. That's why it lives inside the Next server rather than as a separate service: a second build of the engine is how backtest and live drifted apart last time (`strats/parity.fixture.json`).
- **It authenticates as the owner.** Every API route is behind the access gate, so the worker mints the same `pma1.…` token the console's sign-in issues, from `~/.mod/polymarket/server.secret` (`app/lib/server/ownerToken.ts`). No gate, no worker — it fails closed and records why.
- **The window is 1 day** (`HUB_BACKTEST_DAYS`), which is what "which of these is working *right now*" wants. Other windows are replayed in the browser on demand.
- The browser still fills gaps: a strat edited since the last pass, or a window the worker doesn't cover, is replayed locally and merged newest-wins.
- `POLYMARKET_HUB_WORKER=0` disables it. `PUT /polymarket/api/hub` runs a pass synchronously (what the MCP tool uses); `POST …?run=1` queues one.

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

## MCP server

The module speaks **Model Context Protocol**, so any MCP client (Claude Code / Desktop, an agent, another module) can read it:

```bash
python3 src/mcp.py                      # stdio
python3 src/mcp.py --http --port 50092  # Streamable HTTP: POST /mcp
m polymarket/mcp                        # same, via the module fn
```

| tool | what it answers |
|---|---|
| `pm_health` | is the module up, when did the worker last run |
| `pm_markets` | busiest open markets, or a text search |
| `pm_top_traders` | the leaderboard strats seed watchlists from |
| `pm_trader` | one leader's flow + **what share of it is sub-hour candle games** |
| `pm_strats` | the strategies this console runs, with every gate |
| `pm_backtests` | the worker's cached backtests **with the funnel** |
| `pm_backtest_run` | replay every strat now |
| `pm_live_sessions` | what the engine is running, executing vs dry |
| `pm_live_gates` | **why the engine isn't copying** — the per-gate tally |

**Read-only by design.** There is deliberately no order-placing tool: the console signs real money through the deposit wallet and a mis-prompted agent must not reach it. The one tool with a side effect (`pm_backtest_run`) spends CPU and data-api calls, nothing else. Auth is the owner token minted from `server.secret` — the server works exactly when the local owner's console works, and never accepts a caller-supplied token.

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

## Mod Protocol

- **Module**: `polymarket`
- **Ports**: API 50091, App 3091
- **Serve**: `m polymarket/serve` (FastAPI + Next.js)
- **Kill**: `m polymarket/kill`
- **Config**: `config.json` with endpoints, fns, ports
- **Logs**: `/tmp/polymarket/api.log`, `/tmp/polymarket/app.log`
