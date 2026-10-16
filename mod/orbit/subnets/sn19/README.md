# sn19 — blockmachine t

Harnessing Bittensor's incentive layer to forge self-optimizing infrastructure.

Bittensor subnet **19** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/taostat/blockmachine/) · [url](https://blockmachine.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.009045 | +0.00% | +1.16% | +0.20% | 56,335 | 30,818 | 1,085 |

## Last 24h flow

77 trades by 49 coldkeys · 40 buys (611.81 τ) / 37 sells (446.30 τ) · net 165.51 τ

## Use

```bash
m subnets.sn19/info        # live identity + market (snapshot if bt is down)
m subnets.sn19/news        # scraped news
m subnets.sn19/trades      # 24h alpha tape
m subnets.sn19/daily       # daily candles
python3 orbit/subnets/sn19/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
