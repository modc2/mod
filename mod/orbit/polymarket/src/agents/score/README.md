# ▦ SCORE WRITER — `polymarket.agents.score`

Writes the board's SCORE (expression, JS or Python) from plain words. The formula compiles in the browser before it runs.

- **kind** `chat` · **tools** none
- **route** `POST /polymarket/_api/score-agent` (owner Bearer token)
- **switch** console ▸ STRATS ▸ AGENTS, or from `src/`: `python3 -m agents.score.mod on|off|toggle|info`

Off = the route answers `409 {"error":"agent off","agent":"score"}`.
State is shared by every agent in `~/.mod/polymarket/agents.json`.
