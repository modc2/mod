# MOD Protocol

**A modular framework for building, serving, and owning software as composable modules.**

**Version 1.0 | July 2026**

> Looking for the two-page version? Flip the docs to **Human** mode. The StakeTime validator-network design lives with its own module: `mod/orbit/staketime/WHITEPAPER.md`.

**Abstract.** MOD turns software into modules: self-describing directories that one convention makes loadable from Python, callable from a CLI, servable as HTTP APIs, routable through a gateway, and registrable on-chain. The protocol is deliberately small — a module anatomy, one URL rule, a null-call discovery convention, a shared signature-based identity layer, and a suite of smart contracts on Base (the BlocTime suite) for registration, payments, revenue sharing, and time-weighted staking. Everything else — 200+ modules spanning trading engines, storage networks, AI agents, and games — is built on top of, not into, the protocol.

---

## 1. Introduction

Software today is published as packages, deployed as services, and monetized through platforms — three disconnected systems with three permission models. MOD collapses them into one primitive, the **module**:

- **A package**: a directory with an anchor class, loadable by name.
- **A service**: its public methods become POST endpoints when served.
- **An asset**: its name and metadata can be registered on-chain to an owner address, with payments and revenue distribution handled by protocol contracts.

The design bet is that the primitive should be boring. A module needs no framework imports, no decorators, no manifest beyond a small `config.json`. The protocol's job is to make the path from "directory of code" to "discoverable, payable, owned service" as short as possible.

## 2. The Module

```
mod/orbit/<name>/
├── mod.py          # anchor: class Mod — public methods are the module's functions
├── config.json     # name, description, port (API), app_port (frontend), schema (IPFS CID)
├── README.md       # human documentation
├── skill.md        # agent documentation
└── app/, src/, …   # implementation (any language; Python, Rust, and Next.js are common)
```

Modules are organized into **orbits** — namespaces searched in order: `core` (the framework itself), `orbit` (the ecosystem, 200+ modules), `mods` (registry-installed), and `local`.

```python
import mod as m
app = m.mod('agent')()          # load
m.fn('agent/forward')(query=…)  # call one function
```

```bash
m agent/forward query="build a hello world"    # identical call via CLI
```

Public functions execute the same whether invoked from Python, the CLI, or HTTP — the protocol treats transport as an implementation detail.

## 3. The Protocol Conventions

Four rules make modules interoperable (full detail in [Protocol](protocol)):

1. **Anatomy** — an anchor class in a directory under an orbit; `config.json` declares ports and metadata.
2. **Serving** — `m serve <name>` exposes functions as `POST /{fn}` and `POST /mod/{name}/{fn}`; PM2 supervises processes.
3. **Null-call discovery** — `POST /mod/{name}` with no function returns the module's info (functions, schema, description). Any module can be introspected by hitting its bare URL.
4. **One URL rule** — every gateway routes `/{mod}` to the module's app and `/api/{mod}` (prefix stripped) to its API. Three interchangeable implementations exist: the production Caddy gateway (routes auto-generated from module configs), the `routy` Rust gateway, and the core Next.js middleware.

## 4. Identity, Keys, and Auth

Identity is a keypair, not an account. The core key manager supports Ethereum (ECDSA), Substrate (sr25519/ed25519), and Solana keys, stored encrypted under `~/.mod/key/`.

A single shared auth module (`m.mod('auth')`) issues and verifies signed tokens; the verified identity is the recovered signer address. Modules gate privileged functions by comparing that address to an owner declared in config or to on-chain state (e.g. token holdings). Private authorization state — whitelists, ACLs, grants — lives off-chain under `~/.mod/{module}/`, never in committed files.

## 5. Storage

- **Local**: all state persists as JSON under `~/.mod/`, with optional AES encryption (`m put` / `m get`).
- **Content-addressed**: IPFS backs module schemas, shared objects, and portable data; a CID in `config.json` describes each module's interface. Anything with a CID can be re-fetched, verified, and imported on another machine.

## 6. The BlocTime Contract Suite

The on-chain layer is a set of small, single-purpose Solidity contracts (0.8.20, OpenZeppelin, Hardhat) deployed on Base Sepolia (chainId 84532), each usable alone:

| Contract | Purpose |
|----------|---------|
| **Registry** | On-chain module registration: name + IPFS metadata → owner; update, transfer, remove |
| **BlocTime** | Time-weighted staking: lock the native token for `lockBlocks`, mint BlocTime at `amount × multiplier(lock) / 10000` along a monotonic piecewise-linear curve; burn to unstake after the lock |
| **Market** | USD-pegged credit tokens: deposit whitelisted stablecoins, oracle-converted, with configurable fees to Treasury |
| **Debit** | EIP-712 signed client→provider debits with multi-authority approval and daily limits |
| **Treasury** | Proportional revenue distribution to governance-token holders, on current balances |
| **TokenGate** | Token whitelist + per-token oracle adapters (Manual, Chainlink, Pyth) |
| **Perms** | Hierarchical parent→child key permissions |
| **Safe** | Gnosis Safe multisig for progressive decentralization of contract ownership |

Deployed addresses live in `mod/core/chain/config.json`; per-contract detail in [Contracts](contracts).

**How the pieces meet:** a module registers its name and schema CID in the Registry; users buy Market credits with stablecoins; providers debit credits for module invocations; fees accumulate in the Treasury and distribute to token holders; BlocTime holdings gate premium access (modules check holder balances on-chain); ownership of any contract can be transferred to a Safe and eventually renounced.

BlocTime is intentionally minimal: it has no validators, no slashing, and no emissions — it converts lock-time into weight, and other modules decide what that weight means (access, priority, governance). A full validator-network composition (consensus scoring, inflation curves, competitive subnet registry) is explored in the separate `staketime` orbit module and its own whitepaper.

## 7. Security Model

- **Contracts**: ReentrancyGuard and SafeERC20 throughout; checked arithmetic (0.8.x); Ownable access control with an explicit `setOwnerless()` renunciation path; monotonic multiplier-curve enforcement; no proxy/upgrade pattern — logic is immutable, parameters are owner-configurable.
- **Staking**: BlocTime cannot be flash-loaned into existence — minting requires a real lock, and unstaking is block-height-enforced with no override.
- **Off-chain**: privileged module functions require signature-verified owners; secrets and ACLs never enter the repo; served modules that execute untrusted work drop privileges.

## 8. Decentralization Path

1. **Deployer control** — rapid iteration, parameters tunable.
2. **Multisig** — ownership transferred to a Safe.
3. **Renunciation** — `setOwnerless()`; contracts run autonomously with frozen parameters.

The same path applies per-contract, so subsystems decentralize independently.

## 9. Conclusion

MOD is one primitive applied uniformly: everything is a module. The protocol stays small — an anatomy, a URL rule, a discovery call, a signature identity, and a handful of composable contracts — so that the ecosystem above it can stay large.

---

*MOD Protocol — modc2.com · docs at /docs · contracts on Base Sepolia.*
