# mod store

The mod **store** module: a local key-value store **and** an app for
decentralized storage via the `localfs`, `filecoin` and `hippius` backends,
gated by the mod **protocol auth system** (wallet-signed tokens), a per-address
**whitelist**, and per-user **storage quotas**. Runs under **pm2**.

On top of storage it adds a full **access model**:

- **Private-by-default + publish** — every object is private to its owner until
  explicitly made public.
- **Timed access grants** — give any address time-bounded read or read+write
  access to one object or your whole set; it auto-expires.
- **Data pools** — named shared spaces; every member gets mutual read access to
  objects pooled in, with roles (owner/editor/viewer) and optional timed
  membership.
- **QR auth handoff** — move a signed-in session from computer to phone by
  scanning a one-time, short-TTL QR — no wallet needed on the second device.
- **CID-agnostic** — objects can be native localfs/filecoin/hippius CIDs **or**
  references to any external system (arweave tx, ipfs from another node, s3 key,
  …) registered with an optional gateway URL.
- **File / text / image input** — store an uploaded file, pasted text, or a
  captured photo; all are content-addressed the same way.

Access state lives OFF-CHAIN in `~/.mod/store/` (`access.db` for grants / pools
/ handoffs / per-object ACL; never committed).

```
mod/core/store/
├── src/                      # local-FS Store class + backend adapters
│   ├── mod.py                # Store (KV) + serve()/app()/api()/backends()
│   ├── filecoin/mod.py       # adapter → m.mod('filecoin')()
│   ├── hippius/mod.py        # adapter → m.mod('hippius')()
│   └── localfs/              # localfs backend (existing)
├── api/api.py                # FastAPI gateway: protocol auth + whitelist + quota
├── app/                      # Next.js app: wallet sign-in → upload → CID + QR
├── config.json
├── ecosystem.config.js       # pm2 process defs (store-api / store-app)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── docker-entrypoint.sh
├── serve.sh                  # pm2 launcher (no docker)
└── test/
```

## Run

### Docker (recommended)

```bash
cd ~/mod/mod/core/store
docker compose up --build         # API: 50150, App: 50151
# open http://localhost:50151
```

Tear down:
```bash
docker compose down -v            # also remove the data volume
```

The compose file uses the repo root as its build context so the image can
include `mod/`, `mod/orbit/filecoin/`, `mod/orbit/hippius/`, `mod/orbit/dstore/`,
and the root `config.json` / `requirements.txt` the mod runtime needs.

### Local (no docker) — pm2

```bash
~/mod/mod/core/store/serve.sh         # pm2 start store-api + store-app
~/mod/mod/core/store/serve.sh status  # pm2 status
~/mod/mod/core/store/serve.sh logs     # tail store-api logs
~/mod/mod/core/store/serve.sh stop     # pm2 stop both
```

Or programmatically via the mod protocol:

```bash
m store/serve                     # pm2 start (store-api + store-app)
m store/serve no_api=True         # app only
m store/serve no_app=True         # api only
m store/serve prod=True           # next start (built) instead of next dev
m store/stop                      # pm2 stop
```

## Endpoints

| Method | Path         | Auth | Notes |
|--------|--------------|------|-------|
| GET    | `/health`    | —    | liveness |
| GET    | `/status`    | —    | module + backend status |
| GET    | `/backends`  | —    | list backends |
| GET    | `/me`        | ✓    | caller address, `admin`, `authorized`, `quota` |
| GET    | `/quota`     | ✓    | caller usage + limit |
| POST   | `/quota`     | owner| set a per-user byte limit `{address, limit_bytes}` |
| GET    | `/whitelist` | —    | owner + allowed uploader addresses |
| POST   | `/whitelist` | owner| add an address `{address}` |
| DELETE | `/whitelist` | owner| remove an address `?address=0x…` |
| POST   | `/put`       | ✓ wl | multipart upload (form: `file, backend, key, public, pool`) |
| POST   | `/register`  | ✓ wl | reference an external CID `{cid, scheme?, backend?, url?, public?, pool?}` |
| GET    | `/get`       | opt  | retrieve by CID; private objects need `?token=` or Bearer |
| GET    | `/preview`   | opt  | peek content: truncated text + `size`/`truncated` flag |
| GET    | `/object`    | opt  | full info: stored when/by-whom, backends, visibility, semhash, **who has access** |
| POST   | `/publish`   | owner| flip an object private⇄public `{cid, public}` |
| POST   | `/pin`       | ✓ wl | pin a CID on a backend |
| GET    | `/pins`      | ✓    | list the caller's pinned objects |
| DELETE | `/pin`       | ✓ wl | unpin `?cid=…[&backend=…]` |
| GET    | `/list`      | ✓    | list caller's objects (+`visibility/scheme/url/semhash`) |
| GET    | `/search`    | ✓    | filter by `q`/backend/scheme/visibility; rank by `semantic_q` |
| GET    | `/shared`    | ✓    | objects shared **with** caller (grants + pools) |
| DELETE | `/rm`        | ✓ wl | remove an index record |
| DELETE | `/pools/{id}`| owner| delete a pool (members + objects) |
| POST   | `/tickets`   | ✓    | mint single-use short-TTL fetch ticket `{cid, ttl_seconds=10}` |
| GET    | `/tickets`   | ✓    | the caller's active (unused, unexpired) tickets |
| GET    | `/ticket/{code}` | — | redeem → serve the object **exactly once** (anti-replay) |
| POST   | `/grants`    | ✓    | grant timed access `{grantee, cid?, scope, ttl_seconds?}` |
| GET    | `/grants`    | ✓    | grants I made + grants made to me |
| DELETE | `/grants/{id}` | ✓  | revoke a grant |
| POST   | `/pools`     | ✓ wl | create a pool `{name, description?}` |
| GET    | `/pools`     | ✓    | pools I own or belong to |
| GET    | `/pools/{id}`| member| pool members + objects |
| POST   | `/pools/{id}/members` | owner/editor | add a member `{address, role?, ttl_seconds?}` |
| DELETE | `/pools/{id}/members` | owner/self | remove a member `?address=0x…` |
| POST   | `/pools/{id}/objects` | owner/editor | pool an object `{cid, backend?, key?}` |
| DELETE | `/pools/{id}/objects` | owner/editor | unpool an object `?cid=…` |
| POST   | `/handoff`   | ✓    | mint a one-time code carrying my session token `{ttl_seconds?}` |
| GET    | `/handoff/{code}` | — | claim → token (single use, short TTL) |

