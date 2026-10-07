# sn19 — blockmachine t

Harnessing Bittensor's incentive layer to forge self-optimizing infrastructure.

Bittensor subnet **19** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/taostat/blockmachine/) · [url](https://blockmachine.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.009164 | +0.00% | +1.61% | -0.11% | 56,410 | 30,940 | 1,814 |

## Last 24h flow

163 trades by 119 coldkeys · 116 buys (1,010 τ) / 47 sells (780.66 τ) · net 229.31 τ

## Use

```bash
m subnets.sn19/info        # live identity + market (snapshot if bt is down)
m subnets.sn19/news        # scraped news
m subnets.sn19/trades      # 24h alpha tape
m subnets.sn19/daily       # daily candles
python3 orbit/subnets/sn19/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
