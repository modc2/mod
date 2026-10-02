# sn113 — LongShort Ѓ

pivoting to long-short DEX of alpha token

Bittensor subnet **113** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.001886 | -0.00% | -0.87% | -3.86% | 4,695 | 1,579 | 84.99 |

## Last 24h flow

17 trades by 12 coldkeys · 8 buys (39.06 τ) / 9 sells (45.41 τ) · net -6.36 τ

## Use

```bash
m subnets.sn113/info        # live identity + market (snapshot if bt is down)
m subnets.sn113/news        # scraped news
m subnets.sn113/trades      # 24h alpha tape
m subnets.sn113/daily       # daily candles
python3 orbit/subnets/sn113/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
