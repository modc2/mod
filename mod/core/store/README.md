# mod store

The mod **store** module: a local key-value store **and** an app for
decentralized storage via the `filecoin` and `hippius` orbit modules, gated
by the mod **protocol auth system** (wallet-signed tokens), a per-address
**whitelist**, and per-user **storage quotas**. Runs under **pm2**.

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
| POST   | `/put`       | ✓ wl | multipart upload (form: file, backend, key) |
| GET    | `/get`       | —    | retrieve by CID |
| POST   | `/pin`       | ✓ wl | pin a CID on a backend |
| GET    | `/list`      | ✓    | list caller's objects |
| DELETE | `/rm`        | ✓ wl | remove an index record |

`✓` = valid protocol token required; `✓ wl` = token **and** whitelist membership.

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
