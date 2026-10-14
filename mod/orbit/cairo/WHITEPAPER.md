# Cairo · a ZK contract workbench — Whitepaper

*Version 0.1 · October 2026 · the `cairo` module of the mod protocol. Served at `GET /cairo/api/whitepaper`, readable by agents through the `cairo_whitepaper` MCP tool, and shown in the console under **Paper**.*


## 1. Thesis

Zero-knowledge contracts are the part of crypto where the gap between "I have an idea" and "I have something I can click" is widest. Cairo is a good language and Starknet a good chain, but the path from a blank file to a working app runs through a toolchain (Scarb, starknet-foundry, a devnet, an account file, a declare step, a deploy step, calldata encoded as field elements) that most people never get through.

This module closes that gap with one rule: **every contract is a mod.** A contract is not a file inside a tool. It is a small self-contained project with its own name on the protocol (`cairo.<name>`), its own tests, its own API and its own app page. You can make one from a starter, have an agent write it, test it, deploy it on a practice chain and hand someone a link — all from one console, all against the real toolchain.

Three verbs carry the whole thing:

| Verb | What it does | Function |
|---|---|---|
| **make** | scaffold a contract from a starter, or fork an existing one | `new`, `fork` |
| **vibecode** | an agent edits the contract, then the workbench builds and tests it | `vibe` |
| **test** | `scarb build` + `snforge test`, with diagnostics, gas and failure reasons | `build`, `test` |

and one verb to use the result: **open** — every contract gets an app at `/cairo/<name>/`.

## 2. A contract is a mod

Each contract lives at `orbit/cairo/src/mods/<name>/`:

```
src/mods/<name>/
  Scarb.toml        the Cairo package (edition 2024_07, snforge_std 0.31)
  src/lib.cairo     the contract
  tests/            snforge tests
  config.json       mod metadata: description, template, fns
  mod.py            a thin anchor binding the workbench's fns to this contract
  README.md
  app.json          optional: the plain-language layer for its app
  app/index.html    optional: a fully custom app
  ext.py            optional: extra fns (e.g. cairo.zuma's keygen/submit/claim)
```

The protocol's path rule drops `src` and `mods`, so that directory is addressed `cairo.<name>`, and `m cairo.counter/test` runs that contract's tests from anywhere on the box. Package names are snake_case and match the directory; the contract module is PascalCase (`counter` → `mod Counter`, `ICounter`, `ICounterDispatcher`). `fork()` rewrites all of those — package, module, interface, dispatcher, `use` paths, test filename — so a fork is a real new contract, not a copy with a stale name.

Why one project per contract instead of one workspace: isolation. A broken contract cannot break another one's build, an agent scoped to one directory cannot touch its neighbours, and deleting a contract is deleting a folder.

## 3. The toolchain — real, not simulated

The workbench is orchestration (Python standard library only) over binaries already on the host:

| Tool | Version | Role |
|---|---|---|
| `scarb` | 2.8.4 | build Cairo 2 → Sierra + CASM |
| `snforge` | 0.31.0 | unit + integration tests, gas per test |
| `sncast` | 0.31.0 | declare / deploy / invoke on the sandbox |
| `starknet-devnet` | 0.2.4 | the practice chain (RPC 0.7, port 51292) |
| `universal-sierra-compiler` | 2.10 | Sierra → CASM for snforge |

Versions are pinned deliberately: devnet 0.2.x speaks RPC 0.7, which is what sncast 0.31 speaks; devnet 0.3+ speaks RPC 0.8 and breaks the pair. Build output is parsed, not just echoed: `build` returns `diagnostics` with `path:line` (the console turns them into red lines you can click), `test` returns per-test pass/fail, gas and the panic reason, and the noise the snforge scarb plugin prints on every compile is stripped.

## 4. Vibecoding on the agent protocol

