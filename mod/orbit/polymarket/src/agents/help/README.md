# ? HELP — `polymarket.agents.help`

The guide: explains the console and answers "why isn't it trading" from a read-only snapshot of the live copy book.

- **kind** `chat` · **tools** none — reads a copy-book snapshot
- **route** `POST /polymarket/_api/help-agent` (owner Bearer token)
- **switch** console ▸ STRATS ▸ AGENTS, or from `src/`: `python3 -m agents.help.mod on|off|toggle|info`

Off = the route answers `409 {"error":"agent off","agent":"help"}`.
State is shared by every agent in `~/.mod/polymarket/agents.json`.
