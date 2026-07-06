---
name: dstore
description: Unified decentralized storage over filecoin + hippius, with MetaMask SIWE auth and Next.js app.
type: orbit-module
---

# dstore

Decentralized storage facade over the `filecoin` and `hippius` orbit modules.
Includes a FastAPI gateway with EIP-4361 (SIWE) MetaMask sign-in and a Next.js
app for browser uploads.

> The name `store` is taken by `mod/core/dstore/` (a local-FS key-value store).
> This module is its decentralized counterpart. The `filecoin` and `hippius`
> backends are *also* exposed as adapters under `mod/core/dstore/src/`.

## Capabilities

- `put` / `get` / `pin` / `list` across filecoin + hippius backends
- Per-owner indexing keyed by Ethereum address from SIWE
- FastAPI gateway: `/nonce`, `/verify`, `/put`, `/get`, `/list`, `/pin`
- Next.js app: MetaMask connect → SIWE sign-in → upload to backend
- Owner-scoped object lists pulled from local SQLite index

## Usage

### Python

```python
import mod as m
s = m.mod('dstore')()
s.put('/path/to/file', backend='both', owner='0xabc')
s.get('bafy...')
s.list(owner='0xabc')
s.status()
```

### CLI

```bash
m ddstore/status
m ddstore/backends
m ddstore/put /path/to/file backend=filecoin owner=0xabc
m ddstore/get bafy...
m ddstore/list owner=0xabc
m ddstore/start    # start filecoin + hippius daemons
```

### Serve API + app

```bash
m ddstore/serve            # via mod core (api + app)
# or directly:
cd ~/mod/mod/orbit/store && uvicorn api.api:app --port 50150 --reload
cd ~/mod/mod/orbit/dstore/app && npm install && npm run dev
```

App: http://localhost:50151 — API: http://localhost:50150

## SIWE auth flow

```
client                            server
  | --- GET /nonce?address=0x... ----> |
  | <-- {nonce, domain, origin} ------ |
  | (MetaMask personal_sign SIWE msg)  |
  | --- POST /verify {msg,sig} ------> |
  |   server: ecrecover + nonce check  |
  | <-- {token, expires_in} ---------- |
  | --- Authorization: Bearer <tok> -> |  (all auth'd endpoints)
```

Tokens are HMAC-SHA256 over a JSON payload `{sub: address, exp: ...}`,
signed with `STORE_JWT_SECRET`.

## API

| Function | Description |
| -------- | ----------- |
| `put(path, backend='filecoin', owner=None, key=None)` | Upload to filecoin / hippius / both. |
| `get(cid, backend=None, out=None)` | Retrieve by CID, with cross-backend fallback. |
| `pin(cid, backend, owner=None)` | Pin a CID on chosen backend(s). |
| `list(owner=None, backend=None, limit=100)` | List indexed objects. |
| `rm(cid)` | Drop a record. |
| `status()` | Module + backend status. |
| `backends()` | Available backends. |
| `start()` | Start both backend daemons. |
| `stop()` | Stop both backend daemons. |

## Structure

```
dstore/
├── dstore/mod.py            # Mod anchor class
├── api/api.py              # FastAPI + SIWE
├── app/                    # Next.js app (MetaMask + SIWE)
│   ├── src/app/page.tsx
│   ├── src/lib/wallet.ts
│   └── src/lib/api.ts
├── config.json
└── skill.md
```

State: `~/.store-mod/store.db` (SQLite index).

## Env vars

- `STORE_JWT_SECRET` — HMAC secret (generated per-run if absent — set this for stable sessions)
- `STORE_DOMAIN` — SIWE domain field (default `localhost:50151`)
- `STORE_ORIGIN` — SIWE URI field (default `http://localhost:50151`)
- Filecoin backend: see `filecoin/skill.md`
- Hippius backend: see `hippius/skill.md`

## Mod protocol

Anchor class `Mod` in `dstore/mod.py`. Reachable as `m.mod('dstore')()` or `m ddstore/<fn>`.
Composes the `filecoin` and `hippius` orbit modules via `m.mod('filecoin')()` / `m.mod('hippius')()`.
