# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.046152 | +0.02% | +0.10% | -1.07% | 202,899 | 76,951 | 984.72 |

## Last 24h flow

287 trades by 83 coldkeys · 46 buys (301.43 τ) / 241 sells (450.45 τ) · net -149.02 τ

## News

- 2026-10-05 · commit · [Record learned-parent completion and capacity-bound hourly audit budgets](https://github.com/AffineFoundation/affine/commit/1a4e9272570f5c736ac95b56810c43455730715f) — AffineFoundation/affine
- 2026-10-05 · commit · [Honor signed artifact budget when decoding owned miner commitments](https://github.com/AffineFoundation/affine/commit/32331b1bff7fc3ade5a0c3f3094447644b92a5ed) — AffineFoundation/affine
- 2026-10-05 · commit · [Record measured hourly bottlenecks and bounded audit preparation](https://github.com/AffineFoundation/affine/commit/ffa84e91311237a6a4ecaefb6b984adf9cf65dca) — AffineFoundation/affine
- 2026-10-05 · commit · [Project readback inventory while preserving full tensor descriptor bi…](https://github.com/AffineFoundation/affine/commit/5158eb5d86b0696151254edd4d006d3ceac37afd) — AffineFoundation/affine
- 2026-10-05 · commit · [Record actual training completion and hourly qualification blockers](https://github.com/AffineFoundation/affine/commit/fdabb4be3a2cc764a5e8c1fdfef8d8673c24589d) — AffineFoundation/affine
- 2026-10-04 · commit · [Document qualified reused H200 as fourth live verifier](https://github.com/AffineFoundation/affine/commit/2bada823f377a750323b4f821184f9802c9ac9db) — AffineFoundation/affine
- 2026-10-04 · commit · [Record E9 receipt training admission and failed legacy GPU controls](https://github.com/AffineFoundation/affine/commit/d28d2e93e749590b71173e5202842552157a62b1) — AffineFoundation/affine
- 2026-10-04 · commit · [Publish isolated full-forward sampler qualification and H200 controls](https://github.com/AffineFoundation/affine/commit/eb5be1f5f2c0ae5b40bf32db284b0b476183c591) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
