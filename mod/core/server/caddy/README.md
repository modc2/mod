# Caddy

Reverse proxy manager for mod. Routes `modc2.com` (app) and `api.modc2.com` (API) to module ports based on config.json URLs.

## Usage

```bash
# Generate Caddyfile from live module ports
m caddy/generate

# Generate + write Caddyfile + reload if running
m caddy/sync

# Start Caddy via PM2
m caddy/serve

# Stop Caddy
m caddy/kill

# Restart (regenerate + restart)
m caddy/restart

# Check status
m caddy/status

# View logs
m caddy/logs
```

```python
import mod as m
caddy = m.mod('caddy')()
caddy.serve()       # generate Caddyfile, start via PM2
caddy.sync()        # regenerate + reload
caddy.status()      # check if running
caddy.kill()        # stop
```

## How it works

`generate()` scans all `config.json` files in `orbit/` and `core/` for `urls.app` and `urls.api` entries. Each module gets a path-based route (`/modulename/*`) on the appropriate domain. Modules whose ports aren't live are skipped by default.

## Routing

| Domain | Default backend | Purpose |
|---|---|---|
| `modc2.com` | `localhost:3000` | App frontends |
| `api.modc2.com` | `localhost:8000` | API backends |

Module routes: `modc2.com/claude/*` -> `localhost:8821`, `api.modc2.com/claude/*` -> `localhost:8820`, etc.

## Options

```bash
m caddy/generate domain=modc2.com api_domain=api.modc2.com app_port=3000 api_port=8000 check_ports=false
```
