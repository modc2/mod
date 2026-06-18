# arweave

Store and retrieve data on Arweave via a Python client, FastAPI server, and a minimal web app — all served on a single port.

## Backends

| Backend     | What it does                                       | Required env             |
|-------------|----------------------------------------------------|--------------------------|
| Gateway     | Public reads via Arweave gateways (always works)   | `ARWEAVE_GATEWAY` (default: `https://arweave.net`) |
| Wallet      | Real signed uploads via `arweave-python-client`    | `ARWEAVE_WALLET` (path to wallet JSON) |
| Local index | SQLite under `~/.arweave-mod/index.db`             | —                        |

Without `ARWEAVE_WALLET`, `put` records a local-only placeholder (it does not upload to Arweave). Reads work for any real tx id via the gateway list.

## Usage

### Python

```python
import mod as m
ar = m.mod('arweave')()

ar.put({'hello': 'arweave'})         # → {'txid': '…', 'size': …, 'backend': 'arweave'}
ar.get('…')                           # → {'hello': 'arweave'}
ar.put_file('/path/to/file.bin')     # → {'txid', 'size', 'backend'}
ar.list()                            # local index
ar.tx('…')                           # tx record
ar.price(1024)                       # cost in winston
```

### CLI

```sh
m arweave/serve
m arweave/put data='{"hello":"arweave"}'
m arweave/get txid=…
m arweave/status
```

### Web

```sh
m arweave/serve
# open http://localhost:50151
```

## API

All endpoints are mounted under `/api`:

- `GET  /api/health`
- `GET  /api/info`
- `GET  /api/status`
- `POST /api/put` — JSON body `{data, name?}` or multipart `file`
- `GET  /api/get/{txid}` — `?raw=true` for raw bytes
- `GET  /api/list?limit=100`
- `DELETE /api/rm/{txid}`
- `GET  /api/tx/{txid}`
- `GET  /api/price/{num_bytes}`
- `GET  /api/network`
