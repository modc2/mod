# polymarket

Polymarket prediction market interface with trading, data, scraping, and backtesting. Rust-powered engine with Python CLI and a modern dark Next.js terminal app (Inter / JetBrains Mono, rounded panels, live market ticker).

## Capabilities

- **Market Data**: Search, list, filter, and sort prediction markets by volume/liquidity/end date
- **Live Price Ticker**: Slim auto-scrolling tape above every page — top 24 markets, polls every 8s, Δ since last poll with up/down arrows, paused while tab is hidden
- **Trading**: Place limit and market orders via Polymarket CLOB (requires wallet + API credentials)
- **Copy Trading**: Track top traders by PNL/volume, view their positions and activity
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

**Theming**: dark is the default; setting `data-theme="light"` on `<html>` flips every color through a single set of CSS vars (`--bg`, `--fg`, `--panel-from/to`, `--border`, etc., plus channel-style `--pixel-*-rgb` vars so Tailwind opacity modifiers like `text-pixel-white/60` keep working).

Component-size sweep: every `text-[8–14px]` across all `app/components/*.tsx` and `app/**/page.tsx` was bumped one step up (8→11, 9→12, 10→12, 11→13, 12→14, 13→15, 14→16, 18→26) so Inter has room to breathe.

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

### Caching

Markets/search hit the Polymarket API live (short TTL). Trader data and historical data are **persisted to disk** on first fetch and never re-requested — survives server restarts, no risk of rate limits.

| Layer | What | TTL | Storage |
|-------|------|-----|---------|
| **Frontend** (localStorage) | Market search, price history, market trades, positions, wallet trades | Hourly (same-hour = no refetch) | Browser |
| **Rust proxy** (in-memory) | Markets, events, search | 5 min | Memory only |
| **Rust proxy** (memory + disk) | Trader activity, positions, price history, market trades, leaderboard | 24h memory / **indefinite on disk** | `/tmp/polymarket-proxy-cache/` |
| **Pipeline** (memory + disk) | Aggregated active-trader data | 1 hour, warmed in background | `/tmp/polymarket-active-traders-cache/` |

**Persistent endpoints** (disk-cached on first fetch): `activity`, `positions`, `users/`, `trades`, `v1/` (leaderboard), `holders`, `value`, `prices-history`, `market-trades`

**Ephemeral endpoints** (memory-only, fine to re-hit API): `markets`, `events`, `public-search`, `book`, `midpoint`, `price`

The proxy serves stale cache on upstream errors and sets `x-cache: HIT|MISS|STALE` headers.

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

## Mod Protocol

- **Module**: `polymarket`
- **Ports**: API 50091, App 3091
- **Serve**: `m polymarket/serve` (FastAPI + Next.js)
- **Kill**: `m polymarket/kill`
- **Config**: `config.json` with endpoints, fns, ports
- **Logs**: `/tmp/polymarket/api.log`, `/tmp/polymarket/app.log`
