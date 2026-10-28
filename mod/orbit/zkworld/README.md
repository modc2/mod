# zkworld — a multi-framework ZK workbench

Make, **vibecode**, test and run zero-knowledge projects from one module, in
**any** ZK framework. Seven are driven directly: **Cairo** (Starknet
contracts), **Circom**, **Noir**, **ZoKrates**, **Leo** (Aleo), **gnark** (Go)
and **halo2** (Rust). Every other framework (RISC Zero, SP1, Jolt, OpenVM,
arkworks, Plonky3, o1js and the rest of the catalog) plugs in through the
**runner protocol**. Every project is its own **mod** under
`orbit/zkworld/src/mods/<name>/` (addressed `zkworld.<name>`), with
`framework` in its `config.json`.

zkworld is a fork of [`cairo`](../cairo) widened to other frameworks. The
anchor (`src/mod.py`) is thin orchestration (Python stdlib, no dependencies);
everything toolchain-specific is in one adapter per framework in
`src/frameworks.py`.

## Frameworks

| framework | marker | toolchain on this box | build | native tests | run on inputs |
|-----------|--------|-----------|-------|------|--------|
| `cairo` | `Scarb.toml` | scarb 2.8.4, snforge/sncast 0.31 | `scarb build` | `snforge test` | live app on a local starknet-devnet (:51351): deploy/call/events |
| `circom` | `main.circom` | circom 2.2 (`~/.cargo/bin`) + node | `circom --r1cs --wasm --sym` | (JSON cases) | wasm witness, `.wtns` parsed |
| `noir` | `Nargo.toml` | nargo 1.0.0-rc.4 (`~/.nargo/bin`) | `nargo compile` | `nargo test` | `nargo execute` (throwaway prover toml) |
| `zokrates` | `main.zok` | zokrates 0.8.8 (`~/.zokrates/bin`) | `zokrates compile` | (JSON cases) | `compute-witness --abi --stdin` |
| `leo` | `program.json` (`.aleo`) | leo 4.4.4 (`~/.local/bin`) | `leo build --offline` | `leo test` | `leo run --offline main` |
| `gnark` | `go.mod` (gnark) | go 1.27 (`/usr/local/go/bin`) | `go mod tidy && go build -o target/runner` | `go test` | the project's runner (`zkworld.go`, `test.IsSolved`) |
| `halo2` | `Cargo.toml` (halo2_proofs) | cargo | `cargo build --release` | `cargo test` | the project's binary (MockProver) |
| `custom` | `config.json` `zk` block | whatever it names | `zk.build` | `zk.test` | `zk.run` (runner protocol) |

The **catalog** (`m zkworld/frameworks` → `catalog`, the console's
*Frameworks* view) lists 29 frameworks in three kinds: circuit languages
(cairo, circom, noir, zokrates, leo, o1js, compact, noname, lurk), zkVMs
(risc0, sp1, jolt, openvm, zisk, nexus, miden, zkwasm, cairo-vm) and libraries
(gnark, halo2, arkworks, bellman, plonky2, plonky3, stwo, binius, expander,
snarkjs, libsnark). Each one without an adapter carries a `preset` (the
`zk` block a project of it needs), so `new(framework='risc0')` makes a
runner-protocol project and the agent writes the code behind it.

### One statement, every framework

Every project shares one test format, so the same claim is checked in every
framework:

```json
[
  {"name": "three_times_eleven", "input": {"a": "3", "b": "11"}, "expect": {"c": "33"}},
  {"name": "rejects_trivial_factor", "input": {"a": "1", "b": "33"}, "expect": {"c": "33"}, "fail": true},
  {"name": "rejects_wrong_product", "input": {"a": "3", "b": "5"}, "expect": {"c": "16"}, "fail": true}
]
```

Frameworks differ on whether a public value is an *output* (circom computes
`c <== a*b`) or a public *input* (noir/zokrates/leo/gnark/halo2 take `c` in
and assert). `execute()` bridges that: an `expect` name the program takes as
input is fed in, an input name the program computes is checked. A `fail`
case passes when the claim does not hold (rejected, or computed something
else). Each adapter runs these cases through its `witness()` on top of its
native tests.

