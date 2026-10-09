# sn2 — DSperse β

Verifiable and distributed inference on Bittensor

Bittensor subnet **2** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/inference-labs-inc/subnet-2) · [url](https://subnet2.inferencelabs.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002971 | +0.26% | +0.21% | -3.08% | 17,605 | 7,619 | 36.43 |

## Last 24h flow

31 trades by 17 coldkeys · 13 buys (22.17 τ) / 18 sells (13.96 τ) · net 8.21 τ

## Use

```bash
m subnets.sn2/info        # live identity + market (snapshot if bt is down)
m subnets.sn2/news        # scraped news
m subnets.sn2/trades      # 24h alpha tape
m subnets.sn2/daily       # daily candles
python3 orbit/subnets/sn2/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
