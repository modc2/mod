# taox

Get into **TAO** from **Solana**, **Base** or **Ethereum**.

Two things live here. The **bridge board** is the one to reach for: it knows every
route that currently exists into TAO, prices the ones with a key-free quote API
live, and ranks them. The **desk** is this deployment's own custodial swap, kept
for comparison — it rarely wins.

## Bridge board

`GET /bridges` is the catalog; `POST /bridges/quote` prices one trade across
every quotable route at once.

```bash
curl -s localhost:3870/api/bridges | jq '.routes[].name'
curl -s -X POST localhost:3870/api/bridges/quote \
  -H 'Content-Type: application/json' \
  -d '{"asset":"base:USDC","amount":1000}' | jq '.best_native, .routes[0]'
```

Source assets are `{chain}:{symbol}` keys — eight of them:

| Chain | Assets |
| ----- | ------ |
| Solana | `sol:SOL` `sol:USDC` `sol:USDT` |
| Base | `base:ETH` `base:USDC` |
| Ethereum | `eth:ETH` `eth:USDC` `eth:USDT` |

### The routes

| Route | Kind | Delivers | Sources | Live quote |
| ----- | ---- | -------- | ------- | ---------- |
| SideShift.ai | instant swap | native TAO (ss58) | all 8 | yes |
| ChangeNOW | instant swap | native TAO (ss58) | all 8 | yes |
| Godex | instant swap | native TAO (ss58) | all 8 | yes |
| Exolix | instant swap | native TAO (ss58) | Solana + Ethereum | yes |
| Jupiter | DEX | TAO on Solana (SPL) | Solana | yes |
| taox desk | desk | native TAO (ss58) | Solana + Ethereum | indicative |
| StealthEX | instant swap | native TAO (ss58) | all | needs API key |
| SimpleSwap | instant swap | native TAO (ss58) | all | needs API key |
| Sunrise / Wormhole NTT | on-chain bridge | TAO on Solana (SPL) | Solana | no |
| TaoFi (Hyperlane) | on-chain bridge | USDC on Bittensor EVM | USDC anywhere | no |
| TaoBridge (wTAO) | on-chain bridge | wTAO ERC-20 | Ethereum | no |
| Bittensor EVM transfer | last hop | native TAO (ss58) | — | no |
| Centralized exchange | exchange | native TAO (ss58) | all | no |

### Two rules the ranking will not bend

The board exists to avoid a comparison that flatters the wrong route, so:

* **Ranking is within a delivery form, never across it.** Jupiter usually
  prints the best headline rate because it buys SPL TAO on Solana and stops
  one hop short of an ss58 balance you can actually stake. It is listed
  separately, under a heading that says so.
* **A route that will refuse your size is not a cheap route, it is no route.**
  Anything outside a provider's own min/max comes back `unavailable` carrying
  the bound that failed, with its rate and amount cleared rather than left on
  screen.

The desk's own row is flagged `indicative`: it is priced off the CoinGecko mid
minus a fixed fee rather than off a book, which structurally undercuts every
desk quoting a real spread. It is kept off `best_native` for that reason.

Reference addresses the board also surfaces:

* Canonical TAO on Solana (Wormhole NTT, via Sunrise): `taoC6xyv2v8tDLcev4uaGUgV4vdQsWJrGft2kcBRrBY`
* wTAO on Ethereum: `0x77E06c9eCCf2E797fd462A92B6D7642EF85b0A44`
* Bittensor EVM: chain ID **964** (`0x3c4`), RPC `https://lite.chain.opentensor.ai`

## Desk model

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
m taox/bridges
m taox/bridge_quote asset=base:USDC amount=1000
m taox/quote from_token=eth amount=0.1
m taox/health
m taox/kill
```

## API surface

| Method | Path                          | Notes                                |
| ------ | ----------------------------- | ------------------------------------ |
| GET    | `/bridges`                    | Full route catalog + coverage matrix |
| GET    | `/bridges/assets`             | The 8 source assets                  |
| POST   | `/bridges/quote`              | `{asset, amount}` → every route, ranked |
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

* **Port 8870 is contested.** `config.json` claims it for the API, but `orbit/dev`
  already owns 8870 on this host. Nothing breaks today — taox's API only listens
  *inside* the container, and the container's own Caddy serves both the app and
  `/api/*` on the single exposed port 3870. But it is why taox has no gateway
  route: auto-generating one would point `/api/taox` at the dev LLM gateway.
  Give taox a free API port before running `m caddy/apply`.
* Provider tickers were established by probing each API, not from their docs —
  they are asymmetric in ways that are easy to get wrong (ChangeNOW calls
  Ethereum USDC `usdc` but Ethereum USDT `usdterc20`; SideShift wants
  `{coin}-{network}`; Exolix has no Base pairs against TAO at all).
* Pricing is best-effort; rates cache for 60s. If CoinGecko is unreachable a `rates_stale` flag is returned.
* Delivery of native TAO is **operator-driven**: this module records intent and tracks state but does not sign Substrate extrinsics for you. Wire `mark_paid` to your Bittensor signing pipeline (`bittensor` SDK / `subtensor.transfer`).
* SubWallet exposes accounts via `window.injectedWeb3['subwallet-js']`. Bittensor uses sr25519/ed25519; Solana uses its own type.
