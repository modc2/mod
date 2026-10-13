# sn3 — Teutonic γ

Coordinated Learning

Bittensor subnet **3** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/unarbos/teutonic) · [url](https://www.teutonic.ai/) · discord `@unarbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.025790 | -0.02% | +0.43% | +0.83% | 161,645 | 76,396 | 602.00 |

## Last 24h flow

76 trades by 49 coldkeys · 34 buys (283.80 τ) / 42 sells (211.17 τ) · net 72.63 τ

## News

- 2026-10-12 · commit · [Optimize evaluator shard downloads with cached HTTP range reads](https://github.com/unarbos/teutonic/commit/5118c326a10fde4bdf485e1963c0f09f9c759645) — unarbos/teutonic
- 2026-10-12 · commit · [Add download button for shard list and update UI layout for shard sum…](https://github.com/unarbos/teutonic/commit/0aefc6906c9e9ff51f9a23ee9b6d26841df00700) — unarbos/teutonic
- 2026-10-10 · commit · [Implement cache locking mechanism for model and shard cleanup processes](https://github.com/unarbos/teutonic/commit/132be8469e67e6aba25f3e90b297bd469b987f24) — unarbos/teutonic
- 2026-10-09 · commit · [Document-index download retries](https://github.com/unarbos/teutonic/commit/58e37fa8a22a0d8c5366d7c2b2728ab8206b406c) — unarbos/teutonic
- 2026-10-09 · commit · [Update early-stopping feature check to use token-weighted observed qu…](https://github.com/unarbos/teutonic/commit/66dd1b15f2effdc898475fd6b691a5dea8ddba4f) — unarbos/teutonic
- 2026-10-09 · commit · [Add competition filters to evaluation history and update dataset summ…](https://github.com/unarbos/teutonic/commit/14a9bb2cd8f28b645fa6abb146fe00a1593e1c96) — unarbos/teutonic
- 2026-10-09 · commit · [Add document-masked evaluation and token-weighted scoring with 2K–8K …](https://github.com/unarbos/teutonic/commit/f3988cf2cc28c1528ffb51746a1a235f3d04b252) — unarbos/teutonic
- 2026-10-08 · commit · [Add competition dataset panel and update evaluation history layout](https://github.com/unarbos/teutonic/commit/25c64ca4857015c01f10257eac609d2fc8389fe5) — unarbos/teutonic

## Use

```bash
m subnets.sn3/info        # live identity + market (snapshot if bt is down)
m subnets.sn3/news        # scraped news
m subnets.sn3/trades      # 24h alpha tape
m subnets.sn3/daily       # daily candles
python3 orbit/subnets/sn3/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
