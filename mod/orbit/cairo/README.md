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

## Layout

```
orbit/cairo/
├── config.json          module manifest (anchor = src/mod.py)
├── README.md
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
