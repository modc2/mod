# sn29 — hoτfloaτ ה

Inference optimization

Bittensor subnet **29** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/coldint/hotfloat) · [url](http://hotfloat.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002535 | -0.00% | -0.05% | -9.16% | 15,525 | 7,941 | 2.21 |

## Last 24h flow

9 trades by 7 coldkeys · 2 buys (0.14 τ) / 7 sells (1.38 τ) · net -1.24 τ

## Use

```bash
m subnets.sn29/info        # live identity + market (snapshot if bt is down)
m subnets.sn29/news        # scraped news
m subnets.sn29/trades      # 24h alpha tape
m subnets.sn29/daily       # daily candles
python3 orbit/subnets/sn29/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
