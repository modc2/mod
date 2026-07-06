# whitepaper

MOD off-chain Merkle-tree registry whitepaper module. Ships the LaTeX source, a Next.js viewer, and a Python reference implementation of the tree.

## Why

The current `Registry.sol` stores each module as its own row keyed by an IPFS CID. With 200+ modules and agent-driven publishing on the horizon, the linear per-row gas cost is the binding constraint. This module proposes (and demonstrates) replacing the per-module hash table with a single Merkle root anchored on chain. The full tree lives off chain in IPFS manifests; authenticity is preserved via Merkle inclusion proofs.

## Layout

```
mod/orbit/whitepaper/
  mod.py              # anchor class — Mod (Python protocol surface)
  config.json         # ports + proxy routing
  Caddyfile           # :3000 → /whitepaper (app) + /api/whitepaper (api)
  whitepaper.tex      # LaTeX source
  src/api/            # Rust API (axum + tiny-keccak)
    Cargo.toml
    src/main.rs       # routes + state
    src/tree.rs       # canonical-JSON keccak Merkle tree
    src/mod_protocol.rs  # config.json + `m` CLI bridge
  src/app/            # Next.js viewer (basePath /whitepaper)
  skill.md            # this file
```

The Python `Mod` class and the Rust binary share one source of truth:

| Surface | Reads/writes |
|---------|--------------|
| `config.json` | both — Rust binary discovers it by walking up from its own path |
| `whitepaper.tex` | both |
| `~/.mod/whitepaper/tree.json` | both — Python `tree_*` and Rust HTTP endpoints both round-trip through this file |
| `m` CLI | Rust calls back into the mod protocol via `POST /mod/call` → `subprocess m <fn>` |

## Ports

| Service | Port  | Proxy path          |
|---------|-------|---------------------|
| API     | 50106 | `/api/whitepaper/*` |
| App     | 3106  | `/whitepaper`       |

## Usage (Python)

```python
import mod as m
wp = m.mod('whitepaper')()

wp.info()                       # ports + tex location
wp.build()                      # compile pdf (needs pdflatex on PATH)
wp.serve()                      # start api + app + caddy

# Reference Merkle tree
wp.tree_build([{ 'name': 'agent', 'owner': '0x..', 'cid': 'Qm..' }])
wp.tree_root()
wp.tree_proof(name='agent')
wp.tree_verify(leaf='0x..', proof=['0x..'])

# Orbit + core surface
wp.list_mods('orbit')           # everything in orbit/
wp.list_mods('all')             # orbit + core
wp.mod_info('polymarket')       # path + config for any module
wp.merkle('orbit')              # build a Merkle tree from the live ecosystem
```

## Usage (CLI)

```bash
m whitepaper/info
m whitepaper/build
m whitepaper/serve
m whitepaper/tree_root
m whitepaper/tree_build records='[{"name":"agent","cid":"Qm.."}]'
m whitepaper/tree_proof name=agent
```

## API (Rust, axum)

### Whitepaper

| Method | Path             | Purpose                                              |
|--------|------------------|------------------------------------------------------|
| GET    | `/info`          | Module info                                          |
| GET    | `/paper`         | Tex source as JSON                                   |
| GET    | `/paper.tex`     | Tex source as raw text                               |

### Merkle tree

| Method | Path                 | Purpose                                                  |
|--------|----------------------|----------------------------------------------------------|
| GET    | `/tree/root`         | Current root + epoch                                     |
| POST   | `/tree/build`        | Build a tree from an explicit record list                |
| POST   | `/tree/merkle`       | Build the tree from the live orbit/core ecosystem        |
| POST   | `/tree/proof`        | Inclusion proof for a named record                       |
| POST   | `/tree/verify`       | Verify a `(leaf, proof)` against a root                  |

### Protocol bridge — orbit + core

| Method | Path                       | Purpose                                                  |
|--------|----------------------------|----------------------------------------------------------|
| GET    | `/mods?scope=orbit\|core\|all` | List every module on disk with port + path             |
| GET    | `/mods/:name`              | Full info: scope, path, config (data-unwrap applied)     |
| GET    | `/mods/:name/config`       | Config only                                              |
| POST   | `/mod/http`                | HTTP-forward to a peer module's API by name              |
| POST   | `/mod/call`                | Subprocess bridge — `m <fn> [k=v ...]`                   |

The bridge resolves the surrounding mod root automatically: from `mod/orbit/whitepaper/` it walks up until it finds the directory that contains both `orbit/` and `core/`. Both Python (`m.mods()`, `m.config(name)`) and Rust (`/mods`, `/mods/:name/config`) read the same on-disk state, with the same `{"data": {...}, "encrypted": ...}` unwrap so the resulting Merkle roots match across languages.

Build the binary:

```bash
m whitepaper/build_api          # or: cd src/api && cargo build --release
```

Build the binary:

```bash
m whitepaper/build_api          # or: cd src/api && cargo build --release
```

All endpoints are exposed under `/api/whitepaper/*` via the Caddy stanza.

## Proxy

```
:3000 {
    @whitepaper_api path /api/whitepaper /api/whitepaper/*
    handle @whitepaper_api {
        uri strip_prefix /api/whitepaper
        reverse_proxy localhost:50106
    }
    handle /whitepaper* {
        reverse_proxy localhost:3106
    }
}
```

Merge this stanza into the master `Caddyfile` (or run `caddy run --config mod/orbit/whitepaper/Caddyfile` for standalone testing).

## Mod protocol

- Anchor: `mod.py` → `class Mod`
- Serve: `m whitepaper/serve` starts API (50106) + Next (3106) + Caddy
- Kill: `m whitepaper/kill`
- Logs: `/tmp/whitepaper/{api,app,caddy}.log`
- State: `~/.mod/whitepaper/tree.json`
