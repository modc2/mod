# sn76 — Ormas ن

Ormas is an Outcomes API for coding work on Bittensor subnet 76, in development. Clients post a change and the test that proves it; miners quote a firm price for the passing result and deliver a branch. Miners will be paid only when independent validators accept the delivery, and clients will be charged the accepted quote and nothing on a miss. Protocol, thin client, reference miner and reference validator: MIT.

Bittensor subnet **76** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/heroncovelabs/ormas-subnet) · [url](https://ormas.ai) · discord `ormasheroncovelabs_43871`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005347 | +0.00% | -9.62% | -18.29% | 1,015 | 481.0051 | 32.99 |

## Last 24h flow

38 trades by 15 coldkeys · 6 buys (4.02 τ) / 32 sells (26.18 τ) · net -22.16 τ

## News

- 2026-09-16 · commit · [fix(skeleton): renew through the whole lease, retry a transient compl…](https://github.com/heroncovelabs/ormas-subnet/commit/b5bd859fdcac9dbd84980a8d3779052deeb43e4a) — heroncovelabs/ormas-subnet
- 2026-09-16 · commit · [test(63982bd8): fail-on-base for completion retry, lease renewal and …](https://github.com/heroncovelabs/ormas-subnet/commit/8cca6c4d3fddf74ccb60a50350f03e0679666918) — heroncovelabs/ormas-subnet
- 2026-09-16 · commit · [feat(client): optional chosen miner_id on registration — receipts, pr…](https://github.com/heroncovelabs/ormas-subnet/commit/98161f8483e231f4be64928e8d8e3b8013dfef8b) — heroncovelabs/ormas-subnet
- 2026-09-15 · commit · [docs: FAQ from the first external miner's day one (expected first-run…](https://github.com/heroncovelabs/ormas-subnet/commit/9645bf4707ea29c4c16eb4ce58a57bf90a6a8e4f) — heroncovelabs/ormas-subnet
- 2026-09-15 · commit · [docs: day-one fixes from the first third-party miner (2026-09-15) — I…](https://github.com/heroncovelabs/ormas-subnet/commit/59141d2ea9b05687ffcd51adf46bf61c08aea745) — heroncovelabs/ormas-subnet

## Use

```bash
m subnets.sn76/info        # live identity + market (snapshot if bt is down)
m subnets.sn76/news        # scraped news
m subnets.sn76/trades      # 24h alpha tape
m subnets.sn76/daily       # daily candles
python3 orbit/subnets/sn76/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
