---
name: copytensor
description: Bittensor dTAO copy trading — mirror subnet allocations of top performers via a rotating pool of public RPC endpoints. No third-party APIs, no wallet required to browse.
type: orbit-module
---

# copytensor

Mirror top Bittensor validators' dTAO subnet allocations. All read paths
(leaderboard, subnets, account, trader, PnL) work without a wallet against
a round-robin pool of public Bittensor RPC endpoints. Only stake/unstake
operations need a wallet.

## Capabilities

- **Public reads, no wallet needed**: leaderboard, subnets, account positions, trader profile, PnL — all served from public RPC.
- **Round-robin RPC pool**: `entrypoint-finney.opentensor.ai`, `archive.chain.opentensor.ai`, `lite.chain.opentensor.ai`, `bittensor-finney.api.onfinality.io` — shuffles on init, auto-fails over on RPC errors.
- **Copy engine**: replicate a target validator's subnet allocations onto your own hotkey with safety limits (per-tx cap, daily cap, rebalance threshold).
- **Index of traders (polymarket-style)**: build a named, weighted basket of validators and "Start Index Live" — the frontend spawns one server-side copy per trader with capital split by weight. Pause / Resume / Sync / Stop act on the whole basket. Stored client-side in localStorage (`copytensor:indexes:v1`).
- **Seed validators**: ships with a curated list of well-known coldkeys so the leaderboard renders on first boot.

## Usage

### Python
```python
import mod as m
ct = m.mod("copytensor")()

ct.serve()                                    # docker by default, falls back to local
ct.subnets()                                  # list all subnets (public)
ct.leaderboard(days=7, top=50)                # top performers (public)
ct.account("5GcCYwRyEFp...")                  # coldkey allocations + PnL
ct.rpc_pool()                                 # which RPC is active + full pool
ct.set_wallet(mnemonic="...")                 # only needed to actually stake
ct.create_copy(target_ss58="...", our_hotkey="...")
```

### CLI
```bash
m copytensor/serve
m copytensor/leaderboard days=7 top=20
m copytensor/account ss58=5GcCYwRyEFp...
m copytensor/rpc_pool
m copytensor/create_copy target_ss58=... our_hotkey=...
```

## API surface

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Active RPC + full pool |
| GET | `/status` | Block height, tracked accounts, active copies |
| GET | `/subnets` | All subnets (netuid, alpha price, total stake, tempo, emission) |
| GET | `/leaderboard?days=7&top=50` | Top performers by alpha PnL |
| GET | `/account/{ss58}?days=7` | Allocations + PnL |
| GET | `/account/{ss58}/pnl?days=7` | Detailed per-subnet PnL |
| GET | `/trader/{ss58}` | Full profile |
| GET | `/trades?limit=50&copy_id=...` | Copy-engine trade history |
| POST | `/watch` | Add coldkey to watchlist |
| GET | `/watches` | List watched coldkeys |
| POST | `/copy` | Create copy config |
| GET | `/copies` | List active copies |
| POST | `/copy/{id}/{pause,resume,sync}` | Manage copy |
| POST | `/wallet/set` | Set mnemonic (only for staking) |
| GET | `/wallet/balance` | Wallet TAO balance |

## Structure

```
mod/orbit/copytensor/
├── config.json                  # fns list, endpoints, public RPC pool, seed validators
├── docker-compose.yml
├── Dockerfile
└── src/
    ├── mod.py                   # Mod orchestrator (Copytensor)
    ├── api/
    │   ├── app.py               # FastAPI app
    │   └── models.py            # Pydantic response models
    ├── chain/
    │   ├── client.py            # SubtensorClient with round-robin RPC failover
    │   └── snapshot.py          # Periodic snapshot capture
    ├── engine/
    │   ├── leaderboard.py       # Rank watched accounts by N-day PnL
    │   ├── pnl.py               # Per-subnet PnL calc
    │   ├── copier.py            # Copy engine
    │   └── safety.py            # Safety limits
    ├── db.py                    # SQLite (snapshots, trades, copies, watches)
    └── app/                     # Next.js frontend (pixel theme, CRT shell)
```

## Env vars

| Name | Purpose |
|---|---|
| `COPYTENSOR_API_URL` | Override API URL (default `http://localhost:50150`) |
| `NEXT_PUBLIC_API_URL` | Frontend → API base (default same as above) |

## Mod protocol

- **Anchor**: `src/mod.py` class `Copytensor` (aliased as `Mod`)
- **Load**: `m.mod("copytensor")()`
- **Call any fn**: `m.fn("copytensor/leaderboard")(days=7)`
- **Default entry**: `forward()` returns module info; `forward(fn="leaderboard")` dispatches
- **Logs**: `/tmp/copytensor/api.log`, `/tmp/copytensor/app.log` (local mode), `docker logs copytensor` (docker mode)
- **Ports**: api 50150, app 3150
- **Gateway**: registered in `server.namespace.app_namespace` on first `serve()`. Accessible via the mod-protocol gateway on :3001 (`/copytensor` for app, `/api/copytensor/*` for API) and the caddy edge on :3000. Use `m.copytensor.gateway()` (or `m copytensor/gateway`) to print live URLs.
- **Docker**: `docker compose up -d --build` from the module dir, or `m copytensor/serve` (auto-picks docker when available, falls back to local with the prebuilt arm64 binary). Image: `copytensor-copytensor:latest`. Rust 1.93+ required (older base images choke on `ar_archive_writer`/`constant_time_eq` edition2024 features).
