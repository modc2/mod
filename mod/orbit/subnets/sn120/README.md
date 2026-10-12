# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.044289 | +0.02% | -0.13% | -4.04% | 197,076 | 75,794 | 785.00 |

## Last 24h flow

844 trades by 103 coldkeys · 32 buys (183.18 τ) / 812 sells (410.47 τ) · net -227.29 τ

## News

- 2026-10-12 · commit · [Rotate overused math tasks through existing epoch manifests](https://github.com/AffineFoundation/affine/commit/d59a6bde8bc88c975b3b2523732b2d2af9a857a0) — AffineFoundation/affine
- 2026-10-11 · commit · [Bound hourly audit evidence memory without changing scores](https://github.com/AffineFoundation/affine/commit/332b5b4861ebd32f7f9ef2e7e84de478f1184ea6) — AffineFoundation/affine
- 2026-10-11 · commit · [Project finalized training metrics from authenticated receipts](https://github.com/AffineFoundation/affine/commit/a16f3813a9e6f996a991c80c520902d4d8c8a046) — AffineFoundation/affine
- 2026-10-11 · commit · [Add prospective bounded 64-worker token capture](https://github.com/AffineFoundation/affine/commit/ab9f817c03e9edd421907805d2d0074d8e0f4c0f) — AffineFoundation/affine
- 2026-10-11 · commit · [Document independent epoch openings and hourly reward fallback](https://github.com/AffineFoundation/affine/commit/e17cade34f89cdf313163928b4975a152023b790) — AffineFoundation/affine
- 2026-10-11 · commit · [Publish retained serial heldout128 outputs for finalized checkpoints](https://github.com/AffineFoundation/affine/commit/2c64f897401b91860ab9fb0270a4ead344511eb2) — AffineFoundation/affine
- 2026-10-11 · commit · [docs: publish fixed endpoint results and paired uncertainty](https://github.com/AffineFoundation/affine/commit/09c56eb6454c8911595d28d743a5adb4824adc6a) — AffineFoundation/affine
- 2026-10-11 · commit · [docs: report completed 16-update study and pending evaluation](https://github.com/AffineFoundation/affine/commit/e63bd5bc14f2a2692a9add1c83425438e41b1278) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
