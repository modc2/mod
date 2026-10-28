# ✎ STRAT CHAT — `polymarket.agents.stratchat`

The CHAT tab on every strat: answers in the strat's vocabulary and proposes a settings patch you APPLY by hand.

- **kind** `chat` · **tools** none — proposes a validated PATCH
- **route** `POST /polymarket/_api/strat-chat` (owner Bearer token)
- **switch** console ▸ STRATS ▸ AGENTS, or from `src/`: `python3 -m agents.stratchat.mod on|off|toggle|info`

Off = the route answers `409 {"error":"agent off","agent":"stratchat"}`.
State is shared by every agent in `~/.mod/polymarket/agents.json`.
