# cairo — a ZK contract workbench

Make, **vibecode**, and test Cairo/Starknet contracts from one module. Every
contract is a self-contained [Scarb](https://docs.swmansion.com/scarb/) project
that lives as its own **submod** under `orbit/cairo/<name>/`, so a contract is
a first-class mod you can version, share, and deploy on its own.

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

## Layout

```
orbit/cairo/
├── config.json          module manifest (lists submods, fns, toolchain)
├── mod.py               the anchor: new / vibe / build / test / ...
├── README.md
├── templates/           the scaffold: Scarb.toml, src/lib.cairo, tests/
└── counter/             ← an example contract SUBMOD
    ├── config.json      submod manifest ("submod_of": "cairo")
    ├── Scarb.toml
    ├── src/lib.cairo
    └── tests/test_counter.cairo
```

A directory is a contract submod iff it holds a `Scarb.toml`. The package name
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
| `new name= [desc=] [force=]` | scaffold a contract submod |
| `vibe name= prompt= [model=] [verify=] [timeout=]` | agent-edit then build + test |
| `build name=` | `scarb build` |
| `test [name=]` | `snforge test` a submod; no name = module self-test |
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

## HTTP API (optional)

```sh
m cairo/serve background=true          # :51291
curl localhost:51291/health
curl localhost:51291/status
curl -XPOST localhost:51291/cairo/new  -d '{"name":"token"}'
curl -XPOST localhost:51291/cairo/test -d '{"name":"token"}'
```

`route` is `false` by default — this is a local dev tool, not a public app. Flip
it and `m caddy/apply` to expose it.

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
