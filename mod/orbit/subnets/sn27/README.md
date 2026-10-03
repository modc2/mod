# sn27 — Orion ג

A decentralized data subnet that discovers, generates, and curates high-quality training data for LLMs

Bittensor subnet **27** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/SILX-LABS/Orion)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002227 | +0.00% | -0.01% | -3.57% | 13,795 | 6,085 | 60.46 |

## Last 24h flow

7 trades by 6 coldkeys · 3 buys (30.02 τ) / 4 sells (30.28 τ) · net -0.26 τ

## Use

```bash
m subnets.sn27/info        # live identity + market (snapshot if bt is down)
m subnets.sn27/news        # scraped news
m subnets.sn27/trades      # 24h alpha tape
m subnets.sn27/daily       # daily candles
python3 orbit/subnets/sn27/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
