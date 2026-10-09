# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.045209 | +0.00% | -0.35% | -2.94% | 200,124 | 76,400 | 1,146 |

## Last 24h flow

515 trades by 380 coldkeys · 55 buys (311.65 τ) / 460 sells (632.05 τ) · net -320.40 τ

## News

- 2026-10-08 · commit · [Handle fresh-run null trainer state](https://github.com/AffineFoundation/affine/commit/5ea505fed2c36dfa2ef05a11dd6e36537d86580a) — AffineFoundation/affine
- 2026-10-08 · commit · [Skip unresolved math attempts during mining](https://github.com/AffineFoundation/affine/commit/a88d0e4ec20bbc4aafb67b35fd3e52b80b4ef86f) — AffineFoundation/affine
- 2026-10-08 · commit · [Confirm fresh public epoch and authenticated base evaluation](https://github.com/AffineFoundation/affine/commit/6ffee74f5e9c1b223ceb8cc72daccb5115b995be) — AffineFoundation/affine
- 2026-10-08 · commit · [Record observed base restart and verifier handover status](https://github.com/AffineFoundation/affine/commit/c738712f07473d85fbee81b44618c053552e9f07) — AffineFoundation/affine
- 2026-10-08 · commit · [Reuse authenticated hourly audit assessment during calibration retries](https://github.com/AffineFoundation/affine/commit/7739056e108d703a891fb8dc41b9856446007bba) — AffineFoundation/affine
- 2026-10-08 · commit · [Activate completed-answer base restart and isolate new-run diagnostics](https://github.com/AffineFoundation/affine/commit/148ada720c962c387b17eeb5f862e2bf733ea4f7) — AffineFoundation/affine
- 2026-10-08 · commit · [Classify incomplete math answers as unresolved under a versioned cont…](https://github.com/AffineFoundation/affine/commit/96ab5e7948c85b36f46965c9b1e3648245b52119) — AffineFoundation/affine
- 2026-10-08 · commit · [Admit prospective 2048-token math harness cutover without changing ac…](https://github.com/AffineFoundation/affine/commit/c0f7d1f063e091ebb3e309782fa8046eb0a97892) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
