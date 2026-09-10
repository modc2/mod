---
name: chain
description: Hub for the modular smart-contract fleet on Base — deploy, inspect and operate 11 contract modules (token, oracle, registry, perms, tokengate, bloctime, treasury, market, debit, safe, bridge) from Python, CLI, HTTP API or the web console.
type: core-module
---

# chain

Orchestrator for the mod protocol's on-chain layer. Each contract module owns
one concern and they compose into a single protocol: test stables + native
token, price oracles, an on-chain name registry, payment-token gating,
time-weighted staking (BlocTime), treasury fee accrual, a mint/credit market,
signed debit pulls, a Safe multisig, and a bridge (WIP). One console deploys
the fleet per network, inspects every contract, and drives every function.

Ports: API **:8800** (FastAPI, `/docs` for OpenAPI UI) · app **:8801** (Next.js
console — Hub / Interact / Contracts / Control / Protocol / Owner / Docs).

## Capabilities

- **Dependency-ordered deploys**: modules deploy as groups — parallel within a
  group, sequential across: `[token, oracle, registry, perms] → [tokengate,
  bloctime] → [treasury] → [market] → [debit]`. Single-module deploys
  auto-resolve constructor deps from prior deployments.
- **Per-network deployments** in `config.json` (`testnet` = Base Sepolia
  84532, `ganache` 1337, `mainnet` = Base 8453) with each artifact's ABI and
  source pinned to IPFS CIDs.
- **Fork the whole protocol**: `fork(owner, ...)` deploys a fresh labelled copy
  of the fleet owned by another address.
- **Protocol ops**: mint native token with USDC/USDT, credit, transfer, stake /
  unstake BlocTime (stake × lock-blocks = weight), register names on-chain,
  rewards-pool snapshot/claim epochs, yield-vault strategies.
- **Admin / owner console**: read owner of every contract, encode or execute
  owner-only setters, transfer all ownership (e.g. to the Safe), export calls
  as a Safe multisig batch. Contract verification + deploy scripts via hardhat.
- **Contract builder**: compile arbitrary Solidity with solc 0.8.26 in a
  subprocess (`src/build/compile.js`). Imports resolve three ways — against the
  project's own files (whatever layout it was uploaded in), against the module's
  `node_modules` (`@openzeppelin/contracts`, `@chainlink`, `hardhat/console.sol`),
  and through remappings that point foundry's `lib/openzeppelin-contracts/…` at
  the installed packages. The API returns ABI + bytecode — deployment is signed
  in the browser, so no user key ever reaches the server. Starter templates
  (`Counter`, `Token`, `NFT`, `Vault`, `Splitter`) live in
  `src/build/templates/`; projects and recorded builds go to
  `~/.mod/chain/build/{projects,deployments}.json`, keyed by deployer address.
- **ABIs in the store module**: every ABI — fleet contracts and user builds
  alike — is written into `m.mod('dstore')` as a content-addressed object owned
  by the deploying address (`chain/abi/<network>/<name>.json`), and only the CID
  is recorded elsewhere (`config.json` "abi"/"src", `abi_cid`/`src_cid` on a
  build row). `GET /build/abi/{cid}` hands back the parsed ABI, so a contract
  deployed by one project can be driven from another with nothing but a CID.
- **Project gallery**: upload a project off your machine, or publish one you
  built so anybody can fork it (`~/.mod/chain/build/shared.json`, keyed
  `<author>/<name>`). Every fleet module ships in the gallery read-only —
  `fleet/token`, `oracle`, `registry`, `perms`, `tokengate`, `bloctime`,
  `treasury`, `market`, `debit`, `safe`, `defi` — each laid out as a Hardhat
  project with the contracts it depends on, its README and its own tests, and
  `fleet/protocol` bundles all of them with every test. Fork one and the tests
  run as-is.
- **Owner-only host readout**: per-core CPU, memory, swap, disk, per-interface
  network traffic, socket census and the top processes of the machine the API
  runs on. Gated by a wallet signature from an address in
  `~/.mod/chain/owners.json`; the console renders nothing for anyone else.
