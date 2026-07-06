# filecoin

Store and retrieve data on Filecoin via a Python client, FastAPI server, and a minimal web app — all served on a single port.

## Backends

| Backend     | What it does                                       | Required env             |
|-------------|----------------------------------------------------|--------------------------|
| Lotus RPC   | Chain head, deal lookups, raw JSON-RPC pass-through | `FILECOIN_RPC` (default: `https://api.node.glif.io/rpc/v1`), optional `FILECOIN_TOKEN` |
| Lighthouse  | Free uploads → Filecoin storage deals               | `LIGHTHOUSE_API_KEY`     |
| Gateway     | Public reads via IPFS gateways (always works)       | `FILECOIN_GATEWAY` (default: `https://gateway.lighthouse.storage`) |
| Local index | SQLite under `~/.filecoin-mod/index.db`             | —                        |

Without `LIGHTHOUSE_API_KEY`, `put` records a local-only placeholder (it does not upload to Filecoin). Reads work for any real CID via the gateway list.

## Usage

### Python

```python
import mod as m
fc = m.mod('filecoin')()

fc.put({'hello': 'filecoin'})        # → {'cid': 'bafy…', 'size': …, 'backend': 'lighthouse'}
fc.get('bafy…')                       # → {'hello': 'filecoin'}
fc.put_file('/path/to/file.bin')      # → {'cid', 'size', 'backend'}
fc.list()                             # local index
fc.deals('bafy…')                     # storage deals
fc.chain_head()                       # Lotus chain head
fc.rpc('Filecoin.StateNetworkName', [])
fc.serve()                            # bring up server at http://localhost:50150
```

### CLI

```bash
m filecoin/serve
m filecoin/put data='{"hello":"world"}'
m filecoin/get cid=bafy…
m filecoin/status
m filecoin/chain_head
```

### HTTP

Once `serve()` is running:

```
GET  /                       # web app
GET  /api/health
GET  /api/info
GET  /api/status
POST /api/put                # JSON {data, name?}  OR  multipart file
GET  /api/get/{cid}          # ?raw=true to force binary
GET  /api/list
DELETE /api/rm/{cid}
GET  /api/deals/{cid}
GET  /api/chain/head
POST /api/rpc                # {method, params}
```

## Layout

```
filecoin/
├── config.json
├── requirements.txt
├── mod.py                # Mod class — serve/kill/status + client proxies
├── filecoin/
│   ├── __init__.py
│   └── client.py         # FilecoinClient
├── api/
│   └── api.py            # FastAPI app
└── app/
    └── index.html        # Web UI (no build step)
```
