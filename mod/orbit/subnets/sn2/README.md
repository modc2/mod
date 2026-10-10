# sn2 — DSperse β

Verifiable and distributed inference on Bittensor

Bittensor subnet **2** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/inference-labs-inc/subnet-2) · [url](https://subnet2.inferencelabs.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002961 | +0.00% | -0.36% | -2.92% | 17,562 | 7,605 | 13.82 |

## Last 24h flow

8 trades by 8 coldkeys · 0 buys (0.00 τ) / 8 sells (13.56 τ) · net -13.56 τ

## Use

```bash
m subnets.sn2/info        # live identity + market (snapshot if bt is down)
m subnets.sn2/news        # scraped news
m subnets.sn2/trades      # 24h alpha tape
m subnets.sn2/daily       # daily candles
python3 orbit/subnets/sn2/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
