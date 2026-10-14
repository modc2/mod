# sn77 — Liquidity ه

Supply liquidity on external chains via uniswap, incentivize any project

Bittensor subnet **77** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/creativebuilds/sn77) · [url](https://sn77.xyz) · discord `CreativeBuilds`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004921 | -0.00% | -0.53% | -2.14% | 28,408 | 11,552 | 30.47 |

## Last 24h flow

16 trades by 8 coldkeys · 0 buys (0.00 τ) / 16 sells (29.73 τ) · net -29.73 τ

## Use

```bash
m subnets.sn77/info        # live identity + market (snapshot if bt is down)
m subnets.sn77/news        # scraped news
m subnets.sn77/trades      # 24h alpha tape
m subnets.sn77/daily       # daily candles
python3 orbit/subnets/sn77/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
