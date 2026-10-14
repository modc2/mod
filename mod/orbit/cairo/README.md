# cairo — a ZK contract workbench

Make, **vibecode**, and test Cairo/Starknet contracts from one module. Every
contract is a self-contained [Scarb](https://docs.swmansion.com/scarb/) project
that lives as its own **mod** under `orbit/cairo/src/mods/<name>/` (addressed
`cairo.<name>`), so a contract is a first-class mod you can version, share, and deploy on its own.

The anchor (`mod.py`) is thin orchestration — Python stdlib, no dependencies —
over the real toolchain already installed on the box:

| tool | version | role |
|------|---------|------|
| `scarb` | 2.8.4 | compile Cairo 2 → Sierra + CASM |
| `snforge` (starknet-foundry) | 0.31.0 | unit / integration tests |
| `sncast` | 0.31.0 | declare / deploy (left to you, not driven here) |
| `universal-sierra-compiler` | 2.10.x | Sierra → CASM for tests |

All four are found in `~/.local/bin` first, then `$PATH`.

## The three verbs

```sh
# MAKE — scaffold a new contract submod from templates/
m cairo/new name=counter desc="a felt252 get/set example"

# VIBECODE — drive the headless Claude CLI, scoped to that one submod,
#            then auto-verify with build + test
m cairo/vibe name=counter prompt="make it an owner-only u256 counter with increment/reset and an Incremented event"

# TEST — snforge the submod (build is `m cairo/build name=...`)
m cairo/test name=counter
```

## Starter templates

`new` scaffolds from one of four starters in `src/templates/<id>/`, each with
a test suite that passes on arrival (`m cairo/templates` lists them):

| template | what you get | tests |
|----------|--------------|-------|
| `counter` (default) | felt252 get/set + event: the smallest thing that deploys | 2 |
| `token` | a dependency-free ERC20: balances, allowances, `transfer_from`, events | 5 |
| `commit_reveal` | hide a value behind a Poseidon commitment bound to the caller, reveal later: sealed bids, private votes, fair games | 5 |
| `merkle_allowlist` | one Poseidon Merkle root on-chain, claims proven by sibling path; leaked proofs are useless to anyone else | 5 |

```sh
m cairo/new name=airdrop template=merkle_allowlist desc="claim with a Merkle proof"
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

Open `/cairo/<name>/` (or **Open app** in the console) and the contract is a
small working app: reads (views, auto-refreshing), writes (transactions), a
deploy form built from the constructor, and a live event log. Pick who you
act as — `alice`, `bob`, `carol`, `dave` — and type `alice` anywhere a
`ContractAddress` is wanted.

- **The page** is one plain HTML file with no build step. By default every
  contract gets `src/templates/app.html`, which renders itself from the ABI.
  Want a custom UI for one contract? Put it at
  `src/mods/<name>/app/index.html` (copying the default is the easy start);
  only that contract changes. `vibe` knows this.
- **The chain** is a local `starknet-devnet` 0.2.4 on :51292
  (`src/sandbox.py`), started when the page opens, state dumped every block to
  `src/.sandbox/` so it survives restarts. Seed-0 test accounts; nothing
  touches a real network.
- **Typed values, not felts**: the server encodes inputs and decodes outputs
  and events from the ABI — `u256` as one number, `bool`, `ByteArray` as
  text, `felt252` as a number or `'short text'`, `Span<T>` as `1, 2, 3`,
  structs/enums as JSON. Reverts come back as the panic reason (`invalid proof`).

```sh
m cairo.counter/deploy
m cairo.counter/call fn=set args='{"value": 42}' account=bob
m cairo.counter/call fn=get          # -> ['42']
m cairo.counter/events
m cairo/sandbox action=reset          # throw the chain away (owner)
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
orbit/cairo/
├── config.json          module manifest (anchor = src/mod.py)
├── README.md
├── docs/                getting-started.md (plain words) · simple-apps.md (app.json reference)
├── src/                 the module's implementation (not a submod)
│   ├── mod.py           the anchor: new / vibe / build / test / ...
│   ├── api/server.py    stdlib HTTP server: protocol API + jobs + console
│   ├── app/             Next.js 14 console (static export, basePath /cairo)
│   ├── build.sh         build the console -> src/releases/<ts>, flip src/dist
│   ├── templates/       the scaffold: <template>/*.cairo.tmpl, Scarb.toml.tmpl, mod.py.tmpl
│   └── mods/            one contract MOD per dir
│       └── counter/     ← an example contract, addressed `cairo.counter`
│           ├── mod.py       anchor: the parent's fns bound to this contract
│           ├── config.json  mod manifest ("submod_of": "cairo", "anchor": "mod.py")
│           ├── README.md
│           ├── app.json     optional: Simple-view labels, hidden inputs/outputs, visuals
│           ├── Scarb.toml
│           ├── src/lib.cairo
│           └── tests/test_counter.cairo
```

Every contract is addressable on its own through the protocol:

```sh
m cairo.counter/build
m cairo.counter/test
m cairo.counter/vibe prompt="add an owner-only reset"
```

`path2name` drops the `src` and `mods` segments, which is why
`orbit/cairo/src/mods/counter` is `cairo.counter`. The contract's `mod.py`
loads the parent anchor by path and binds every fn to its own name, so the
toolchain logic lives in one place. `m cairo/adopt` (re)writes those files for
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
| `serve [port=] [background=]` | tiny stdlib JSON API on :51291 |
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

One stdlib Python process on :51291 serves both halves:

| URL | what |
|-----|------|
| `/cairo` | the console: contract list, file editor, build/test, vibecode |
| `/cairo/api/<fn>` | the protocol API (canonical; bare `/<fn>` also works since the gateway strips the prefix) |
| `/api/cairo/<fn>` | legacy alias of the same API |
| `/cairo/_api/<fn>` | the app's own self-calls, mapped onto the same handlers |

GET takes query params, POST a JSON body of kwargs. `build`, `test`, `vibe`
and `status` also take `"async": true` and return a job, polled with
`GET /cairo/api/job?id=` (`/jobs` lists recent ones). The console uses that, so
a multi-minute vibe never hangs on an HTTP timeout. Two runs on the same contract
queue behind a per-contract lock instead of overlapping.

```sh
bash src/build.sh                       # build the console (once, or after app edits)
m cairo/serve                           # background on :51291 (log: server.log)
curl localhost:51291/cairo/api/list
curl -XPOST localhost:51291/cairo/api/new  -d '{"name":"token"}'
curl -XPOST localhost:51291/cairo/api/test -d '{"name":"token","async":true}'
curl 'localhost:51291/cairo/api/job?id=<id>'
```

The server binds `127.0.0.1` (set `CAIRO_HOST` to widen) and has no auth, and
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
  (`Dirpath ... does not exist` from `m cairo/*`); fix with `rm -rf ~/.mod/tree`.
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

Examples: `cairo.usd` (5 tests) and `cairo.vaultusd` (6 tests, including a
price crash followed by a liquidation).

## The helper (bottom-right) and the MCP server

The round **?** button opens a helper that speaks the fleet agent contract
(`GET /cairo/agents`, `POST /cairo/run[/stream]`). Every fact it looks up is a
call to a cairo **MCP** tool, and the trail under each answer shows which.
It has two brains:

- **Quick**: free, instant, open to everyone. It routes your question to
  lessons, glossary words and the contract you have open.
- **Smart**: owner only. Claude runs through orbit/claudecode and calls the
  MCP server itself with curl.

The MCP server is at `POST /cairo/mcp` (JSON-RPC 2.0). To use it from Claude Code:

    claude mcp add --transport http cairo http://localhost:51291/cairo/mcp

Reads and practice-chain calls are open. `cairo_new`, `cairo_write`,
`cairo_build` and `cairo_test` follow the same owner rule as the REST API.
