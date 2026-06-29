# Mod Framework Documentation

Mod is a modular Python framework for building, deploying, and managing decentralized applications. It combines a powerful CLI, 200+ pluggable modules, smart contracts on Base, IPFS storage, AI-native agents, and a Next.js frontend into one unified system.

## Architecture

```
mod/
├── mod/core/
│   ├── mod.py          # Framework core (CLI, module loader, crypto, storage)
│   ├── utils.py        # 75KB of battle-tested utilities
│   ├── cli/            # CLI interface (m / c commands)
│   ├── api/            # FastAPI server (module registry, IPFS, blockchain)
│   ├── app/            # Next.js 14 frontend (TypeScript, Tailwind, ethers.js)
│   ├── chain/          # BlocTime Protocol (Solidity, Hardhat, Base Sepolia)
│   ├── server/         # PM2-based server management
│   ├── store/          # Encrypted key-value storage
│   └── key/            # Multi-chain key management (ETH, Substrate, Solana)
│
├── mod/orbit/          # 200+ pluggable modules
│   ├── agent/          # Agentic AI workflows
│   ├── claude/         # Claude Code integration
│   ├── ipfs/           # IPFS storage (auto-managed Kubo daemon)
│   ├── bridge/         # Sr25519 → EVM token bridge
│   ├── uniswap/        # DEX strategies (Rust engine)
│   └── ...             # safe, filecoin, lighthouse, web, cache, etc.
│
└── docs/               # You are here
```

## Quick Start

```bash
# Install
pip install -e .

# List all modules
m mods

# Load a module and call a function
m agent/forward query="build a hello world"

# Start the API server
m serve api

# Store and retrieve data
m put mykey '{"hello": "world"}'
m get mykey
```

## Documentation

| Doc | What's Inside |
|-----|--------------|
| [Getting Started](getting-started.md) | Installation, setup, first commands |
| [CLI Reference](cli.md) | `m` / `c` commands, argument parsing, examples |
| [Modules](modules.md) | Loading, searching, creating, and introspecting modules |
| [Storage](storage.md) | Key-value store, encryption, IPFS integration |
| [Keys](keys.md) | Multi-chain crypto keys, signing, verification |
| [Servers](servers.md) | PM2 server management, registry, ports |
| [API Server](api.md) | FastAPI endpoints, module registration, blockchain ops |
| [Smart Contracts](contracts.md) | BlocTime Protocol — Treasury, Market, Staking, Registry |
| [Frontend](frontend.md) | Next.js app, wallet integration, network layer |
| [Orbit Modules](orbit.md) | Full catalog of 140+ modules by category |
| [Skills](skills.md) | Every capability in one place — the full cheat sheet |
| [Utilities](utils.md) | Core utility functions reference |

## Core Concepts

**Modules** — Everything is a module. Each module lives in `mod/orbit/` with an anchor file (`mod.py`, `agent.py`, or `{name}.py`). Load any module with `m.mod('name')()` or call functions directly with `m.fn('name/function')()`.

**Orbits** — Modules are organized into orbits (namespaces): `core` (framework internals), `inner` (your modules), and `outer` (community modules). The framework searches all orbits when resolving module names.

**Keys** — Multi-chain cryptographic identity. Supports Ethereum (ecdsa), Substrate (sr25519/ed25519), and Solana. Keys are stored encrypted at `~/.mod/key/`.

**Storage** — Everything persists to `~/.mod/` as JSON files. Optional AES encryption. IPFS integration for decentralized storage via the `ipfs` orbit module.

**Servers** — Any module can be served as an HTTP API via PM2. Functions become POST endpoints. Service discovery through a built-in registry.

**BlocTime Protocol** — On-chain smart contracts on Base for revenue distribution (Treasury), marketplace credits (Market), time-weighted staking (BlocTime), module registry (Registry), and access control (TokenGate, Perms).
