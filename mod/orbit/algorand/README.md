# algorand

The Algorand protocol as one mod: read anything on chain, simulate any
contract call for free, and sign payments, assets and smart-contract calls
from a local keystore. It follows the fleet's api/app split: the API (REST +
MCP) is `api/` on **51340**, reached through the gateway at `/algorand/api`;
the browser console is `app/` on **51341** at `/algorand`, and it proxies
`/algorand/_api` to the API so the page only ever talks to its own origin.

Zero hard dependencies. Canonical msgpack, base32 addresses, 25-word
mnemonics, ed25519 and ARC-4 encoding live in `codec.py`. PyNaCl is used for
speed when it's installed. Reads go to AlgoNode's free algod and indexer
(mainnet, testnet, betanet), or to your own node via `ALGOD_URL` /
`INDEXER_URL` / `ALGOD_TOKEN`.

## Algorand in five lines

- **Accounts** are 58-char base32 addresses (or NFD names like `alice.algo`).
  Every account keeps a *minimum balance*: 0.1 ALGO, plus 0.1 for each asset
  or app it opts into.
- **Assets (ASAs)** are numbered. An account must **opt in** before it can
  receive one.
- **Smart contracts** are numbered *applications* written in TEAL, with
  global, local and box storage. Each app has its own account.
- **Atomic groups**: up to 16 transactions that all succeed or all fail.
  `pay_algo=` on an app call uses one to fund the call in the same step.
- **Simulate** runs any transaction group on algod with no signature and no
  fee. This is how you make a "view call" on Algorand.

## Use

    m algorand/account algo.algo               # balances, min-balance, ASAs, apps
    m algorand/asset USDC holder=<addr>        # an ASA + one holder
    m algorand/assets q=gold                   # search assets
    m algorand/app 1002541853 program=true     # decoded state, boxes, TEAL
    m algorand/simulate <app> method='get(address)uint64' args=<addr>
    m algorand/history <addr> kind=axfer
    m algorand/network

    m algorand/wallet op=generate              # key into ~/.mod/algorand/
    m algorand/send <addr> 1.5
    m algorand/asset_create name=Mod unit=MOD total=1000000 decimals=6
    m algorand/optin 10458941                  # testnet USDC
    m algorand/app_create approval="$(cat approval.teal)" global_ints=1
    m algorand/app_call <app> method='inc()uint64'
    m algorand/export                          # mnemonic backup, shell only

    m algorand/serve                           # API+MCP :51340, console :51341
    pm2 start ecosystem.config.js              # same, as algorand-api + algorand-app

Testnet ALGO comes from <https://bank.testnet.algorand.network>. It's behind
a captcha, so fund a generated address there by hand.

## Tools

Reads: `algo_account` · `algo_resolve` · `algo_asset` · `algo_assets` ·
`algo_app` · `algo_box` · `algo_local_state` · `algo_simulate` · `algo_tx` ·
`algo_history` · `algo_block` · `algo_network` · `algo_price` ·
`algo_compile` · `algo_rpc`

Writes: `algo_wallet` · `algo_send` · `algo_asset_create` ·
`algo_asset_optin` · `algo_asset_send` · `algo_app_create` ·
`algo_app_call` · `algo_app_update`

REST: every tool has a route (`GET|POST /account`, `/simulate`,
`/app_call`, …). `GET /` lists them all. MCP: `POST /mcp` (Streamable HTTP)
or `python3 mcp.py` (stdio).

App args: with `method=` (an ARC-4 signature) the values are ABI-encoded
(uint*, bool, byte, address, string, byte[]). Without it they go raw: ints
become uint64, and you can also pass `str:…`, `addr:…`, `b64:…` or `0x…`.
Box names follow the same prefixes.

## Safety

- Keys live in `~/.mod/algorand/keys.json` (0600), never in the repo. No
  route or tool returns a secret. `export` exists only as a local-shell fn.
- Writes default to **testnet**. Any other network refuses without
  `confirm=true`.
- A write over HTTP (REST or HTTP MCP) needs `token=` from
  `~/.mod/algorand/token`. Stdio MCP and `m algorand/...` are local and skip
  the token check.
- Rekeyed accounts are detected, and the envelope carries `sgnr`
  automatically.

## Files

| file | what |
|---|---|
| `codec.py` | msgpack, addresses, txids, group ids, ed25519, mnemonics, ARC-4 |
| `chain.py` | algod + indexer + NFD client, reads, submit, wait, simulate |
| `wallet.py` | keystore, token, transaction builders, sign + submit |
| `mcp.py` | tool registry + JSON-RPC 2.0 |
| `api/api.py` | the API: REST routes + `POST /mcp`, stdlib HTTP server |
| `app/index.html` | the console: EXPLORE · APPS · WALLET · ASSETS · DEPLOY · CHAIN · MCP |
| `app/server.py` | serves the console at `/algorand`, proxies `/algorand/_api` to the API |
| `ecosystem.config.js` | pm2: `algorand-api` + `algorand-app` |
| `test/` | wire-format tests offline, live chain tests online (`ALGORAND_OFFLINE=1` skips) |
