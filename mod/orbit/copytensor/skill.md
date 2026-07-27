---
name: copytensor
description: Bittensor dTAO copy trading — mirror subnet allocations of top performers. Reads (subnets, positions, trader history, PnL) are served by the bt module's local index, with a rotating pool of public RPC endpoints as fallback. No third-party APIs, no wallet required to browse.
type: orbit-module
---

# copytensor

Mirror top Bittensor validators' dTAO subnet allocations. All read paths
(leaderboard, subnets, account, trader, PnL) work without a wallet. They are
served by the **`bt` module's local index** — the same SQLite indexer behind
the bt explorer, which snapshots subnet pools and tracked coldkeys on an
interval — and fall back to a round-robin pool of public Bittensor RPC
endpoints whenever bt is stopped. Only stake/unstake operations need a
wallet, and those always go out over our own wallet + RPC pool.

## Capabilities

- **Public reads, no wallet needed**: leaderboard, subnets, account positions, trader profile, PnL.
- **bt-backed reads**: `src/chain/bt_source.py` wraps bt's `POST /api/call` in a `SubtensorClient` (`BtBackedClient`), so every existing read path — endpoints, snapshot loop, PnL, the copy engine's target lookup — is served from bt's index without changing its call sites. `/subnets` drops from a multi-second `all_subnets()` walk to ~150 ms. Each override falls back to the inherited RPC implementation if bt can't answer.
- **Trader tracking**: watching a coldkey registers it with bt's trader index (`bt_track`), which then keeps its equity curve, windowed PnL and an inferred trade tape. Read them via `/traders`, `/traders/{ss58}`, `/traders/{ss58}/history`, `/traders/{ss58}/flows`, `/flows`.
- **Round-robin RPC pool**: `entrypoint-finney.opentensor.ai`, `archive.chain.opentensor.ai`, `lite.chain.opentensor.ai`, `bittensor-finney.api.onfinality.io` — shuffles on init, auto-fails over on RPC errors.
- **Copy engine**: replicate a target validator's subnet allocations onto your own hotkey with safety limits (per-tx cap, daily cap, rebalance threshold).
- **Index of traders (polymarket-style)**: build a named, weighted basket of validators and "Start Index Live" — the frontend spawns one server-side copy per trader with capital split by weight. Pause / Resume / Sync / Stop act on the whole basket. Stored client-side in localStorage (`copytensor:indexes:v1`).
- **Seed accounts**: ships with the owner coldkeys of the top delegates by total stake (real, checksum-verified, actively allocating) so the leaderboard renders on first boot; `POST /discover?top=N` re-derives the list live from `get_delegates()` (~1 min) and auto-watches any new ones. Every ss58 entering the watchlist is checksum-validated.
- **Honest PnL**: baselines come from local snapshots (30-min loop) or bt's trader index (`bt_trader_at`), which only counts a snapshot as a baseline if it actually sits near the block asked for — otherwise today's book would masquerade as last week's and PnL would read 0. The old archive-node query is off by default (`COPYTENSOR_ARCHIVE_FALLBACK=1` re-enables it); if neither exists yet the entry reports `baseline: false` and PnL 0 (UI shows "— warming") — numbers are never invented. Leaderboard horizons (1/3/7/14/30d) are pre-warmed at startup and served stale-while-revalidate (TTL 120 s).

## Usage

### Python
```python
import mod as m
ct = m.mod("copytensor")()

ct.serve()                                    # docker by default, falls back to local
ct.subnets()                                  # list all subnets (public)
ct.leaderboard(days=7, top=50)                # top performers (public)
ct.account("5CWzmvA17MAM...")                 # coldkey allocations + PnL
ct.rpc_pool()                                 # which RPC is active + full pool
ct.source()                                   # reads served by bt or by RPC?
ct.traders()                                  # tracked traders: value, PnL, allocation
ct.trader_history("5CWzmvA17MAM...")          # equity curve from bt's index
ct.flows(hours=24)                            # inferred buys/sells across traders
ct.set_wallet(mnemonic="...")                 # only needed to actually stake
ct.create_copy(target_ss58="...", our_hotkey="...")
```

### CLI
```bash
m copytensor/serve
m copytensor/leaderboard days=7 top=20
m copytensor/account ss58=5CWzmvA17MAM...
m copytensor/rpc_pool
m copytensor/source
m copytensor/traders
m copytensor/flows hours=24
m copytensor/create_copy target_ss58=... our_hotkey=...
```

## API surface

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Active RPC + full pool |
| GET | `/status` | Block height, tracked accounts, active copies, `reads: bt\|rpc` |
| GET | `/subnets` | All subnets (netuid, alpha price, total stake, tempo, emission) |
| GET | `/leaderboard?days=7&top=50` | Top performers by alpha PnL |
| GET | `/account/{ss58}?days=7` | Allocations + PnL |
| GET | `/account/{ss58}/pnl?days=7` | Detailed per-subnet PnL |
| GET | `/trader/{ss58}` | Full profile |
| GET | `/trades?limit=50&copy_id=...` | Copy-engine trade history |
| GET | `/traders?sort_by=total_tao` | Tracked traders: value, allocation, windowed PnL (bt) |
| GET | `/traders/{ss58}?hours=168` | Indexed profile: positions, equity curve, inferred trades |
| GET | `/traders/{ss58}/history?hours=168` | Portfolio value over time |
| GET | `/traders/{ss58}/flows?hours=168` | One trader's inferred buys/sells |
| GET | `/flows?hours=168` | The tape across every tracked trader |
| POST | `/watch` | Add coldkey to watchlist (checksum-validated) |
| GET | `/watches` | List watched coldkeys |
| POST | `/discover?top=8` | Auto-watch owner coldkeys of top-N delegates (~1 min) |
| GET | `/tao_price` | TAO/USD (coingecko, 5-min server cache) |
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
    │   ├── bt_source.py         # BtSource + BtBackedClient — reads via the bt module
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
| `COPYTENSOR_BT_URL` | Where the bt module lives (default `http://localhost:50280`; `bt_url` in config.json also works) |
| `COPYTENSOR_BT` | `0` disables the bt path entirely — reads go straight to the RPC pool |
| `COPYTENSOR_ARCHIVE_FALLBACK` | `1` re-enables deep archive-node queries when bt has no history |
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
