# near

NEAR Protocol as one mod: a REST API, a browser console and the MCP tools
running the same code on one port — the reads, and a write half
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
  function. `near_contract` parses the deployed binary's export section, and
  `near_abi` goes the rest of the way: it infers which methods are views and
  which are changes, what arguments each takes and of what type, which need a
  deposit — and `/near/c/<contract>` renders that as a working page for any
  contract on the network.

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
| `near_abi` | the interface, inferred — view/change/init per method, typed args with examples, deposit rules, confidence; embedded near-abi when there is one |
| `near_contracts` | the contracts that are ON — curated per network + your own deploys, each verified live |
| `near_directory` | every contract scraped off the chain itself — q= filters, limit/offset page |
| `near_search` | the census asked in plain words — "stablecoin" finds USDt, "lending" finds Burrow, a method name finds contracts exporting it |
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

## The interface — inferred, for every contract

`GET /abi?account_id=wrap.near` (tool `near_abi`, `m near/abi wrap.near`)
answers what the chain never stored: an ABI. Four sources, merged by how much
each can be trusted:

1. **Embedded ABI** — contracts built with `cargo near build` export
   `__contract_abi`, a zstd-compressed near-abi document. Decoded, it is
   exact: every function, its kind, modifiers (`init`, `payable`, `private`),
   every argument's JSON schema, the result type. Confidence 1.0.
2. **Probing** — every other method is called as a free view with `{}` and
   the panic text read back. `ProhibitedInView` means the method touches
   state or the caller: a change method. ``missing field `x` `` names a
   required argument; it is filled with a guess from its name (`*_id` is an
   account, `amount` a yocto string, `limit` a number) or with `null` so
   serde says what it wanted — `invalid type: null, expected a string` — and
   the probe goes again until the method stops asking. `Requires attached
   deposit of exactly 1 yoctoNEAR` is the transfer convention; `already
   initialized` is the constructor. A clean answer is a view, and the sample
   is kept. Confidence 0.9.
3. **The standards** — NEP-141/145/148/171/177/178/181/245/330 fix the names,
   argument shapes, optionality and deposits of the methods everyone uses, so
   `storage_deposit(account_id?, registration_only?)` is known before any
   probe runs. Confidence 0.8; the probe confirms.
4. **History** — the indexer's recent receipts to the contract carry the
   real JSON args of real calls: examples, and names the probe cannot reach.

The spec is cached under `~/.mod/near/abi.{network}.json` by code hash, so a
redeploy re-infers and an unchanged contract never costs a second probe.
`refresh=true` forces it; `deep=false` spends one call per method;
`method=` returns one method plus an args template.

**`/near/c/<contract>` is the generic app.** One HTML file, any contract:
the spec rendered as forms — a typed input per argument with the example as
its placeholder, VIEW buttons that call free, SIGN + CALL buttons that go
through the keystore with the deposit pre-filled (1 yocto where the standard
wants it) and a mainnet confirm box. The inference can be wrong on a method
with no standard name and no ABI; "as change" / "as view" flips one, and the
`{ }` button edits raw JSON args. `/near/c/wrap.near`, `/near/c/intents.near`,
`/near/c/myapp.testnet?network=testnet`.

## The census — scraping every contract on the network

No RPC lists contracts, and the public indexers time out when asked, so the
module builds the list itself from the chain's own primitive:
`EXPERIMENTAL_changes_in_block` names every account whose contract code was
touched in a block, one call per block.

- a **live tail** follows the head and catches every deploy the moment it
  lands — from the day the module starts, coverage of new deploys is total
- a **backfill** walks history backwards through FastNEAR's free archival
  RPC at a polite pace (`NEAR_SCAN_RPS`, default 1/s), so the index grows
  toward genesis for as long as the module runs
- every lookup that finds code (`near_account`, `near_contract`, the curated
  probe) feeds the same store — anything a user ever touches is indexed

It all lands in `~/.mod/near/contracts.{network}.json` — no database, no API
key, no third-party indexer — and both water marks persist, so a restart is
a resume. The console's EXPLORE tab renders it below the curated grid and
re-polls the store every 15 s, so the browser never drifts from the index.
`NEAR_SCRAPE=0` turns the threads off; the stored index still answers.

```bash
m near/directory q=.near limit=20        # newest deploys matching a filter
curl :50910/directory?network=mainnet    # same thing over REST
```

## Semantic search — the census asked in plain words

`near_search` / `GET /search?q=` ranks the directory by meaning, not by
substring, and it runs entirely locally — no model download, no embedding
API, no third party. Three layered signals (`search.py`):

- a **concept lexicon** maps what people mean onto what contracts are
  called — `stablecoin` → USDt/USDC, `lending` → Burrow, `swap` → Ref —
  applied to corpus and query alike, so terms meet in concept space
