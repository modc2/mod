# cshare

Agent Protocol hub (agent/1.0): one card, one index, one share/install surface
over every content-addressed agent artifact (prompts, agents, toolboxes,
memory notes).

## When to use

- Discover what agent artifacts exist locally: `m cshare/index` (filter with
  `kind=prompt|agent|toolbox|memory`, `q=`, `tag=`)
- Share any artifact by CID: `m cshare/share kind=prompt id=p-0`
- Install anything from a CID without knowing its kind:
  `m cshare/install cid=Qm...` (the bundle's `type` picks the installer)
- Inspect an unknown CID first: `m cshare/resolve cid=Qm...`
- Get the protocol descriptor: `m cshare/card` (HTTP: `/.well-known/agent.json`)

## Endpoints

API `:50290` (gateway `/cshare/api`), app `:50291/cshare`.
`m cshare/serve` starts both under pm2 (`cshare.api`, `cshare.app`).

## Notes

- Backed by the agent module's registries (`orbit/agent/src`); artifacts stay
  in `~/.mod/agent/…` — cshare is the protocol surface, not a second store.
- Conversations resolve/validate but install via the agent console (per-user).
