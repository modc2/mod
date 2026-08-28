# BlocTime

Time-weighted staking protocol on Base Sepolia. Stake native tokens for blocks, earn BLOC tokens via duration multiplier curve. Longer locks = higher multiplier.

## Capabilities

- **Staking** — stake ERC20 tokens with optional block-lock for multiplier boost
- **Multiplier Curve** — owner-configurable piecewise-linear curve (e.g. 1x → 3x over 100k blocks)
- **BLOC Token** — ERC20 reward token minted proportional to stake × time × multiplier
- **Weekly Pot** — rewards collect in a pot (inflation mints into it, anyone can `fundPot`); every Friday at 12:00 EST the whole pot is swept to BLOC holders pro-rata. Permissionless trigger, one payout per week
- **Unstaking** — withdraw after lock expires, BLOC balance snapshots on unstake
- **Deploy** — deploy new BlocTime contracts via MetaMask from the app
- **Fork** — `m bloctime/fork name=x` copies the whole module into orbit/x with its own ports/route
- **Marketplace** — registry of deployed BlocTime instances (`~/.mod/bloctime/registry.json`); MARKET tab browses them, USE switches the app onto any instance (reads via its RPC, writes via wallet)
- **Self-deploy** — DEPLOY tab ships ABI+bytecode (`GET /factory`) so anyone deploys NativeToken+BlocTime from their own wallet, then auto-registers on the market
- **Bridge** — BRIDGE tab + `/bridge/*` proxy into the bridge module (Substrate/Solana snapshot → Base claims), with activator wake-on-access fallback
- **Version Check** — API + app detect new commits on the current git branch

## Usage

```python
import mod as m
bt = m.mod('bloctime')()

# overview
bt.overview()                       # your staking positions + balances
bt.overview(address='0x...')        # another address
bt.status()                         # deployment info, network, explorer link

# deploy & test
bt.deploy(network='testnet')        # deploy BlocTime contract
bt.test()                           # run chain-level tests

# serve
bt.serve()                          # start API (8851) + app (8852) in dev mode
bt.serve(api_port=9000, app_port=9001)
bt.kill()                           # stop all

# compile & deploy contracts
bt.compile()                        # compile Solidity via Hardhat
bt.deploy(network='base_sepolia')   # deploy NativeToken + BlocTime

# staking
bt.stake(amount=100, lock_blocks=10000)
bt.unstake(stake_id=0)
bt.get_multiplier(block_count=10000)
bt.get_points()

# weekly pot — Friday 12:00 EST (17:00 UTC)
bt.pot()                            # pot size, next payout, seconds remaining
bt.fund_pot(50)                     # add 50 BLOC to this week's pot
bt.distribute()                     # sweeps the pot; no-ops outside the window
bt.claim_rewards()                  # take your share

# fork & marketplace
bt.fork('mybloctime')                       # your own module copy (auto ports, route, basePath)
bt.market(stats=True)                       # browse every registered instance
bt.register_instance(name='mybloctime', rpc='https://sepolia.base.org', bloctime='0x...')
bt.unregister_instance(id='mybloctime')     # local trusted removal (API needs owner signature)

# bridge (Substrate/Solana snapshot -> Base)
bt.bridge()                                 # health
bt.bridge(fn='in_snapshot', address='5H...')
bt.bridge(fn='status')
```

### API surface added

- `GET /factory` — ABI + bytecode + default params (browser-wallet deploys)
- `GET /registry` — all instances w/ live on-chain stats (60s cache)
- `POST /registry/register` — on-chain verified (probes totalBlocTime/nativeToken, records owner())
- `POST /registry/unregister` — requires personal_sign of `bloctime:unregister:<id>` by owner()
- `GET /bridge/info`, `POST /bridge/{fn}` — whitelisted read-only proxy to the bridge module
  (path-param fns in_snapshot/has_claimed/unclaimed/commitment; falls back to activator :9000 wake)

### CLI

```bash
m bloctime                          # overview (default forward)
m bloctime/overview address=0x...
m bloctime/status
m bloctime/deploy network=testnet
m bloctime/serve
m bloctime/kill
```

### App mod

The frontend is its own mod (`app/mod.py`) representing the Next.js console —
identity, UI map, endpoints it consumes, and lifecycle:

```bash
m bloctime/app                      # info: url, port, framework, running state
m bloctime/app ui                   # declarative map of the UI (tabs, panels, actions)
m bloctime/app endpoints            # API endpoints the frontend calls
m bloctime.app/serve                # start next dev on 8852 (serves at /bloctime)
m bloctime.app/status               # running? http 200? api up?
m bloctime.app/build                # production build
m bloctime.app/logs n=100
m bloctime.app/kill
```

