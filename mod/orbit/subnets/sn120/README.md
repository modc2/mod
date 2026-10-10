# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.045237 | +0.02% | +0.06% | -2.46% | 200,597 | 76,483 | 806.84 |

## Last 24h flow

259 trades by 103 coldkeys · 77 buys (224.50 τ) / 182 sells (384.24 τ) · net -159.74 τ

## News

- 2026-10-10 · commit · [docs: publish active epoch110 nine-batch and 512-task contract](https://github.com/AffineFoundation/affine/commit/b41255b9a5d9a0c8dbad3b3e4014cf42e3aca046) — AffineFoundation/affine
- 2026-10-10 · commit · [Support prospective nine-batch mining and 512-task training](https://github.com/AffineFoundation/affine/commit/65ff85fd9c85af66679f06919c2c9d5ea2cb392b) — AffineFoundation/affine
- 2026-10-09 · commit · [Retain partial miner batches and advance sampling attempts on retries](https://github.com/AffineFoundation/affine/commit/34d3b2a6a291770f2f022c34148a5d3fc8c71387) — AffineFoundation/affine
- 2026-10-09 · commit · [Reuse fresh hourly assessments for learner openings](https://github.com/AffineFoundation/affine/commit/c8312d347231e3fa940dd4598047969dd8985279) — AffineFoundation/affine
- 2026-10-09 · commit · [Recover hourly miner weight submissions from finalized chain evidence](https://github.com/AffineFoundation/affine/commit/068a8ce6c7ab508ffd2ca893766562408758f90d) — AffineFoundation/affine
- 2026-10-09 · commit · [Remove stale pre-deployment status from reward documentation](https://github.com/AffineFoundation/affine/commit/4a188d279a3dbca2740a4f4781080cf69ac85eb1) — AffineFoundation/affine
- 2026-10-09 · commit · [Keep hourly miner rewards running without owner burn fallbacks](https://github.com/AffineFoundation/affine/commit/4e8e4644c47f815377ebcf6b781e9f505261788a) — AffineFoundation/affine
- 2026-10-09 · commit · [Display authenticated continuous audits in network batch counts](https://github.com/AffineFoundation/affine/commit/ce14b38287df3bb17de95ee5fbf12698827700f0) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