`✓` = valid protocol token required; `✓ wl` = token **and** whitelist membership;
`opt` = optional token (anonymous allowed for public objects).

## Access model (grants · pools · QR handoff · CID-agnostic)

Private auth/access state lives under `~/.mod/store/` (never committed):

| File | What |
|------|------|
| `owner.json` / `whitelist.json` / `quotas.json` | admin, uploaders, per-user byte limits |
| `access.db` (SQLite) | per-object ACL, timed grants, pools + members + objects, QR handoff codes |

- **Visibility** — uploads are **private** by default (`public=true` to open, or
  `POST /publish` later). Unknown CIDs (stored before this layer existed) are
  treated as public for back-compat. `/get` of a private object requires the
  owner, a live grant, or shared pool membership; pass the token as a Bearer
  header **or** `?token=` so a scanned QR / plain link can authenticate.
- **Grants** — `POST /grants {grantee, cid, scope:"read"|"write", ttl_seconds}`.
  Omit `cid` (or use `"*"`) to share your whole object set. Grants auto-expire
  and are listed/revoked via `GET`/`DELETE /grants`.
- **Pools** — `POST /pools` then add members and objects. Every live member can
  read every pooled object — mutual access. Membership can be time-boxed
  (`ttl_seconds`); access lapses automatically when it expires.
- **QR auth handoff** — on the desktop, `POST /handoff` mints a one-time code;
  the app shows it as a QR pointing at `…/store/?claim=<code>`. The phone scans,
  the app calls `GET /handoff/{code}` to claim the token, and is signed in — no
  MetaMask on the phone. Codes are single-use and expire in ~3 min.
- **CID-agnostic** — `POST /register {cid, url, scheme?}` indexes a CID from any
  other system as a first-class store object (listable, shareable, poolable)
  without uploading bytes; `/get` redirects to its gateway `url`. Schemes are
  inferred (`ipfs`/`arweave`/`s3`/…) but never enforced.

## Search, semantic hash, tickets & object info

- **Search** — `GET /search?q=…` filters the caller's objects (and, with
  `scope=shared|all`, those shared with them) by CID/key substring plus exact
  `backend`/`scheme`/`visibility`. `GET /search?semantic_q=…` ranks results by
  **semantic similarity** (see below), nearest first, attaching `distance` +
  `similarity` to each.
- **1-bit semantic encoder** (`api/semantic.py`) — a fully **local**, pure-stdlib
  SimHash / random-hyperplane LSH. Every stored object gets a binary semantic
  hash (a 64-bit latent vector, shown as 16 hex chars). Cosine-similar content →
  small **Hamming distance**, so semantic / near-duplicate search is just
  `popcount(a ^ b)` — fast to scan and LSH-bandable. No model download, no
  network; the interface would let a heavier local embedder slot in later.
- **One-time access tickets** (`POST /tickets`, default `ttl_seconds=10`) — a
  single-use, short-TTL capability for one fetch of a CID (even a private one).
  The QR/link works **once** and only within the window; the claim is atomic
  (`UPDATE … WHERE claimed=0`, rowcount-checked) so a captured ticket can never
  be replayed — even by two requests racing in parallel.
- **Object info** — `GET /object?cid=…` returns when it was stored, who stored
  it, backends, visibility, pinned state, the semantic hash, and (for the owner)
  the full access roster: every active grant (grantee/scope/expiry) and every
  pool it lives in. Non-owners only learn whether *they* can read it.
- **Pin management** — `GET /pins` lists your pins; `POST /pin` pins (and tracks)
  a CID; `DELETE /pin?cid=…` unpins.
