# web — the mod protocol front door

A live, searchable explorer of the entire mod ecosystem, served at
**[modc2.com/web](https://modc2.com/web)**.

```
api/   Rust (axum) catalog gateway   →  :50420   (binary: mod-api)
app/   Next.js front-end             →  :3420    (basePath /web)
```

## What it does

The mod monorepo is a tree of modules — each a directory under `mod/orbit/<name>/`
with a `config.json`. `mod-api` walks that tree, parses every config, and serves a
uniform catalog. The Next app renders it: a hero with live ecosystem stats, an
instant-search module grid, and a per-module detail view (functions, ports,
gateway mount, raw config).

The API is pure read-side over the filesystem — no chain, no auth, no state — so
it's fast and impossible to break. It rescans on a 3-second TTL, so the catalog
stays live as modules are added or edited.

## API

Behind the gateway, all routes are reachable under `/api/web` (the prefix is
stripped before proxying to `mod-api`).

| Route          | Description                                  |
| -------------- | -------------------------------------------- |
| `GET /`        | Protocol info + live ecosystem stats         |
| `GET /health`  | Liveness probe (orbit path + module count)   |
| `GET /mods`    | Catalog of every orbit module                |
| `GET /mods/:n` | Single module detail (parsed `config.json`)  |
| `GET /stats`   | Aggregate stats (modules/functions/rust/app) |
| `GET /search?q=` | Filter the catalog                         |

## Run

```bash
./start.sh            # build Rust + Next, (re)start both under pm2
```

Env knobs: `MOD_WEB_API_PORT` (50420), `MOD_WEB_APP_PORT` (3420),
`MOD_WEB_MODE` (`prod`|`dev`), `MOD_ORBIT_DIR` (defaults to `../../orbit`).

## Gateway

Caddy (`/etc/caddy/Caddyfile`, `modc2.com` block):

```
@web_api path /api/web /api/web/*
handle @web_api { uri strip_prefix /api/web; reverse_proxy localhost:50420 }
@web_app path /web /web/*
handle @web_app { reverse_proxy localhost:3420 }
```
