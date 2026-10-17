# sn27 — Orion ג

A decentralized data subnet that discovers, generates, and curates high-quality training data for LLMs

Bittensor subnet **27** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/SILX-LABS/Orion)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002346 | -0.29% | -10.91% | +5.52% | 14,772 | 6,246 | 4,337 |

## Last 24h flow

273 trades by 85 coldkeys · 147 buys (1,983 τ) / 126 sells (2,348 τ) · net -364.93 τ

## Use

```bash
m subnets.sn27/info        # live identity + market (snapshot if bt is down)
m subnets.sn27/news        # scraped news
m subnets.sn27/trades      # 24h alpha tape
m subnets.sn27/daily       # daily candles
python3 orbit/subnets/sn27/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