Projects proving the same thing share `statement` in `config.json`.
`zkworld.factors` (circom) has five ports: `factors_noir`, `factors_zok`,
`factors_leo`, `factors_gnark`, `factors_halo2`.

```sh
m zkworld/statements                                   # factors -> 6 frameworks
m zkworld/compare statement=factors input='{"a":"3","b":"5","c":"16"}'
#   every port: rejected (circom: "computes c=15, not 16") -> agree: true
m zkworld/port_to src=agecheck framework=noir          # agent re-implements it, held to agecheck's cases
m zkworld/new name=fib framework=sp1                   # a runner-protocol project for a zkVM
```

### The runner protocol

Libraries and zkVMs have no CLI that runs the program on some inputs, so
the project ships a small runner:

```
<runner> --abi          -> {"circuit", "inputs": [{"name", "public"}], "outputs": [{"name"}]}
<runner> input.json     -> exit 0 + last stdout line {"outputs": {...}}   (holds)
                           exit != 0, reason on the last stderr line         (rejected)
```

gnark and halo2 starters include one. For anything else, `config.json`:

```json
"zk": {"build": ["cargo build --release"], "test": "cargo test --release",
       "run": "target/release/host", "env": {"RISC0_DEV_MODE": "1"}}
```

**Adding a framework natively** = subclass `Framework` (or `Runner`) in
`src/frameworks.py` (marker + needle, tools, build/test/abi/witness, agent
prompt, rename), register it in `FRAMEWORKS`, add it to `CATALOG`, and drop
starters in `src/templates/<id>/<starter>/` with a `meta.json` (`statement`
ties a starter to its ports). The catalog, market, fleet scan, fork, vibe,
port, compare, MCP and console pick it up with no other change.

The rest of this README is the Cairo path, inherited from `cairo`.

## Submods: every framework runs, every project belongs to one

Two kinds of submod live under `src/mods/`:

