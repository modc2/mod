# chutes — CHUTES ARCADE

**chutes.ai** serverless GPU inference behind a single **Rust MCP server**
(`chutes-rs/`, axum) and an 8-bit browser console.

The backend is one binary (`chutes-api`) that speaks the Model Context Protocol
(JSON-RPC 2.0) over two transports and serves a REST surface that dispatches
through the same MCP tool layer — the MCP server *is* the backend. chutes.ai
speaks the OpenAI-compatible shape, so any OpenAI client points at it too.

```
┌──────────────┐   tools/call    ┌─────────────────┐   HTTPS   ┌───────────────┐
│ MCP clients  │ ──────────────► │  chutes-api     │ ────────► │ api.chutes.ai │
│ REST clients │  /mcp | stdio   │  (Rust, :50300) │           │               │
│ 8-bit console│  /chat /models… │  tool registry  │           └───────────────┘
│ mod.py       │                 │                 │
└──────────────┘                 └─────────────────┘
```

## The console (`GET /` in a browser)

`CHUTES ARCADE` — pixel font, CRT scanlines, square-wave blips, no build step
(one HTML file, `include_str!`'d into the binary).

| Tab | What |
| --- | --- |
| **CHAT** | Streaming chat — chute picker, temperature, system prompt, per-message latency/tokens/cost |
| **CHUTES** | Every chute on api.chutes.ai (~500), loaded once and browsed locally: search across name/tagline/owner/GPU/engine/readme, chips for kind · LIVE NOW · hot/tee/vllm/sglang, GPU + owner pickers, nine sorts, 50/100/ALL per page, LIST or GRID. `INFO` opens the card — prices (in/out/cached/rig-hour), live replicas, GPUs, engine image, owner, dates, full readme, a curl. `USE` sends it to CHAT (or IMAGE), `+ VS LANE` to VS. Keys: ↑↓ move · ENTER open · U use · V lane · / search · ←→ page |
| **VS** | Same prompt, several chutes, at once. Lanes race with live timers; fastest gets the ★ FASTEST ribbon, failures get KO'd |
| **IMAGE** | Text → image on a diffusion chute |
| **KEYS** | Key vault — THIS BOX (owner-signed, writes the server key) and THIS BROWSER (localStorage, `x-chutes-key`), plus a live TEST button |
| **MCP** | The tool registry as a runnable console: pick a tool, edit JSON args, `tools/call`, see the raw result |

The cabinet card at the top shows whether a key resolves (and from where), the
default chute, the catalog size and the upstream URL.

## Where it runs

`:50300` on the box, and — since `config.json` opts into the mod router with
`"route": true` — publicly at **`{host}/chutes`** (console) and
**`{host}/api/chutes`** (API), e.g. `https://modc2.com/chutes`. The router keeps
the prefix on app routes, so the server answers on both the bare and the
prefixed form: `strip_base` rewrites `/chutes/...` → `/...` before routing, which
is exactly what the console's `BASE`-relative fetches need. `CHUTES_BASE_PATH=`
turns it off.

## Endpoints (`:50300`)

| Route | What |
| --- | --- |
| `POST /mcp` | MCP Streamable HTTP — `initialize`, `ping`, `tools/list`, `tools/call` |
| `POST /chat` | Chat completion — `{message\|messages, model, stream}` (`stream:true` = SSE pass-through) |
| `POST /compare` | Race one prompt across `models:[...]` |
| `POST /route` | Rank chutes under filters; `ask` runs the winner |
| `POST /images` | Image generation on a diffusion chute |
| `GET /models?q=&kind=&tag=&gpu=&owner=&live=1&sort=&offset=&limit=&facets=1` | Normalized catalog (10-min cache, `refresh=1` busts it). Rows carry `in/out/cache_price` ($/1M), `hour_price`, `instances` (active replicas), `gpus`, `gpu_count`, `owner`, `logo`, `image`, `readme` (first 600 chars), `created_at`/`updated_at`. Sorts: price · out_price · expensive · name · invocations · instances · newest · updated · gpus. `facets=1` adds counts per kind/tag/gpu/owner |
| `GET /chute/{name or chute_id}` | The full upstream record — whole readme, every instance, node selector |
| `GET /status?counts=1` | Default chute, key status (never the key), catalog size |
| `POST /forward` | Generic `{action, ...args}` → any MCP tool |
| `GET /tools` | Tool registry (REST view of `tools/list`) |
| `GET /` | Console (browser) / info JSON (curl) |

MCP tools: `chat`, `compare`, `route`, `models`, `status`, `generate_image`,
`list_chutes`, `get_chute`, `warmup`, `utilization`, `deploy_chute`,
`delete_chute`. The last six are the chutes.ai control plane.

## The router

Every chute is normalized to `{id, chute_id, in_price, out_price, kind, tags,
invocations}` with prices in **USD per 1M tokens**:

```bash
curl -s localhost:50300/models?q=qwen\&kind=chat\&sort=price
curl -s 'localhost:50300/models?live=1&gpu=h200&sort=instances'   # what's actually running on H200s right now
curl -s localhost:50300/chute/zai-org/GLM-5.1-TEE                   # one chute, in full
curl -s -X POST localhost:50300/route \
  -d '{"kind":"chat","max_price":0.05,"limit":5,"ask":"write a haiku about GPUs"}'
# → cheapest chat chutes under $0.05/M, and `ask` runs the prompt on the top one
```

A chute with **no published price** ranks last under `sort=price` — unknown is
not free. `ask` runs the winner and walks down the ranking (up to 5) if it can't
answer. The chute catalog doesn't publish context windows, so there is no
`min_context` filter.

## Run

```bash
m chutes/build        # cargo build --release
m chutes/serve        # pm2 start → chutes-api on :50300
m chutes/test         # health + MCP handshake + catalog + key status + a chat
```

## Use as an MCP server (stdio)

```bash
claude mcp add chutes -- /root/mod/mod/orbit/chutes/chutes-rs/target/release/chutes-api --stdio
```

Or Streamable HTTP: point any MCP client at `http://localhost:50300/mcp`.

## Auth

The key resolves per request:

`x-chutes-key` header (or `x-api-key` / `Authorization: Bearer`) →
`CHUTES_API_KEY` → `~/.mod/chutes/api_key` → `~/.mod/chutes/key.json` →
`~/.mod/model/chutes/apikeys.json` (the shape the `model` mod writes).

Keys live off-tree and are never logged or echoed; `/status` reports only
*whether* a key resolved and *from where*.

### Setting the key from the app

The console's **KEYS** tab has two cards:

- **THIS BOX** writes `~/.mod/chutes/api_key` on the server — the key MCP,
  stdio, the `m` CLI and every browser without its own key share. Only the
  box's owner may set or remove it: CONNECT WALLET (one `personal_sign`, no
  transaction) or PASTE TOKEN (`m chutes/token`, minted on the box). The key
  is tried against chutes.ai on a free authenticated call before it lands on
  disk, so a typo is refused instead of saved.
