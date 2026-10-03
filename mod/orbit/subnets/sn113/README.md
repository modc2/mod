# sn113 — LongShort Ѓ

pivoting to long-short DEX of alpha token

Bittensor subnet **113** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.001831 | +0.00% | -3.28% | -7.35% | 4,569 | 1,557 | 465.93 |

## Last 24h flow

61 trades by 20 coldkeys · 31 buys (219.95 τ) / 30 sells (245.56 τ) · net -25.60 τ

## Use

```bash
m subnets.sn113/info        # live identity + market (snapshot if bt is down)
m subnets.sn113/news        # scraped news
m subnets.sn113/trades      # 24h alpha tape
m subnets.sn113/daily       # daily candles
python3 orbit/subnets/sn113/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
