# sn27 — Orion ג

A decentralized data subnet that discovers, generates, and curates high-quality training data for LLMs

Bittensor subnet **27** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/SILX-LABS/Orion)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002285 | — | -3.14% | -8.79% | 14,419 | 6,165 | 301.42 |

## Last 24h flow

41 trades by 27 coldkeys · 20 buys (101.12 τ) / 21 sells (199.98 τ) · net -98.85 τ

## Use

```bash
m subnets.sn27/info        # live identity + market (snapshot if bt is down)
m subnets.sn27/news        # scraped news
m subnets.sn27/trades      # 24h alpha tape
m subnets.sn27/daily       # daily candles
python3 orbit/subnets/sn27/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