`vibe(name, prompt)` does not call a model directly. It posts the task to the fleet's agent harness (`orbit/claudecode`, `/run/stream`), with the contract directory as the working path and a fixed set of Cairo rules as the goal prefix: stay inside this directory, keep the package and module names, write tests, run `scarb build` and `snforge test` yourself. Every step the agent takes streams back as a job step, so the console shows a live trail; when the agent stops, the workbench re-runs build + test itself and reports *that* result. The agent's claim that it passed counts for nothing; the workbench's own run is the result. If the harness is down, `vibe` falls back to the Claude CLI directly, scoped with `--add-dir`.

Vibecoding spends the host's model credits, so it is owner-only (section 7).

## 5. Every contract gets an app

`/cairo/<name>/` serves the contract's own `app/index.html` if it has one, otherwise a shared default page (`src/templates/app.html` — one plain HTML file, no build step) that drives entirely off the ABI:

- **Read** cards for every view function, refreshed automatically.
- **Write** cards for every external function, sent as a transaction.
- A **constructor** form to deploy it.
- **Act as** alice / bob / carol / dave — the devnet's public seed-0 accounts — so one person can play every role.
- **Activity**: the contract's events, decoded.

Underneath is `src/sandbox.py`: it boots the devnet on demand (state dumped every block, so it survives restarts), signs with sncast against a generated accounts file, and reads views, receipts and events over JSON-RPC. An ABI codec turns typed values into felts and back — a `u256` is one number, a `ByteArray` is text, `'short str'` is a felt, spans are comma lists, structs and enums are JSON, and account names stand in for addresses. A revert comes back as its panic reason, not a hex blob.

### Simple and developer views

Raw ABIs are unreadable for most people. A contract can ship an `app.json` that relabels it in plain words: a title, an intro, numbered steps, a label/help/button per function, inputs hidden and filled with "me" or a fixed value, outputs shown as a big number, a meter, a chart, a yes/no or a list, and events rewritten as sentences (`"{who} claimed {amount}"`). The page has a simple | developer switch; simple is the default. Hiding is presentation only — it is not access control, and the developer view always shows everything. The reference is `docs/simple-apps.md`.

## 6. Starters, the market and the school

**Starters** (`src/templates/<id>/`) are working contracts with tests and an `app.json`: counter, token (hand-written ERC-20), commit_reveal (Poseidon commitment bound to the caller), merkle_allowlist (sorted-pair Poseidon), stablecoin (cash-backed, mint ≤ reserves) and vault_stable (150% collateral ratio, oracle price, liquidation). Each passes its own suite out of the box.

**Market** lists every contract on the box as a card — build state, reads/writes/events, size, test count, whether it is live on the sandbox — with Open, App and Fork.

**Learn** is a seven-lesson track that teaches stablecoins and Cairo from zero, each lesson ending in the starter that implements it. A helper orb in the corner answers questions; its free brain is a keyword router that reads through the MCP tools, and its owner-only brain is a full agent on the harness.

Notable contracts built here:

- `cairo.subnet` — **private Yuma consensus**: validators commit sealed weight vectors (`poseidon('yuma', contract, epoch, who, salt, w…)`), reveal later, and anyone finalizes; the contract computes a stake-weighted median, clipping, incentives, EMA bonds and dividends in fixed point (ONE = 1e9).
- `cairo.zuma` — **ZK Yuma**: the same round, but miners and validators prove membership with LSAG ring signatures, so the contract knows each vote came from the set without knowing from whom. Per-round nullifiers stop double votes and double claims; a ring must be a single stake tier because the vote is weighted by the tier, not the signer.

## 7. Trust model — who can do what

The console is publicly routed. The rule is simple:

| Action | Who |
|---|---|
| Read anything: contracts, files, ABIs, events, market, fleet, lessons, this paper | anyone |
| Call a contract on the sandbox, deploy a built contract that is not live yet | anyone |
| write / new / fork / vibe / build / test / clean / remove / adopt / status | owner |
| Start, stop or reset the sandbox | owner |
| Helper agent with the `claude` brain | owner |

