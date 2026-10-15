---
name: near
description: NEAR Protocol whole — read accounts/keys/contracts/tokens/txns/validators, AND deploy + manage contracts (keystore in ~/.mod/near/, testnet faucet, signed calls, transfers, access keys). near_abi infers ANY contract's interface (view/change/init, typed args, deposits — embedded near-abi or probed), and /near/c/<contract> is a generic page that drives any contract as forms. Semantic search over the contract census runs locally (near_search — "stablecoin" finds USDt, no external service). REST + console + MCP tools on :50910. Non-testnet writes need confirm=true; HTTP writes need the ~/.mod/near/token.
---

# near

One port, three surfaces, same code: REST at `:50910/`, console at
`:50910/near` (or `{host}/near`), MCP at `POST :50910/mcp`.

## The shape of NEAR

An account and a contract are the same object; accounts are names
(`alice.near`) or 64-hex implicit accounts. Access keys are scoped —
FullAccess or FunctionCall-limited to one contract. NEAR keeps no ABI on
chain, but `near_contract` reads the deployed WASM's export section, so you
get the method list anyway, and `near_abi` infers the rest — which methods
are views, which are changes, what args each takes and of what type, which
need a deposit (embedded near-abi when there is one, probed off the chain
otherwise; cached by code hash). `near_view` then calls any view method free.
Call `near_abi` first when handed an unfamiliar contract; `method=` gives you
one method plus an args template. `{host}/near/c/<contract>` is the same spec
as a working page.
Deploying again over old code is the upgrade path — state survives.

## Quick answers

```bash
curl -s 'localhost:50910/account?account_id=root.near'      # balances + USD
curl -s 'localhost:50910/contract?account_id=wrap.near'     # method list
curl -s 'localhost:50910/abi?account_id=wrap.near'          # interface, inferred
curl -s 'localhost:50910/view?contract=wrap.near&method=ft_metadata'
curl -s 'localhost:50910/history?account_id=root.near'      # via indexer
curl -s 'localhost:50910/validators?limit=10'               # + Nakamoto
```

## Writing — deploy and manage contracts

```bash
m near/create_account myapp.testnet      # faucet mints + funds ~10 N, key saved
m near/deploy wasm_path=out.wasm account_id=myapp.testnet init_method=new
m near/call myapp.testnet set_status args='{"message":"hi"}'
m near/send bob.testnet 1.5
m near/key add public_key=ed25519:... contract=app.near methods=set_status
m near/wallet                            # keystore status — public halves only
```

- Writes default to **testnet**; a non-testnet write REFUSES without
  `confirm=true` — do not set it unless the human asked for mainnet.
- Over HTTP (REST or MCP) writes also need `token=` from `~/.mod/near/token`
  on the host; local callers (`m near/…`, stdio MCP) are exempt.
- Keys live in `~/.mod/near/keys.json`; nothing ever returns a secret key.
- `near_create_account`: fresh `*.testnet` names come from the public faucet
  (free, no signer); anywhere else the name must be a sub-account of the
  signer (`app.you.near` from `you.near`).

## Traps

- `near_history` and sender-less `near_tx` lean on the NearBlocks free
  indexer, which rate-limits by IP — a 502 means throttled, retry.
- Old transactions have been GC'd from regular nodes; the module falls back
  to `archival-rpc.mainnet.near.org` automatically.
- Nonces are read at OPTIMISTIC finality so back-to-back writes work; a
  one-off `INVALID_TRANSACTION` right after another write is a stale nonce —
  just retry.
- nearcore's prepare step rejects overly minimal WASM (`PrepareError:
  Deserialization`) — deploy real compiled contracts (`cargo near build`).
- `network=testnet` works on every tool; `rpc=` overrides the endpoint pool.

## Workshop — teach, build, and the ZK handoff to cairo

- `near_lessons` / `near_lesson id=` — the school (12 lessons: contracts from
  zero → tokens → cross-contract → ZK ladder → cairo). Quote these before
  explaining NEAR contracts yourself.
- `near_templates`, `near_new name= template=` (counter | token |
  commit_reveal | merkle_allowlist), `near_project`, `near_read`, `near_write`,
  `near_build` / `near_verify` / `near_check` (jobs — `wait=true` blocks, else
  poll `near_job`), `near_ship` (deploy via the keystore, testnet default).
- `near_vibe name= prompt=` — orbit/claudecode edits the crate, then build +
  test. `near_ask query=` — the free tutor; `near_zk` — the cairo mod's
  lessons/starters/contracts over its MCP.
- Agents: `GET /near/agents`, `POST /near/run` `{query, agent: tutor|builder}`
  (`/run/stream` = SSE). The builder is token/loopback only. If the ask says
  zk / cairo / anonymous / private it is forwarded to orbit/cairo's builder
  (`:51291`) and the answer links to `/cairo/#studio/<name>`; otherwise it
  builds a Rust crate here. `m near/make "..."` is the shell form.
- Writes here (new/write/build/vibe/ship/remove) take the same
  `~/.mod/near/token` or a loopback caller; ship keeps the testnet/confirm
  rules. Jobs and files live under `contracts/` in the module.
