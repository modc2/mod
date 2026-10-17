# ⌕ TRADER SCOUT — `polymarket.agents.scout`

Describe the trader you want; it finds them on the leaderboards and returns a validated shortlist of addresses.

- **kind** `chat` · **tools** read-only MCP (pm_top_traders, pm_trader)
- **route** `POST /polymarket/_api/trader-agent` (owner Bearer token)
- **switch** console ▸ STRATS ▸ AGENTS, or from `src/`: `python3 -m agents.scout.mod on|off|toggle|info`

Off = the route answers `409 {"error":"agent off","agent":"scout"}`.
State is shared by every agent in `~/.mod/polymarket/agents.json`.
