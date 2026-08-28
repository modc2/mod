# chutes

Chutes.ai serverless GPU inference, backed by a **Rust MCP server** (`chutes-rs/`, axum).

The backend is a single Rust binary (`chutes-api`) that speaks the Model Context
Protocol (JSON-RPC 2.0) over two transports and serves a REST surface that
dispatches through the same MCP tool layer — the MCP server *is* the backend.

```
┌──────────────┐   tools/call    ┌─────────────────┐   HTTPS   ┌────────────┐
│ MCP clients  │ ──────────────► │  chutes-api     │ ────────► │ chutes.ai  │
│ REST clients │  /mcp | stdio   │  (Rust, :50300) │           │            │
│ mod.py       │  /chat /models… │  tool registry  │           └────────────┘
└──────────────┘                 └─────────────────┘
```

## Endpoints (`:50300`)

| Route | What |
| --- | --- |
| `POST /mcp` | MCP Streamable HTTP — `initialize`, `ping`, `tools/list`, `tools/call` |
| `POST /chat` | Chat completion (add `"stream": true` for SSE pass-through) |
| `POST /images` | Image generation |
| `GET /models?q=` | Model/chute search (paginates the full upstream list) |
| `POST /forward` | Generic `{action, ...args}` → any MCP tool |
| `GET /tools` | Tool registry (REST view of `tools/list`) |
| `GET /` | Slick console (browser) / info JSON (curl) |

MCP tools: `chat`, `generate_image`, `models`, `list_chutes`, `get_chute`,
`warmup`, `utilization`, `deploy_chute`, `delete_chute`.

## Run

```bash
m chutes/build        # cargo build --release
m chutes/serve        # pm2 start → chutes-api on :50300
m chutes/test         # health + MCP handshake + live upstream check
```

## Use as an MCP server (stdio)

```bash
claude mcp add chutes -- /root/mod/mod/orbit/chutes/chutes-rs/target/release/chutes-api --stdio
```

Or Streamable HTTP: point any MCP client at `http://localhost:50300/mcp`.

## Auth

API key precedence (per request): `x-api-key` header / `Authorization: Bearer` →
`CHUTES_API_KEY` env → `~/.mod/chutes/api_key` (off-chain, `m chutes/set_api_key key=... persist=true`).
Public upstream endpoints (`utilization`, chute listing) work without a key.

## Python client

`mod.py` is a thin client over the MCP server (`mcp_call(tool, args)`); every
fn falls back to calling chutes.ai directly if the server is down.
