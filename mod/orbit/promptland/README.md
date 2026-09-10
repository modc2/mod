# promptland ✎

Store and share prompts under wallet identities.

- **API** `:50580` (FastAPI) — gateway route `/api/promptland`
- **App** `:50581` (zero-dep console, vendored ethers) — `/promptland`
- **State** `~/.mod/promptland/` (off-chain: owner, server secret, per-address prompt files, gallery index)

## Auth — same flow as the Build console

`GET /auth/challenge?address=` returns a nonce'd message → the wallet signs it →
`POST /auth/verify {address, signature, message}` recovers the signer and mints an
HMAC bearer token (`address:timestamp:hmac`, 7-day TTL, secret in
`~/.mod/promptland/server.secret`). The **first wallet ever to verify claims
ownership** (`~/.mod/promptland/owner.json`). Sign-in stays open after that —
every wallet gets its own private library.

Three ways in, mirroring build:

1. **Browser wallet** — MetaMask / any injected EVM wallet (`personal_sign`)
2. **Local wallet** — an ethers seed generated in the browser, kept in
   localStorage `promptland_seed`, reused across visits
3. **Password key** — deterministic key from `keccak256(password)`; same
   password = same identity anywhere

## Prompts & sharing

Each address owns its prompts (`/prompts` CRUD). `POST /prompts/{id}/share`
pins `{type: "promptland/prompt@1", name, description, tags, body, author,
shared_at}` to **localfs** and lists the CID in the public gallery
(`GET /shared`). Anyone can read a shared prompt by CID (`GET /shared/{cid}`)
and any signed-in wallet can `POST /import {cid}` it into their own library.
The author (or the instance owner) can delist a gallery entry.

## SDK

```python
import mod as m
pl = m.mod('promptland')()
p = pl.save_prompt('reviewer', 'You are a strict code reviewer…', tags=['review'])
cid = pl.share_prompt(p['id'])['cid']   # localfs CID, importable anywhere
pl.import_prompt(cid)
```

CLI callers act as the claimed owner (host operator trust); browser callers
always go through the wallet-signed session.

## Run

```
m promptland/serve       # or: pm2 start promptland.api / promptland.app
```
