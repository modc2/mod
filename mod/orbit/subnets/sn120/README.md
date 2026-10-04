# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.046107 | +0.02% | -0.59% | +2.29% | 202,364 | 76,853 | 1,718 |

## Last 24h flow

387 trades by 209 coldkeys · 117 buys (555.03 τ) / 270 sells (966.39 τ) · net -411.35 τ

## News

- 2026-10-04 · commit · [Wire opt-in covered training through signed epochs and original job r…](https://github.com/AffineFoundation/affine/commit/339256d84dd082d7041c669c012504b89964b8db) — AffineFoundation/affine
- 2026-10-04 · commit · [Record independently checked full-coverage H100 control](https://github.com/AffineFoundation/affine/commit/d3869a06a45c4fe2cc52551ca426b63561eb013c) — AffineFoundation/affine
- 2026-10-04 · commit · [Add prospective full-coverage epoch training and isolated GPU control](https://github.com/AffineFoundation/affine/commit/8d87e223703a40d93eb7cce787335b81952b528c) — AffineFoundation/affine
- 2026-10-04 · commit · [Record recovered verifier access and completed cache retirement](https://github.com/AffineFoundation/affine/commit/a66fcb55b96f0ad28d6d385a41efc9bc0d92d49c) — AffineFoundation/affine
- 2026-10-04 · commit · [Prepare immutable source admission while live mining continues](https://github.com/AffineFoundation/affine/commit/c510c0767f9d1a3fe6f2c3b7f3107c5b21c4565d) — AffineFoundation/affine
- 2026-10-03 · commit · [Supervise authenticated completed trainer archival and replica retire…](https://github.com/AffineFoundation/affine/commit/8a1d46439817fc9c2d38ee6ec8345c7ef4dfb01c) — AffineFoundation/affine
- 2026-10-03 · commit · [Bind verifier startup to the approved deployment identity](https://github.com/AffineFoundation/affine/commit/d4ca51704ae5eca76e98712f6cb6d55242d29f47) — AffineFoundation/affine
- 2026-10-03 · commit · [Retire archived completed trainer replicas and publish the live stora…](https://github.com/AffineFoundation/affine/commit/f6c004fb1540e7d4500b2c5165d99e40a95bfa53) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
