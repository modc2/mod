---
name: hippius
description: Hippius substrate node manager + S3-compatible decentralized storage gateway.
type: orbit-module
---

# hippius

Manage a local Hippius substrate node, upload via the S3-compatible gateway,
and retrieve CIDs through IPFS-style gateways.

## Capabilities

- Start/stop the `hippius-node` substrate binary (subprocess)
- Upload files via S3 (boto3) — Hippius runs an S3 facade over its IPFS network
- Retrieve via IPFS gateway fallback
- Pin CIDs through the substrate IPFS pallet (RPC)
- Track recorded objects per-owner in SQLite

## Usage

### Python

```python
import mod as m
h = m.mod('hippius')()
h.start_node()
h.put('/path/to/file', owner='0xabc')
h.get('bafy...')
h.peers()
```

### CLI

```bash
m hippius/status
m hippius/start_node
m hippius/put /path/to/file owner=0xabc
m hippius/get bafy...
m hippius/peers
m hippius/stop_node
```

## API

| Function | Description |
| -------- | ----------- |
| `put(path, owner=None, key=None)` | Upload via S3 (preferred) or local node IPFS. |
| `get(cid, out=None)` | Retrieve by CID via gateway. |
| `pin(cid, owner=None)` | Pin via substrate IPFS pallet. |
| `list(owner=None)` | List recorded objects. |
| `rm(cid)` | Drop record. |
| `start_node(chain='mainnet')` | Launch substrate node. |
| `stop_node()` | SIGTERM the node. |
| `node_status()` | Daemon + RPC health. |
| `peers()` | Substrate peers. |
| `account()` | Chain/system info. |

## Structure

```
hippius/
├── hippius/mod.py
├── config.json
└── skill.md
```

State: `~/.hippius-mod/hippius.db`, `~/.hippius-mod/hippius.pid`.

## Env vars

- `HIPPIUS_BIN` — substrate node binary path
- `HIPPIUS_PATH` — base path (`~/.hippius`)
- `HIPPIUS_RPC` — substrate JSON-RPC URL
- `HIPPIUS_S3_ENDPOINT` / `HIPPIUS_S3_KEY` / `HIPPIUS_S3_SECRET` / `HIPPIUS_S3_BUCKET`
- `HIPPIUS_IPFS_GATEWAY` — retrieval gateway

## Mod protocol

Anchor class `Mod` in `hippius/mod.py`. Reachable as `m.mod('hippius')()` or `m hippius/<fn>`.
