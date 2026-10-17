# zkworld · a ZK workbench for any framework — Whitepaper

*Version 0.3 · October 2026 · the `zkworld` module of the mod protocol. Served at `GET /zkworld/api/whitepaper`, readable by agents through the `zkworld_whitepaper` MCP tool, and shown in the console under **Paper**.*


## 1. Thesis

Zero-knowledge contracts are the part of crypto where the gap between "I have an idea" and "I have something I can click" is widest. Cairo is a good language and Starknet a good chain, but the path from a blank file to a working app runs through a toolchain (Scarb, starknet-foundry, a devnet, an account file, a declare step, a deploy step, calldata encoded as field elements) that most people never get through.

This module closes that gap with one rule: **everything is a mod.** Each framework is a running submod, and each contract is a submod owned by its framework (section 2b). A contract is not a file inside a tool. It is a small self-contained project with its own name on the protocol (`zkworld.<name>`), its own tests, its own API and its own app page. You can make one from a starter, have an agent write it, test it, deploy it on a practice chain and hand someone a link — all from one console, all against the real toolchain.

Three verbs carry the whole thing:

| Verb | What it does | Function |
|---|---|---|
| **make** | scaffold a contract from a starter, or fork an existing one | `new`, `fork` |
| **vibecode** | an agent edits the contract, then the workbench builds and tests it | `vibe` |
| **test** | `scarb build` + `snforge test`, with diagnostics, gas and failure reasons | `build`, `test` |

and one verb to use the result: **open** — every contract gets an app at `/zkworld/<name>/`.

## 2. A contract is a mod