"Owner" means a request from loopback with no proxy headers, or a mod-protocol token (one `personal_sign`) from an address listed as owner in config, in the build console's private flag, or in `CAIRO_OWNERS`. The same gate guards REST, MCP and the helper. No private key for a real network exists anywhere in this module; the sandbox's accounts are public test keys, and nothing is sent to a live chain. `sncast` is on the box for anyone who wants to deploy by hand.

## 8. The fleet scan

The workbench is not the only place on this box that speaks Cairo. `fleet()` walks every mod under `orbit/` and `core/` — a mod is any directory with a `config.json` — and classifies it by the strongest evidence it has:

| Level | Evidence |
|---|---|
| **contract** | a `Scarb.toml` that builds a Starknet contract (`starknet` dependency or `starknet-contract` target) |
| **cairo** | a `Scarb.toml` for a plain Cairo program or circuit |
| **mentions** | no Scarb project, but its `config.json` names cairo / starknet / scarb / snforge / sncast / katana |

Each `Scarb.toml` is credited to the nearest enclosing mod, so a contract under `cairo/src/mods/x` counts for `cairo.x`, not for `cairo`; a parent that only hosts nested projects reports them as `nested_projects`. "devnet" alone is not evidence — every chain module has one. For each project the scan reports the package, the declared Cairo version, whether it is a contract, and whether it has snforge or `cairo_test` tests. Results are cached for a minute (`?refresh=1` skips the cache); a full scan of ~540 mods takes about half a second.

Snapshot on 2026-10-14: 21 mods with a Cairo signal out of 540 — 9 with Starknet contracts (`starktime`, `zquorum`, `zuma`, and the six `cairo.*` contracts), 1 plain Cairo package (`postquant`'s Cairo-MSS primitive), and 11 that only mention it (`starknet` and its app submods, and this module itself, which hosts its contracts in nested mods).

The point of the scan is reuse. Before writing a ring signature or a commit-reveal, look at who already has one: `zuma`'s `lsag.cairo` became `cairo.zuma` by copying one file.

## 9. Interfaces

Everything is one set of functions exposed four ways:

- **REST** — `GET|POST /cairo/api/<fn>` (and `/cairo/_api/<fn>` for the console itself). Long work (`build`, `test`, `vibe`, `status`) takes `async=true` and returns a job to poll at `/cairo/api/job?id=`; jobs persist across restarts and come back as `interrupted` rather than vanishing.
- **MCP** — `POST /cairo/mcp`, streamable HTTP, `cairo_*` tools as thin wrappers over the same fns, behind the same gate. `claude mcp add --transport http cairo http://localhost:51291/cairo/mcp`.
- **Agent** — `/cairo/run/stream` on the fleet's agent contract (`/cairo/agents`, `/.well-known/agent.json`).
- **CLI** — `m cairo/<fn>` and, per contract, `m cairo.<name>/<fn>`.

## 10. Limits, said plainly

- The sandbox is a local devnet. Nothing here touches Starknet mainnet or Sepolia, and a contract that works here has been tested, not audited.
- `snforge test` recompiles the test crate every run (~30 s). That is the tool, not a bug.
- The simple view hides inputs; it does not protect them.
- Vibecoding is only as good as the agent and the tests. The workbench guarantees the result was built and tested after the agent stopped; it does not guarantee the tests ask the right questions.
- The fleet scan reads config files and directory layouts. A mod that compiles Cairo some other way, with no `Scarb.toml` and no mention in its config, is invisible to it.

## 11. Roadmap

- Fleet import: pull a contract project found by the scan into the workbench as a `cairo.<name>` mod (today: fork by hand).
- Testnet deploy through the user's own Starknet wallet, never a server key.
- Proof artifacts next to tests: for programs run under `cairo-run`, keep the trace and proof as content-addressed objects, so "it passed" can be checked by someone who did not run it.
