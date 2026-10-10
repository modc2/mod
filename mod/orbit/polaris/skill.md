# polaris

The Polaris GPU cloud ([polaris.computer](https://polaris.computer)) as a mod
protocol module. GPUs and CPU boxes billed by the second, sixteen one-click
template deployments, a 400-model inference catalog, and the billing behind all
of it — behind mod's verbs, a REST API, a browser console and MCP tools.

## Start here

```bash
m polaris/gpus max_usd_hr=2            # the catalog, cheapest first — no key needed
m polaris/status                       # balance, what is running, hours of runway
m polaris/quote gpu_type='H100 SXM5 80GB' hours=4
m polaris/rent gpu_type='H100 SXM5 80GB' confirm=1   # spends YOUR credits
m polaris/ssh                          # how to get in, once it is up
m polaris/stop instance_id=<id>        # THIS is what ends the billing
```

`m polaris/serve` runs the REST API, the console at `/polaris` and the MCP
server on port 50870, all from one process with no dependencies.

## The key

Bring your own. Resolution order:

1. `POLARIS_KEY` / `POLARIS_API_KEY` in the environment
2. `~/.mod/polaris/api_key` — 0600, off-tree

```bash
m polaris/set_key key=pi_sk_…          # writes the keystore, 0600
m polaris/identity                     # is a key set? which SSH key do we rent with?
```

Get one at polaris.computer. **Never put it in `config.json`, `skill.md` or
anything else in the tree** — a key in the repo is a key in git history.

## Money

Billing is per second, quoted per hour. Three things are worth internalizing:

- **`hours` prices the quote, it does not bound the rental.** An instance bills
  until you stop it. Nothing expires on its own.
- **`stop` is the only thing that ends the billing.** Not closing the console,
  not losing the SSH session.
- A rental estimated above **$0.50** (`POLARIS_CONFIRM_USD`) is refused unless
  you pass `confirm=1`, and the refusal hands back the quote so you can look at
  it before retrying.

```bash
m polaris/credits                      # balance + status: active / low_balance / restricted
m polaris/history limit=20             # the ledger — every debit and what caused it
m polaris/usage                        # API requests, and compute seconds this period
m polaris/packs                        # top-up sizes (buying is a browser flow)
```

`status` = `active` above $5, `low_balance` under it, `restricted` at zero,
where new instances are refused upstream.

## Everything else Polaris does

The published quickstart documents four endpoints. There are more, and this
module wraps them:

```bash
m polaris/templates category=agents    # 16 one-click images: Ollama, Jupyter,
                                       # ComfyUI, LangFlow, n8n, agent runtimes
m polaris/template template_id=ollama  # one in full, with the params it takes
m polaris/deployments state=running    # template deployments, with endpoints
m polaris/logs deployment_id=<id>      # why a deployment says "failed"
m polaris/activity limit=25            # the account event tape
m polaris/models provider=anthropic    # 400+ models with per-token pricing
m polaris/pricing                      # the billing table behind the catalog
m polaris/account                      # tier, quota, compute hours, storage
```

`m polaris/raw path=/some/route` reaches anything not wrapped here.

## MCP

Seventeen tools at `POST /mcp`, or over stdio with `python3 mcp.py`:

```json
{"mcpServers": {"polaris": {"type": "http", "url": "http://127.0.0.1:50870/mcp"}}}
```

`polaris_gpus` → `polaris_quote` → `polaris_rent` → `polaris_instances` /
`polaris_ssh` → `polaris_stop` is the whole rental arc. `polaris_status` answers
"what's running and how much have I got left" in one call.

## Who may do what

Reading a catalog spends nothing and reveals nothing, so it stays open. Reading
an account or spending its money does not.

| tier | routes |
|------|--------|
| open | `gpus`, `pricing`, `templates`, `models`, `quote`, `packs`, `tools` |
| byok | every account route, when the caller sends `x-polaris-key: pi_sk_…` |
| owner | `rent`, `stop`, `raw`, `set_key`, `token` |

Owner means: `Authorization: Bearer $(m polaris/token)`, or a request from
localhost that did not pass through the gateway. Caddy stamps
`X-Forwarded-For` on everything it proxies, so a public request can never look
loopback.

## Siblings

`orbit/compute` aggregates Polaris alongside thirteen other markets as provider
`polaris` — use that to compare prices across markets, and this module for
everything Polaris does that a generic compute interface cannot express:
templates, deployments, the model catalog, the ledger.

## Upstream notes

- Base is `https://api.polaris.computer/api`. No OpenAPI document is published;
  the routes here were mapped against the live API.
- `/compute/gpus` returns `available_count: null` when it knows a type is up but
  not how many are free. That is *available*, not sold out.
- `gpu_type` in a rent call is the catalog's `name` ("H100 SXM5 80GB"), not its
  slug `id` ("h100-sxm5-80gb").
- `/models` ignores query parameters and returns all 429 rows — filtering is
  done in this module.
- There is no by-id route for instances; `instance` filters the list.
- `/api/credits` does not exist. Balance lives at `/api/billing/credits`.
