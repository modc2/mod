# sn30 — Endure Network ו

The risk intelligence network

Bittensor subnet **30** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [url](https://endure.network)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003320 | -0.28% | -1.25% | +3.28% | 20,260 | 8,404 | 193.12 |

## Last 24h flow

21 trades by 14 coldkeys · 6 buys (70.13 τ) / 15 sells (122.63 τ) · net -52.50 τ

## Use

```bash
m subnets.sn30/info        # live identity + market (snapshot if bt is down)
m subnets.sn30/news        # scraped news
m subnets.sn30/trades      # 24h alpha tape
m subnets.sn30/daily       # daily candles
python3 orbit/subnets/sn30/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
