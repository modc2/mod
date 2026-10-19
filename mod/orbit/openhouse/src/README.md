# openhouse - source map

Everything the module ships. `config.json` (name, ports, `fns` list) stays one level up, next to the Dockerfile. In `mod.py`, `module_dir` is this `src/` directory and `root_dir` is the module root.

| Path | What it is | Doc |
|---|---|---|
| `mod.py` | The `Mod` class: all protocol logic (terms, rent, equity, pools, banks, proofs, serving) and the 75 callable `fns` | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| `api/api.py` | FastAPI REST surface over `Mod` (port 50132) | [API](docs/API.md) |
| `api/mcp_server.py` | Hand-rolled MCP server at `POST /mcp` | [API](docs/API.md) |
| `app/` | Next.js site (port 50131): home, `/manifesto /split /simulator /invest /paper /landscape /code /proofs` | [APP](docs/APP.md) |
| `contracts/` | Solidity (Foundry): OpenHouse, ApprovedTokens, Trust, Factory, CivicRegistry, OHX, Proofs | [CONTRACTS](docs/CONTRACTS.md) |
| `bank/` | Bank adapters and the reconciler | [BANK](docs/BANK.md) |
| `proof/`, `near/`, `proofs.py`, `pq.py` | Proof of rent paid (Cairo + NEAR WASM + Ethereum) and post-quantum signatures | [PROOFS](docs/PROOFS.md) |
| `civic/server.py` | The server a government runs (verify + override) | [CIVIC](docs/CIVIC.md) |
| `simulate.py`, `examples.py`, `demo.py` | Years-long simulations, replayable walkthroughs, demo seed | [SIMULATION](docs/SIMULATION.md) |
| `peers.py`, `fx.py` | Competitor landscape and ETH/fiat rates (keyless, cached 15 min) | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| `tests/` | 149 pytest tests (forge tests live in `contracts/test/`) | [TESTING](docs/TESTING.md) |

## Running

```bash
python3 -c "import mod as m; m.mod('openhouse')().serve_app()"   # starts both pm2 processes, registers the gateway route
python3 -c "import mod as m; m.mod('openhouse')().kill_app()"    # stops both
```

`pm2` names: `openhouse.api` (uvicorn, `--app-dir src/api`, `--reload`) and `openhouse.app` (`next dev -p 50131`).
pm2 bakes the command in, so after moving files re-run `serve_app()` rather than `pm2 restart`.
