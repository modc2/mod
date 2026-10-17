# sn92 — REAX र

Verified decision-model inference on Bittensor.

Bittensor subnet **92** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/reaxlabs/reax-subnet) · [url](https://reax.co/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.034440 | -0.48% | +1.46% | +662.51% | 7,285 | 2,455 | 18,929 |

## Last 24h flow

1444 trades by 183 coldkeys · 323 buys (9,382 τ) / 1121 sells (9,421 τ) · net -38.85 τ

## News

- 2026-10-17 · release · [v1.2.0-rc7.post1](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc7.post1) — reaxlabs/reax-subnet
- 2026-10-17 · commit · [Release 1.2.0rc7.post1: keep-alive pays the median inference weight p…](https://github.com/reaxlabs/reax-subnet/commit/b05e834275f66315e0cdb0883eac08f2f1ef0d02) — reaxlabs/reax-subnet
- 2026-10-16 · commit · [release 1.2.0rc7: capacity probe in parallel shard processes (one mac…](https://github.com/reaxlabs/reax-subnet/commit/d16d0f1ede3274eb76a42e88b4bc046b953ce237) — reaxlabs/reax-subnet
- 2026-10-16 · release · [v1.2.0-rc7](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc7) — reaxlabs/reax-subnet
- 2026-10-16 · commit · [release 1.2.0rc6.post1: fair first crowning on specialist tracks (ope…](https://github.com/reaxlabs/reax-subnet/commit/dad79622086a1866a88c8285680fd1c9fb6abd11) — reaxlabs/reax-subnet
- 2026-10-16 · release · [v1.2.0-rc6.post1](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc6.post1) — reaxlabs/reax-subnet
- 2026-10-16 · commit · [release 1.2.0rc6: specialist draws 15 % procedural / 300 items with f…](https://github.com/reaxlabs/reax-subnet/commit/076c800b2dbb866101fed40e73aa7e7fd28ef744) — reaxlabs/reax-subnet
- 2026-10-16 · release · [v1.2.0-rc6](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc6) — reaxlabs/reax-subnet

## Use

```bash
m subnets.sn92/info        # live identity + market (snapshot if bt is down)
m subnets.sn92/news        # scraped news
m subnets.sn92/trades      # 24h alpha tape
m subnets.sn92/daily       # daily candles
python3 orbit/subnets/sn92/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