- **Framework submods** — `zkworld.cairo` :51353, `zkworld.circom` :51354, `zkworld.noir` :51355, `zkworld.zokrates` :51356, `zkworld.leo` :51357, `zkworld.gnark` :51358, `zkworld.halo2` :51359, `zkworld.custom` :51360. Each one runs as its own server (`src/api/fwserver.py`, loopback). The parent starts it on boot, restarts it within 20s if it dies, and proxies it at `/zkworld/<id>/` (page) and `/zkworld/<id>/api/<fn>`. Fns: `info health toolchain templates projects abi witness` (open) and `new build test vibe clean` (owner, gated by the parent; only this framework's projects).
- **Project submods** — `zkworld.<name>` as before, owned by whichever framework `detect()` picks for the directory.

```sh
m zkworld/submods                 # ports, running, projects per framework
m zkworld/submods action=restart  # owner
m zkworld.noir/projects
curl localhost:51350/zkworld/circom/api/witness?name=factors\&input='{"a":3,"b":5}'
```

`submods action=sync` re-renders the framework dirs from `src/templates/fwmod.py.tmpl`.

## The three verbs

```sh
# MAKE — scaffold a new contract submod from templates/
m zkworld/new name=counter desc="a felt252 get/set example"

# VIBECODE — drive the headless Claude CLI, scoped to that one submod,
#            then auto-verify with build + test
m zkworld/vibe name=counter prompt="make it an owner-only u256 counter with increment/reset and an Incremented event"

# TEST — snforge the submod (build is `m zkworld/build name=...`)
m zkworld/test name=counter
```

## Starter templates

`new` scaffolds Cairo from the starters in `src/templates/cairo/<id>/`, each with
a test suite that passes on arrival (`m zkworld/templates` lists them):

| template | what you get | tests |
|----------|--------------|-------|
| `counter` (default) | felt252 get/set + event: the smallest thing that deploys | 2 |
| `token` | a dependency-free ERC20: balances, allowances, `transfer_from`, events | 5 |
| `commit_reveal` | hide a value behind a Poseidon commitment bound to the caller, reveal later: sealed bids, private votes, fair games | 5 |
| `merkle_allowlist` | one Poseidon Merkle root on-chain, claims proven by sibling path; leaked proofs are useless to anyone else | 5 |

```sh
m zkworld/new name=airdrop template=merkle_allowlist desc="claim with a Merkle proof"
```

`airdrop` ships as the worked example of `merkle_allowlist`.

## What build / test return

The raw logs are kept (foundry's own rustc lint noise is stripped), but the
useful parts come back parsed:

- `build` → `diagnostics` (compiler errors/warnings pinned to `path:line:col`
  inside the contract) and `artifacts` (the ABI: entrypoints, constructor,
  events, Sierra/CASM size). `abi name=` reads the same from `target/dev`.
- `test` → `tests` (per-test `pass`/`fail` + L2 gas + panic reason) and
  `totals`. snforge never compiles the deployable class, so `test` runs
  `build` first whenever the contract is not fresh.
- `list` → each contract's `state`: `fresh`, `stale` (a source is newer than
  the artifacts) or `unbuilt`.

## Every contract gets an app

Open `/zkworld/<name>/` (or **Open app** in the console) and the contract is a
small working app: reads (views, auto-refreshing), writes (transactions), a
deploy form built from the constructor, and a live event log. Pick who you
act as — `alice`, `bob`, `carol`, `dave` — and type `alice` anywhere a
`ContractAddress` is wanted.

- **The page** is one plain HTML file with no build step. By default every
  contract gets `src/templates/cairo/app.html`, which renders itself from the ABI.
  Want a custom UI for one contract? Put it at
  `src/mods/<name>/app/index.html` (copying the default is the easy start);
  only that contract changes. `vibe` knows this.
- **The chain** is a local `starknet-devnet` 0.2.4 on :51351
  (`src/sandbox.py`), started when the page opens, state dumped every block to
  `src/.sandbox/` so it survives restarts. Seed-0 test accounts; nothing
  touches a real network.
- **Typed values, not felts**: the server encodes inputs and decodes outputs
  and events from the ABI — `u256` as one number, `bool`, `ByteArray` as
  text, `felt252` as a number or `'short text'`, `Span<T>` as `1, 2, 3`,
  structs/enums as JSON. Reverts come back as the panic reason (`invalid proof`).

```sh
m zkworld.counter/deploy
m zkworld.counter/call fn=set args='{"value": 42}' account=bob
m zkworld.counter/call fn=get          # -> ['42']
m zkworld.counter/events
m zkworld/sandbox action=reset          # throw the chain away (owner)
```

Access on the public route: `call` is open (it's a sandbox); `deploy` is open
only for a built contract with no live deployment (no compiling on the box,
no wiping someone's running instance); `sandbox` start/stop/reset is
owner-only.

## Simple view: hide inputs and outputs

Each contract page has a **simple / developer** switch (top right). Simple is
the default and is for people who don't read Cairo: plain titles, people
(alice/bob) instead of addresses, Yes/No badges, big numbers, charts, events as
sentences, and no types, hashes or felts. Developer is the raw page.

An optional `src/mods/<name>/app.json` shapes Simple. It sets labels and help
text, hides functions or inputs (a hidden input is sent as *you*, or as a fixed
`value`), picks visuals (`big`, `chart`, `meter`, `yesno`, `address`, `list`,
…), sets token `decimals` and the event sentences. Reload the page to see a
change; nothing is rebuilt. Starters ship one.

```json
{ "title": "Shared number",
  "fns": { "get": { "label": "The number right now", "show": "chart" },
           "set": { "label": "Change the number", "button": "Save" },
           "root": { "hide": true } },
  "events": { "ValueSet": { "say": "The number was set to {value}" } } }
```

Full reference with screenshots: [docs/simple-apps.md](docs/simple-apps.md).
New here? Start with [docs/getting-started.md](docs/getting-started.md).

## Layout

```
orbit/zkworld/
├── config.json          module manifest (anchor = src/mod.py)
├── README.md
├── docs/                getting-started.md (plain words) · simple-apps.md (app.json reference)
├── src/                 the module's implementation (not a submod)
│   ├── mod.py           the anchor: new / vibe / build / test / ...
│   ├── api/server.py    stdlib HTTP server: protocol API + jobs + console
│   ├── app/             Next.js 14 console (static export, basePath /zkworld)
│   ├── build.sh         build the console -> src/releases/<ts>, flip src/dist
│   ├── templates/       the scaffold: <template>/*.cairo.tmpl, Scarb.toml.tmpl, mod.py.tmpl
│   └── mods/            one contract MOD per dir
│       └── counter/     ← an example contract, addressed `zkworld.counter`
│           ├── mod.py       anchor: the parent's fns bound to this contract
│           ├── config.json  mod manifest ("submod_of": "zkworld", "anchor": "mod.py")
│           ├── README.md
│           ├── app.json     optional: Simple-view labels, hidden inputs/outputs, visuals
│           ├── Scarb.toml
│           ├── src/lib.cairo
│           └── tests/test_counter.cairo
```

Every contract is addressable on its own through the protocol:

```sh
m zkworld.counter/build
m zkworld.counter/test
m zkworld.counter/vibe prompt="add an owner-only reset"
```

`path2name` drops the `src` and `mods` segments, which is why
`orbit/zkworld/src/mods/counter` is `zkworld.counter`. The contract's `mod.py`
loads the parent anchor by path and binds every fn to its own name, so the
toolchain logic lives in one place. `m zkworld/adopt` (re)writes those files for
every contract and pulls any legacy root-level contract into `src/mods/`.

A directory under `src/mods/` is a contract iff it holds a `Scarb.toml`. The package name
is the directory name (snake_case); the contract module is its PascalCase form
(`counter` → `mod Counter`, interface `ICounter`, `ICounterDispatcher`).

## Functions

| fn | what it does |
|----|--------------|
| `info` / `forward` | module info + toolchain + contract list |
| `toolchain` | which Cairo tools are present and their versions |
| `list` / `contracts` | every contract submod + build state |
| `get name=` | one submod: Scarb.toml, file tree, build state |
| `tree name=` | file tree of a submod |
| `read name= path=` | read a file inside a submod |
| `write name= path= content=` | write a file inside a submod |
| `templates` | starter contracts `new` can scaffold from |
| `new name= [desc=] [template=counter] [force=]` | scaffold a contract submod |
| `vibe name= prompt= [model=] [verify=] [timeout=]` | agent-edit then build + test |
| `build name=` | `scarb build` → log + diagnostics + ABI |
| `abi name=` | the built class: entrypoints, constructor, events, Sierra/CASM size |
| `test [name=]` | `snforge test` a submod → per-test verdicts + gas; no name = module self-test |
| `clean name=` | drop a submod's `target/` + snforge cache |
| `remove name=` | delete a submod |
| `serve [port=] [background=]` | tiny stdlib JSON API on :51350 |
| `kill [port=]` | stop the API |
| `status [deep=]` | toolchain + catalog; `deep=true` builds & tests each |

## How `vibe` works

`vibe` shells out to the host `claude` CLI in headless mode:

```
claude -p "<your prompt>" \
  --append-system-prompt "<Cairo 2.8.4 + snforge house rules>" \
  --permission-mode acceptEdits \
  --add-dir <submod> \
  --output-format json
```

run with `cwd` set to the submod, and `~/.local/bin` prepended to `PATH` so the
agent's own `scarb build` / `snforge test` calls resolve. The system prompt
pins the toolchain versions and the "always build + test before finishing"
loop. When the CLI returns, `vibe` re-runs `build` and `test` itself (unless
`verify=false`) so the result object carries proof the change compiles and
passes. It uses the host Claude OAuth credentials and **spends against them**.

## App + API (mod protocol)

One stdlib Python process on :51350 serves both halves:

| URL | what |
|-----|------|
| `/zkworld` | the console: contract list, file editor, build/test, vibecode |
| `/zkworld/api/<fn>` | the protocol API (canonical; bare `/<fn>` also works since the gateway strips the prefix) |
| `/api/zkworld/<fn>` | legacy alias of the same API |
| `/zkworld/_api/<fn>` | the app's own self-calls, mapped onto the same handlers |

GET takes query params, POST a JSON body of kwargs. `build`, `test`, `vibe`
and `status` also take `"async": true` and return a job, polled with
`GET /zkworld/api/job?id=` (`/jobs` lists recent ones). The console uses that, so
a multi-minute vibe never hangs on an HTTP timeout. Two runs on the same contract
queue behind a per-contract lock instead of overlapping.

```sh
bash src/build.sh                       # build the console (once, or after app edits)
m zkworld/serve                           # background on :51350 (log: server.log)
curl localhost:51350/zkworld/api/list
curl -XPOST localhost:51350/zkworld/api/new  -d '{"name":"token"}'
curl -XPOST localhost:51350/zkworld/api/test -d '{"name":"token","async":true}'
curl 'localhost:51350/zkworld/api/job?id=<id>'
```

The server binds `127.0.0.1` (set `ZKWORLD_HOST` to widen) and has no auth, and
`route` stays `false`: `write` and `vibe` edit files and spend the host Claude
account, so it must not be reachable from the internet as it stands.

## Safety / scope

- `vibe` is scoped to a single submod directory via `--add-dir`; edits and tool
  runs stay inside that contract's project.
- Nothing here signs or submits to a live chain. `sncast` is present for you to
  `declare`/`deploy` by hand once a contract is ready.
- First `build` of any contract fetches `snforge_std` from git and compiles its
  scarb plugin (~30–60 s once); subsequent builds are seconds.

## Notes

- After creating or moving a module the core tree cache can go stale
  (`Dirpath ... does not exist` from `m zkworld/*`); fix with `rm -rf ~/.mod/tree`.
- Related: [`starktime`](../starktime) (Rust API + a Cairo 2 contract),
  [`starknet`](../starknet) (explorer + RPC/ABI API).

## Learn: stablecoins from zero

The **Learn** tab is seven short plain-English lessons (`src/tutorials.py`):
what a stablecoin is, smart contracts as vending machines, building a
cash-backed dollar (like USDC) and a crypto-backed one (like DAI), tests,
changing it with the agent, and what changes before real money. The two
"build" lessons make a working contract in one click from new starters:

| starter | like | the rule it enforces |
|---|---|---|
| `stablecoin` | USDC | the issuer can never mint more coins than the dollars it reports in reserve |
| `vault_stable` | DAI | borrow at most 2/3 of your collateral's value; under 150% anyone may liquidate |

Examples: `zkworld.usd` (5 tests) and `zkworld.vaultusd` (6 tests, including a
price crash followed by a liquidation).

## The helper (bottom-right) and the MCP server

The round **?** button opens a helper that speaks the fleet agent contract
(`GET /zkworld/agents`, `POST /zkworld/run[/stream]`). Every fact it looks up is a
call to a zkworld **MCP** tool, and the trail under each answer shows which.
It has two brains:

- **Quick**: free, instant, open to everyone. It routes your question to
  lessons, glossary words and the contract you have open.
- **Smart**: owner only. Claude runs through orbit/claudecode and calls the
  MCP server itself with curl.

The MCP server is at `POST /zkworld/mcp` (JSON-RPC 2.0). To use it from Claude Code:

    claude mcp add --transport http zkworld http://localhost:51350/zkworld/mcp

Reads and practice-chain calls are open. `zkworld_new`, `zkworld_write`,
`zkworld_build` and `zkworld_test` follow the same owner rule as the REST API.
