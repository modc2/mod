# MOD — The Simple Whitepaper

**Everything is a module.**

## The idea

Today, code lives in three disconnected worlds: packages you install, services you deploy, and products you sell. Each has its own accounts, permissions, and plumbing.

MOD collapses them into one thing — a **module**. A module is just a directory with a Python class in it. The protocol makes that same directory:

- **callable** — `m hello/greet` from the CLI, `m.fn('hello/greet')()` from Python
- **a live API** — serve it and every public method becomes a POST endpoint
- **a web app** — reachable at `https://host/{module}`, its API at `https://host/api/{module}`
- **discoverable** — POST to its bare URL and it describes itself (its functions, its schema)
- **ownable** — register its name and metadata on-chain to your address

No decorators, no manifest, no platform account. If you can write a class, you've written a module.

## The rules (all five of them)

1. **A module is a directory** under an orbit (`core`, `orbit`, `mods`, `local`) with an anchor class. `config.json` declares its name, description, and ports.
2. **Serving is automatic.** Public methods become HTTP endpoints; JSON in, `{"result": ...}` out.
3. **One URL rule.** `/{mod}` is the app, `/api/{mod}` is the API. Every gateway (Caddy in production, Rust, Next.js) enforces the same rule.
4. **Null call = discovery.** `POST /mod/{name}` with nothing else returns what the module is and what it can do.
5. **Identity is a signature.** Your address (Ethereum, Substrate, or Solana key) is your account. Modules verify signatures, not passwords. Secrets stay in `~/.mod/`, never on-chain or in the repo.

## The chain

A small suite of contracts on Base — the **BlocTime suite** — gives modules an economy. Each contract does one thing:

- **Registry** — claim a module name, point it at IPFS metadata, transfer or sell it.
- **Market + Debit** — buy USD-pegged credits with stablecoins; providers debit them per use, with signed authorizations and daily limits.
- **Treasury** — fees flow in; token holders claim their proportional share.
- **BlocTime** — lock tokens for time, get BlocTime tokens weighted by how long you locked. Time-in beats size-of-wallet. Modules use BlocTime holdings to gate access and rank priority.
- **TokenGate, Perms, Safe** — token whitelisting with price oracles, key-based permissions, and multisig ownership for progressive decentralization.

No contract needs the others; together they make modules registrable, payable, and revenue-sharing.

## Why it matters

- **For builders**: a directory of code becomes a deployed, discoverable, monetizable service with one command.
- **For users**: every module works the same way — same URLs, same discovery, same wallet-based identity.
- **For the network**: value routes to the people who build and hold, not to a platform in the middle. Locking time, not spending capital, earns weight.

## Where it is

200+ modules run on the protocol today — trading engines, storage, AI agents, games, governance — all at `modc2.com/{module}`. Contracts are live on Base Sepolia. The full technical version is the [whitepaper](whitepaper); the conventions are specified in [Protocol](protocol).

**One primitive, uniformly applied: everything is a module.**
