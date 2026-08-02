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
  subprocess (`src/build/compile.js`); imports resolve against the module's
  `node_modules`, so `@openzeppelin/contracts` works. The API returns ABI +
  bytecode — deployment is signed in the browser, so no user key ever reaches
  the server. Starter templates (`Counter`, `Token`, `NFT`, `Vault`,
  `Splitter`) live in `src/build/templates/`; drafts and recorded builds go to
  `~/.mod/chain/build/{drafts,deployments}.json`, keyed by deployer address.
- **Wallets in the web console**: MetaMask (auto chain-switch/add on send) or a
  browser-local ethers keypair (import/export private key); reads are
  wallet-free via RPC.

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
| GET/POST/DELETE | `/build/drafts` | Per-address source drafts |
| GET/POST | `/build/deployments` | Per-address record of wallet-signed builds |

## Environment

Used by `hardhat.config.js` / deploy tooling (all optional; sane defaults):
`PRIVATE_KEY`, `MNEMONIC`, `BASE_RPC_URL`, `BASE_TESTNET_RPC_URL`,
`GANACHE_URL`, `ETHERSCAN_API_KEY` (verification), plus `ETH_*` / `POLYGON_*` /
`ARBITRUM_*` RPC overrides for other networks. The app honors `PORT` and
`NEXT_PUBLIC_API_URL`; the API honors `PORT`.

## Key paths

- `config.json` — per-network deployments: addresses + pinned ABI/src CIDs
- `src/mod.py` — Python orchestrator (the full-power surface)
- `src/api/api.py` — FastAPI server; `src/api/start.sh`
- `src/app/` — Next.js console; `src/app/start.sh` (`DEV=0` → build + start)
- `src/contracts/<module>/` — Solidity sources, one README per module
- `src/build/compile.js` + `src/build/templates/` — contract builder toolchain
- `~/.mod/chain/build/` — builder drafts + recorded user deployments (off-tree)
- `scripts/deploy-defi.js`, `hardhat.config.js` — hardhat tooling
