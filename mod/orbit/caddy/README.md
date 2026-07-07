# caddy — the mod router

One generated Caddy site block routes `{host}/{mod}` → the module's app port
and `{host}/api/{mod}` → its API port (prefix stripped). The **whole** site —
every module route, the root redirect, the catch-all — is generated into
`mod_site.caddy`, which the base `/etc/caddy/Caddyfile` imports at top level.
No hand-written per-module routes.

## Route sources (merged, in order of precedence)

1. `~/.mod/caddy/overrides.json` — hand-tuned per-module routes (custom
   upstreams like the activator on `:9000`, docker-name hosts, strip rules).
   Seeded automatically by `m caddy/migrate` from the legacy hand-written
   site block on first apply.
2. Module `config.json` — any module that opts in with `"route": true` and
   declares `port` (API) / `app_port` (app). Dead ports are skipped.

## Anyone can run a router

The public host is a *setting*, not a constant:

```sh
m caddy/host mydomain.com     # repoint the router + regenerate + reload
```

Point your domain's DNS at the box, and `mydomain.com/{mod}` routes your
modules. Settings and overrides are deployment state and live off-tree in
`~/.mod/caddy/` (settings.json, overrides.json) — never committed.

## Functions

| fn | what |
|---|---|
| `m caddy/settings` | get/set `host`, `upstream_host`, `root_redirect`, `fallback` |
| `m caddy/host <h>` | change the router's domain (regenerates + reloads) |
| `m caddy/discover` | modules that would be auto-routed from config.json |
| `m caddy/routes` | the merged route table (auto + overrides) |
| `m caddy/generate` | render the full site block (dry run) |
| `m caddy/migrate` | parse legacy hand routes into overrides.json |
| `m caddy/apply` | write include, one-time legacy cutover, validate, reload |
| `m caddy/reload` | reload Caddy via its admin API |

`apply` always backs up the Caddyfile before rewriting it, runs
`caddy validate`, and rolls back instead of reloading if validation fails.
