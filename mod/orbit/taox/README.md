# taox

Convert **ETH** and **SOL** into **native TAO**. Wallets: **MetaMask** for ETH, **SubWallet** for SOL and the TAO destination ss58.

## Model

Custodial swap-by-deposit (intentionally simple, not a true bridge):

1. UI quotes a rate from CoinGecko (ETH/USD, SOL/USD, TAO/USD) minus a fee.
2. User opens an **order** — gets a **deposit address** on the source chain.
3. User wallet sends ETH or SOL to that deposit address (MetaMask sends ETH directly; SOL is sent from SubWallet's Solana account, then the user pastes the signature back).
4. Operator (or a watcher) confirms the deposit and dispatches native TAO from a hot wallet to the user's ss58. The order moves to `completed`.

State machine: `awaiting_deposit → deposit_seen → confirming → delivering → completed` (or `failed` / `cancelled`).

## Layout

```
taox/
├── config.json     # ports, sources (eth/sol), destination (bittensor), fee_bps
├── mod.py          # core: rates, quote, swap, orders, mark_paid
├── api.py          # FastAPI wrapper
├── start.sh        # launch API + Next.js app
├── stop.sh         # tear down
├── Caddyfile       # /api/taox + /taox routes
├── requirements.txt
└── app/            # Next.js 14 frontend
    ├── app/{layout,page,globals.css}.tsx
    └── package.json
```

## Configuration (env)

| Var                  | Purpose                                                 |
| -------------------- | ------------------------------------------------------- |
| `TAOX_ETH_DEPOSIT`   | EVM deposit address users send ETH to                   |
| `TAOX_SOL_DEPOSIT`   | Solana deposit address users send SOL to                |
| `TAOX_TAO_RPC`       | Bittensor RPC (defaults to finney)                      |
| `TAOX_ADMIN_KEY`     | Required for `/order/{id}/mark_paid`                    |
| `TAOX_PORT`          | Override API port (default 8870)                        |
| `TAOX_APP_PORT`      | Override app port (default 3870)                        |

## Run

```bash
# API + app
bash start.sh

# Or via the mod CLI
m taox/serve
m taox/quote from_token=eth amount=0.1
m taox/health
m taox/kill
```

## API surface

| Method | Path                          | Notes                                |
| ------ | ----------------------------- | ------------------------------------ |
| GET    | `/health`                     | Service status                       |
| GET    | `/status`                     | Order counts by state                |
| GET    | `/rates?refresh=`             | USD prices and source→TAO ratios     |
| POST   | `/quote`                      | `{from_token, amount}` → quote       |
| GET    | `/deposit_address?from_token=`| Configured deposit address           |
| POST   | `/swap`                       | Open an order                        |
| GET    | `/order/{id}`                 | Read an order                        |
| GET    | `/orders?source_address=`     | List recent orders                   |
| POST   | `/order/{id}/confirm`         | User reports source tx hash          |
| POST   | `/order/{id}/mark_paid`       | **Admin** — record TAO delivery tx   |
| POST   | `/order/{id}/cancel`          | Cancel before completion             |

## Notes

* Pricing is best-effort; rates cache for 60s. If CoinGecko is unreachable a `rates_stale` flag is returned.
* Delivery of native TAO is **operator-driven**: this module records intent and tracks state but does not sign Substrate extrinsics for you. Wire `mark_paid` to your Bittensor signing pipeline (`bittensor` SDK / `subtensor.transfer`).
* SubWallet exposes accounts via `window.injectedWeb3['subwallet-js']`. Bittensor uses sr25519/ed25519; Solana uses its own type.
