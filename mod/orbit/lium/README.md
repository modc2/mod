# lium

Rent GPUs from **Lium** — the decentralized compute marketplace of **Bittensor
subnet 51** — through a **Rust MCP server** (`lium-rs/`, axum).

Providers put GPU nodes on the subnet, validators score them and set weights
on-chain, renters take pods by the hour. This module is one binary that speaks
the live Lium platform API upstream and exposes the whole thing as MCP tools,
a REST surface and a console — all on one port.

```
┌──────────────┐   tools/call    ┌─────────────────┐   HTTPS   ┌──────────────┐
│ MCP clients  │ ──────────────► │  lium-api       │ ────────► │ lium.io/api  │
│ REST clients │  /mcp | stdio   │  (Rust, :50430) │           │  (SN51)      │
│ console      │  /executors …   │  tool registry  │           └──────────────┘
│ mod.py       │  /up /pods …    └─────────────────┘
└──────────────┘
```

## Endpoints (`:50430`)

| Route | What |
| --- | --- |
| `POST /mcp` | MCP Streamable HTTP — `initialize`, `ping`, `tools/list`, `tools/call` |
| `GET /executors` | Marketplace: `gpu_type, max_price, min_gpus, country, tier, available_only, sort, limit` |
| `GET /executors/:id` | One node (uuid or unique prefix) + live hardware utilization |
| `GET /subnet` | SN51 in one shot: supply, utilization, capacity, validator weights |
| `GET /stats`, `/capacity` | Rented-vs-total per GPU type; open provider capacity |
| `GET /provider/:hotkey` | Provider (miner) statistics by Bittensor hotkey |
| `GET /templates?q=` | Docker templates you can launch |
| `POST /up` | Rent a node → pod (`{executor_id, name?, template_id?, gpu_count?, termination_hours?}`) |
| `GET/DELETE /pods/:id`, `POST /pods/:id/reboot`, `GET /pods/:id/logs` | Pod lifecycle |
| `GET /me`, `/ssh-keys`, `/volumes` | The account behind the key |
| `GET /endpoints?q=` | Every Lium operation, read from its live OpenAPI 3.1 spec |
| `POST /api` | Passthrough: `{method, path, query, body}` — the escape hatch |
| `GET /tools`, `POST /forward` | Tool registry; `{action, ...args}` → any tool |
| `GET /lium` | Console (browser) · `GET /` info JSON (curl) |

MCP tools: `lium_info`, `executors`, `executor`, `gpu_types`, `capacity`,
`subnet`, `provider`, `templates`, `pods`, `pod`, `up`, `down`, `reboot`,
`logs`, `ssh_keys`, `add_ssh_key`, `me`, `volumes`, `endpoints`, `api`.

## Run

```bash
m lium/build        # cargo build --release
m lium/serve        # pm2 start → lium-api on :50430
m lium/test         # health + MCP handshake + live subnet read
pytest tests/       # 20 tests against the live marketplace
```

## Console (`/lium`)

Five tabs, no build step, no dependencies:

- **MARKET** — every rentable node, filtered and sorted; `rent` opens a dialog
  that picks a verified template for that GPU and your registered SSH keys.
- **PODS** — status, ssh command, logs, reboot, stop.
- **SUBNET** — supply and utilization, open capacity, and the latest validator
  weight set with the top-scoring uids.
- **API** — explorer over Lium's published OpenAPI spec: click an operation, run
  it in the playground with your own key, watch the call log.
- **MCP** — the tool registry, a `tools/call` playground, and client config.

## Use as an MCP server

```bash
claude mcp add lium -- /root/mod/mod/orbit/lium/lium-rs/target/release/lium-api --stdio
```

Or Streamable HTTP: point any MCP client at `http://localhost:50430/mcp`
(behind the fleet router, `/lium/_api/mcp`).

## Auth

Bring your own key — get one at lium.io → Settings → API Keys. Precedence per
request: `x-api-key` / `Authorization: Bearer` → `LIUM_API_KEY` →
`~/.mod/lium/api_key` (off-tree, `m lium/set_api_key key=... persist=true`).
Every rental is billed to the account behind the caller's key, never a shared
house key. Public reads — nodes, templates, stats, subnet weights — need no key
at all, so the marketplace and subnet views work signed-out.

## Python client

`mod.py` is a thin client over the MCP server (`mcp_call(tool, args)`); every fn
falls back to calling lium.io directly when the server is down. `ls`/`ps`/`rm`
are aliased to `executors`/`pods`/`down` to match the Lium CLI.

```python
m.mod('lium').executors(gpu_type='H200', available_only=True, limit=5)
m.mod('lium').subnet()['marketplace']
m.mod('lium').up(executor_id='78e78195', name='trainer', termination_hours=2)
```
