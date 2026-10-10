# sn30 — Endure Network ו

The risk intelligence network

Bittensor subnet **30** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [url](https://endure.network)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003355 | +0.00% | +1.04% | +4.36% | 20,495 | 8,448 | 443.41 |

## Last 24h flow

41 trades by 21 coldkeys · 21 buys (243.64 τ) / 20 sells (199.37 τ) · net 44.28 τ

## Use

```bash
m subnets.sn30/info        # live identity + market (snapshot if bt is down)
m subnets.sn30/news        # scraped news
m subnets.sn30/trades      # 24h alpha tape
m subnets.sn30/daily       # daily candles
python3 orbit/subnets/sn30/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
