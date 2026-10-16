# zkapi

Zero-knowledge API layer — **scaffold**.

The intent: one HTTP surface where a caller submits a statement, gets a proof
back, and verifies proofs, without caring which ZK backend does the work.
zkapi would dispatch to the fleet's existing ZK modules (`zkworld` for
circom/noir/gnark/…, `cairo` for Starknet, `zkwasm` for WASM execution). No
backend is wired up yet; this is the standard module skeleton.

## Layout

```
config.json   module manifest (port 51420, route /zkapi)
mod.py        anchor — CLI fns + a stdlib HTTP stub serving /health, /info, /status
README.md     this file
state/        runtime state (created on first serve)
```

## CLI

```
m zkapi/info      # module description + status
m zkapi/test      # self-check (config sanity)
m zkapi/serve     # start the stub API on :51420
m zkapi/status    # is it running?
m zkapi/kill      # stop it
```

## API (stub)

| Endpoint  | Method | Docs                        |
|-----------|--------|-----------------------------|
| `/health` | GET    | liveness                    |
| `/info`   | GET    | module description + status |
| `/status` | GET    | `{running, port, version}`  |

## Next steps

- Define the statement/proof wire format (`POST /prove`, `POST /verify`).
- First backend: proxy to `zkworld`'s framework runners.
