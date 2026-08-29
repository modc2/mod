# hub (core module)

The module catalog. It walks the repo (`orbit/` + `core/`) and answers one
question: **what modules exist, and what is each one?**

- **CLI:** `m hub/modules`, `m hub/names orbit`, `m hub/doc claude`,
  `m hub/desc claude`, `m hub/dir claude`, `m hub/search auth`, `m hub/info`

```python
import mod as m
hub = m.mod('hub')()
hub.modules('core')          # [{name, group, description, readme, skill}, ...]
hub.doc('claude')            # {module, description, readme, skill}
```

A module's description comes from its `config.json` (`<mod>/config.json`, or
`<mod>/<name>/config.json` for modules that nest their package). Set `MOD_REPO`
to point the catalog at a different tree.

Used by [`docs`](../docs), which lists `hub` in its `deps` and re-exports
`m docs/modules` / `m docs/doc` on top of it.

## The catalog service (api.py, :50520)

The HUB data plane copied out of `orbit/build` — see
`orbit/build/docs/hub-extraction.md` (this is phase 1). `bash start.sh [port]`
runs it (pm2 name `hub-api`); it binds **127.0.0.1 only** and is **not routed
publicly** (`"route": false`): rows are the raw on-disk catalog with no
privacy overlay or owner attribution — consoles (build) apply their own
visibility on the way out.

```
GET  /health                        GET  /search?q=
GET  /config                        GET  /probe?ports=8890,8893   # {port: bool}
GET  /modules?q=&anchor=            GET  /autosnap/status
GET  /modules/{name}                POST /autosnap/tick
GET  /modules/{name}/doc
GET  /modules/{name}/screenshot     # ?refresh=1 ?fresh=1 → image/png
```

`/modules` is key-for-key identical to build's `GET :8890/modules` (verified
0 field mismatches across the fleet), minus build's overlay fields
(`owner` attribution, `private`). Screenshots are headless-chromium captures
through the local caddy, cached under `~/.mod/hub/screenshots/` with the same
policy constants as build (fresh 6h / fail 10m / refresh floor 60s / fresh
floor 10s / 2 chrome slots). The autosnap loop (60s tick, 3 per tick, doubling
backoff, 256MB tree cap, skips modules with an enabled record under
`~/.mod/build/private/` — read file-only, hub never calls build) mints missing
registry CIDs via `POST {api}/api/reg`; it ships **disabled**
(`HUB_AUTOSNAP=0` in the pm2 env) until build's own loop is retired in
phase 2, to avoid double-pushing.

New CLI fns on top: `m hub/probe 8890,8893`, `m hub/screenshot claude`,
`m hub/snapshot_status`.