- **tf-idf cosine** over everything the module knows about a contract:
  account-id parts, curated labels and blurbs, and the WASM method names of
  every interface it ever parsed — so `ft_transfer` finds token contracts
- **trigrams** rescue typos: `usdcc` still lands on USDC

Every result carries `matched:` — the terms that ranked it, so the answer
explains itself. Exact and substring hits on account ids always surface
first (the old behaviour, kept). The console's lookup bar uses it as a
net: a query that isn't an account name, or a name the chain has never
heard of, falls through to search instead of a dead `UNKNOWN_ACCOUNT`.

The engine (`search.VectorIndex`) is deliberately generic — docs in,
ranked matches with explanations out — so any module with a corpus to
search can lift it whole.

```bash
m near/search q="liquid staking"         # meta-pool + linear, ranked
curl ':50910/search?q=stablecoin'        # same thing over REST
```

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

## Workshop — learn to write contracts, build them here, climb into ZK

`{host}/near/workshop` (or the **LEARN · BUILD** link in the console) is a
school with a builder attached. Nothing in it needs a model to read.

**LEARN** — twelve lessons in plain English, start to finish: an account IS a
contract, the four parts of every contract, who is calling (keys, sessions,
the one-yocto trick), build-test-ship, your own NEP-141 token, cross-contract
calls, then the zero-knowledge ladder (commitment → merkle membership →
nullifier → SNARK verifier → proof-native chain), commit-reveal, merkle
proofs, using the cairo mod, testing, and a pre-mainnet checklist. Each
lesson that has a starter ends in a **try it** that scaffolds it.

**BUILD** — a project is a Rust crate (near-sdk 5) under `contracts/<name>/`
made from a starter: `counter`, `token` (NEP-141 by hand), `commit_reveal`
(sealed votes, rung 1) or `merkle_allowlist` (rung 2). Build = the host's
cargo to wasm32 (one shared target dir, so near-sdk compiles once); Test =
the unit tests in `lib.rs`; Ship = `near_deploy` through the keystore with
the starter's init batched in, testnet by default, every confirm/token rule
intact. Every run is a job you can poll.

**Two agents** on the fleet's agent protocol (`GET /near/agents`,
`POST /near/run[/stream]`, the orb in the page's corner):

| agent | who | does |
|---|---|---|
| `tutor` | anyone, free, no model | answers from the lessons + glossary, reads your project (methods, last build/test), reads any contract on chain via the near tools, and pulls the cairo mod's lessons for anything zero-knowledge; every read is a `mcp:<tool>` step. `brain=claude` (token holders) hands the findings to orbit/claudecode |
| `builder` | token / loopback | "make a tip jar called tips where anyone sends and the owner withdraws" → scaffolds the closest starter, orbit/claudecode shapes it, cargo build + test, steps stream back. Point it at an open project to edit it. `vibe=false` = scaffold + test only |

**Zero knowledge goes to cairo.** Rungs 1 and 2 are a hash each and ship on
NEAR from here. Say *zk*, *cairo*, *anonymous* or *private* and the builder
forwards the whole ask to the cairo mod's builder (`NEAR_CAIRO_URL`, default
`:51291`); its steps stream back and the answer links to `/cairo/#studio/<name>`.
`near_zk` reads the cairo school (lessons, starters, contracts) over its MCP.

```bash
m near/lessons                               # the school
m near/lesson zk-ladder
m near/new tips template=token               # scaffold
m near/check tips                            # build + test
m near/make "a tip jar called tips where anyone sends and the owner withdraws"
m near/make "a zk vote called secretvote"    # → built on the cairo mod
m near/ask "how do I use zk proofs with cairo?"
m near/ship tips                             # testnet, via the keystore
```

Needs: `cargo` + the `wasm32-unknown-unknown` target on the host
(`near_toolchain` says), orbit/claudecode on `:51260` for vibe/builder, and
orbit/cairo on `:51291` for the ZK rungs. Without claudecode the builder
still scaffolds and tests; without cairo the ZK asks fall back to the
hash-only rungs here.

## Transport notes

- RPC goes through a failover pool (FastNEAR, dRPC, Shitzu, near.org, 1RPC)
  because NEAR's public endpoints throttle unevenly — a 429 or a gateway
  that lacks a method just moves to the next node; `NEAR_RPC` pins your own.
- Regular nodes garbage-collect state after a few epochs (free FastNEAR
  keeps roughly the last 100k blocks); older blocks and transactions fall
  back to `archival-rpc.mainnet.fastnear.com` — free, full history. The
  near.org archival endpoint is deprecated and answers -429.
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
