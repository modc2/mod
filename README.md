<div align="center">

```
 _____ _______ ______
|     |       |      \
| | | |   -   |   -  |
|_|_|_|_______|______/
```

# mod

**A modular runtime for building, registering, and monetizing software on-chain.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-yellow.svg)](https://python.org)
[![Solidity 0.8.20](https://img.shields.io/badge/solidity-0.8.20-363636.svg)](https://soliditylang.org)
[![Base](https://img.shields.io/badge/chain-Base-0052FF.svg)](https://base.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

</div>

---

Write a module. Register it on-chain. Set a price. Get paid every time someone calls it.

```
You write code ──> Register on-chain ──> Users call it ──> You get paid
```

## Install

```bash
git clone https://github.com/modc2/mod.git
cd mod
./setup.sh        # installs node, pm2, rust, mod, commune
```

## Deploy

Three scripts standardize all deployment: `setup.sh`, `start.sh`, `stop.sh`.

### Local

```bash
./setup.sh        # one-time: installs all dependencies
./start.sh        # starts API + App via PM2
./stop.sh         # stops all servers
./stop.sh api     # stops a specific server
```

### Docker

```bash
./start.sh --docker              # build + run (image: mod, container: mod)
./start.sh --docker myimg mycon  # custom image/container names
./stop.sh --docker               # stops the mod container
./stop.sh --docker mycon         # stops a specific container
```

### Scripts

| Script | Purpose |
|--------|---------|
| `setup.sh` | Installs node, pm2, rust, mod (editable), commune |
| `start.sh` | Starts API + App locally, or `--docker` for containerized |
| `stop.sh` | Stops all mod servers (PM2) or `--docker` for container |

## Usage

### CLI

```bash
m mods                        # list all 218 modules
m info agent                  # inspect a module
m serve agent                 # start its API server
m kill agent                  # stop it
```

### Python

```python
import mod as m

agent = m.mod('agent')()                              # load a module
result = m.fn('agent/chat')({'message': 'hello'})     # call a function
m.serve('agent')                                       # start server
```

### Calling a function from the CLI

```bash
m agent/chat message="hello"    # calls agent.chat(message="hello")
m safe/balance                  # calls safe.balance()
```

Arguments are auto-coerced: ints, floats, bools, JSON all just work.

## Architecture

Three layers: **Orbit** (modules), **Core** (engine), **Chain** (on-chain protocol).

```
┌──────────────────────────────────────────────────────┐
│  ORBIT  218 modules                                  │
│  agent · claude · safe · bridge · uniswap · ipfs     │
│  goldfi · prefi · hyperliquid · venice · zcash · ... │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  CORE   mod.py (1800 lines, 210+ methods)            │
│  module loading · routing · crypto · storage · cli   │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  CHAIN  StakeTime Protocol (Base)                    │
│  registry · market · treasury · staking · oracles    │
└──────────────────────────────────────────────────────┘
```

### Directory layout

```
mod/
├── core/
│   ├── mod.py          # the engine — module loading, crypto, storage, 210+ methods
│   ├── utils.py        # networking, async, system helpers
│   ├── chain/          # Solidity contracts (StakeTime protocol)
│   ├── app/            # Next.js frontend
│   ├── api/            # module registry, IPFS, blockchain ops
│   ├── server/         # process management
│   ├── store/          # encrypted KV store (AES-256)
│   ├── key/            # key management (ED25519, ECDSA, SR25519)
│   ├── cli/            # the `m` command
│   ├── router/         # API gateway
│   └── gate/           # auth & token gating
│
└── orbit/              # 218 modules
    ├── agent/          # AI agents with memory & planning
    ├── claude/         # Claude integration
    ├── safe/           # Gnosis Safe multisig
    ├── bridge/         # cross-chain (Across, Squid, Hyperlane)
    ├── uniswap/        # Uniswap v3
    ├── hyperliquid/    # perpetual DEX
    ├── goldfi/         # gold-backed stablecoin
    ├── ipfs/           # decentralized storage
    └── ...             # 210 more
```

## Modules

Every module lives in `mod/orbit/<name>/` and contains:

| File | Purpose |
|------|---------|
| `mod.py` | The module class — all logic lives here |
| `config.json` | Name, ports, contracts, schema, endpoints |
| `skill.md` | Capability docs, usage examples, API table |

### Notable modules

| Module | What it does |
|--------|-------------|
| `agent` | Autonomous AI agents with memory, skills, multi-step planning |
| `claude` | Claude API — Rust backend + React UI |
| `safe` | Gnosis Safe multisig via ethers.js + ABI |
| `bridge` | Cross-chain bridges (Across, Squid, Hyperlane) |
| `uniswap` | Uniswap v3 — swaps, LP, analytics |
| `goldfi` | Gold-backed stablecoin (XAU-pegged) |
| `prefi` | Prediction markets with AMM |
| `hyperliquid` | Hyperliquid perps integration |
| `ipfs` | IPFS/Filecoin storage |
| `venice` | Venice AI models |
| `zcash` | Privacy transactions |

Browse all: [`mod/orbit/`](mod/orbit/)

## StakeTime Protocol

The on-chain layer. Modular primitives for registration, payments, staking, and consensus — each deployable independently, composable together.

**Chain:** Base (EVM L2) &nbsp;|&nbsp; **Contracts:** Solidity 0.8.20 + OpenZeppelin

### Contracts

| Contract | Purpose |
|----------|---------|
| **Registry** | Module registration, metadata, versioning |
| **Market** | Pricing, purchases, instant withdrawals |
| **Treasury** | Revenue distribution, multi-token splits |
| **BlocTime** | Time-weighted staking, governance weight |
| **TokenGate** | Token-gated access control |
| **Perms** | Role-based permissions |
| **Oracles** | Price feeds (Chainlink, Pyth) |

### Payment flow

```
Register module → Set price → User calls function → Payment to treasury → 80/20 split → Withdraw anytime
```

Developer gets 80%. Protocol gets 20%. No vesting, no delays.

### Deploy contracts

```bash
cd mod/core/chain
npx hardhat node                                        # local
npx hardhat run scripts/deploy.js --network localhost   # deploy local
npx hardhat run scripts/deploy.js --network base        # deploy mainnet
```

Full protocol spec: [`docs/whitepaper.md`](docs/whitepaper.md)

## CLI Reference

```bash
# discovery
m mods                        # list modules
m info <mod>                  # inspect module config, endpoints, deps
m code <mod>                  # view source
m dp <mod>                    # get module directory path

# servers
m serve <mod>                 # start API server (wraps module class)
m serve <mod>.app             # start API + Next.js frontend
m servers                     # list running servers
m kill <mod>                  # stop server
m restart <mod>               # restart server

# development
m test <mod>                  # run tests
m build <mod>                 # build (Docker, npm, etc.)
m deploy <mod>                # deploy to registry
m logs <mod>                  # tail logs

# git
m push "message"              # add + commit + push
m status                      # git status
m diff                        # git diff
```

## Python API

```python
import mod as m

# modules
agent = m.mod('agent')()
result = m.fn('agent/chat')({'message': 'hello'})
info = m.info('agent')

# servers
m.serve('api', port=8000)
m.kill('api')

# crypto
key = m.get_key('my_key')
sig = m.sign({'data': 'value'}, key='my_key')
encrypted = m.encrypt('secret', key='my_key')

# storage
m.put('user:1', {'name': 'alice'})
user = m.get('user:1')
m.put('api_key', 'secret', encrypt=True)

# AI
response = m.ask('explain this', model='claude')
```

## Stack

| Layer | Tech |
|-------|------|
| Contracts | Solidity 0.8.20, Hardhat, OpenZeppelin, ethers.js v6 |
| Backend | Python 3.11+, FastAPI, Redis |
| Frontend | Next.js 14, React, TypeScript, Tailwind |
| Chain | Base (EVM L2) |
| AI | Claude, OpenAI |
| Storage | IPFS, AES-256 encrypted KV |

## Docs

| Doc | Contents |
|-----|----------|
| [`docs/cli.md`](docs/cli.md) | CLI reference |
| [`docs/modules.md`](docs/modules.md) | Module system |
| [`docs/orbit.md`](docs/orbit.md) | Orbit module guide |
| [`docs/api.md`](docs/api.md) | API & endpoints |
| [`docs/contracts.md`](docs/contracts.md) | Smart contract spec |
| [`docs/keys.md`](docs/keys.md) | Key management |
| [`docs/storage.md`](docs/storage.md) | Storage system |
| [`docs/whitepaper.md`](docs/whitepaper.md) | StakeTime protocol paper |

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

*Code is capital.*

</div>