Each contract lives at `orbit/zkworld/src/mods/<name>/`:

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
  ext.py            optional: extra fns (e.g. zkworld.zuma's keygen/submit/claim)
```

The protocol's path rule drops `src` and `mods`, so that directory is addressed `zkworld.<name>`, and `m zkworld.counter/test` runs that contract's tests from anywhere on the box. Package names are snake_case and match the directory; the contract module is PascalCase (`counter` → `mod Counter`, `ICounter`, `ICounterDispatcher`). `fork()` rewrites all of those — package, module, interface, dispatcher, `use` paths, test filename — so a fork is a real new contract, not a copy with a stale name.

Why one project per contract instead of one workspace: isolation. A broken contract cannot break another one's build, an agent scoped to one directory cannot touch its neighbours, and deleting a contract is deleting a folder.

## 2b. Submods: frameworks run, projects belong to them

zkworld is a tree of mods, two levels deep, all under `orbit/zkworld/src/mods/`:

```
zkworld                      the workbench: console, API, MCP, agents   :51350
├── zkworld.cairo            framework submod (running)                 :51353
│   ├── zkworld.counter        project submod (a Cairo contract)
│   ├── zkworld.airdrop
│   └── ... usd, vaultusd, subnet, zuma, tipjar_demo
├── zkworld.circom           framework submod                           :51354
│   └── zkworld.factors, zkworld.agecheck
├── zkworld.noir             :51355   zkworld.factors_noir, zkworld.squareroot
├── zkworld.zokrates         :51356   zkworld.factors_zok
├── zkworld.leo              :51357   zkworld.factors_leo
├── zkworld.gnark            :51358   zkworld.factors_gnark
├── zkworld.halo2            :51359   zkworld.factors_halo2
└── zkworld.custom           :51360   runner-protocol projects (risc0, sp1, arkworks, ...)
```

**A framework submod** is a service. `src/mods/<id>/` holds a `mod.py` (rendered from `src/templates/fwmod.py.tmpl`), a `config.json` with `kind: "framework"` and its own port, and a README. It runs its own small server (`src/api/fwserver.py <id> <port>`, loopback only) and answers:

| Fn | What | Access |
|---|---|---|
| `info`, `health`, `toolchain` | the adapter, tool versions, whether it is installed, its port | open |
| `templates` | this framework's starters | open |
| `projects` | every project written in this framework, each with its `zkworld.<name>` address and app link | open |
| `abi`, `witness` | the shape of one of its projects, or a run of it on inputs | open |
| `new`, `build`, `test`, `vibe`, `clean` | the project verbs, **held to this framework**: `zkworld.noir/build name=factors` is refused because `factors` is circom | owner |

Its page is `/zkworld/<id>/`. It shows the toolchain, the starters, a new-project form, and the project list with build and test buttons. `m zkworld.<id>/<fn>` works the same from the CLI.

**A project submod** is unchanged from section 2: a directory with a framework marker (`Scarb.toml`, `main.circom`, `Nargo.toml`, ...), addressed `zkworld.<name>`. Which framework owns it is not stored anywhere new. It is the adapter that `detect()` picks for the directory, so moving a project between frameworks (via `port_to`) needs no registry edit. Project addresses stay flat (`zkworld.factors_noir`, not `zkworld.noir.factors_noir`), so every existing link, CLI call and MCP argument keeps working.

**Why frameworks run as their own processes.** Each toolchain has its own failure modes: a hung `cargo` build, a Go module download, a `leo` binary that panics. A process per framework keeps them apart. A wedged halo2 build cannot block a noir witness, and one framework can be restarted without touching the others. Each one also gets its own address, page and API, which makes it a real mod rather than a branch inside one big function.

**Supervision and access.** The parent server starts any framework submod that is not listening, on boot and every 20 seconds after (`submods(action=up)`). `GET /zkworld/api/submods` lists ports, liveness and the projects each one owns. The framework servers bind loopback only and are reached publicly through the parent, which proxies `/zkworld/<id>/*` to them. **The owner gate stays in the parent.** A write is forwarded only after `_whoami` clears it. Everything else goes out stamped with `X-Forwarded-For`, so the framework server treats it as non-local and refuses to write. That holds even for a crafted path the parent did not recognise as a write. There is one rule and it is enforced in one place, with the framework server as the second line.

## 3. The toolchain — real, not simulated

The workbench is orchestration (Python standard library only) over binaries already on the host:

| Tool | Version | Role |
|---|---|---|
| `scarb` | 2.8.4 | build Cairo 2 → Sierra + CASM |
| `snforge` | 0.31.0 | unit + integration tests, gas per test |
| `sncast` | 0.31.0 | declare / deploy / invoke on the sandbox |
| `starknet-devnet` | 0.2.4 | the practice chain (RPC 0.7, port 51351) |
| `universal-sierra-compiler` | 2.10 | Sierra → CASM for snforge |

Versions are pinned deliberately: devnet 0.2.x speaks RPC 0.7, which is what sncast 0.31 speaks; devnet 0.3+ speaks RPC 0.8 and breaks the pair. Build output is parsed, not just echoed: `build` returns `diagnostics` with `path:line` (the console turns them into red lines you can click), `test` returns per-test pass/fail, gas and the panic reason, and the noise the snforge scarb plugin prints on every compile is stripped.

## 3b. Any framework, one statement

Cairo is one framework among many. `src/frameworks.py` holds one adapter per toolchain: cairo, circom, noir, zokrates, leo, gnark and halo2 are driven directly, each with its real compiler and native test runner. The catalog lists 29 frameworks in three kinds: circuit languages, zkVMs (RISC Zero, SP1, Jolt, OpenVM, ZisK, Nexus, Miden, ...) and libraries (arkworks, bellman, Plonky2/3, Stwo, Binius, ...). The ones with no adapter join through the **runner protocol**. The project ships a runner that answers `--abi` (its inputs and outputs) and `input.json` (exit 0 + outputs when the statement holds, non-zero + reason when it does not). Its `config.json` names the build/test/run commands. zkworld never has to learn the toolchain.

What connects them is one test format. `tests/*.json` cases (`input`, `expect`, `fail`) run unchanged through every adapter. Where one framework computes a public value as an output (circom's `c <== a*b`) and another takes it as a public input (noir's `c: pub Field`), `execute()` feeds or checks the value as needed. So a case is a **claim** ("a=3, b=5 gives c=16"), and every framework must agree on whether it holds. Projects that prove the same thing share a `statement`. `compare` runs one input through all of them side by side, and `port_to` has the agent re-implement a project in another framework, held to the source's cases. The shipped example is `factors` in six frameworks (circom, noir, zokrates, leo, gnark, halo2). All six agree on every claim in its case file.

## 4. Vibecoding on the agent protocol

`vibe(name, prompt)` does not call a model directly. It posts the task to the fleet's agent harness (`orbit/claudecode`, `/run/stream`), with the contract directory as the working path and a fixed set of Cairo rules as the goal prefix: stay inside this directory, keep the package and module names, write tests, run `scarb build` and `snforge test` yourself. Every step the agent takes streams back as a job step, so the console shows a live trail; when the agent stops, the workbench re-runs build + test itself and reports *that* result. The agent's claim that it passed counts for nothing; the workbench's own run is the result. If the harness is down, `vibe` falls back to the Claude CLI directly, scoped with `--add-dir`.

Vibecoding spends the host's model credits, so it is owner-only (section 7).

## 5. Every contract gets an app

`/zkworld/<name>/` serves the contract's own `app/index.html` if it has one, otherwise a shared default page (`src/templates/cairo/app.html` — one plain HTML file, no build step) that drives entirely off the ABI:

- **Read** cards for every view function, refreshed automatically.
- **Write** cards for every external function, sent as a transaction.
- A **constructor** form to deploy it.
- **Act as** alice / bob / carol / dave — the devnet's public seed-0 accounts — so one person can play every role.
- **Activity**: the contract's events, decoded.

Underneath is `src/sandbox.py`: it boots the devnet on demand (state dumped every block, so it survives restarts), signs with sncast against a generated accounts file, and reads views, receipts and events over JSON-RPC. An ABI codec turns typed values into felts and back — a `u256` is one number, a `ByteArray` is text, `'short str'` is a felt, spans are comma lists, structs and enums are JSON, and account names stand in for addresses. A revert comes back as its panic reason, not a hex blob.

### Simple and developer views

Raw ABIs are unreadable for most people. A contract can ship an `app.json` that relabels it in plain words: a title, an intro, numbered steps, a label/help/button per function, inputs hidden and filled with "me" or a fixed value, outputs shown as a big number, a meter, a chart, a yes/no or a list, and events rewritten as sentences (`"{who} claimed {amount}"`). The page has a simple | developer switch; simple is the default. Hiding is presentation only — it is not access control, and the developer view always shows everything. The reference is `docs/simple-apps.md`.

## 6. Starters, the market and the school

**Starters** (`src/templates/cairo/<id>/`) are working contracts with tests and an `app.json`: counter, token (hand-written ERC-20), commit_reveal (Poseidon commitment bound to the caller), merkle_allowlist (sorted-pair Poseidon), stablecoin (cash-backed, mint ≤ reserves) and vault_stable (150% collateral ratio, oracle price, liquidation). Each passes its own suite out of the box.

**Market** lists every contract on the box as a card — build state, reads/writes/events, size, test count, whether it is live on the sandbox — with Open, App and Fork.

**Learn** is a seven-lesson track that teaches stablecoins and Cairo from zero, each lesson ending in the starter that implements it. A helper orb in the corner answers questions; its free brain is a keyword router that reads through the MCP tools, and its owner-only brain is a full agent on the harness.

Notable contracts built here:

- `zkworld.subnet` — **private Yuma consensus**: validators commit sealed weight vectors (`poseidon('yuma', contract, epoch, who, salt, w…)`), reveal later, and anyone finalizes; the contract computes a stake-weighted median, clipping, incentives, EMA bonds and dividends in fixed point (ONE = 1e9).
- `zkworld.zuma` — **ZK Yuma**: the same round, but miners and validators prove membership with LSAG ring signatures, so the contract knows each vote came from the set without knowing from whom. Per-round nullifiers stop double votes and double claims; a ring must be a single stake tier because the vote is weighted by the tier, not the signer.

## 7. Trust model — who can do what

The console is publicly routed. The rule is simple:

| Action | Who |
|---|---|
| Read anything: contracts, files, ABIs, events, market, fleet, lessons, this paper | anyone |
| Call a contract on the sandbox, deploy a built contract that is not live yet | anyone |
| write / new / fork / vibe / build / test / clean / remove / adopt / status | owner |
| A framework submod's new / build / test / vibe / clean; `submods` up/restart/sync | owner (gated by the parent) |
| Start, stop or reset the sandbox | owner |
| Helper agent with the `claude` brain | owner |

"Owner" means a request from loopback with no proxy headers, or a mod-protocol token (one `personal_sign`) from an address listed as owner in config, in the build console's private flag, or in `ZKWORLD_OWNERS`. The same gate guards REST, MCP and the helper. No private key for a real network exists anywhere in this module; the sandbox's accounts are public test keys, and nothing is sent to a live chain. `sncast` is on the box for anyone who wants to deploy by hand.

## 8. The fleet scan

The workbench is not the only place on this box that speaks Cairo. `fleet()` walks every mod under `orbit/` and `core/` — a mod is any directory with a `config.json` — and classifies it by the strongest evidence it has:

| Level | Evidence |
|---|---|
| **contract** | a `Scarb.toml` that builds a Starknet contract (`starknet` dependency or `starknet-contract` target) |
| **zkworld** | a `Scarb.toml` for a plain Cairo program or circuit |
| **mentions** | no Scarb project, but its `config.json` names zkworld / starknet / scarb / snforge / sncast / katana |

Each `Scarb.toml` is credited to the nearest enclosing mod, so a contract under `zkworld/src/mods/x` counts for `zkworld.x`, not for `zkworld`; a parent that only hosts nested projects reports them as `nested_projects`. "devnet" alone is not evidence — every chain module has one. For each project the scan reports the package, the declared Cairo version, whether it is a contract, and whether it has snforge or `cairo_test` tests. Results are cached for a minute (`?refresh=1` skips the cache); a full scan of ~540 mods takes about half a second.

Snapshot on 2026-10-14: 21 mods with a Cairo signal out of 540 — 9 with Starknet contracts (`starktime`, `zquorum`, `zuma`, and the six `zkworld.*` contracts), 1 plain Cairo package (`postquant`'s Cairo-MSS primitive), and 11 that only mention it (`starknet` and its app submods, and this module itself, which hosts its contracts in nested mods).

The point of the scan is reuse. Before writing a ring signature or a commit-reveal, look at who already has one: `zuma`'s `lsag.cairo` became `zkworld.zuma` by copying one file.

## 9. Interfaces

Everything is one set of functions exposed four ways:

- **REST** — `GET|POST /zkworld/api/<fn>` (and `/zkworld/_api/<fn>` for the console itself). Long work (`build`, `test`, `vibe`, `status`) takes `async=true` and returns a job to poll at `/zkworld/api/job?id=`; jobs persist across restarts and come back as `interrupted` rather than vanishing.
- **MCP** — `POST /zkworld/mcp`, streamable HTTP, `zkworld_*` tools as thin wrappers over the same fns, behind the same gate. `claude mcp add --transport http zkworld http://localhost:51350/zkworld/mcp`.
- **Agent** — `/zkworld/run/stream` on the fleet's agent contract (`/zkworld/agents`, `/.well-known/agent.json`).
- **CLI** — `m zkworld/<fn>`, per framework `m zkworld.<framework>/<fn>`, and per project `m zkworld.<name>/<fn>`.
- **Framework submods** — `GET|POST /zkworld/<framework>/api/<fn>`, proxied to that framework's own server; `GET /zkworld/api/submods` lists them (MCP: `zkworld_submods`).

## 10. Limits, said plainly

- The sandbox is a local devnet. Nothing here touches Starknet mainnet or Sepolia, and a contract that works here has been tested, not audited.
- `snforge test` recompiles the test crate every run (~30 s). That is the tool, not a bug.
- The simple view hides inputs; it does not protect them.
- A framework submod's async jobs live in that process's memory. If it restarts mid-build, the job is lost; the parent's own job table (`/zkworld/api/job`) persists, so use that for long work. Eight framework servers cost about eight idle Python processes.
- Vibecoding is only as good as the agent and the tests. The workbench guarantees the result was built and tested after the agent stopped; it does not guarantee the tests ask the right questions.
- The fleet scan reads config files and directory layouts. A mod that compiles Cairo some other way, with no `Scarb.toml` and no mention in its config, is invisible to it.

## 11. Roadmap

- Fleet import: pull a contract project found by the scan into the workbench as a `zkworld.<name>` mod (today: fork by hand).
- Testnet deploy through the user's own Starknet wallet, never a server key.
- Proof artifacts next to tests: for programs run under `cairo-run`, keep the trace and proof as content-addressed objects, so "it passed" can be checked by someone who did not run it.
