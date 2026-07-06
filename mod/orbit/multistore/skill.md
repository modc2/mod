---
name: multistore
description: Unified storage facade — IPFS + Filecoin + Hippius + Arweave + local FS, with auto-detected backends and cross-backend replication.
type: orbit-module
---

# multistore

One module that talks to every storage backend in `~/mod/mod/orbit`.

- Detects which backends are alive at runtime
- Fan-out `put` across all of them for durability, or target one
- `get` falls back across backends until one returns
- `replicate` moves a CID from one backend to another (IPFS → Filecoin, etc.)
- Per-owner SQLite index of `(cid, backend, owner, key, size, timestamp)`
- Auto-UI via `m serve` exposes every method without writing a custom app

## Backends

| name | module | role |
|------|--------|------|
| `ipfs` | `orbit/ipfs` | Kubo daemon — cheap mutable pinning |
| `filecoin` | `orbit/filecoin` | Lotus + gateway — paid durable storage deals |
| `hippius` | `orbit/hippius` | Substrate + S3 gateway |
| `arweave` | `orbit/arweave` | Permanent storage (no-op until backend implements put) |
| `localfs` | `core/store` | Local file-system KV (default fallback, always alive) |

## Usage

### Python

```python
import mod as m
s = m.mod('multistore')()

s.backends()                                # {'ipfs': {'available': True}, ...}
s.put('/path/to/file', backend='all')       # fan-out everywhere alive
s.put('/path/to/file', backend='ipfs')      # single target
s.get('Qm...')                              # auto-fallback
s.replicate('Qm...', from_='ipfs', to='filecoin')
s.list(owner='0xabc', search='Qm', limit=20)
s.health()                                  # rich per-backend status
```

### CLI

```bash
m multistore/backends
m multistore/put /path/to/file backend=all owner=0xabc
m multistore/get Qm...
m multistore/replicate cid=Qm... from_=ipfs to=filecoin
m multistore/list owner=0xabc search=Qm
m multistore/health
m multistore/start_all          # start all daemons (lotus, substrate, kubo)
m multistore/stop_all
```

### App (auto-generated)

`m serve mod=multistore port=50160` exposes every endpoint via the mod core's
auto-UI: visit `http://localhost:50160` to drive put/get/list interactively.

## API

| Function | Description |
| -------- | ----------- |
| `backends()` | List every known backend + alive flag |
| `health()` | Per-backend status / node_status / id |
| `status()` | Total objects + counts per backend |
| `put(path, backend='all', owner=None, key=None)` | Upload; fan-out or single target |
| `get(cid, backend=None, out=None)` | Fetch; auto-falls back |
| `pin(cid, backend='all', owner=None)` | Pin across one or all backends |
| `rm(cid, backend='all', caller=None)` | Remove + drop from index |
| `list(owner=None, backend=None, limit=100, offset=0, search=None)` | Paginated index |
| `replicate(cid, from_, to, owner=None)` | Copy CID from one backend to another |
| `start_all()` | Best-effort start every daemon |
| `stop_all()` | Stop every daemon |

## Structure

```
multistore/
├── multistore/mod.py        # Mod facade class
├── config.json              # ports, endpoints, backends
├── skill.md                 # this file
└── README.md
```

State: `~/.multistore/index.db` (SQLite cross-backend index).

## Mod protocol

Anchor class `Mod` in `multistore/mod.py`. Reachable as `m.mod('multistore')()` or `m multistore/<fn>`.
Composes filecoin/hippius/ipfs/arweave orbit modules + core/store via `m.mod(...)`.
