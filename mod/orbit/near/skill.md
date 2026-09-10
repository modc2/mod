---
name: near
description: NEAR Protocol whole — read accounts/keys/contracts/tokens/txns/validators, AND deploy + manage contracts (keystore in ~/.mod/near/, testnet faucet, signed calls, transfers, access keys). REST + console + 18 MCP tools on :50910. Non-testnet writes need confirm=true; HTTP writes need the ~/.mod/near/token.
---

# near

One port, three surfaces, same code: REST at `:50910/`, console at
`:50910/near` (or `{host}/near`), MCP at `POST :50910/mcp`.

## The shape of NEAR

An account and a contract are the same object; accounts are names
(`alice.near`) or 64-hex implicit accounts. Access keys are scoped —
FullAccess or FunctionCall-limited to one contract. NEAR keeps no ABI on
chain, but `near_contract` reads the deployed WASM's export section, so you
get the method list anyway; `near_view` then calls any view method free.
Deploying again over old code is the upgrade path — state survives.

## Quick answers

```bash
curl -s 'localhost:50910/account?account_id=root.near'      # balances + USD
curl -s 'localhost:50910/contract?account_id=wrap.near'     # method list
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
