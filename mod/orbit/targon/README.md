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

## Quick start

```bash
m targon/serve                 # build if needed, run under pm2 as targon-api
m targon/test                  # health + MCP handshake + live upstream calls
m targon/inventory gpu=True    # what the network has, priced, right now
m targon/cheapest gpu_type=H200
```

Console (inventory, workloads, tool list): <http://localhost:50440/>

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

`GET /tools` lists them with schemas.

## Layout

```
config.json           port, routes, fns
mod.py                thin Python client over the MCP server, with a
                      direct-to-Targon fallback for the fns it exposes
targon-rs/src/
  tools.rs            the tool table + schema generation + dispatch
  targon.rs           upstream HTTP client, key resolution
  mcp.rs              JSON-RPC 2.0 core, stdio transport
  http.rs             axum routes (/mcp, REST adapters, console)
  console.html        zero-dep browser console
```

## Notes

- Targon's older OpenAI-compatible inference endpoint (`/v1/chat/completions`)
  is not wired up: it is not in the current docs and answers 403 from here.
  This module covers the compute API, which is what the network ships today.
- `workload_logs?follow=true` and `exec` streaming are not exposed as tools —
  MCP tool results are unary. `workload_logs` takes `tail`/`since` instead, and
  `workload_exec` returns the command's collected output.
- Workload revisions return 501 upstream, so there are no tools for them.
