# cshare

Fractional compute marketplace (compute/1.0): machines listed as share-issuing
assets, shares traded on an order book, hourly rental income streamed pro-rata
to every shareholder at booking time.

## When to use

- Browse the market: `m cshare/nodes` (filter `q=`, `gpu=`, `region=`,
  `status=available|rented`), detail with `m cshare/node node_id=n1`
- Fractionalize a machine: `m cshare/list_node address=0xme name=gpu-01
  gpu="H100 SXM" rate_hour=24 total_shares=1000 offer_shares=600`
- Trade shares: `m cshare/sell address=… node_id=n1 shares=100 price=2.5` /
  `m cshare/buy address=… order_id=o1 shares=50` (partial fills ok)
- Rent by the hour (fee splits to the cap table now):
  `m cshare/rent address=… node_id=n1 hours=4`
- Positions and income: `m cshare/portfolio address=…`, market totals:
  `m cshare/stats`
- Credits: `m cshare/deposit address=… amount=1000` (test faucet)
- Seed a lived-in market for demos: `m cshare/demo` (never runs automatically)

## Endpoints

API `:50290` (gateway `/cshare/api`), app `:50291/cshare`.
`m cshare/serve` starts both under pm2 (`cshare.api`, `cshare.app`).

## Notes

- State is a single JSON doc at `~/.mod/cshare/state.json` (override
  `CSHARE_STATE`); `m cshare/reset confirm=true` wipes it.
- Listed shares stay in the holder's balance and keep earning — they are only
  locked against double-selling.
- Credits are an internal ledger, not on-chain value; rental `access` is a
  placeholder host/token, cshare does not provision the machine.
