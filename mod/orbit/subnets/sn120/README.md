# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.045366 | -0.00% | -2.48% | -2.61% | 200,477 | 76,474 | 4,265 |

## Last 24h flow

714 trades by 453 coldkeys · 90 buys (1,439 τ) / 624 sells (2,600 τ) · net -1,160 τ

## News

- 2026-10-08 · commit · [Document verifier checkpoint lifetime and measured warm audit reuse](https://github.com/AffineFoundation/affine/commit/e41d423ec37bf1299e7277e093d28d7b1252fe65) — AffineFoundation/affine
- 2026-10-08 · commit · [Reuse verifier GPU runtime and retain checkpoints across audit jobs](https://github.com/AffineFoundation/affine/commit/594f9409d3f244723088b4d817b2ee852f98aa1b) — AffineFoundation/affine
- 2026-10-08 · commit · [Activate eight-rollout miner contract in epoch 59](https://github.com/AffineFoundation/affine/commit/2ca8bf15f1867e5443a284b9ff499bec259d7933) — AffineFoundation/affine
- 2026-10-08 · commit · [Admit manifest-sized miner-bound batches through the CPU training peer](https://github.com/AffineFoundation/affine/commit/978fdc1d4f019d5c66d1a6e42b006b948e3af683) — AffineFoundation/affine
- 2026-10-08 · commit · [Drive balanced rollout quotas and grading budgets from one batch-size…](https://github.com/AffineFoundation/affine/commit/2adf1379238d772e09230987f79b6e4e5924bfd0) — AffineFoundation/affine
- 2026-10-07 · commit · [Admit explicitly signed source-bound verifier capacity sidecars](https://github.com/AffineFoundation/affine/commit/ef6720f96454862c56d9965892defb08d077a547) — AffineFoundation/affine
- 2026-10-07 · commit · [Reserve audit capacity for current epochs while retaining history](https://github.com/AffineFoundation/affine/commit/8c051eb9e68aa5d2dd21c0302c5932c4e94033aa) — AffineFoundation/affine
- 2026-10-07 · commit · [Authenticate miner checkpoints without optimistic cache hints](https://github.com/AffineFoundation/affine/commit/39d2625257c1e955e747b83921f67c01e4ac8bde) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
