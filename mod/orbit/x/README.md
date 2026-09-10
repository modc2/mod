# x

The **X (Twitter) API v2** as one Rust binary (`x-rs/`, axum) wearing three
faces: an **MCP server**, a **REST API**, and a **browser app**.

All three are projections of a single tool layer. Each X capability is defined
exactly once, in `mcp.rs`; `/mcp`, every REST route and every button in the app
land on the same `call_tool`. Nothing can drift, because there is nowhere for
it to drift to.

```
┌──────────────┐  tools/call   ┌─────────────────┐   HTTPS   ┌────────────┐
│ MCP clients  │ ────────────► │  x-api          │ ────────► │ api.x.com  │
│ REST clients │ /mcp | stdio  │  (Rust, :50350) │           │    /2      │
│ browser app  │ /search /me … │                 │           └────────────┘
│ mod.py       │ GET /         │  ONE tool layer │
└──────────────┘               └─────────────────┘
```

## The app (`GET /`)

A real client, not a debug panel — served from the same port, no build step,
hash-routed so every view is a link you can paste.

| View | What |
| --- | --- |
| **Search** | Full X query syntax, sort/limit, preset queries, 7-day volume sparkline in the rail |
| **Post** | One post by id or pasted URL — **works with no credentials at all** |
| **Profile** | Header, bio, stats, and tabs: posts · posts & replies · mentions · followers · following |
| **Mentions** | The authenticated account's mentions |
| **Compose** | Char counter (URLs weighted at 23, like X), reply, quote, polls |
| **API & MCP** | The live route table, a runner for any tool, the registry, client snippets |
| **Auth** | Both credential rails, stored to `~/.mod/x/credentials.json` from the browser |

Posts render as cards: authors joined from `includes.users`, `t.co` links
resolved to their display form out of `entities`, `@handles` and `#tags`
linked back into the app, metrics formatted, and like/repost/reply wired to the
write routes. `curl /` still returns info JSON — the HTML is `Accept`-gated.

## REST API (`:50350`)

Every MCP tool has a route; a test asserts it. `GET /openapi.json` is an
OpenAPI 3.1 document generated from the route table and the tool schemas.

| Route | What |
| --- | --- |
| `GET /` | App (browser) / info JSON (curl) |
| `POST /mcp` | MCP Streamable HTTP — `initialize`, `ping`, `tools/list`, `tools/call` |
| `GET /openapi.json` | The REST surface, generated — never hand-maintained |
| `GET /search?q=` | Recent search (full X query syntax) |
| `GET /counts?q=` | Post volume over time, bucketed |
| `GET /posts/:id` | One post — id or a pasted `x.com/…/status/…` URL |
| `POST /posts` | Publish `{text, reply_to?, quote_post_id?, poll_options?}` |
| `DELETE /posts/:id` | Delete one of your posts |
| `POST /posts/:id/like` · `POST /posts/:id/repost` | Act on a post |
| `GET /users/:handle` | Profile lookup |
| `GET /users/:handle/timeline` | Recent posts from an account |
| `GET /users/:handle/mentions` | Posts mentioning an account |
| `GET /users/:handle/followers` · `/following` | Account lists |
| `POST /users/:handle/follow` | Follow an account |
| `GET /me` · `GET /mentions` | The authenticated account |
| `GET /auth` | Which rails are configured (never the secrets) |
| `POST /auth/keys` | Store credentials — **loopback only** |
| `POST /forward` | Generic `{action, ...args}` → any MCP tool |
| `GET /tools` | Tool registry (REST view of `tools/list`) |

MCP tools: `search`, `counts`, `get_post`, `user`, `timeline`, `mentions`,
`followers`, `following`, `me`, `post`, `delete_post`, `like`, `repost`,
`follow`, `auth_status`.

## Run

```bash
m x/build        # cargo build --release
m x/serve        # pm2 start → x-api on :50350
m x/app          # where the app is, and what it can do at the current auth level
m x/test         # health + MCP handshake + a live keyless read
```

Then open <http://localhost:50350/> and, with no keys configured, fetch post
`20` — the app has something real to show before you have credentials.

## Use as an MCP server (stdio)

```bash
claude mcp add x -- /root/mod/mod/orbit/x/x-rs/target/release/x-api --stdio
```

Or Streamable HTTP: point any MCP client at `http://localhost:50350/mcp`.

## Auth

Two rails, because X has two:

- **Reads** — an app-only **Bearer** token.
- **Writes and `me`** — **OAuth 1.0a** user context (`api_key`, `api_secret`,
  `access_token`, `access_token_secret` from the X developer portal). Signing
  is checked against X's own published test vector in `cargo test`.

Precedence per field: request header (`x-api-key` / `Authorization: Bearer`) or
tool arg → env (`X_BEARER_TOKEN`, `X_API_KEY`, …) → `~/.mod/x/credentials.json`.
Secrets live off-tree, `0600`, never in `config.json`:

```bash
m x/set_keys bearer_token=AAAA... persist=True
m x/set_keys api_key=... api_secret=... access_token=... access_token_secret=... persist=True
m x/auth_status   # {"reads": true, "writes": true} — never echoes the secrets
```

The app's **Auth** view does the same thing over `POST /auth/keys`, which
accepts **loopback requests only** — these are the keys that let the server post
as you, so a request from another machine has no business setting them. Either
way the file is `0600` and credentials are re-read per request, so saving takes
effect with nothing to restart.

**One thing works with no credentials at all:** `get_post` falls back to the
public syndication CDN, so a fresh install still reads real data (fewer fields,
flagged as `"source": "syndication"`).

`"route": false` is deliberate — with credentials configured, a public gateway
route would let anyone post as your account through the API *or* the app. Flip
it and run `m caddy/apply` only behind your own gate. The same reasoning applies
on the LAN: `:50350` binds `0.0.0.0`, so anything that can reach the port can
spend your write rail. Only `POST /auth/keys` is loopback-guarded.

## Python client

`mod.py` is a thin client over the MCP server (`mcp_call(tool, args)`); every
fn is one wrapper over one tool. There is no direct-to-X fallback on purpose —
the signing and field logic live in Rust, once.

```python
import mod as m
x = m.mod('x')()

x.get_post('https://x.com/jack/status/20')      # keyless
x.search('bittensor -is:retweet', max_results=25)
x.timeline('@jack', exclude='retweets,replies')
x.post('shipped', reply_to='1234567890')
```

```bash
m x/search query="bittensor -is:retweet"
m x/get_post id=20
m x/user username=jack
```

## Tests

```bash
cargo test --release --manifest-path x-rs/Cargo.toml   # signing, encoding, creds, route table
python3 -m pytest tests/ -q                            # protocol, tool layer, REST surface, app, live keyless read
```

The pytest suite spawns the backend on a throwaway port with a scrubbed
credential environment, so no test can post as a real account. Two tests keep
the surface honest: every tool must have a REST route, and every route in the
generated OpenAPI document must actually be routed.

Because `X_BASE_URL` redirects the upstream, the app can also be driven against
`tests/mock_x.py`, a stand-in api.x.com — the only way to exercise the read
views without live keys. Its header says how.
