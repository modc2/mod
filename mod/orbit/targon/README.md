# targon

Targon is **Bittensor subnet 4**: miners supply GPUs, the Targon Hub API rents
them out. This module is an **MCP server** over that API — 49 tools that let an
agent price the network, rent a machine, deploy a container or confidential VM,
attach volumes and SSH keys, read logs and run commands inside the box.

- **MCP:** `POST /mcp` (JSON-RPC 2.0, Streamable HTTP) and `targon-api --stdio`
- **Backend:** `targon-rs/` (Rust, axum) — every REST route dispatches through
  the same tool layer, so there is one implementation, not two
- **Upstream:** `https://api.targon.com/tha/v2`
- **Port:** 50440 (API + console on one port)
- **Console:** `/targon` — a Game Boy of a control panel, eight swappable skins

## Quick start

```bash
m targon/serve                 # build if needed, run under pm2 as targon-api
m targon/test                  # health + MCP handshake + live upstream calls
m targon/inventory gpu=True    # what the network has, priced, right now
m targon/cheapest gpu_type=H200
```

### Console

<http://localhost:50440/targon> (or `/targon` behind the fleet gateway) — a
zero-dep page styled as a handheld: the plastic shell holds the key field and
the skin picker, the LCD holds the app.

| tab | what it does |
| --- | --- |
| MARKET | live inventory, priced per GPU-hour, filtered; `cheapest` picker; click a tier to load it |
| RENT | name + image + tier → `rent` (register **and** deploy), or estimate the call first |
| WORKLOADS | your machines: state, logs, events, deploy, suspend, reboot, delete, `exec` |
| STORAGE | volumes (create/delete) and templates — a template pours into the rent form |
| KEYS | SSH keys and Targon API keys |
| WALLET | connect a browser wallet, read your TAO, top up credits on-chain |
| MCP | all 49 tools with schemas, a `tools/call` playground, client config, request log |

The plastic under the LCD is wired, not decoration: the **D-pad** walks the tabs
(◀ ▶) and scrolls the list you are on (▲ ▼), **A** runs the tab's main action,
**B** goes back to MARKET, **SELECT** cycles the skin and **START** reloads the
tab. Arrow keys and `a`/`b` do the same when you aren't typing in a field.

Eight skins — DMG, Pocket, Light, Virtual Boy, Super GB, Micro, Terminal,
Manual — picked from **THEME** in the header, cycled with SELECT, and remembered
in `localStorage`. Each is one CSS block of tokens (`--shell` / `--plate` /
`--screen` / `--ink` and the status inks); nothing else in the stylesheet names a
colour, so a new skin is one more rule in `console.html`.

The key you paste in the header stays in `sessionStorage` for that tab and rides
along as `x-api-key`; the server still reads its own key from `TARGON_API_KEY`
or `~/.mod/targon/api_key`.

### As an MCP server

```bash
claude mcp add targon -- /root/mod/mod/orbit/targon/targon-rs/target/release/targon-api --stdio
```

or point an HTTP MCP client at `http://localhost:50440/mcp`. `m targon/mcp_config`
prints both snippets.

## Auth

Inventory, version and health are open — no key. Everything else needs a Targon
API key, resolved in this order:

1. per-call `api_key` argument, or `x-api-key` / `Authorization: Bearer` header
2. `TARGON_API_KEY`
3. `~/.mod/targon/api_key` (mode 0600, off-tree — never in config.json)

```bash
m targon/set_api_key api_key=tgn_...      # writes ~/.mod/targon/api_key
```

## Paying: TAO in, credits out

Deploys spend credits, and credits are bought by sending TAO to the SS58 address
`GET /wallet` hands you. The console's **WALLET** tab does that round trip in the
browser: connect a polkadot-js compatible extension (Talisman, SubWallet,
Polkadot{.js}, Bittensor Wallet), pick a coldkey, and send.

Custody stays where it belongs — **the extension holds the key and does the
signing**. This server only encodes and relays:

```
GET  /chain/account?address=…   free / reserved / transferable TAO and the nonce
POST /chain/prepare  {from, to, tao}        → the call and a signer payload
POST /chain/submit   {payload, signature}   → author_submitExtrinsic, tx hash
```

