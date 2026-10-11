# zkwasm

Zero-knowledge WASM workbench — **scaffold**.

The intent: compile guest programs to WebAssembly, execute them in a sandboxed
interpreter, and produce/verify zero-knowledge proofs of that execution, so a
module can prove "this output came from this program on this input" without
re-running it. No prover backend is wired up yet; this is the standard module
skeleton waiting for the first integration.

## Layout

```
config.json   module manifest (port 51310, route /zkwasm)
mod.py        anchor — CLI fns + a stdlib HTTP stub serving /health, /info, /status
README.md     this file
state/        runtime state (created on first serve)
```

## CLI

```
m zkwasm/info      # module description + status
m zkwasm/test      # self-check (imports, config sanity)
m zkwasm/serve     # start the stub API on :51310
m zkwasm/status    # is it running?
m zkwasm/kill      # stop it
```

## API (stub)

| Endpoint  | Method | Docs                          |
|-----------|--------|-------------------------------|
| `/health` | GET    | liveness                      |
| `/info`   | GET    | module description + status   |
| `/status` | GET    | `{running, port, version}`    |

## Next steps

- Pick a prover backend (wasmi + a zkVM, or a dedicated zkWASM circuit).
- Add `prove` / `verify` endpoints and matching CLI fns.
- Console app (`app/` → `dist/`) once there is something to look at.
