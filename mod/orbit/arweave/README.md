# arweave

Store and retrieve data on [Arweave](https://arweave.net) via a Python client, FastAPI server, and a minimal web app — all served on a single port (**:50153**).

Reads always work through public Arweave gateways and need no credentials. Uploads that actually land on the permaweb need a wallet JWK; without one, `put` records a **local-only placeholder** (it does not upload).

## Backends

| Backend     | What it does                                       | Required config |
|-------------|----------------------------------------------------|-----------------|
| Gateway     | Public reads + tx/price/network info (always works) | `ARWEAVE_GATEWAY` (default `https://arweave.net`; falls back to `arweave.dev`, `g8way.io`) |
| Wallet      | Real signed uploads via `arweave-python-client`    | `$ARWEAVE_WALLET` or `~/.mod/arweave/wallet.json` (off-tree, never committed) |
| Local index | SQLite object index at `~/.arweave-mod/index.db`   | —               |

`put` without a wallet returns `{txid: "sha256-…", backend: "local", pinned: false, warning: …}` and indexes it locally so you can list/track it, but nothing is written to Arweave. Fetching a `sha256-…` placeholder errors by design — configure a wallet and re-upload to get a real weave tx id.

## Running

Served on a single port for both API and app. In the fleet it runs under **pm2 as `arweave-api`** and is registered in the module namespace (reachable at `{host}/arweave` and `{host}/arweave/api`).

```sh
# fleet (durable, survives restarts)
sudo pm2 start _serve.sh --name arweave-api   # already registered; restart with: sudo pm2 restart arweave-api

# ad-hoc
m arweave/serve                                # → uvicorn on :50153, logs to /tmp/arweave/server.log
```

Check connectivity any time with `m arweave/status` — it reports the gateway height, whether a wallet is configured, and how many objects are indexed locally.

## Usage

### Python

```python
import mod as m
ar = m.mod('arweave')()

ar.put({'hello': 'arweave'})         # → {'txid': '…', 'size': …, 'backend': 'local'|'arweave'}
ar.get('…')                           # → {'hello': 'arweave'}  (parsed JSON when possible)
ar.put_file('/path/to/file.bin')     # → {'txid', 'size', 'backend'}
ar.list()                            # local index, newest first
ar.tx('…')                           # Arweave tx record
ar.price(1024)                       # storage cost in winston
ar.rm('…')                           # drop from the local index
ar.status()                          # gateway height + wallet + indexed count
```

> `Mod.call()` only issues GET/POST, so the DELETE route (`/rm`) 405s through it — call `ar.rm(txid)` directly.

### CLI

```sh
m arweave/serve
m arweave/put data='{"hello":"arweave"}'
m arweave/get txid=…
m arweave/status
m arweave/price num_bytes=1024
```

### Web

```sh
m arweave/serve
# open http://localhost:50153  (status pill shows gateway height + wallet state)
```

## API

All endpoints are mounted under `/api`:

- `GET  /api/health` — liveness
- `GET  /api/info` — module info + configured backends
- `GET  /api/status` — gateway connectivity, wallet state, indexed count
- `POST /api/put` — JSON body `{data, name?}` or multipart `file`
- `GET  /api/get/{txid}` — `?raw=true` for raw bytes
- `GET  /api/list?limit=100` — local index
- `DELETE /api/rm/{txid}` — remove from local index
- `GET  /api/tx/{txid}` — Arweave tx record
- `GET  /api/price/{num_bytes}` — cost in winston
- `GET  /api/network` — gateway network info
</content>
</invoke>