- **Wallets in the web console**: MetaMask (auto chain-switch/add on send) or a
  browser-local ethers keypair (import/export private key); reads are
  wallet-free via RPC.
- **Claude Code over a project (AGENT tab)**: `src/agent/mod.py` is a harness
  runner in the `orbit/agent` sense — `harness()` + `run(query, project=,
  address=, network=, on_step=)` — registered there as `chainmod` behind the
  shipped `chain-mod` agent, exactly like the build console's `buildmod`. A run
  lays the project out as a Hardhat workspace under
  `~/.mod/chain/build/agent/run/<address>/<project>/`, spawns the `claude` CLI
  there with `--permission-mode acceptEdits` (edits inside the workspace only),
  `--allowedTools "Bash(npx hardhat:*)"` (the shell runs hardhat and nothing
  else) and no web/subagent tools, translates its stream-json into the fleet's
  step dicts, and writes `contracts/` + `test/` back into the project when it
  ends (even after a timeout). The console reaches it through `POST /agent/run`
  → `orbit/agent` `POST /run/stream` (`agent_type: chain-mod`, `harness_args:
  {project, address, network, model}`) — never straight from the browser — so
  the agent module's owner gate (harness runs are owner-only: the host's own
  Claude account) and task ledger apply. `GET /agent/status` says whether it
  can run here and who may press the button; `GET /agent/runs` is the ledger.

## Usage

### Python
```python
import mod as m
chain = m.mod('chain')             # defaults to testnet; Mod('ganache') etc.

chain.deploy(network='testnet')    # deploy all groups in dependency order
chain.deploy_mod('market')         # one module, deps auto-resolved
chain.fork(owner='0x...', label='myfork')

chain.balance(token='usdc')        # balances / tokens / decimals
chain.stake(10**18, 1000)          # stake 1 token for 1000 blocks (BlocTime)
chain.bloctime_balance('0x...')    # time-weighted balance
chain.is_bloctime_holder('0x...')  # gate check used by other modules

chain.mint(payment_token='usdc', usd=1.0)   # market mint
chain.register('my-mod', 'Qm...')  # on-chain Registry: name → data
chain.regall(); chain.get_mod(1)   # read the registry

chain.pool(); chain.pool_claimable(); chain.pool_claim(token='usdc')
chain.yield_strategies(); chain.yield_deposit(0, 10.0)

chain.admin_owner('market')        # owner console
chain.admin_send('market', 'setFee', [30])
chain.verify_contract('testnet', 'Market')
```

### CLI
```bash
m chain/deploy network=testnet
m chain/balances address=0x...
m chain/stake amount=1000000000000000000 lock_blocks=1000
m chain/register name=my-mod data=Qm...
m chain/pool_claimable
```

### HTTP
```bash
curl localhost:8800/status                       # deployments per network
curl localhost:8800/mods                         # sub-module ports + liveness
curl -X POST localhost:8800/deploy -H 'Content-Type: application/json' \
  -d '{"network": "testnet", "mods": ["market"]}'
curl -X POST localhost:8800/call -H 'Content-Type: application/json' \
  -d '{"module": "registry", "function": "nextModId", "args": []}'
```

