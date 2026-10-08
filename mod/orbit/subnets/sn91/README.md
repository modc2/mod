# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://cascadesub.net) · discord `christensor_49068`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004937 | +0.26% | +2.83% | +1.51% | 9,328 | 3,311 | 1,509 |

## Last 24h flow

218 trades by 74 coldkeys · 114 buys (774.74 τ) / 104 sells (728.93 τ) · net 45.81 τ

## News

- 2026-10-06 · commit · [king sync (finney): vault/direct@sha256:bc25aa4323c91d3313e038c1a62f4…](https://github.com/TensorLink-AI/cascade/commit/9404c3a87e2a00679d5ec15362c9ef165293d349) — TensorLink-AI/cascade
- 2026-10-06 · commit · [Merge pull request #355 from TensorLink-AI/web/missing-is-final](https://github.com/TensorLink-AI/cascade/commit/fa84d7617a795bf46f597274f605ddcfc4c84ef6) — TensorLink-AI/cascade
- 2026-10-06 · commit · [Dashboards: a missing object is final, not retried on every endpoint](https://github.com/TensorLink-AI/cascade/commit/df9fe446cad0662a3cbdfe7d231efe930d19fef4) — TensorLink-AI/cascade
- 2026-10-05 · commit · [Merge pull request #354 from TensorLink-AI/web/cut-vercel-transfer](https://github.com/TensorLink-AI/cascade/commit/2a830466b5a2e5ad7f83da5f83cf99bae2e144bf) — TensorLink-AI/cascade
- 2026-10-05 · commit · [Dashboards: stop pulling ~11 MB through Vercel on every poll](https://github.com/TensorLink-AI/cascade/commit/efad751a9e0bb76ea11013cf0fb713be6385164c) — TensorLink-AI/cascade
- 2026-10-05 · commit · [Merge pull request #353 from TensorLink-AI/fix/king-pod-bench-driver](https://github.com/TensorLink-AI/cascade/commit/127f03cc5b20056c6aca2daaaa077cb97d02a21f) — TensorLink-AI/cascade
- 2026-10-04 · commit · [King pod must be able to run the public benchmarks (driver >= 580)](https://github.com/TensorLink-AI/cascade/commit/5ffa7b0ced7408a22d071d0303f9b70456abba90) — TensorLink-AI/cascade
- 2026-10-04 · commit · [king sync (finney): vault/direct@sha256:298591a20129a4b1326f4ffcd7b56…](https://github.com/TensorLink-AI/cascade/commit/7d64fc02a7dd4dbce2549eb593fe01cef8a1c402) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
