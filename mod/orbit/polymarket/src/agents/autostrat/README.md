# ∞ AUTO STRAT — `polymarket.agents.autostrat`

The strat factory: a loop that invents a random new strat every interval while its own ENABLED setting is on. Switching the agent off stops the loop AND blocks manual runs; its own settings are left untouched.

- **kind** `background` · **tools** spawns lab-style runs
- **route** `POST /polymarket/_api/autostrat?run=1` (owner Bearer token)
- **switch** console ▸ STRATS ▸ AGENTS, or from `src/`: `python3 -m agents.autostrat.mod on|off|toggle|info`

Off = the route answers `409 {"error":"agent off","agent":"autostrat"}` and its loop skips ticks.
State is shared by every agent in `~/.mod/polymarket/agents.json`.
