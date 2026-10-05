# sn19 — blockmachine t

Harnessing Bittensor's incentive layer to forge self-optimizing infrastructure.

Bittensor subnet **19** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/taostat/blockmachine/) · [url](https://blockmachine.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.009016 | -0.64% | -0.03% | -1.70% | 55,350 | 30,671 | 388.74 |

## Last 24h flow

34 trades by 27 coldkeys · 12 buys (176.50 τ) / 22 sells (191.86 τ) · net -15.36 τ

## Use

```bash
m subnets.sn19/info        # live identity + market (snapshot if bt is down)
m subnets.sn19/news        # scraped news
m subnets.sn19/trades      # 24h alpha tape
m subnets.sn19/daily       # daily candles
python3 orbit/subnets/sn19/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