- **THIS BROWSER** keeps a key in localStorage and sends it as `x-chutes-key`;
  it wins over the box key for that browser's own requests.

The same door over HTTP — a mod-protocol token as `Authorization: Bearer`
(or `x-mod-token`), signed by the owner:

```bash
GET    /key                 # key status, owner, whether you may write
POST   /key {key, verify?}  # save it (verify defaults to true)
DELETE /key                 # remove it
```

The owner is `CHUTES_OWNER` → `~/.mod/chutes/owner.json` `{"owner": "0x…"}`
→ config.json `owner` → the box's own key (`m.key().address`), which is what
the `m` CLI signs with — so on a fresh box the CLI is already the owner.
Tokens are good for `CHUTES_TOKEN_MAX_AGE` seconds (default 7 days).

```bash
m chutes/set_api_key api_key=... persist=true   # → ~/.mod/chutes/api_key, directly
m chutes/set_server_key api_key=...             # the same, through POST /key
m chutes/token                                  # a token to paste into the console
m chutes/whoami                                 # GET /key as the box key sees it
m chutes/clear_api_key
```

Public endpoints (the catalog, `utilization`) work with no key at all.

## The default chute

`CHUTES_DEFAULT_MODEL` → `~/.mod/chutes/defaults.json` → `Qwen/Qwen3-32B-TEE`.

`~/.mod/chutes/defaults.json` is box-local deployment state (never committed),
and `models` may be a **list**:

```json
{ "models": ["Qwen/Qwen3-32B-TEE", "some/stand-in"] }
```

When nobody named a model, the server walks that list until one answers — a
chute that's cold, delisted or out of capacity moves the call to the next
stand-in instead of failing the request. Streaming does the same, and reports
which chute actually answered in the `x-model` response header. A model the
*caller* names is never second-guessed.

## Python client

`mod.py` is a thin client over the MCP server (`mcp_call(tool, args)`) and falls
back to calling chutes.ai directly if the server is down.

```python
m.chat('hello')
m.compare('explain MoE in one line', models=['Qwen/Qwen3-32B-TEE', 'deepseek-ai/DeepSeek-V3'])
m.route(kind='chat', max_price=0.05, ask='write a haiku about GPUs')
m.status()                                    # default chute + key status
```
