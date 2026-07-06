# uniswap

Multi-chain Uniswap V3 trader scraper and analytics platform.

## Capabilities

- Scrape all Uniswap V3 traders over the last N days (1-30d)
- 5 chains: Ethereum, Arbitrum, Base, Polygon, Optimism
- Full metric suite per trader: volume, realized PnL (FIFO), win rate, token flow, pool diversity, MEV detection
- NDJSON streaming progress during scrapes
- 3-tier caching (memory + disk + background warmup)
- Composite scoring with MEV penalty
- 12-point P&L and volume curves

## Architecture

- **Backend**: Rust (Axum 0.7) at `src/api/` — paginated Graph queries, 64-way concurrent enrichment
- **Frontend**: Next.js 14 at `src/app/` — leaderboard table, trader profiles, SVG sparklines
- **Protocol**: `src/mod.py` — Python interface, orchestrates cargo + next

## Usage

### Python
```python
m = mod.mod('uniswap')()
m.serve()                                    # Start API (50088) + App (3088)
m.traders(chain='base', days=30, limit=20)   # Top traders
m.trader('0x...', chain='base')              # Single trader profile
m.scrape(chain='ethereum', days=7)           # Fresh scrape
m.kill()                                     # Stop services
```

### CLI
```bash
m uniswap/serve
m uniswap/traders chain=base days=30 limit=20
m uniswap/trader 0xabc123 chain=base
m uniswap/scrape chain=ethereum days=7
m uniswap/health
m uniswap/chains
m uniswap/kill
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service status + per-chain cache info |
| GET | `/chains` | Supported chains list |
| GET | `/traders` | Leaderboard (params: chain, days, limit, min_swaps, sort, pool) |
| GET | `/traders/:address` | Single trader full profile |
| GET | `/traders/stream` | NDJSON streaming pipeline with progress events |

## Metrics Per Trader

- **Volume**: total, buy, sell, avg trade size
- **PnL**: realized (FIFO cost-basis), win rate per pool, 12-point curve
- **Tokens**: top tokens, concentration (HHI), net flow
- **Pools**: unique pools, diversity score, per-pool PnL
- **MEV**: sandwich count, arb count, avg swaps/day, min interval, bot classification
- **Score**: composite (volume 25% + PnL 35% + win rate 25% + activity 15%, MEV penalty)

## Data Source

The Graph (Uniswap V3 subgraphs) — paginated swap queries with `id_gt` cursor pagination.

## Structure

```
uniswap/
├── mod.py              # Module entry point
├── config.json         # Ports, functions
├── skill.md            # This file
├── src/
│   ├── mod.py          # Python mod protocol class
│   ├── api/            # Rust backend (Axum)
│   │   ├── Cargo.toml
│   │   └── src/        # main, routes, pipeline, models, cache
│   └── app/            # Next.js frontend
│       ├── package.json
│       └── app/        # Pages, components, lib
```

## Environment Variables

- `GRAPH_API_KEY` — The Graph API key (optional, uses public endpoint without)
- `PORT` — API port override (default: 50088)

## Ports

- API: 50088
- App: 3088

## Mod Protocol

- Module: `uniswap`
- Load: `m.mod('uniswap')()`
- Functions: serve, kill, traders, trader, scrape, chains, health
