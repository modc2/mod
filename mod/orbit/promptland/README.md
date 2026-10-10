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

## Harvest — scrape public prompt collections

The HARVEST tab (and `/harvest/*` API) pulls prompts from open-licensed
collections on the internet into a local catalog: stdlib-only fetching, no
scraping SaaS, no API keys. Sources are small dicts handled by pluggable
adapters — `csv`, `json`, `github_tree` (walk a public repo's tree), `url`
(one document = one prompt). Defaults: **awesome-chatgpt-prompts** (CC0),
**fabric patterns** (MIT), **LLM-Prompt-Library** (MIT),
**ChatGPT-System-Prompts** (MIT).

Everything lands in `~/.mod/promptland/harvest/`, deduped by sha256 of the
normalized body, with source, license and origin URL kept for attribution —
re-running only adds what's new. Browsing the catalog is public; **keep →
library** copies an item into the caller's own library (any signed-in
wallet); running the scraper and editing sources are owner-only, since
fetches happen from this server. Budgets: ≤500 files per source, ≤2000 new
items per run, 40–200k chars per prompt.

```
GET  /harvest?q=&source=&offset=&limit=   browse/search (public)
GET  /harvest/{hid}                       full prompt (public)
POST /harvest/{hid}/keep                  copy into your library (auth)
POST /harvest/run {sources?, limit?}      background run (owner)
GET  /harvest/status                      live progress + stats
GET/POST /harvest/sources                 list (public) / add (owner)
```

Add your own source (owner): `{name, kind: csv|json|github_tree|url,
url|repo, branch?, include?/exclude? globs, name_col?/body_col?,
name_key?/body_key?, license?, tags?}`.

## SDK

```python
import mod as m
pl = m.mod('promptland')()
p = pl.save_prompt('reviewer', 'You are a strict code reviewer…', tags=['review'])
cid = pl.share_prompt(p['id'])['cid']   # localfs CID, importable anywhere
pl.import_prompt(cid)

pl.harvest()                             # scrape every configured source (sync)
pl.harvest_list(q='code review')         # search the catalog
pl.harvest_keep('7c746328e1fa2aee')      # copy one into the operator library
pl.harvest_add_source(name='my-list', kind='csv', url='https://…/prompts.csv')
```

CLI callers act as the claimed owner (host operator trust); browser callers
always go through the wallet-signed session.

## Run

```
m promptland/serve       # or: pm2 start promptland.api / promptland.app
```
