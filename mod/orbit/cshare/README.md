# cshare — fractional ownership of compute

A machine is an asset. List it, mint it into shares, sell slices of it on an
order book, and let anyone rent it by the hour — every rental fee splits
pro-rata across whoever holds the shares at booking time.

**compute/1.0.** Four moving parts, nothing else:

| | |
|---|---|
| **node** | a compute machine (GPU or CPU) listed as an asset, minted into `total_shares` — all to the issuer at listing |
| **share** | fractional ownership of one node; earns rental income pro-rata, forever |
| **order** | an ask on the book. Listed shares stay in your holding and keep earning until filled; they're just locked against double-selling. Partial fills allowed |
| **rental** | an exclusive hourly booking. The fee is charged up front and distributed to the cap table immediately — no accrual, no claim step |

Credits are a self-contained ledger (`deposit` is the test faucet). State is one
JSON document under `~/.mod/cshare/` — off-chain, off-repo.

## The economics

A node renting at `$R`/hour with `S` shares defaults to a share price of
`R × 24 × 90 / S` — priced to pay back in ~90 days of full utilization. Buyers
who think the machine will be busier than that bid the floor up; the market cap
(`floor × total_shares`) is the market's live valuation of the machine.

Rent flows the other way: `rent(node, hours)` charges `R × hours` and pays every
holder `cost × shares / total_shares` in the same transaction. Own 15% of a
pod, collect 15% of every hour it sells.

## Endpoints (API `:50290`, gateway `/cshare/api`)

```
GET  /card                        protocol card
GET  /.well-known/compute.json    same card, standard location
GET  /stats                       market cap, revenue, hours, open asks
GET  /nodes?q=&region=&gpu=&status=   the marketplace
POST /nodes {address, name, gpu, rate_hour, total_shares, offer_shares, …}
GET  /nodes/{id}                  cap table + order book + rentals + tape
GET  /market?node_id=             open asks, cheapest first
POST /orders {address, node_id, shares, price}      place an ask
POST /orders/{id}/cancel {address}
POST /buy {address, order_id, shares?}              fill (partial ok)
POST /rent {address, node_id, hours}                book + pay shareholders
GET  /rentals?address=&node_id=
GET  /portfolio/{address}         holdings, marks, income, asks, rentals
GET  /balance/{address}
POST /deposit {address, amount}   test faucet
POST /demo                        seed a lived-in market (never automatic)
POST /forward {action, params}    mod protocol dispatch
GET  /health
```

## CLI

```bash
m cshare/deposit address=0xme amount=1000
m cshare/list_node address=0xme name=hopper-01 gpu="H100 SXM" gpu_count=8 \
    rate_hour=24 total_shares=1000 offer_shares=600
m cshare/nodes
m cshare/node node_id=n1
m cshare/buy address=0xyou order_id=o1 shares=150
m cshare/rent address=0xyou node_id=n1 hours=4
m cshare/portfolio address=0xyou
m cshare/serve             # api :50290 + app :50291 under pm2
```

## App

Zero-dependency console at `/cshare` (`:50291`): market grid with live specs,
floor, market cap and distribution bar; a per-node sheet with the order book,
cap table, rental log and trade tape; a portfolio tab with marks and accrued
income; and a listing form that fractionalizes a machine in one shot. Wallet is
just an address in `localStorage` — `app/server.js` proxies `/cshare/api/*` so
the page works directly and through the gateway alike.

## Layout

- `mod.py` — marketplace core (ledger, listings, order book, rentals, portfolio)
- `api/api.py` — FastAPI on `:50290`
- `app/` — zero-dep console on `:50291/cshare`

## Not yet

Settlement is marketplace credits, not on-chain value, and `access` on a rental
is a placeholder host/token — cshare models the ownership and income mechanics,
it does not yet provision the machine.
