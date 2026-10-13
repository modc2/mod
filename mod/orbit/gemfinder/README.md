# gemfinder

Scans the internet for crypto tokens and stocks that a hot narrative is starting
to carry, and ranks them by how fast attention is piling in.

```
m gemfinder/top                               # one line per gem, crypto + stocks
m gemfinder/scan asset=crypto k=10            # full JSON: score parts, why, flags, metrics, links
m gemfinder/scan asset=stock narrative=quantum
m gemfinder/narratives                        # which themes are hot right now, and why
m gemfinder/history                           # past scans with price at scan time
m gemfinder/serve                             # REST on :51320 (pm2 `gemfinder`)
```

No API keys. Stdlib only. Run `top` to scan everything (about 4s cold; cached after that).

## Where it looks

| source | asset | what it adds |
|---|---|---|
| CoinGecko trending | crypto | most-searched coins of the last 24h, plus trending categories |
| CoinGecko categories | crypto | 24h market-cap move of every category, used for narrative heat |
| DexScreener | crypto | tokens people pay to boost, plus fresh token profiles, priced from their deepest pool (liquidity, buys/sells, pair age) |
| ApeWisdom | both | ticker mentions across crypto and WSB/stock subreddits, with the count from 24h earlier |
| Yahoo Finance | stock | trending tickers; small-cap gainers, day gainers and most-active screens (relative volume, market cap) |
| Hacker News | — | story count per narrative over 7 days: whether the wider internet is talking about it |

Stocktwits and reddit's own JSON are blocked by Cloudflare when called from a server, so ApeWisdom supplies the reddit data.

## How it ranks

1. **Gather.** All feeds are fetched in parallel, and their results are merged per ticker. If two DEX tokens share a ticker but have different contracts, they stay separate.
2. **Narratives.** `gemscan/narratives.py` lists 13 themes: AI, quantum, nuclear, space, defense, robotics, crypto treasuries/miners, RWA, DePIN, memes, biotech, gaming and privacy. Each theme has keywords, CoinGecko categories, seed stock tickers and an HN query. A theme's **heat** (0-100, scored separately for crypto and stocks) is a percentile blend of:
   - the market move of its CoinGecko categories (crypto)
   - the day move of its seed stocks (stocks)
   - its HN buzz
   - how many scanned candidates fall into it

   This file defines the module's thesis, so edit it to add or change themes.
3. **Score** (0-100):

| part | weight | meaning |
|---|---|---|
| attention | 30 | how many independent feeds list it, and how high |
| velocity | 20 | reddit mentions and rank compared with 24h earlier, 1h volume pace, buy/sell ratio, relative volume |
| narrative | 25 | heat of the hottest theme it belongs to |
| momentum | 15 | rising, but not already vertical (the best range is +0..60%; above +150% counts as already mooned) |
| size | 10 | small enough to move (crypto $5M-100M, stocks $300M-2B score best) |
| − flags | | liquidity under $25k, no pooled liquidity, FDV more than 100x liquidity, pair younger than 24h, sell pressure, penny stock |

Every gem includes `why` (the reasons it ranked) and `flags` (the reasons it could blow up).

**What the score is not:** it measures attention, not a forecast. It surfaces the usual early shape of a pump: a hot theme, rising mentions and volume, a small cap and a move that hasn't finished. Most of these still round-trip, and many micro-cap DEX tokens are rugs. This is not financial advice.

## Layout

```
mod.py              Mod class (CLI fns above)
gemscan/http.py     JSON fetch + SQLite cache of raw bodies (stale copy served when a source is down)
gemscan/sources.py  one function per feed -> Signals
gemscan/narratives.py  the themes (edit me)
gemscan/engine.py   gather -> merge -> enrich -> heat -> score -> scan
gemscan/server.py   REST: /health /scan /narratives /history /sources (also under /gemfinder/api)
test/               offline tests (m gemfinder/test)
```

State is stored in `~/.mod/gemfinder/`: `cache.db` holds the raw HTTP bodies and `scans.jsonl` logs every scan's picks with the price at scan time.

## Next

- `review`: re-price the picks in `scans.jsonl` after 1d/7d/30d, then tune the weights against actual hit rate.
- More feeds: X/Twitter cashtags (via orbit/x), the `news` module for headline velocity, Google Trends, and pump.fun/GeckoTerminal new pools.
- An LLM pass that reads the top N's descriptions and news to judge how strong each narrative fit is.
- An MCP endpoint and a small app.