## API surface (port 8800)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` · `/info` | Liveness; module names, ports, endpoints |
| GET | `/mods` · `/status` | Sub-module liveness; per-network deployments |
| POST | `/deploy` | `{network, mods?}` — all or selected modules |
| GET | `/block` · `/timestamp` | Chain head / time |
| POST | `/contracts` | Addresses for a network |
| GET | `/contracts/mods` · `/contracts/source` · `/contracts/abis` | Mapping, source, ABIs |
| GET | `/cid/{cid}` | Fetch pinned IPFS artifact |
| POST | `/call` | Read any contract function |
| GET | `/wallet` · `/balances` · `/tokens` | Server wallet + balances |
| POST | `/mint` · `/credit` · `/transfer` | Market + ERC-20 ops |
| POST | `/stake` · `/unstake` — GET `/stakes` · `/bloctime/owner` | BlocTime staking |
| POST | `/register` · `/registry/register` — GET `/registry/mods` · `/registry/all` | On-chain registry |
| GET/POST | `/pool` `/pool/claimable` `/pool/claim` `/pool/epochs` `/pool/snapshot` | Rewards pool |
| GET/POST | `/yield/strategies` `/yield/position` `/yield/deposit` `/yield/withdraw` `/yield/harvest` `/yield/claim` | Yield vault |
| GET | `/admin/owners` | Owner of each contract |
| POST | `/admin/encode` · `/admin/send` · `/admin/transfer-all` | Owner-only calls / ownership transfer |
| GET | `/control/status` — POST `/control/verify` · `/control/deploy-script` | Toolchain, verification, scripts |
| POST | `/build/compile` | `{source, filename?, optimize?, runs?}` → deployable `{name, abi, bytecode, size, constructor}` + errors/warnings |
| GET | `/build/templates` | Starter contracts |
| GET/POST/DELETE | `/build/projects` — GET `/build/projects/{name}` | Per-address projects (a named bag of files) |
| GET/POST/DELETE | `/build/shared` — GET `/build/shared/{id}` | Shared project gallery; publish yours, fork anyone's. Ships every fleet module read-only (`fleet/<module>`, `fleet/protocol` = all of them) |
| GET/POST/DELETE | `/build/drafts` | Per-address source drafts |
| GET/POST | `/build/deployments` | Per-address record of wallet-signed builds; POST stores the ABI + source and returns their CIDs |
| GET | `/build/abi/{cid}` | One ABI out of the store, parsed |
| POST | `/build/abi` | `{address, name, network, abi, source?}` → `{cid}` — store an ABI for a contract deployed anywhere |
| GET | `/build/abis?address=` | ABIs that address has in the store |
| GET | `/cid/{cid}` | Raw stored content (ABI JSON or `.sol` source) |
| GET | `/agent/status` | Claude Code CLI + orbit/agent reachable, `chainmod` registered, the agent module's owner (harness runs are owner-only) |
| POST | `/agent/run` | `{key, query, project?, network?, model?}` → SSE bridge to orbit/agent `/run/stream` as `chain-mod`; events `start` / `step` / `done` / `error` |
| GET | `/agent/runs?address=` | Past agent runs on that address's projects |
| GET | `/system/access` · `/system/challenge` — POST `/system/login` | Owner sign-in for the host readout (wallet signature → 12h Bearer token) |
| GET | `/system/stats` | **Owner-only.** CPU per core, memory, disk, network traffic per interface, sockets, top processes |

## Environment

Used by `hardhat.config.js` / deploy tooling (all optional; sane defaults):
`PRIVATE_KEY`, `MNEMONIC`, `BASE_RPC_URL`, `BASE_TESTNET_RPC_URL`,
`GANACHE_URL`, `ETHERSCAN_API_KEY` (verification), plus `ETH_*` / `POLYGON_*` /
`ARBITRUM_*` RPC overrides for other networks. The app honors `PORT` and
`NEXT_PUBLIC_API_URL`; the API honors `PORT`.

## Key paths

- `config.json` — per-network deployments: addresses + pinned ABI/src CIDs
- `src/mod.py` — Python orchestrator (the full-power surface)
- `src/agent/mod.py` — the Claude Code harness runner (`m.mod('chain.agent')`)
- `tests/test_agent_harness.py` — the runner without the CLI (stubbed `claude`)
- `src/api/api.py` — FastAPI server; `src/api/start.sh`
- `src/app/` — Next.js console; `src/app/start.sh` (`DEV=0` → build + start)
- `src/contracts/<module>/` — Solidity sources, one README per module
- `src/build/compile.js` + `src/build/templates/` — contract builder toolchain
- `src/api/host.py` — /proc reader behind `/system/stats` (stdlib only)
- `~/.mod/chain/build/` — builder drafts + recorded user deployments (off-tree)
- `~/.mod/chain/owners.json` — who may read the host (0600, seeded from the
  non-local deployers; `CHAIN_OWNERS=0x…,0x…` overrides it)
- `~/.mod/chain/server.secret` — HMAC key for host-access tokens (0600)
- `scripts/deploy-defi.js`, `hardhat.config.js` — hardhat tooling
