# ⚗ STRAT LAB — `polymarket.agents.lab`

A long-running agent that hunts for the best strat this deployment's data supports, backtesting and refining until confident. The bench (?candidate=1) is NOT gated — other agents backtest through it.

- **kind** `background` · **tools** polymarket MCP (pm_lab_backtest)
- **route** `POST /polymarket/_api/lab` (owner Bearer token)
- **switch** console ▸ STRATS ▸ AGENTS, or from `src/`: `python3 -m agents.lab.mod on|off|toggle|info`

Off = the route answers `409 {"error":"agent off","agent":"lab"}` and its loop skips ticks.
State is shared by every agent in `~/.mod/polymarket/agents.json`.
