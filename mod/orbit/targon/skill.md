---
name: targon
description: Rent GPUs from Targon (Bittensor subnet 4) through an MCP server — price the network, deploy container rentals or confidential VMs, attach volumes and SSH keys, read logs, exec inside the box.
type: orbit-module
---

# targon

MCP server over the Targon Hub API (`https://api.targon.com/tha/v2`). Targon is Bittensor
subnet 4: miners supply the GPUs, the Hub rents them out as workloads. 49 tools, served at
`POST /mcp` (JSON-RPC 2.0, Streamable HTTP) and over stdio (`targon-api --stdio`).

Port **50440** — API, MCP and console share it.

## Money rule

Deploys bill the key's Targon credits by the hour, and keep billing until the workload is
deleted or suspended. Before renting: check `cheapest` for the price and `credits` for the
balance, and say the hourly number out loud. After a rental is finished, `delete_workload`
(billing stops) or `suspend_workload` (config kept, runtime released — RENTAL only).

Key resolution: per-call `api_key` / `x-api-key` header → `TARGON_API_KEY` →
`~/.mod/targon/api_key` (0600, off-tree). `inventory`, `version`, `health`, `readiness` and
`cheapest` need no key at all.

## Capabilities

- **Price the network** — `inventory` (every tier: GPU type, vCPU, memory, $/hr, units free
  right now), `cheapest` (filter by `gpu_type` / `min_gpus` / `max_cost_per_hour`, returns the
  best match plus alternatives; GPU names match loosely, `RTX4090` finds `NVIDIA-GeForce-RTX-4090`)
- **Rent in one call** — `rent` registers *and* deploys, auto-picking a tier when
  `resource_name` is omitted; returns the uid, the hourly price and the ssh line
- **Full lifecycle** — `create_workload` `deploy_workload` `update_workload` `suspend_workload`
  `reboot_workload` `delete_workload`, plus `workload_state` / `workload_events` /
  `workload_logs` / `workload_exec`
- **Confidential VMs** — `type: VM` with `vm_config.password`; `vm_images` lists what boots
- **Storage** — `create_volume` `list_volumes` `attach_volume` `detach_volume` (RENTAL only)
- **Access** — `create_ssh_key` `attach_ssh_key`, then `ssh <uid>@ssh.deployments.targon.com`
- **Templates** — reusable workload manifests, public or private
- **Account** — `credits`, `wallet` (your Bittensor SS58 address), API key CRUD + rotate
- **Images** — `build_image` proxies the Heim build service

## The trap: register ≠ deploy

`create_workload` returns `state: registered` and provisions nothing. It only starts (and only
starts billing) after `deploy_workload`. Use `rent` unless you specifically want a saved-but-
unstarted config. Failed deploys explain themselves in `workload_events`, not in the create
response — check credits and `available` on the tier first.

## Usage

```bash
m targon/serve                              # pm2 targon-api on :50440
m targon/test                               # health, MCP handshake, live upstream
m targon/inventory gpu=True
m targon/cheapest gpu_type=H200 min_gpus=1
m targon/rent name=my-box gpu_type=H200 image=pytorch/pytorch:latest
m targon/state workload_uid=<uid>
m targon/exec workload_uid=<uid> command="nvidia-smi"
m targon/delete_workload workload_uid=<uid>
```

MCP clients: `claude mcp add targon -- <module>/targon-rs/target/release/targon-api --stdio`,
or HTTP at `http://localhost:50440/mcp`. `m targon/mcp_config` prints both.

## Not covered

- The old OpenAI-compatible inference endpoint (`/v1/chat/completions`) — absent from current
  docs, 403 from here. This module is the compute API.
- Streaming (`logs?follow`, exec streams) — MCP tool results are unary; use `tail`/`since`.
- Workload revisions — upstream returns 501.
