# web — the mod protocol front door

A live, searchable explorer of the entire mod ecosystem, served at
**[modc2.com/web](https://modc2.com/web)**.

```
api/   Rust (axum) catalog gateway   →  :50420   (binary: mod-api)
app/   Next.js front-end             →  :3420    (basePath /web)
```

## What it does

The mod monorepo is a tree of modules — each a directory with a `config.json`,
under `mod/orbit/<name>/` (and the sibling `mod/core/` for core modules like
web, chain, store). `mod-api` walks those roots, parses every config, and serves
a uniform catalog. The Next app renders it as a live, browsable explorer:

- **Browse** — a hero with ecosystem stats and an instant-search module grid.
- **Inspect** — a per-module detail view with three tabs:
  - **Overview** — functions, ports, gateway mount, raw `config.json`.
  - **Code** — a file-tree browser + viewer over the module's own source. The
    `/tree` and `/file` endpoints are sandboxed to each module's directory
    (path traversal and symlink escapes are refused), skip build output
    (`target`, `node_modules`, `.next`, …), and cap file size at 512 KB.
  - **App** — the module's live app embedded via iframe at its gateway URL,
    with an open-in-new-tab link.

The API is pure read-side over the filesystem — no chain, no auth, no writes — so
it's fast and impossible to break. It rescans on a 3-second TTL, so the catalog
stays live as modules are added or edited.

## API

Behind the gateway, all routes are reachable under `/api/web` (the prefix is
stripped before proxying to `mod-api`).

| Route          | Description                                  |
| -------------- | -------------------------------------------- |
| `GET /`        | Protocol info + live ecosystem stats         |
| `GET /health`  | Liveness probe (orbit path + module count)   |
| `GET /mods`    | Catalog of every module (orbit + core)       |
| `GET /mods/:n` | Single module detail (parsed `config.json`)  |
| `GET /mods/:n/tree` | Recursive source tree (build output elided) |
| `GET /mods/:n/file?path=` | One source file, sandboxed to the module dir |
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
