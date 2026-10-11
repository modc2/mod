# sn30 — Endure Network ו

The risk intelligence network

Bittensor subnet **30** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [url](https://endure.network)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003347 | -0.00% | -0.23% | +4.61% | 20,473 | 8,438 | 25.64 |

## Last 24h flow

3 trades by 2 coldkeys · 1 buys (8.00 τ) / 2 sells (17.39 τ) · net -9.39 τ

## Use

```bash
m subnets.sn30/info        # live identity + market (snapshot if bt is down)
m subnets.sn30/news        # scraped news
m subnets.sn30/trades      # 24h alpha tape
m subnets.sn30/daily       # daily candles
python3 orbit/subnets/sn30/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
