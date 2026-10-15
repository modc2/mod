# ◉ DESK CHAT — `polymarket.agents.desk`

The left-column agent with hands: answers anything, reads the board, desk and strats and operates them through the module's own MCP server (src/mcp.py). Streams SSE.

- **kind** `chat` · **tools** polymarket MCP (pm_*) — money/strat writes pause for owner approval
- **route** `POST /polymarket/_api/agent/chat` (owner Bearer token)
- **switch** console ▸ STRATS ▸ AGENTS, or from `src/`: `python3 -m agents.desk.mod on|off|toggle|info`

Off = the route answers `409 {"error":"agent off","agent":"desk"}`.
State is shared by every agent in `~/.mod/polymarket/agents.json`.
