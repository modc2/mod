# Agents

Every AI agent in polymarket is its own mod: `src/agents/<name>/` holds a
`config.json` (the contract), a `mod.py` (switch + handle, inherits
`base/mod.py`) and a `README.md`. The console's **STRATS ▸ AGENTS** tab is a
roster built from these folders — add a folder, it shows up; delete it, it
is gone (its route then refuses as "unknown agent" — off by absence).

| mod | kind | route |
|---|---|---|
| desk | chat | `/_api/agent/chat` |
| help | chat | `/_api/help-agent` |
| scout | chat | `/_api/trader-agent` |
| score | chat | `/_api/score-agent` |
| stratchat | chat | `/_api/strat-chat` |
| vibe | chat | `/_api/lab?vibe=1` (+ `?draft=1`) |
| lab | background | `/_api/lab` (run start; the `?candidate=1` bench is ungated) |
| autostrat | background | `/_api/autostrat` (loop + `?run=1`) |
| bundler | background | `/_api/bundler` (6h loop, off by default + `?run=1`) — consistent copy-trade bundles proven on a holdout |

## config.json

```json
{ "name": "scout", "mod": "polymarket.agents.scout", "title": "TRADER SCOUT",
  "glyph": "⌕", "kind": "chat|background", "route": "/_api/trader-agent",
  "query": "optional", "tools": "what it can touch", "description": "…",
  "default_enabled": true, "order": 2, "version": "0.1.0" }
```

## On / off

One state file for all of them: `~/.mod/polymarket/agents.json`
`{"off": [...], "on": [...]}`. Read by the Next server on every request
(`lib/server/agentMods.ts`), so a switch applies with no restart.

- `GET  /polymarket/_api/agents` — roster + on/off (public: names only, no secrets)
- `POST /polymarket/_api/agents {name, enabled}` — owner Bearer token
- `python3 -m agents.<name>.mod on|off|toggle|info` from `src/` (local = owner)

Switching an agent OFF never touches money: the desk's approval gate, live
sessions and exits are unaffected — it only stops that agent from spending
inference.
