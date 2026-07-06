# localfs

Content-addressable local filesystem. IPFS-compatible CIDs (v0 + v1) without
running a daemon. Wraps the canonical `LocalFS` implementation that lives under
`mod/core/store/src/localfs` and exposes it as an orbit module with an HTTP
API and a static web UI.

## Capabilities

- **Put / Get** — store bytes or JSON, retrieve by CID
- **CID-compatible** — produces the same `Qm…` digests as IPFS (UnixFS DAG-PB)
- **Pinning** — pin/unpin to control garbage collection
- **GC** — sweep unpinned blocks
- **Stats** — block count, pinned count, on-disk size
- **App + API** — FastAPI on `:8860`, static UI on `:8861`

## Usage

### Python

```python
import mod as m
fs = m.mod('localfs')()

cid = fs.put({"hello": "world"})    # → "Qm..."
fs.get(cid)                          # → {"hello": "world"}
fs.cid(b'hello')                     # compute without storing
fs.pin(cid); fs.unpin(cid)
fs.pins()                            # list pinned
fs.stats()
fs.gc()
```

### CLI

```bash
m localfs                            # info (default forward)
m localfs/put '{"hello":"world"}'
m localfs/get Qm...
m localfs/stats
m localfs/serve                      # api :8860 + app :8861
m localfs/kill
```

## API Endpoints

| Method | Path             | Description                         |
|--------|------------------|-------------------------------------|
| GET    | /health          | Health check                        |
| GET    | /info            | Module info                         |
| GET    | /stats           | Storage stats                       |
| POST   | /put             | Store body, return CID              |
| GET    | /get/{cid}       | Retrieve by CID                     |
| POST   | /cid             | Compute CID without storing         |
| GET    | /pins            | List pinned CIDs                    |
| POST   | /pin/{cid}       | Pin a CID                           |
| POST   | /unpin/{cid}     | Unpin a CID                         |
| DELETE | /rm/{cid}        | Remove a CID                        |
| POST   | /gc              | Garbage-collect unpinned blocks     |

## Structure

```
localfs/
├── localfs/mod.py     # Mod class (wraps core LocalFS)
├── api/api.py         # FastAPI backend
├── app/index.html     # Static web UI (served by http.server)
├── config.json
└── skill.md
```

## Environment

- `~/.localfs/` — default on-disk store (override via `storage_path` in
  config.json or `LocalFS(storage_path=…)` ctor arg).

## Mod Protocol

- name: `localfs`
- api port: `8860`
- app port: `8861`
- owner: same as dev module — gates registry writes via `dev/register name=localfs`.
- registered in the dev merkle alongside `dev`, `claude`, `codex`.
