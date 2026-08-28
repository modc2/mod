# bitstore

Anchor CIDs from IPFS/Filecoin/Hippius/LocalFS onto Bitcoin, Kaspa, and Bittensor
as proof-of-existence records. Anchors are stored locally (`~/.bitstore/anchors.db`)
and marked `pending` until a chain RPC is configured, then `confirmed` on broadcast.

## Run

```bash
pm2 start bash --name bitstore-api -- -c "m serve mod=bitstore port=50250 key=bitstore remote=0"
# gateway: modc2.com/api/bitstore (route: true in config.json, applied via `m caddy/apply`)
```

## Usage

```bash
m bitstore/status                          # chain connectivity + anchor counts
m bitstore/anchor QmXyz... source=ipfs chains=bitcoin,kaspa
m bitstore/from_localfs /path/to/file      # hash file → CID → anchor
m bitstore/verify QmXyz...                 # is this CID anchored?
m bitstore/history chain=kaspa
```

```python
import mod as m
b = m.mod('bitstore')()
b.from_ipfs('QmXyz...')
b.verify('QmXyz...')
```

## Chains

- **Bitcoin** — OP_RETURN via `BITCOIN_RPC` (bitcoind wallet RPC); offline/pending without it
- **Kaspa** — script payload via `KASPA_RPC`; status reads public api.kaspa.org
- **Bittensor** — `set_commitment` (needs `pip install bittensor` + wallet)

On-chain payload: `BS:<src4>:<cid>` (sha256-truncated if over 80 bytes).

Read fns (`status`, `verify`, ...) are public; anchor/write fns require mod
protocol auth — anything that can spend from a wallet stays gated.
