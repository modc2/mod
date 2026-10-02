# sn30 — Endure Network ו

The risk intelligence network

Bittensor subnet **30** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [url](https://endure.network)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003215 | -0.00% | -0.03% | -1.70% | 19,463 | 8,268 | 1.77 |

## Last 24h flow

10 trades by 9 coldkeys · 4 buys (60.17 τ) / 6 sells (1.22 τ) · net 58.94 τ

## Use

```bash
m subnets.sn30/info        # live identity + market (snapshot if bt is down)
m subnets.sn30/news        # scraped news
m subnets.sn30/trades      # 24h alpha tape
m subnets.sn30/daily       # daily candles
python3 orbit/subnets/sn30/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
