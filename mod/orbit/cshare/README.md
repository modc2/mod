# cshare — the Agent Protocol hub

One protocol over every content-addressed agent artifact.

The `agent` module pins five artifact kinds to localfs — prompts, agents,
toolboxes, memory notes, conversations — each as a JSON bundle whose `type`
field names its kind, each shareable by CID. Before cshare they had five
bespoke share/import paths; cshare unifies them under **agent/1.0**:

- one **card** describing the hub and every kind it speaks
  (served at `/.well-known/agent.json`)
- one **index** across all kinds, every record normalized to
  `{kind, id, name, description, tags, cid}`
- one **install** path: give it any CID, the bundle's `type` picks the
  installer — no kind flag needed

## The protocol

### Artifacts

Every artifact is a portable JSON bundle on localfs (content-only fields, so
identical content ⇒ identical CID):

| kind | bundle | required | installable |
|---|---|---|---|
| `prompt` | `{type:"prompt", name, text, description, tags}` | name, text | yes |
| `memory` | `{type:"memory", name, content, tags}` | name, content | yes |
| `agent` | `{type:"agent", name, goal, skills, model, icon, …}` | name | yes |
| `toolbox` | `{type:"toolbox", name, tools, description}` | name, tools | yes |
| `conversation` | `{type:"conversation", query, agent_type, messages}` | messages | no (per-user; agent console) |

### Card

`GET /card` (also `/.well-known/agent.json` and the null call `GET /`) returns
the protocol card: protocol id, kinds spec, endpoints, urls. `POST /publish`
pins the card itself to localfs so the whole descriptor travels as one CID.

### Endpoints (API :50290, gateway `/cshare/api`)

```
GET  /card                     protocol card
GET  /.well-known/agent.json   same card, standard location
GET  /kinds                    kind → description
GET  /index?kind=&q=&tag=      unified artifact index
GET  /resolve/{cid}            fetch + classify + validate any bundle
POST /validate {bundle}        shape-check a bundle
POST /share {kind, id}         local artifact → CID (mints if missing)
POST /install {cid}            CID → local artifact (dispatch by type)
POST /publish                  pin the protocol card, get its CID
POST /forward {action, params} mod protocol dispatch
GET  /health
```

## CLI

```bash
m cshare/card
m cshare/index kind=prompt q=review
m cshare/share kind=prompt id=p-0
m cshare/resolve cid=Qm...
m cshare/install cid=Qm...
m cshare/serve            # api :50290 + app :50291 under pm2
```

## App

Zero-dependency viewer at `/cshare` (`:50291`): browse the unified index with
kind filter and search, copy or mint CIDs, resolve and install any bundle by
CID. `app/server.js` proxies `/cshare/api/*` to the API so the page works both
directly and through the gateway.

## Layout

- `mod.py` — protocol core (card / index / resolve / validate / share / install)
- `api/api.py` — FastAPI on `:50290`
- `app/` — zero-dep viewer on `:50291/cshare`

cshare imports the agent module's `Library`, `Agents`, and `Toolboxes`
registries directly from `orbit/agent/src` (override with `MOD_AGENT_SRC`) —
no agent runtime, no model, no vault. Artifacts live where they always did
(`~/.mod/agent/…`); cshare adds the protocol surface, not a second store.
