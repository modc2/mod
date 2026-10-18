# sn92 — REAX र

Verified decision-model inference on Bittensor.

Bittensor subnet **92** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/reaxlabs/reax-subnet) · [url](https://reax.co/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.039526 | +0.53% | +13.32% | +775.09% | 8,640 | 2,671 | 6,396 |

## Last 24h flow

1389 trades by 217 coldkeys · 475 buys (3,234 τ) / 914 sells (3,097 τ) · net 137.07 τ

## News

- 2026-10-17 · release · [v1.2.0-rc8.post2](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc8.post2) — reaxlabs/reax-subnet
- 2026-10-17 · commit · [1.2.0rc8.post2: competition worker queue liveness fixes + first-draw …](https://github.com/reaxlabs/reax-subnet/commit/6d1271ac9528a363cda4b40ef0048cab2cf0e21a) — reaxlabs/reax-subnet
- 2026-10-17 · release · [v1.2.0-rc8.post1](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc8.post1) — reaxlabs/reax-subnet
- 2026-10-17 · commit · [Release 1.2.0rc8.post1 (competition worker only): entries whose repo …](https://github.com/reaxlabs/reax-subnet/commit/fb788084b55352e3bb0ad85c40e736a226b1824a) — reaxlabs/reax-subnet
- 2026-10-17 · release · [v1.2.0-rc8](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc8) — reaxlabs/reax-subnet
- 2026-10-17 · commit · [Release 1.2.0rc8: mechanism v2 release A (keep-alive top 8, immunity-…](https://github.com/reaxlabs/reax-subnet/commit/9bba5e00d96aab1c225b1fa204c2c8bc7f583c80) — reaxlabs/reax-subnet
- 2026-10-17 · release · [v1.2.0-rc7.post4](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc7.post4) — reaxlabs/reax-subnet
- 2026-10-17 · commit · [Release 1.2.0rc7.post4: small-tier pay cap raised (tier-order ratio 0…](https://github.com/reaxlabs/reax-subnet/commit/0b42ca05a8774e3ba7cd505d9a59840c2e7f23d6) — reaxlabs/reax-subnet

## Use

```bash
m subnets.sn92/info        # live identity + market (snapshot if bt is down)
m subnets.sn92/news        # scraped news
m subnets.sn92/trades      # 24h alpha tape
m subnets.sn92/daily       # daily candles
python3 orbit/subnets/sn92/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
