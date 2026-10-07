# sn27 — Orion ג

A decentralized data subnet that discovers, generates, and curates high-quality training data for LLMs

Bittensor subnet **27** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/SILX-LABS/Orion)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002238 | -0.16% | +1.44% | +1.31% | 13,929 | 6,100 | 308.50 |

## Last 24h flow

30 trades by 21 coldkeys · 17 buys (175.96 τ) / 13 sells (132.21 τ) · net 43.75 τ

## Use

```bash
m subnets.sn27/info        # live identity + market (snapshot if bt is down)
m subnets.sn27/news        # scraped news
m subnets.sn27/trades      # 24h alpha tape
m subnets.sn27/daily       # daily candles
python3 orbit/subnets/sn27/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
