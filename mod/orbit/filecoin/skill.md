---
name: filecoin
description: Lotus daemon manager + Filecoin storage deals with HTTP gateway fallback for put/get/pin.
type: orbit-module
---

# filecoin

Manage a local `lotus` daemon for real Filecoin storage deals, with an HTTP
gateway fallback (Lighthouse / public IPFS) so `put`/`get` work even when no
node is running.

## Capabilities

- Start/stop a lotus daemon (subprocess, PID-tracked)
- Import files and initiate storage deals (`client deal --auto`)
- Retrieve CIDs from chain or fall back to public gateways
- Track uploaded objects per-owner in a local SQLite index
- Inspect wallet, balance, deal list

## Usage

### Python

```python
import mod as m
f = m.mod('filecoin')()
f.start_node()
f.put('/path/to/file', owner='0xabc...')
f.get('bafy...')
f.deals()
f.wallet()
```

### CLI

```bash
m filecoin/status
m filecoin/start_node
m filecoin/put /path/to/file owner=0xabc
m filecoin/get bafy...
m filecoin/deals
m filecoin/stop_node
```

## API

| Function | Description |
| -------- | ----------- |
| `put(path, owner=None, deal=False)` | Import file. With `deal=True` and daemon running, initiates storage deal. Else uploads via gateway. |
| `get(cid, out=None)` | Retrieve a CID via lotus or fallback gateway. |
| `pin(cid, owner=None)` | Pin/deal a CID. |
| `list(owner=None, limit=100)` | List recorded objects. |
| `rm(cid)` | Drop a record from the local index. |
| `start_node(network='mainnet')` | Spawn `lotus daemon`. |
| `stop_node()` | SIGTERM the daemon. |
| `node_status()` | Daemon alive + sync state. |
| `deals(limit=50)` | Active deals from `lotus client list-deals`. |
| `wallet()` | List wallet addresses. |
| `balance(address=None)` | Wallet balance. |
| `status()` | Combined module + daemon status. |

## Structure

```
filecoin/
├── filecoin/mod.py         # Mod class
├── config.json             # ports, endpoints, env
└── skill.md                # this file
```

State: `~/.filecoin-mod/filecoin.db` (SQLite index), `~/.filecoin-mod/lotus.pid`, `~/.filecoin-mod/lotus.log`.

## Env vars

- `LOTUS_BIN` — path to lotus binary (defaults to `which lotus`)
- `LOTUS_PATH` — lotus state dir (default `~/.lotus`)
- `FILECOIN_GATEWAY` — HTTP gateway URL (default `https://node.lighthouse.storage`)
- `FILECOIN_GATEWAY_TOKEN` — bearer token for authenticated gateway uploads

## Mod protocol

Anchor class `Mod` in `filecoin/mod.py`. Inherits nothing — plain class. Functions exposed via `fns` list in `config.json`. Reachable as `m.mod('filecoin')()` or `m filecoin/<fn>` from the CLI.
