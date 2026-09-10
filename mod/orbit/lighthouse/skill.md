---
name: lighthouse
description: Perpetual IPFS/Filecoin storage (lighthouse.storage) with a bridge that registers every CID in the store module — CLI, API :50680, console :50681/lighthouse, MCP server (14 tools, schema at GET /mcp).
type: orbit-module
---

# lighthouse

Store bytes forever on IPFS/Filecoin through lighthouse.storage, then register
the CID in the **store** module so it gains visibility, timed grants, data
pools and the marketplace — without the store ever holding the bytes.

## When to reach for it

- something must outlive this box → `push` (upload + register)
- something already in the store must become perpetual → `mirror`
- "where does this CID live / can I read it" → `preview`, `get`, `store/objects`
- "why won't it store" → `store` — it names the blockers instead of guessing

## Auth in one line

Everything authed takes a mod-protocol token (`m.mod('auth')().token({})`) as
`Authorization: Bearer …`, and the **store bridge forwards that same token** —
this module holds no store credential, so the store's whitelist, terms and
quota apply to whoever signed.

## CLI

```bash
m lighthouse/status                 # key configured? gateway? index size?
m lighthouse/store                  # the store link + blockers (can_push)
m lighthouse/set_key lh_...         # owner: persist the deployment key (0600)

m lighthouse/put ./file.pdf         # upload only            → cid
m lighthouse/push ./file.pdf public=true pool=<id>
                                    # upload AND register in the store
m lighthouse/mirror <store-cid>     # store object → Lighthouse → registered back
m lighthouse/get bafy... out=/tmp/x
m lighthouse/list
m lighthouse/serve                  # pm2: lighthouse-api + lighthouse-app
```

## Python

```python
import mod as m
lh = m.mod('lighthouse')()

lh.store()          # {'reachable':.., 'authorized':.., 'blockers':[..], 'can_push':..}
lh.push('/tmp/report.pdf', public=True)
#   → {'cid':.., 'url':.., 'store': {'registered': True, ...}}
lh.mirror('QmcDF4…')            # → adds 'source_cid' and 'same_cid'
```

## HTTP

`http://localhost:50680` · `https://modc2.com/api/lighthouse` ·
`/lighthouse/_api` from the console's origin.

```bash
TOKEN=$(python3 -c "import mod as m; print(m.mod('auth')().token({}))")
H="Authorization: Bearer $TOKEN"

curl -H "$H" localhost:50680/store                      # can I push, and why not
curl -H "$H" -X POST localhost:50680/store/terms/accept # sign the store's terms
curl -H "$H" -F file=@x.pdf -F public=true localhost:50680/put
curl -H "$H" -X POST localhost:50680/store/mirror -d '{"cid":"Qm…"}' \
     -H 'content-type: application/json'
curl      localhost:50680/preview?cid=Qm…               # no auth: gateway read
```

Bring your own Lighthouse key instead of the box's with `-H "x-lh-key: lh_…"` —
never stored, discarded when the request ends.

## MCP

14 tools over the same code. `GET /mcp` is the whole schema as a document — read
it rather than guessing at arguments.

```bash
claude mcp add --transport http lighthouse http://localhost:50680/mcp
python3 mcp.py                       # stdio, with this box's own keys
python3 mcp.py --tools               # print the schema and exit
curl -s localhost:50680/mcp | jq .tools[].name
m lighthouse/mcp_tools name=put      # one tool's inputSchema, from the CLI
m lighthouse/mcp_call tool=status    # run one locally
```

`lighthouse_status` · `lighthouse_put` · `lighthouse_preview` ·
`lighthouse_get` · `lighthouse_list` · `lighthouse_pin` · `lighthouse_forget` ·
`lighthouse_account` · `lighthouse_store` · `lighthouse_terms` ·
`lighthouse_register` · `lighthouse_objects` · `lighthouse_mirror` ·
`lighthouse_set_key`

- Auth is unchanged: a tool that acts for a signer needs `Authorization: Bearer
  <protocol token>` on the MCP request, and that token is what reaches the
  store. `lighthouse_status`, `lighthouse_preview` and `lighthouse_store` need
  none.
- `lighthouse_get`, `lighthouse_set_key` and `path` on `lighthouse_put` are
  **stdio only** — over HTTP they refuse and name the route to use instead.
  Send `text` rather than `path` when you are a remote caller.
- Per-call Lighthouse key: `key` on the read tools, `api_key` on `put`/`mirror`
  (where `key` already means the object's name). Never written to disk.
- Start with `lighthouse_status` and read `store.can_push` before uploading —
  a blocked store is cheaper to find there than in a failed registration.

## Reading the answers

- `store.registered: false` on an upload — the bytes ARE pinned; only the store
  step failed, and `store.error` says why (403 not whitelisted, 451 terms
  unsigned, 503 store down). Retry with `POST /store/register`.
- `same_cid: false` on a mirror — chunking differed; the Lighthouse CID is the
  registered one, the store CID is in `source_cid`.
- `DELETE /rm` does not unpin. Perpetual means perpetual; the row leaves this
  module's index and the CID stays retrievable.
- `/get` needs no token by design — a CID is public bytes. Gate reads in the
  store, not here.

## Gotchas

- No Lighthouse key ⇒ every upload path 400s with instructions. Reads and the
  whole store bridge still work.
- `scope=all` on `/list` is owner-only; the owner is the first address that
  signed in against the deployment (`~/.mod/lighthouse/owner.json`).
- The store must be reachable on `:50152` for the bridge; `m lighthouse/store`
  reports that as a state, never a crash.
