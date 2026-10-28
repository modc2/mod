# API: REST and MCP

Base URL `http://localhost:50132` (through the app: `/openhouse/api/...`). Interactive docs at `/docs`. No auth: the surface is public and the node is a simulation. Anything that is not the sandbox bank needs the operator's key.

`POST /forward {"action": "<fn>", ...}` calls any entry in `config.json` `fns`.

## REST routes

| Area | Routes |
|---|---|
| Health | `GET /` `/health` `/status` |
| Deal | `GET /property` `/terms` `/models` `/rent_ledger` `/rent_stats` `/share_price` `/available_shares` `/balance`; `POST /terms` `/claim_owner` `/quote` `/pay_rent` |
| Equity and pool | `GET /equity/{address}` `/pool` `/pool/history` `/bloctime/{address}`; `POST /pool/close` `/pool/claim` |
| Shares | `GET /shareholders` `/shareholder/{a}` `/portfolio/{a}` `/dividends`; `POST /shareholders` `/purchase` `/dividends` `/distribute` `/record_action` `/transfer_authority` `/toggle_active` |
| Banks | `GET /bank` `/bank/kinds` `/bank/connections` `/bank/accounts` `/bank/transactions` `/bank/reference/{address}` `/bank/links`; `POST /bank/connect` `/disconnect` `/import` `/receive` `/link` `/unlink` `/reconcile` `/pay` |
| Civic | `GET /civic`; `POST /civic/charter` `/civic/override` `/civic/resign` |
| Proofs | `GET /proofs` `/proofs/status` `/proofs/program` `/proofs/pq` `/proofs/{nullifier}` `/proofs/{nullifier}/chain`; `POST /proofs/issue` `/verify` `/open` `/salt` `/submit` `/build` `/pq/upgrade` `/pq/rotate` |
| Simulation | `GET /simulate/presets` `/simulate` `/examples` `/examples/{name}`; `POST /simulate` |
| Context | `GET /peers` `/compare` `/fx` `/source` `/code` |
| Build | `POST /compile` `/deploy` `/forward` |

## MCP

`POST /mcp` speaks JSON-RPC 2.0 over Streamable HTTP (`api/mcp_server.py`, no `mcp` package). Tools are named `openhouse_*` and call the same `Mod` the REST API serves. Read tools are safe; tools that write are tagged `WRITES.` in their description and record local bookkeeping only.

Families: deal (`terms quote models status property rent_stats rent_ledger equity`), writes (`pay_rent purchase set_terms claim_owner distribute toggle_active transfer_authority`), cap table (`shareholders portfolio dividends`), banks (`bank_*`), pool (`pool pool_history pool_claim close_quarter bloctime`), civic (`civic civic_charter civic_override civic_resign`), proofs (`proof* proofs`), simulation (`simulate simulate_presets examples run_example`), context (`landscape fx source`).

The MCP registration for Claude lives in `/root/.claude.json` under `projects["/root/mod"].mcpServers.openhouse`. `tests/test_mcp.py` drives the router against a throwaway Mod.
