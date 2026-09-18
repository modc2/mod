# lighthouse

Perpetual decentralized storage, wired into the store module.

Bytes go to [lighthouse.storage](https://lighthouse.storage) — one payment, an
IPFS CID, a Filecoin deal, pinned perpetually. Then the CID is registered in the
**store** module, which is where visibility, timed grants, data pools and the
marketplace live. The bytes never move into the store: it keeps the gateway url
and redirects readers straight back here.

```
    you ──► lighthouse API ──► lighthouse.storage ──► IPFS + Filecoin (forever)
                    │
                    └────────► store /register   (visibility, grants, pools, market)
                                    ▲
                          your own protocol token, forwarded verbatim
```

Four faces, one module:

| face | where | what |
| --- | --- | --- |
| CLI | `mod.py` | `m lighthouse/put`, `m lighthouse/push`, `m lighthouse/store` |
| API | `api/api.py` → `:50680` | FastAPI, mod-protocol auth, BYOK header |
| console | `app/server.py` → `:50681/lighthouse` | plain ES modules, no build step |
| MCP | `mcp.py` → `POST /mcp` or stdio | 14 tools; schema at `GET /mcp` |

Behind the gateway: `modc2.com/lighthouse` (console) and
`modc2.com/api/lighthouse` (API). The console always calls its own origin at
`/lighthouse/_api`, so one build works in both places and the wallet token never
crosses an origin.

## The seam

This module holds **no store credential**. The caller's own protocol token —
the `{data, time, key, signature}` envelope their wallet signed — is forwarded
to the store as-is, so the store applies *its* whitelist, terms and quota to the
address that actually signed. If you could not store in the store directly,
nothing here will do it for you.

`GET /store` says exactly where you stand, and names what is still in the way:

```json
{
  "url": "http://127.0.0.1:50152", "reachable": true,
  "address": "0x7d7c…d123", "authorized": true, "terms_accepted": true,
  "quota": {"used_bytes": 62620003, "limit_bytes": 104857600},
  "blockers": [], "can_push": true
}
```

### When the store is asleep

The fleet's activator stops modules that have gone idle and restarts them when a
request arrives *through it* (`:9000`). This module talks to the store on its own
port, which the activator never sees — so a slept store would look permanently
dead from here. Every store call therefore knocks on `:9000/api/store/health`
once when the port is closed, waits for it, and retries: opening the console is
enough to bring the bridge back, at the cost of a few seconds on the first hit.

The knock is a plain proxied request, not `/_activator/control` with
`action=wake` — control-wake clears the host's `actl disable` flag, and a peer
module has no business overriding a deliberate "keep this off". A store the host
turned off stays off, and `blockers` says so in words:

```
the store module is not running at http://127.0.0.1:50152 — the host has
turned store off (`actl enable store` to allow it back)
```

Set `LIGHTHOUSE_ACTIVATOR_URL=` (empty) to switch waking off and just report a
down store.

## Quickstart

```bash
# 1. a Lighthouse key (https://files.lighthouse.storage → API Key tab)
m lighthouse/set_key lh_...

# 2. is the store link usable?
m lighthouse/store

# 3. store something forever AND register it
m lighthouse/push ./report.pdf public=true

# 4. make something the store already has perpetual
m lighthouse/mirror QmcDF4HMcnqKZu7vcNxA7Zayjx1ZNfvSLfPKgYc4YArSSJ

# services
m lighthouse/serve          # pm2: lighthouse-api + lighthouse-app
m lighthouse/stop
```

Python:

```python
import mod as m
lh = m.mod('lighthouse')()
lh.store()                       # the link, from this box's key
lh.push('/path/to/file')         # upload + register
lh.get('bafy…')                  # gateway retrieval (public IPFS fallbacks)
```

## API

`http://localhost:50680` — or `modc2.com/api/lighthouse`, or
`/lighthouse/_api` from the console's own origin.

| route | auth | what |
| --- | --- | --- |
| `GET /health` `GET /status` | — | liveness; key state, gateway, index size, store link |
| `GET /me` | token | address, owner flag, which key this request would use |
| `GET/POST /key` | token / owner | key status; persist the deployment key off-chain |
| `POST /put` | token | upload a file → CID; registers in the store unless `register=false` |
| `POST /put/text` | token | the same for a string |
| `GET /get?cid=` | — | stream through the gateway (`?download=1` to attach) |
| `GET /preview?cid=` | — | peek: decoded text, size, truncated flag |
| `GET /list` | token | this module's index (`?scope=all` — owner only) |
| `POST /pin` `DELETE /rm` | token | index bookkeeping — `rm` never unpins |
| `GET /usage` `GET /uploads` | token | Lighthouse's own account view for this key |
| `GET /store` | optional | the link + blockers |
| `GET /store/terms` `POST /store/terms/accept` | token | the store's terms; accept with your own signature |
| `POST /store/register` | token | reference an existing CID in the store — no bytes move |
| `GET /store/objects` | token | your store objects (`?all_backends=1` for every backend) |
| `POST /store/mirror` | token | store object → Lighthouse → registered back |
| `GET /mcp` `GET /mcp/tools` `GET /mcp/config` | — | the MCP schema, the tools, the client config |
| `POST /mcp` | per tool | MCP over Streamable HTTP (JSON-RPC 2.0) |

```bash
TOKEN=$(python3 -c "import mod as m; print(m.mod('auth')().token({}))")
curl -H "Authorization: Bearer $TOKEN" -F file=@./report.pdf -F public=true \
     http://localhost:50680/put
```

## MCP

The same work, spoken as tools. Two transports, and they are deliberately not
equal — a stdio server is a process someone started on this box with this box's
keys, an HTTP caller is not.

```bash
claude mcp add --transport http lighthouse https://modc2.com/api/lighthouse/mcp
# or, on the box:
python3 mcp.py                    # stdio, one JSON-RPC message per line
```

```json
{ "mcpServers": { "lighthouse": { "type": "http",
                                  "url": "http://localhost:50680/mcp" } } }
```

The schema is a document, not something you have to run a client to see:

```bash
curl -s localhost:50680/mcp        | jq .tools[].name   # everything
curl -s localhost:50680/mcp/tools  | jq                 # just the tools
curl -s localhost:50680/mcp/config | jq                 # what to paste
m lighthouse/mcp                                        # the same, from the CLI
m lighthouse/mcp_tools name=put                         # one tool's inputSchema
```

The console renders that same document under **mcp server** — every tool, every
argument, the raw `inputSchema`, and a copy button for the client config.

| tool | needs | what |
| --- | --- | --- |
| `lighthouse_status` | — | key, gateway, index, and the store's verdict on you (`can_push`) |
| `lighthouse_put` | token | `text` (or `path`, stdio) → CID, registered in the store |
| `lighthouse_preview` | — | peek at a CID through the gateway |
| `lighthouse_get` | stdio | download a CID to a path on the box |
| `lighthouse_list` | token | this module's index |
| `lighthouse_pin` `lighthouse_forget` | token | index bookkeeping — `forget` never unpins |
| `lighthouse_account` | token | Lighthouse's own usage + file listing for the key |
| `lighthouse_store` | — | the link + blockers |
| `lighthouse_terms` | token | read the store's terms; `accept=true` signs them |
| `lighthouse_register` | token | reference an existing CID in the store |
| `lighthouse_objects` | token | your store objects |
| `lighthouse_mirror` | token | store object → Lighthouse → registered back |
| `lighthouse_set_key` | stdio | persist the deployment key (0600) |

Three rules hold across the transports:

- **Auth is the module's, unchanged.** A tool that acts for a signer needs that
  signer's token as `Authorization: Bearer …` on the MCP request, and the 401
  arrives at the transport where a client can see it. The token is forwarded to
  the store verbatim, exactly as the REST routes do.
- **The filesystem is stdio's alone.** `lighthouse_get`, `lighthouse_set_key`
  and `path` on `lighthouse_put` refuse over HTTP with a message naming the
  route to use instead — a remote caller shares no filesystem with the server,
  so a path from one is at best meaningless.
- **A key passed as an argument is spent, not stored.** `key` (or `api_key` on
  the upload tools) is lifted into the call's context and never echoed back.

## Keys

Tried in this order, and `/status` always says which one is in play:

1. **`x-lh-key` header** — the caller's own Lighthouse key. Never written to
   disk, discarded when the request ends. This is how a visitor uses the console
   without trusting the box. The console keeps it in a variable for the tab and
   never in `localStorage`: modc2.com is one origin shared by every module.
2. **the deployment key** — `~/.mod/lighthouse/credentials.json` (0600) or
   `LIGHTHOUSE_API_KEY`, set by the owner via `POST /key` or
   `m lighthouse/set_key`. Off-chain, never `config.json`.

The **owner** is the first address to sign in against a fresh deployment; the
claim lives in `~/.mod/lighthouse/owner.json`. Owner is a small privilege: it
may set the deployment key and read the whole index. Everything else is open to
any signed caller with their own key.

## Honest limits

- **`rm` does not unpin.** A Lighthouse pin is perpetual and paid for; the CID
  stays retrievable by anyone who has it. `DELETE /rm` drops the row from this
  module's index, and says so in the response.
- **`/get` is unauthenticated.** An IPFS CID is public bytes to whoever holds
  it. Access *control* is the store's job, gated on the store's side — this
  route is the plain gateway, not a way around that.
- **A mirrored CID can differ.** Same content usually hashes the same, but
  chunking can differ; `same_cid` reports which happened, and the Lighthouse CID
  is the one registered.
- **A failed registration never costs you the CID.** The upload is the part that
  cannot be undone, so it happens first and the store outcome is reported
  alongside it under `store`.

## Layout

```
lighthouse/
├── mod.py                CLI mod — put/get/pin/list + push/mirror/store/serve
├── protocol.py           `import mod` without this module's mod.py winning
├── identity.py           who is calling; the owner claim
├── store_link.py         the ONE place this module and the store meet
├── mcp.py                14 MCP tools — stdio, or mounted at POST /mcp
├── config.json           ports, routes, endpoint table, deps
├── api/api.py            FastAPI :50680 (+ /mcp and its schema)
├── app/                  console :50681 — server.py, index.html, app.js, app.css
├── scripts/shots.py      playwright screenshots, signed in and out
├── ecosystem.config.js   pm2: lighthouse-api + lighthouse-app
└── test/                 pytest — mod shape, auth, bridge failure modes
```

## Tests

```bash
PYTHONPATH=/root/mod python3 -m pytest test/ -q
```

They run against a throwaway state dir (`LIGHTHOUSE_DIR`), never the real
`~/.mod/lighthouse`. Bridge tests that need a live store skip themselves when
nothing is listening on `:50152`.
