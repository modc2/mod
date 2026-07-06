# BlocTime

Time-weighted staking protocol on Base Sepolia. Stake native tokens for blocks, earn BLOC tokens via duration multiplier curve. Longer locks = higher multiplier.

## Capabilities

- **Staking** — stake ERC20 tokens with optional block-lock for multiplier boost
- **Multiplier Curve** — owner-configurable piecewise-linear curve (e.g. 1x → 3x over 100k blocks)
- **BLOC Token** — ERC20 reward token minted proportional to stake × time × multiplier
- **Unstaking** — withdraw after lock expires, BLOC balance snapshots on unstake
- **Deploy** — deploy new BlocTime contracts via MetaMask from the app
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
```

### CLI

```bash
m bloctime                          # overview (default forward)
m bloctime/overview address=0x...
m bloctime/status
m bloctime/deploy network=testnet
m bloctime/serve
m bloctime/kill
```

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
│   └── src/app/page.tsx    # Staking UI
├── scripts/deploy.js       # Hardhat deploy script
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
