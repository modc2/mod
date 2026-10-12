# sn19 — blockmachine t

Harnessing Bittensor's incentive layer to forge self-optimizing infrastructure.

Bittensor subnet **19** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/taostat/blockmachine/) · [url](https://blockmachine.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.008884 | +0.00% | -0.07% | -1.46% | 55,050 | 30,509 | 60.93 |

## Last 24h flow

32 trades by 29 coldkeys · 8 buys (9.19 τ) / 24 sells (34.32 τ) · net -25.13 τ

## Use

```bash
m subnets.sn19/info        # live identity + market (snapshot if bt is down)
m subnets.sn19/news        # scraped news
m subnets.sn19/trades      # 24h alpha tape
m subnets.sn19/daily       # daily candles
python3 orbit/subnets/sn19/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
