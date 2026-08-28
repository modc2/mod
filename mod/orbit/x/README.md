# x

The **X (Twitter) API v2**, backed by a **Rust MCP server** (`x-rs/`, axum).

The backend is a single Rust binary (`x-api`) that speaks the Model Context
Protocol (JSON-RPC 2.0) over two transports and serves a REST surface that
dispatches through the same MCP tool layer — the MCP server *is* the backend.
Each X capability is defined exactly once, in `mcp.rs`.

```
┌──────────────┐   tools/call    ┌─────────────────┐   HTTPS   ┌────────────┐
│ MCP clients  │ ──────────────► │  x-api          │ ────────► │ api.x.com  │
│ REST clients │  /mcp | stdio   │  (Rust, :50350) │           │    /2      │
│ mod.py       │  /search /posts │  tool registry  │           └────────────┘
└──────────────┘                 └─────────────────┘
```

## Endpoints (`:50350`)

| Route | What |
| --- | --- |
| `POST /mcp` | MCP Streamable HTTP — `initialize`, `ping`, `tools/list`, `tools/call` |
| `GET /search?q=` | Recent search (full X query syntax) |
| `GET /posts/:id` | One post — id or a pasted `x.com/…/status/…` URL |
| `POST /posts` | Publish `{text, reply_to?, quote_post_id?, poll_options?}` |
| `DELETE /posts/:id` | Delete one of your posts |
| `GET /users/:handle` | Profile lookup |
| `GET /users/:handle/timeline` | Recent posts from an account |
| `POST /forward` | Generic `{action, ...args}` → any MCP tool |
| `GET /tools` | Tool registry (REST view of `tools/list`) |
| `GET /` | Console (browser) / info JSON (curl) |

MCP tools: `search`, `counts`, `get_post`, `user`, `timeline`, `mentions`,
`followers`, `following`, `me`, `post`, `delete_post`, `like`, `repost`,
`follow`, `auth_status`.

## Run

```bash
m x/build        # cargo build --release
m x/serve        # pm2 start → x-api on :50350
m x/test         # health + MCP handshake + a live keyless read
```

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

**One thing works with no credentials at all:** `get_post` falls back to the
public syndication CDN, so a fresh install still reads real data (fewer fields,
flagged as `"source": "syndication"`).

`"route": false` is deliberate — with credentials configured, a public gateway
route would let anyone post as your account. Flip it and run `m caddy/apply`
only behind your own gate.

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
cargo test --release --manifest-path x-rs/Cargo.toml   # signing, encoding, creds
python3 -m pytest tests/ -q                            # protocol, tool layer, live keyless read
```

The pytest suite spawns the backend on a throwaway port with a scrubbed
credential environment, so no test can post as a real account.
