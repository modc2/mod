# sn3 — Teutonic γ

Coordinated Learning

Bittensor subnet **3** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/unarbos/teutonic) · [url](https://www.teutonic.ai/) · discord `@unarbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.024427 | +0.01% | -2.48% | -4.82% | 153,683 | 74,421 | 2,534 |

## Last 24h flow

161 trades by 90 coldkeys · 72 buys (677.51 τ) / 89 sells (1,730 τ) · net -1,053 τ

## News

- 2026-10-15 · commit · [Increase precision of delta threshold metric display in dataset cell](https://github.com/unarbos/teutonic/commit/5e55c189314df9e9f6fab63f4f0d49ceb0cdab8f) — unarbos/teutonic
- 2026-10-15 · commit · [Require specialist own-source improvement to meet the domain threshold](https://github.com/unarbos/teutonic/commit/3c8d2b60d2616e61083cf3d894e4706ac8c62ea1) — unarbos/teutonic
- 2026-10-15 · commit · [Optimize evaluator dataset downloads with authenticated R2 reads and …](https://github.com/unarbos/teutonic/commit/96e37a395c4530fbfe2e8f5fa7e1cdc15ad8ecc9) — unarbos/teutonic
- 2026-10-15 · commit · [Add legacy BBH and integrate legacy data into benchmark results](https://github.com/unarbos/teutonic/commit/778e0021482bfb01dee08282589a2c0dac4a1f19) — unarbos/teutonic
- 2026-10-15 · commit · [Optimize evaluator model loading by broadcasting weights to GPU replicas](https://github.com/unarbos/teutonic/commit/049de01701e74c8d82edfbec6b8a64908f138170) — unarbos/teutonic
- 2026-10-12 · commit · [Optimize evaluator shard downloads with cached HTTP range reads](https://github.com/unarbos/teutonic/commit/5118c326a10fde4bdf485e1963c0f09f9c759645) — unarbos/teutonic
- 2026-10-12 · commit · [Add download button for shard list and update UI layout for shard sum…](https://github.com/unarbos/teutonic/commit/0aefc6906c9e9ff51f9a23ee9b6d26841df00700) — unarbos/teutonic
- 2026-10-10 · commit · [Implement cache locking mechanism for model and shard cleanup processes](https://github.com/unarbos/teutonic/commit/132be8469e67e6aba25f3e90b297bd469b987f24) — unarbos/teutonic

## Use

```bash
m subnets.sn3/info        # live identity + market (snapshot if bt is down)
m subnets.sn3/news        # scraped news
m subnets.sn3/trades      # 24h alpha tape
m subnets.sn3/daily       # daily candles
python3 orbit/subnets/sn3/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