- **Content viewer** — `GET /preview?cid=…&max_bytes=…` returns up to N bytes
  (text decoded when possible) plus the full size and a `truncated` flag, so the
  app can show huge content truncated and offer "copy all".

The app surfaces all of this: instant CID/name search + a 🧠 semantic toggle, a
per-object content viewer (truncate + copy-all), a 📱 one-time-ticket QR for
phone hand-off, an object-info panel, a fetch-by-CID box, and a pins tab.

## On-chain (BlocTime + chain Registry)

The store integrates with the `chain` core module (same pattern as `claude`):

- **BlocTime-gated access** — a staked **BlocTime holder** is authorized to store
  as if on the whitelist, so access can be earned on-chain without the owner
  editing `~/.mod/store/whitelist.json`. Cached briefly; every RPC failure
  degrades to "not a holder" so the request path never blocks on chain issues.
  `GET /me` reports `bloctime` + `via: config|bloctime|open`.
- **Registry registration** — the module registers itself (`name → data`) in the
  chain mod's permissionless Registry for protocol discovery.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET  | `/onchain` | — | network, BlocTime gate, Registry registration state |
| GET  | `/onchain/bloctime` | ✓ | caller's BlocTime balance + holder flag |
| POST | `/onchain/register` | owner | register in the chain Registry (spends gas) |

```bash
m store/onchain                  # registration + gate status
m store/register_onchain         # register (idempotent; skips if data matches)
```

Config (`config.json`): `bloctime_gate` (default `true`), `chain_network`
(`testnet`), `bloctime_ttl` (cache secs), `onchain_registry` (auto-register on
startup, default `false` to avoid unprompted gas). Env overrides:
`STORE_BLOCTIME_GATE`, `STORE_CHAIN_NETWORK`.

## Protocol auth flow

1. App: MetaMask `personal_sign` over `JSON.stringify({data, time})`.
2. App assembles a base64url token `{data, time, key, signature}` — the envelope
   produced/verified by `mod core/server/auth` (`m.mod('auth')`).
3. App stores the token in `localStorage`, sends `Authorization: Bearer <token>`.
4. Server `AUTH.verify(token)` recovers the signer; the `key` field **is** the
   caller's address and the per-object `owner`. Tokens expire after
   `STORE_SESSION_TTL` (no server-side session/nonce state).

## Access control & quotas (off-chain)

Private auth state lives under `~/.mod/store/` (never in committed config):

| File | Shape | Meaning |
|------|-------|---------|
| `owner.json`     | `{"owner": "0x…"}`     | admin — full whitelist control, **unlimited** storage |
| `whitelist.json` | `["0x…", …]`           | addresses allowed to `put`/`pin`/`rm` |
| `quotas.json`    | `{"0x…": <bytes>}`     | per-address byte overrides |

- Empty whitelist **and** no owner ⇒ open access (bootstrap / back-compat).
- Non-admin addresses default to `quota_bytes` (config.json, default 100 MiB).
- `put` rejects with `413` when an upload would exceed the caller's allowance
  (`both` backend counts twice). The admin is never quota-limited.

To let yourself store: set `~/.mod/store/owner.json` to your address (admin,
unlimited), **or** add your address to `~/.mod/store/whitelist.json` (the owner
can also do this live via `POST /whitelist`).

## Environment

| Var | Default | Notes |
|-----|---------|-------|
| `STORE_SESSION_TTL` | `604800` (7d) | max protocol-token age, seconds |
| `STORE_PRIVATE_DIR` | `~/.mod/store` | off-chain owner/whitelist/quotas dir |
| `STORE_MODE` | `dev` | `prod` ⇒ `next start` under pm2 |
| `FILECOIN_GATEWAY` | `https://node.lighthouse.storage` | gateway for `put`/`get` when lotus not running |
| `FILECOIN_GATEWAY_TOKEN` | — | bearer token for gateway uploads |
| `HIPPIUS_S3_ENDPOINT` | `https://s3.hippius.com` | S3 gateway |
| `HIPPIUS_S3_KEY` / `_SECRET` / `_BUCKET` | — | S3 credentials |
| `HIPPIUS_IPFS_GATEWAY` | `https://get.hippius.network` | retrieval gateway |
| `STORE_API_PORT` / `STORE_APP_PORT` | `50150` / `50151` | port overrides |

## Architecture

```
   ┌─────────────────────┐
   │ Next.js (50151)     │  wallet sign → token; CID + QR per object
   └──────┬──────────────┘
          │ Bearer <protocol token>
   ┌──────▼──────────────┐
   │ FastAPI (50150)     │  AUTH.verify → whitelist + quota → /put /get …
   └──────┬──────────────┘
          │
   ┌──────▼──────────────┐
   │ orbit/dstore        │  unified put/get/list, SQLite index
   └──┬──────────────┬───┘
      │              │
 ┌────▼─────┐  ┌─────▼──────┐
 │ orbit/   │  │ orbit/     │
 │ filecoin │  │ hippius    │
 │ (lotus)  │  │ (substrate)│
 └──────────┘  └────────────┘
```
