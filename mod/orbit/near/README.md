# near

NEAR Protocol as one mod: a REST API, a browser console and eighteen MCP
tools running the same code on one port — twelve reads, and a write half
that deploys and manages contracts with keys from a keystore under
`~/.mod/near/` (never the repo; no API ever returns a secret key).

NEAR's personality is its account model, and the module follows its grain:

- **Accounts are names** — `alice.near`, `app.testnet`, or 64 hex chars for an
  implicit account — and **an account and a contract are the same object**.
  One lookup answers balances (liquid, staked, storage-reserved, USD), access
  keys, and what the deployed code can be asked to do.
- **Access keys carry permissions** — FullAccess, or FunctionCall limited to
  one contract's methods with a gas allowance. Reading them shows which apps
  an account has signed into: session auth, on chain.
- **NEAR stores no ABI**, but every callable method is an exported WASM
  function. `near_contract` parses the deployed binary's export section, so
  you see a contract's interface before calling it with `near_view`.

## Run

```bash
python3 api.py            # API + console + MCP on :50910
python3 mcp.py            # the same tools over stdio
```

- console: `http://localhost:50910/near` (or `{host}/near` behind the gateway)
- API: `GET /` lists every route
- MCP: `POST /mcp` — Streamable HTTP, JSON-RPC 2.0

```json
{"mcpServers": {"near": {"type": "http", "url": "http://localhost:50910/mcp"}}}
```

## Tools

| tool | answers |
|---|---|
| `near_account` | balances, storage, contract flag, USD |
| `near_keys` | access keys and their permissions |
| `near_contract` | callable methods, parsed from the WASM |
| `near_view` | any view method, JSON args, free |
| `near_ft` | a NEP-141 token, balances scaled by its decimals |
| `near_history` | recent txns (NearBlocks indexer — RPC has no by-account query) |
| `near_tx` | one transaction: actions, outcome, fee; archival fallback for old ones |
| `near_block` | a block by height/hash, or latest final |
| `near_network` | height, gas price, validators, stake, price |
| `near_validators` | stake, uptime, Nakamoto coefficient |
| `near_price` | NEAR/USD, 24h change, market cap |
| `near_rpc` | any JSON-RPC method, raw |
| `near_wallet` | the keystore: status / generate / import / select / forget |
| `near_create_account` | a funded `*.testnet` via the faucet, or a sub-account of the signer |
| `near_deploy` | ship a `.wasm` to the signer's own account (+ optional init, same tx) |
| `near_call` | a signed change call — gas in Tgas, deposit in NEAR |
| `near_send` | transfer NEAR |
| `near_key` | add (full or contract-scoped) / delete access keys |

## Writing — deploy and manage contracts

```bash
m near/create_account myapp.testnet        # faucet mints + funds (~10 N), key saved
m near/deploy wasm_path=contract.wasm account_id=myapp.testnet \
    init_method=new init_args='{"owner_id":"myapp.testnet"}'
m near/call myapp.testnet set_status args='{"message":"hi"}'
m near/contract myapp.testnet              # the chain now serves your methods
```

On NEAR the account *is* the contract: deploying again over old code is the
upgrade path, and state survives. The console's DEPLOY tab does the same via
drag-and-drop of the compiled `.wasm`.

Safety, in order of appearance:

- writes default to `network=testnet`; **a non-testnet write refuses without
  `confirm=true`** (the orbit/eth convention)
- a write arriving over HTTP must carry `token=` from `~/.mod/near/token` —
  the port is publicly routed, local callers (stdio MCP, `m near/…`) are exempt
- secret keys live in `~/.mod/near/keys.json` (0600) and never leave it

ed25519 comes from PyNaCl when installed, else a pure-Python RFC 8032
implementation in-tree (tested against the RFC vector and against PyNaCl);
borsh serialization of the transaction schema is in-tree too, so the module
still has zero hard dependencies.

## Transport notes

- RPC goes through a failover pool (FastNEAR, Lava, near.org, 1RPC) because
  NEAR's public endpoints throttle unevenly; `NEAR_RPC` pins your own node.
- Regular nodes garbage-collect transactions after a few epochs; `near_tx`
  retries on an archival node before giving up.
- `near_history` needs an indexer (NearBlocks free tier, IP rate-limited);
  a 502 there means throttled, not missing.
- The account history endpoint returns *receipts*; the module collapses them
  to one row per transaction and skips `system` gas-refund receipts when a
  real predecessor exists.

## Test

```bash
python3 -m pytest -q            # includes live mainnet reads
NEAR_OFFLINE=1 python3 -m pytest -q   # units only
```
