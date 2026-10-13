# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.043659 | +0.01% | -1.42% | -6.22% | 194,605 | 75,310 | 2,443 |

## Last 24h flow

860 trades by 119 coldkeys · 63 buys (763.35 τ) / 797 sells (1,475 τ) · net -711.39 τ

## News

- 2026-10-12 · commit · [Add prospective verifier-only symmetric sampling adjudication](https://github.com/AffineFoundation/affine/commit/9423ccaa8df5432eb46732c44ec3d7537a0d54ef) — AffineFoundation/affine
- 2026-10-12 · commit · [Record detached positive and negative training likelihoods](https://github.com/AffineFoundation/affine/commit/996de84cd1f0b4d0bd9d82c3d12849a5589e7581) — AffineFoundation/affine
- 2026-10-12 · commit · [Clarify deployed preference objective and restart provenance](https://github.com/AffineFoundation/affine/commit/03478f259c569990a79e4e029b630ae9be3c636a) — AffineFoundation/affine
- 2026-10-12 · commit · [Distinguish TRAIN genesis from upstream reference](https://github.com/AffineFoundation/affine/commit/57e533e18f45dc37215e6aa6f3e53f63f403505e) — AffineFoundation/affine
- 2026-10-12 · commit · [Clarify nominal and applied context limits in TRAIN diagnostics](https://github.com/AffineFoundation/affine/commit/df143239d7292d979cd55fd2a5b6fe104bc50d4b) — AffineFoundation/affine
- 2026-10-12 · commit · [Fix TRAIN archive metadata collision when publishing current checkpoint](https://github.com/AffineFoundation/affine/commit/ffbeb2312f817d321d775731ff273d2a1674fb31) — AffineFoundation/affine
- 2026-10-12 · commit · [Plot fixed previously trained tasks in TRAIN fit series](https://github.com/AffineFoundation/affine/commit/7bffe51de366c369b1fac633f2b3a3d3ecf05fc9) — AffineFoundation/affine
- 2026-10-12 · commit · [Recognize CPU16 declarations in authorized learning rate evidence](https://github.com/AffineFoundation/affine/commit/d77ecf6b196231df3b2707015732f4ec8bbc8190) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
