# __NAME__

__VENUE__ copy-trading, scaffolded by [tradekit](../tradekit) from the
hyperliquid framework. Same fn surface as `hyperliquid` and `polymarket`
(spec `tradekit/1`).

| layer | file | what to fill in |
|---|---|---|
| venue adapter | `src/api/src/venue.rs` | real `markets` / `top_traders` / `analyze_trader` calls |
| REST + MCP | `src/api/src/main.rs` | already wired; live engine is DRY RUN only |
| strats | `src/strats/` | canonical `Strat` base (shared schema) + `copy_wallets` |
| orchestrator | `src/mod.py` | done |

```
m __NAME__/build && m __NAME__/serve
m __NAME__/top_traders days=7
m tradekit/check mod=__NAME__
```

Safety: `live_start` is dry run unless `auto_execute=True`, and the
skeleton refuses real execution until an order path is written.
`live_stop` is never gated.
