# sn18 — Zeus σ

Pushing weather forecasts beyond state-of-the-art

Bittensor subnet **18** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/Orpheus-AI/Zeus) · [url](https://www.zeussubnet.com/) · discord `wouter_orpheusai`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003811 | +0.00% | -0.97% | +2.97% | 22,414 | 9,912 | 130.78 |

## Last 24h flow

70 trades by 64 coldkeys · 17 buys (41.02 τ) / 53 sells (89.36 τ) · net -48.34 τ

## News

- 2026-09-07 · release · [Release 2.1.4](https://github.com/Orpheus-AI/Zeus/releases/tag/v2.1.4) — Orpheus-AI/Zeus
- 2026-09-07 · commit · [Stop emissions for non-participating miners (#88)](https://github.com/Orpheus-AI/Zeus/commit/024eb19eeca724aaad135b4492091abf658694ce) — Orpheus-AI/Zeus

## Use

```bash
m subnets.sn18/info        # live identity + market (snapshot if bt is down)
m subnets.sn18/news        # scraped news
m subnets.sn18/trades      # 24h alpha tape
m subnets.sn18/daily       # daily candles
python3 orbit/subnets/sn18/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
