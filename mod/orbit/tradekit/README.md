# tradekit

The hyperliquid framework turned into a standard, **tradekit/1**, for
copy-trading venue modules. hyperliquid is the reference. polymarket and dydx
use the same fn names, config shape and layout.

```
m tradekit/board                                  # every venue module, side by side
m tradekit/check mod=polymarket                   # one module's report
m tradekit/scaffold name=gmx venue=GMX symbol="ETH/USD"   # new venue module
```

## The standard (`src/standard.py`)

| group | required fns |
|---|---|
| lifecycle | serve kill status build logs test forward |
| market | markets |
| traders | top_traders analyze_trader |
| strats | list_strats backtest_strats |
| live | live_start live_stop live_status |
| mcp | mcp mcp_tools mcp_call mcp_config |
| provenance | build_cid publish_build build_onchain |

Optional fns include whoami, trader_curve, backtest_trader, strats_board,
scout, ask, chat, sync and candles. Every module's `config.json` carries
`ports{api,app}`, `urls{api,app,gateway}`, `mcp.tool_prefix`, and a
`standard` block `{spec, venue, currency, symbol, aliases}`.
`aliases` maps a canonical fn to the module's native name. For example,
polymarket maps `top_traders` to `active_traders` and `backtest_strats` to
`backtests`. The strat contract itself (setup, sync, signal, execute, tick,
backtest, teardown, state) is pinned by `orbit/strat`.

The checker is static. It reads config.json and parses `src/mod.py` with
`ast`. It never imports a module or touches a wallet.

## Safety rules (every venue)

- `live_start` defaults to dry run. Real orders need an explicit
  `auto_execute=True`. polymarket additionally needs
  `POLYMARKET_MCP_ALLOW_LIVE=1`, and the scaffold refuses real execution until
  an order path is written.
- `live_stop` is never gated.

## scaffold

`scaffold` copies `src/template/` into `orbit/<name>`. Each module it creates
starts out passing the check and ships `tests/test_standard.py`. It contains:

- `src/mod.py`: the orchestrator copied from hyperliquid.
- `src/api`: an axum skeleton with every canonical route plus MCP. The venue
  code is isolated in `venue.rs`.
- `src/strats`: hyperliquid's canonical `Strat` base plus `copy_wallets`.

`scaffold` picks a free port pair, and the new module is created private.
It will not overwrite a module it didn't create.