The app serves under basePath `/bloctime` → http://localhost:8852/bloctime.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Service health |
| GET | /stats | Contract stats (totalBlocTime, supply, stakes) |
| GET | /points | Multiplier curve points |
| GET | /params | Contract params (maxLockBlocks, distributionPct) |
| POST | /overview | Staking overview for address |
| POST | /get_position | Single stake position by address + ID |
| POST | /get_multiplier | Multiplier for N lock blocks |
| POST | /stake | Stake tokens (server-side signer) |
| POST | /unstake | Unstake by ID (server-side signer) |
| GET | /pot | Pot size, eligible supply, next/last payout, `due` |
| POST | /fund_pot | Add BLOC to the pot (server-side signer) |
| POST | /distribute_rewards | Sweep the pot — 409 outside the weekly window |

## Weekly Pot

Rewards no longer trickle out per epoch. They pool:

- **Inflation** mints into the pot for every completed epoch (halving curve unchanged).
- **Anyone** can top it up: `fundPot(amount)` moves BLOC from your balance into the pot.
- **Every Friday at 12:00 EST** (17:00 UTC — the schedule is pinned to EST, so it does
  not shift with daylight saving) `distributeRewards()` opens. It is permissionless:
  any caller sweeps the *entire* pot to BLOC holders pro-rata by balance, and the window
  closes until the next Friday. A missed week doesn't drift the schedule — the payout
  lands on the window it was called in.
- Holders claim with `claimRewards()`. Rounding dust stays in the pot for next week.

Nothing calls it for you. Point a keeper at it on any cadence — `distribute()` no-ops
politely when the window is shut:

```bash
# hourly is plenty — it no-ops until the window opens
0 * * * * cd /root/mod/mod/orbit/bloctime && m bloctime/distribute
```

Contract surface: `rewardPot()`, `distributableSupply()`, `nextDistributionTime()`,
`distributionDue()`, `getPotInfo()`, `fundPot(uint256)`, `distributeRewards()`.

## Structure

```
bloctime/
├── bloctime/mod.py         # Mod class (serve, kill, deploy, stake, unstake)
├── config.json             # contract addresses, ports, network
├── contracts/              # Solidity contracts
│   ├── BlocTime.sol        # Main staking contract
│   ├── NativeToken.sol     # ERC20 staking token
│   └── mod.py              # contracts module
├── api/api.py              # FastAPI backend (port 8851)
├── app/                    # Next.js frontend (port 8852)
│   ├── src/app/page.tsx      # Staking UI
│   ├── src/app/globals.css   # design tokens + the 11 skins
│   ├── src/app/theme.tsx     # skin context, pre-paint boot, chart colours
│   └── src/app/ThemePicker.tsx
├── scripts/deploy.js       # Hardhat deploy script
├── test/weekly.test.js     # Weekly pot schedule + payout tests (npx hardhat test)
├── hardhat.config.js       # Solidity compiler config
├── package.json            # Hardhat dependencies
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## App Features

- Wallet connect (MetaMask) with chain detection (Base Sepolia / Mainnet)
- Stake form with live multiplier preview + interactive SVG curve chart
- Position table with lock status, BLOC earned, unstake button
- Deploy tab — deploy new contract + set multiplier curve points via MetaMask
- Update banner — checks `/check_update` on load + every 5min, shows commits behind
- **Skins** — 11 of them (MIDNIGHT default, plus SLATE, VAULT, TERMINAL, AMBER,
  NEON, BLUEPRINT, PIXEL and the light PAPER / MINT / SOLAR), picked from the
  header. Colours, radii, fonts and backdrop are all CSS vars, so a skin is one
  `[data-theme="…"]` block in `globals.css` — no component ever names a hue.
  Add one by copying a block, restating every token, and adding a row to
  `THEMES` in `src/app/theme.tsx`.

## Env

- `BASE_TESTNET_RPC_URL` — RPC endpoint (default: `https://sepolia.base.org`)
- `PRIVATE_KEY` — server-side signer for stake/unstake endpoints
- `NETWORK` — `testnet` | `mainnet` | `localhost` (default: `testnet`)
- `NEXT_PUBLIC_API_URL` — app → API URL (default: `http://localhost:8851`)

## Mod Protocol

This module follows the ~/mod framework conventions:

- **Entry**: `m bloctime` or `m.mod('bloctime')()` calls `forward()` → `status()`
- **Config**: `config.json` holds contract addresses per network, ports, URLs
- **Serve**: `serve()` launches uvicorn (API) + next dev (app) as background processes
- **Kill**: `kill()` finds processes by port pattern via pgrep + SIGTERM
- **Logs**: `/tmp/bloctime/api.log` and `/tmp/bloctime/app.log`
- **Ports**: API 8851, App 8852
