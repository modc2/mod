# sn3 — Teutonic γ

Coordinated Learning

Bittensor subnet **3** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/unarbos/teutonic) · [url](https://www.teutonic.ai/) · discord `@unarbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.025601 | +0.02% | -0.24% | -0.04% | 159,865 | 76,040 | 926.40 |

## Last 24h flow

119 trades by 75 coldkeys · 72 buys (308.32 τ) / 47 sells (504.74 τ) · net -196.42 τ

## News

- 2026-10-09 · commit · [Document-index download retries](https://github.com/unarbos/teutonic/commit/58e37fa8a22a0d8c5366d7c2b2728ab8206b406c) — unarbos/teutonic
- 2026-10-09 · commit · [Update early-stopping feature check to use token-weighted observed qu…](https://github.com/unarbos/teutonic/commit/66dd1b15f2effdc898475fd6b691a5dea8ddba4f) — unarbos/teutonic
- 2026-10-09 · commit · [Add competition filters to evaluation history and update dataset summ…](https://github.com/unarbos/teutonic/commit/14a9bb2cd8f28b645fa6abb146fe00a1593e1c96) — unarbos/teutonic
- 2026-10-09 · commit · [Add document-masked evaluation and token-weighted scoring with 2K–8K …](https://github.com/unarbos/teutonic/commit/f3988cf2cc28c1528ffb51746a1a235f3d04b252) — unarbos/teutonic
- 2026-10-08 · commit · [Add competition dataset panel and update evaluation history layout](https://github.com/unarbos/teutonic/commit/25c64ca4857015c01f10257eac609d2fc8389fe5) — unarbos/teutonic
- 2026-10-08 · commit · [Fix evaluator replica race in Transformers module cache](https://github.com/unarbos/teutonic/commit/803af9c332f70a0259921a6e45a3767abc0abc6b) — unarbos/teutonic
- 2026-10-07 · commit · [Refresh stale split baselines after coronation](https://github.com/unarbos/teutonic/commit/3475ddab7411ee695770b6d1af1c9195494fd86b) — unarbos/teutonic
- 2026-10-07 · commit · [Add per-competition error visibility toggles](https://github.com/unarbos/teutonic/commit/7c8b80eeeaeaa7477c9f3f08555f2d6916e42024) — unarbos/teutonic

## Use

```bash
m subnets.sn3/info        # live identity + market (snapshot if bt is down)
m subnets.sn3/news        # scraped news
m subnets.sn3/trades      # 24h alpha tape
m subnets.sn3/daily       # daily candles
python3 orbit/subnets/sn3/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
