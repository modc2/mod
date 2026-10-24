# dydx

dYdX copy-trading, scaffolded by [tradekit](../tradekit) from the
hyperliquid framework. Same fn surface as `hyperliquid` and `polymarket`
(spec `tradekit/1`).

| layer | file | what to fill in |
|---|---|---|
| venue adapter | `src/api/src/venue.rs` | wired to the public dYdX v4 indexer (no key) |
| REST + MCP | `src/api/src/main.rs` | already wired; live engine is DRY RUN only |
| strats | `src/strats/` | canonical `Strat` base (shared schema) + `copy_wallets` |
| orchestrator | `src/mod.py` | done |

dYdX publishes no trader leaderboard, so `top_traders` ranks a **watchlist**
(`m dydx/watch address=dydx1…`, stored in `~/.mod/dydx/watchlist.json`, plus
`$DYDX_WATCHLIST`) by window PnL from `historical-pnl`. `analyze_trader` adds
equity, open positions and the last 100 fills. Execution is not written yet:
`live_start` records a dry-run session only.

```
m dydx/watch address=dydx1…
m dydx/build && m dydx/serve
m dydx/top_traders days=7
m tradekit/check mod=dydx
```

Safety: `live_start` is dry run unless `auto_execute=True`, and the
skeleton refuses real execution until an order path is written.
`live_stop` is never gated.
