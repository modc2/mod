# sn76 — Ormas ن

Ormas is an Outcomes API for coding work on Bittensor subnet 76, in development. Clients post a change and the test that proves it; miners quote a firm price for the passing result and deliver a branch. Miners will be paid only when independent validators accept the delivery, and clients will be charged the accepted quote and nothing on a miss. Protocol, thin client, reference miner and reference validator: MIT.

Bittensor subnet **76** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/heroncovelabs/ormas-subnet) · [url](https://ormas.ai) · discord `ormasheroncovelabs_43871`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005600 | -0.16% | -6.48% | -14.18% | 1,144 | 492.2684 | 69.00 |

## Last 24h flow

29 trades by 15 coldkeys · 11 buys (26.10 τ) / 18 sells (36.26 τ) · net -10.17 τ

## News

- 2026-10-05 · commit · [sync: public_subnet @ tensorbox-spec 6cdf6088b5 — catalog 2026-10-04.…](https://github.com/heroncovelabs/ormas-subnet/commit/29acff137f8bb2238e4b07c2ce415e3f1846a606) — heroncovelabs/ormas-subnet
- 2026-10-03 · commit · [docs: queue route, offers and limit settlement are live since gateway…](https://github.com/heroncovelabs/ormas-subnet/commit/876a546d6ed78a91c7b10dd61e06b0eaa598dacb) — heroncovelabs/ormas-subnet
- 2026-10-03 · commit · [sync: public_subnet @ tensorbox-spec 29170e22ee — firm-or-limit offer…](https://github.com/heroncovelabs/ormas-subnet/commit/bf379667556e93ba23e65b540cbad427edc1b86a) — heroncovelabs/ormas-subnet
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
