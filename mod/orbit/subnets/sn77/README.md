# sn77 — Liquidity ه

Supply liquidity on external chains via uniswap, incentivize any project

Bittensor subnet **77** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/creativebuilds/sn77) · [url](https://sn77.xyz) · discord `CreativeBuilds`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004849 | +0.24% | -0.58% | -1.99% | 28,228 | 11,495 | 205.15 |

## Last 24h flow

16 trades by 11 coldkeys · 3 buys (85.84 τ) / 13 sells (117.40 τ) · net -31.56 τ

## Use

```bash
m subnets.sn77/info        # live identity + market (snapshot if bt is down)
m subnets.sn77/news        # scraped news
m subnets.sn77/trades      # 24h alpha tape
m subnets.sn77/daily       # daily candles
python3 orbit/subnets/sn77/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
