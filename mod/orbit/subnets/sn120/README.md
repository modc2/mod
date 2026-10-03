# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.046380 | -0.13% | -0.43% | +0.85% | 203,220 | 77,037 | 4,423 |

## Last 24h flow

377 trades by 173 coldkeys · 198 buys (1,931 τ) / 179 sells (2,287 τ) · net -355.86 τ

## News

- 2026-10-03 · commit · [Document live open subnet admission and public miner discovery](https://github.com/AffineFoundation/affine/commit/d4a6fc1fa631cea92494f3e1c376090f5024c41a) — AffineFoundation/affine
- 2026-10-03 · commit · [Allow all activated subnet miners at fresh epoch boundaries](https://github.com/AffineFoundation/affine/commit/d0a3d2b4a8768bd975865d598d9755886df19a7d) — AffineFoundation/affine
- 2026-10-03 · commit · [Record natural empty freeze and miner-side R2 bootstrap admission](https://github.com/AffineFoundation/affine/commit/87d7122ddc7de6d8c7a15acec337af1036165df0) — AffineFoundation/affine
- 2026-10-03 · commit · [Document qualified source URL contract and guarded epoch recovery](https://github.com/AffineFoundation/affine/commit/9fe5865ec4210af177746553db2288a44042a2ab) — AffineFoundation/affine
- 2026-10-03 · commit · [Record public bootstrap descriptor mismatch and recovery gate](https://github.com/AffineFoundation/affine/commit/14d45bc20f1bc077b0238c2cf897c5c2c61989ec) — AffineFoundation/affine
- 2026-10-02 · commit · [Record pinned Qwen CPU context audit and bounded corpus gates](https://github.com/AffineFoundation/affine/commit/0258230d1196f8f116f501ca368abc02ff6abed1) — AffineFoundation/affine
- 2026-10-02 · commit · [Record qualified replacement and recovery controller activation](https://github.com/AffineFoundation/affine/commit/1aabb4d837372d4f8e93496f0aa75561f3c9ec2e) — AffineFoundation/affine
- 2026-10-02 · commit · [Wait for the verifier coordinator before starting workers](https://github.com/AffineFoundation/affine/commit/adc9a4733dba182da86e13609e05c9e98a00e41c) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
