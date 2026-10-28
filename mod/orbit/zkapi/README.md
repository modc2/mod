# zkapi

The Ethereum **zkAPI** framework ([vbuterin/zkapi](https://github.com/vbuterin/zkapi),
Open Anonymity + Ethereum Foundation) packaged as a mod.

zkAPI lets you pay for a metered API from an Ethereum deposit without the server
learning which deposit paid. The flow:

1. You deposit ETH into the vault once. The deposit becomes a note in a 32-level Poseidon Merkle tree.
2. Each request carries a Groth16 proof (BN254) that *some* note can cover the
   request's charge cap. The server then mints a short-lived provider key capped at that amount.
3. After the call, the server reads actual usage, charges it in gwei at a pinned
   Chainlink ETH/USD round, and Schnorr-signs your next (re-randomized,
   note-bound Baby-JubJub) balance commitment. Requests have unlinkable nullifiers.
4. You withdraw cooperatively with a clearance signature (`mutualClose`), or
   unilaterally (`initiateEscapeWithdrawal`, which starts a 24h window). If you
   try to escape with an old, already-spent balance, the challenger posts the
   server's archived request proof and restores the note.

## What this module runs

Everything protocol-bearing is **upstream code, unmodified**, from a pinned
checkout in `vendor/zkapi` (commit `045b444e`, circuit `zkapi-v2-note-bound-v1`):

| piece | upstream | here |
| --- | --- | --- |
| `ZkApiVault`, `Groth16ProofAdapter`, `Bn254Poseidon` | `protocol/contracts` | deployed on the devnet (anvil, chain 31337) |
| `zkapi serverd` | `crates/zkapi-serverd` | devnet protocol server, :51423 |
| `zkapi-indexerd` | `crates/zkapi-indexerd` | devnet tree indexer, :51422 |
| `zkapi-challenged` | `crates/zkapi-serverd` | watches escapes and challenges stale ones (anvil acct #1) |
| wallet core + provers | `protocol/rust/crates/zkapi-browser`, `zkapi-proof` | wrapped by `src/wallet` (persistent driver) |
| proving/verifying keys | `protocol/setup/v2` | same files the live mainnet verifier pins |

Module-owned pieces (the parts a host application normally supplies):

- `src/wallet/` is the `zkapi-wallet` Rust driver: a JSONL front end to the upstream
  wallet core. It writes every mutation to disk (fsync, then atomic rename, mode 0600)
  before replying. Deposit secrets are saved *before* the deposit transaction,
  and request journals are saved *before* the request is sent (write-ahead). It
  also keeps a history of signed states and supports recovery: `/expire`
  handshake, then discard or re-send.
- `src/contracts/DevnetPriceFeed.sol` is a Chainlink AggregatorV3-shaped feed. A
  keeper pushes the **live mainnet ETH/USD answer** into it, and the server reads it at
  the `finalized` tag exactly as it reads Chainlink on mainnet. Anvil runs with
  one slot per epoch, so finalized = head − 2.
- `src/zkstack/provider.py` implements OpenRouter's Management API (`/v1/keys`
  with limit, expiry, `include_byok_in_limit`, disable, usage and delete) plus
  `/v1/chat/completions`, so serverd runs its production lease path against it.
  The default engine is `zkapi/echo`, a deterministic local model with no network.
  Set `ZKAPI_UPSTREAM_BASE`/`ZKAPI_UPSTREAM_KEY` to meter a real OpenAI-compatible model.
- `src/zkstack/devnet.py` orchestrates everything above. State is persistent
  (`state/devnet`): anvil state dump, indexer cursor, server SQLite, provider
  ledger and wallet files all survive restarts. A keeper restarts crashed services.
- `src/zkstack/client.py` holds the client flows (deposit, lease, inference, retire,
  settle, recover, withdraw, escape, finalize, claimExpired) over the upstream
  wire protocol. They work against the devnet or a live deployment.
- `src/zkstack/networks.py` holds the live mainnet/Sepolia pins (from the upstream SDK)
  and a `verify()` that cross-checks them.

## Live deployments

`m zkapi/verify network=mainnet` runs 29 checks (27 on Sepolia). It compares:

- the SDK pins against the operator manifest at `/config.json`;
- the on-chain runtime keccak of the vault and verifier against the operator's deployment evidence;
- the vault immutables (signing keys, charge cap, gwei unit, protocol version) against the pins;
- the server's attestation and root against the vault;
- the server's quote against the pinned feed and the finalized Chainlink round;
- the indexer's root and next note id against the vault;
- the local `request.pk`/`withdrawal.pk`/`.vk` hashes against the pins. These hashes are why a proof built here verifies on mainnet.

On 2026-10-17 all checks passed on both networks.

`m zkapi/live network=sepolia action=...` uses a live deployment with this
module's wallet (`status|quote|create|deposit|chat|recover|withdraw|escape`). It
needs `ZKAPI_ETH_PRIVATE_KEY` and `confirm=True` for transactions. Sepolia `/v2`
also needs `ZKAPI_TESTNET_PASSWORD`. The live operators issue keys through the OA org
(`require_oa_key_source`), and inference goes to OpenRouter.

## Agents (agent protocol)

The app has three tabs: **APP** (the zkAPI console), **PARAMS** and **AGENTS**. The shipped agent is **Zeke**. An agent is
just its params: name, about, brain, model, max tokens, temperature, deposit, icon/color and system prompt. All of them
are editable on the PARAMS tab, which opens ready to edit.

- **zkapi brain** (default): each model call the agent makes is a real zkAPI request. The agent's own devnet wallet
  deposits into the vault, proves solvency, takes an anonymous lease, and the charge is settled. Steps:
  `deposit`, `zkapi:lease`, `zkapi:settle`, `finish`.
- **claude brain**: runs through orbit/claudecode `/run/stream` (bills the host account), so only the operator or the
  build owner can run it or switch an agent to it.
- **Editing is open by default** on the echo devnet. The build owner (same-origin `build_jobs_token`, checked through
  build's `whoami`) can always edit. Set `ZKAPI_AGENT_EDIT=owner` to lock editing to the owner.
- **Contract**: `GET /agents` returns `{agents:[...]}`. `POST /run/stream` and `/agents/{id}/run/stream` stream SSE in
  orbit/agent's shape. `GET /.well-known/agent.json` returns the agent card.
- **Publish**: AGENTS ▸ *publish to build* calls `POST /agents/publish`, which mounts `orbit/zkapi` in the build
  console's agent roster (owner only). Build passes `agent_type=<id>` to pick an agent.
- CLI: `m zkapi/agents`, `m zkapi/agent id=zeke prompt="..."`, `m zkapi/run query="hi" agent=zeke`, `m zkapi/publish`.

## CLI

```
m zkapi/setup                      # clone pinned upstream; build serverd/indexer/challenger + wallet; forge build
m zkapi/serve                      # API+app on :51420, boots the devnet (anvil :51421 ...)
m zkapi/devnet                     # chain, vault, price, services, server dashboard
m zkapi/create_wallet name=alice deposit_gwei=10000000
m zkapi/chat name=alice prompt="hello"          # one proof-paid request
m zkapi/withdraw name=alice                     # clearance + withdrawal proof -> mutualClose
m zkapi/escape name=alice                       # unilateral exit; history_index=N = stale/fraud demo
m zkapi/fast_forward seconds=86401 ; m zkapi/finalize note_id=0
m zkapi/verify network=mainnet
m zkapi/test                       # unit + full lifecycle on a scratch devnet (ports 51481-85)
m zkapi/acceptance                 # upstream scripts/v2-acceptance.mjs (needs vendor/node24)
m zkapi/kill
```

## API (`/zkapi/api/*`, prefix optional)

| route | |
| --- | --- |
| `GET health, info, status` | liveness, upstream pin |
| `GET devnet`, `devnet/manifest`, `devnet/price`, `devnet/accounts` | devnet state; SDK-shaped manifest |
| `POST devnet/up\|down\|reset\|fast_forward` | operator only |
| `POST devnet/price {usd?}`, `devnet/mine` | new oracle round / block |
| `ANY devnet/server/*`, `devnet/indexer/*` | passthrough to the upstream server/indexer (SDK clients) |
| `GET networks`, `networks/{mainnet\|sepolia}[?fresh=1]`, `networks/{n}/probe` | live verification |
| `GET wallets`, `wallets/{name}`; `POST wallets {name, deposit_gwei}` → `wallet_token` | |
| `POST wallets/{name}/deposit\|chat\|recover\|withdraw\|escape\|delete` | needs `X-ZKAPI-Wallet-Token` |
| `GET notes/{id}`; `POST notes/{id}/finalize`, `notes/{id}/claim_expired` | |
| `POST v1/chat/completions`, `GET v1/models` | OpenAI-compatible: each call is one anonymous proof-paid request (`X-ZKAPI-Wallet`) |

Access: reads are public. Devnet user actions are open while the provider uses the
local echo engine, because no real value exists on the devnet. Each wallet is
controlled by the token returned when it is created. Operator actions, and every
action once a paid upstream engine is configured, need loopback or `ZKAPI_API_TOKEN`.

## Tested

- `m zkapi/test` (13 tests) covers:
  - deposit, two requests, and a mutual close with exact payout and treasury accounting;
  - the lease-key lifecycle (issued → inference → disabled → usage_read → deleted);
  - a stale escape that the real challenger defeats, after which the honest state is still spendable;
  - an honest escape that finalizes after the window;
  - a superseded oracle round that triggers an `/expire` handshake and a re-quote;
  - a full restart with nothing lost;
  - the HTTP access model, the OpenAI endpoint and the server/indexer passthrough.
- `m zkapi/acceptance` is upstream's own end-to-end suite. It passes on this host.

## Layout

```
mod.py                 anchor (Mod: setup/serve/devnet/wallet/live/verify/test/acceptance)
config.json            manifest (port 51420, route /zkapi)
src/zkstack/           orchestration: paths, chain, wallet, provider, client, devnet, networks, server
src/wallet/            zkapi-wallet (Rust) around upstream zkapi-browser
src/contracts/         DevnetPriceFeed.sol (forge)
src/app/index.html     web app
src/tests/             pytest suite
vendor/                (gitignored) upstream checkout + build, node24
state/                 (gitignored) devnet chain, databases, wallets, logs
```

The upstream protocol is experimental: it uses a single-party Groth16 setup and has
not been audited (see upstream `docs/note-bound-commitments.md`). Don't deposit
more on mainnet than you are willing to lose.
