# lium

Rent GPUs from Lium, the compute marketplace of **Bittensor subnet 51**.
A Rust MCP server (`lium-rs`) speaks the live Lium platform API upstream; the
same tool layer serves REST, the console at `/lium`, and MCP over Streamable
HTTP + stdio.

## When to use

- What's for rent, cheapest first:
  `m lium/executors gpu_type=H200 available_only=1 limit=10`
  (also `max_price=`, `min_gpus=`, `country=`, `tier=`, `sort=price|reliability|gpu_count|vram|uptime`)
- One node in detail: `m lium/executor executor_id=78e78195` (prefix is enough)
- What is the subnet doing: `m lium/subnet` — nodes/GPUs/providers/validators,
  utilization per GPU type, open capacity, latest validator weights + top uids
- Who runs a node: `m lium/provider miner_hotkey=5EPGPs… executors=1`
- Rent it: `m lium/up executor_id=78e78195 name=trainer termination_hours=2`
  — spends credits; picks a verified template for that GPU and your registered
  SSH keys unless you pass `template_id=` / `public_key=`
- Live rentals: `m lium/pods`, `m lium/logs pod_id=…`, `m lium/reboot pod_id=…`,
  `m lium/down pod_id=…`
- Account: `m lium/me` (balance), `m lium/ssh_keys`, `m lium/volumes`
- Anything else in the platform API: `m lium/endpoints q=volume` to find the
  operation, then `m lium/api path=/volumes/cost-summary`

`ls`/`ps`/`rm` alias `executors`/`pods`/`down`, matching the Lium CLI.

## Auth

Bring your own key (lium.io → Settings → API Keys). Precedence per request:
`x-api-key` header → `LIUM_API_KEY` → `~/.mod/lium/api_key`
(`m lium/set_api_key api_key=… persist=1`, written 0600, never committed).
Rentals bill the account behind the caller's key — there is no house key.
Public reads (nodes, templates, stats, subnet weights) work with no key.

## Endpoints

One port: `:50430` serves the API, `/mcp` and the console.
Gateway: `/lium` (console), `/api/lium` and `/lium/_api` (API).
`m lium/serve` builds if needed and starts it under pm2 (`lium-api`).

MCP: `POST /mcp`, or `lium-api --stdio` for MCP clients
(`claude mcp add lium -- …/lium-rs/target/release/lium-api --stdio`).

20 tools — `lium_info`, `executors`, `executor`, `gpu_types`, `capacity`,
`subnet`, `provider`, `templates`, `pods`, `pod`, `up`, `down`, `reboot`,
`logs`, `ssh_keys`, `add_ssh_key`, `me`, `volumes`, `endpoints`, `api`.

## Reading a node

`executors` returns compact rows, not the 40 KB spec blob:
`price_per_gpu_hr` (what you pay per GPU-hour) and `price_per_hr` (the whole
node), `available_gpu_count` (free right now), `vram_gb_per_gpu`, `tier`
(`secure` > `spot`), `reliability`, `uptime_hours`, `location`, and the
Bittensor identity of the node — `miner_hotkey` (provider) and
`validator_hotkey` (who scored it). Pass `raw=1` for the full upstream object.

Only rentable nodes are listed: `subnet.marketplace` reports `nodes_rentable`
against `nodes_total` (everything active on the subnet, rented included).

## Gotchas

- lium.io is behind CloudFront and blocks clients with no User-Agent — the Rust
  client and `mod.py` both set one; anything new must too.
- `up` needs an SSH key on the account. With none registered it refuses rather
  than renting a box you cannot log into: `m lium/add_ssh_key public_key='ssh-ed25519 …'`.
- Pod logs come back as `{"text": …}` when upstream answers in plain text.
- The API explorer reads Lium's live OpenAPI spec, so new upstream endpoints
  appear without a release here.
