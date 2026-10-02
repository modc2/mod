# sn19 — blockmachine t

Harnessing Bittensor's incentive layer to forge self-optimizing infrastructure.

Bittensor subnet **19** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/taostat/blockmachine/) · [url](https://blockmachine.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.009018 | +0.01% | -0.94% | -3.69% | 55,173 | 30,652 | 344.36 |

## Last 24h flow

44 trades by 34 coldkeys · 8 buys (81.89 τ) / 36 sells (233.53 τ) · net -151.64 τ

## Use

```bash
m subnets.sn19/info        # live identity + market (snapshot if bt is down)
m subnets.sn19/news        # scraped news
m subnets.sn19/trades      # 24h alpha tape
m subnets.sn19/daily       # daily candles
python3 orbit/subnets/sn19/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
