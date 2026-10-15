# ✧ VIBE — `polymarket.agents.vibe`

Plain words → strat params → backtest over 1/3/7 days, in one press (also ?draft=1). Saving stays a human press.

- **kind** `chat` · **tools** none — drafts params, then benches them
- **route** `POST /polymarket/_api/lab?vibe=1` (owner Bearer token)
- **switch** console ▸ STRATS ▸ AGENTS, or from `src/`: `python3 -m agents.vibe.mod on|off|toggle|info`

Off = the route answers `409 {"error":"agent off","agent":"vibe"}`.
State is shared by every agent in `~/.mod/polymarket/agents.json`.