`Balances.transfer_keep_alive`, immortal era, no tip — keep-alive so a top-up can
never reap the coldkey. All the SCALE encoding lives in `chain.rs` where the
tests pin it: `cargo test` checks the assembled extrinsic byte-for-byte against
one built by py-substrate-interface on finney, and the `AccountInfo` decode
against a real storage entry (subtensor's `Balance` is a `u64`, not the `u128`
most Substrate chains use). Point `BITTENSOR_RPC` elsewhere for a local node.

```bash
m targon/chain_account address=5F…        # any coldkey, no Targon key needed
```

## Renting a machine

The API is register-then-deploy: `create_workload` only saves the config
(`state: registered`), and `deploy_workload` provisions it. The `rent` tool does
both, and picks a tier for you if you don't name one:

```bash
m targon/rent name=my-box image=pytorch/pytorch:latest gpu_type=H200
# -> { uid, resource_name, deployed: true, ssh: "ssh <uid>@ssh.deployments.targon.com" }
```

Then `m targon/state workload_uid=<uid>` until `status: running`, and
`m targon/logs` / `m targon/exec` to work inside it. `m targon/delete_workload`
stops the billing.

## Tools

Table-driven — one row per Hub API endpoint in `targon-rs/src/tools.rs`, plus
three composed tools. Adding an endpoint is adding a row.

| group | tools |
| --- | --- |
| open | `inventory` `version` `health` `readiness` |
| workloads | `list_workloads` `get_workload` `create_workload` `update_workload` `delete_workload` `deploy_workload` `suspend_workload` `reboot_workload` `workload_state` `workload_events` `workload_logs` `vm_images` `verify_workload` |
| attachments | `attach_volume` `detach_volume` `attach_ssh_key` `detach_ssh_key` |
| volumes | `create_volume` `list_volumes` `get_volume` `volume_state` `volume_events` `update_volume` `delete_volume` |
| ssh keys | `create_ssh_key` `list_ssh_keys` `get_ssh_key` `update_ssh_key` `delete_ssh_key` |
| templates | `create_template` `list_templates` `get_template` `update_template` `delete_template` |
| account | `wallet` `credits` `list_api_keys` `create_api_key` `update_api_key` `delete_api_key` `roll_api_key` |
| images | `build_image` (Heim build service) |
| composed | `cheapest` (price/availability picker) `rent` (create + deploy) `workload_exec` |

`GET /tools` lists them with schemas. REST mirrors the table —
`GET /inventory` `GET|POST /workloads` `POST /workloads/:uid/{deploy,suspend,reboot,exec}`
`GET|POST /volumes` `GET|POST /ssh-keys` `GET|POST /templates` `GET|POST /api-keys`
`GET /credits` `GET /wallet` — and `POST /forward {action, …}` reaches any tool
by name. The `/chain/*` routes are not tools: they talk to Bittensor, not to the
Hub API.

## Layout

```
config.json           port, routes, fns
mod.py                thin Python client over the MCP server, with a
                      direct-to-Targon fallback for the fns it exposes
targon-rs/src/
  tools.rs            the tool table + schema generation + dispatch
  targon.rs           upstream HTTP client, key resolution
  mcp.rs              JSON-RPC 2.0 core, stdio transport
  chain.rs            Bittensor: SS58, balances, the top-up extrinsic
  http.rs             axum routes (/mcp, REST adapters, /chain, console) — the
                      API is served at the root, at /api/targon and /targon/_api
  console.html        zero-dep browser console + the eight skins
```

## Notes

- Targon's older OpenAI-compatible inference endpoint (`/v1/chat/completions`)
  is not wired up: it is not in the current docs and answers 403 from here.
  This module covers the compute API, which is what the network ships today.
- `workload_logs?follow=true` and `exec` streaming are not exposed as tools —
  MCP tool results are unary. `workload_logs` takes `tail`/`since` instead, and
  `workload_exec` returns the command's collected output.
- Workload revisions return 501 upstream, so there are no tools for them.
- The wallet tab needs an extension that implements `window.injectedWeb3`. With
  none installed it still shows the deposit address to send TAO to by hand.
