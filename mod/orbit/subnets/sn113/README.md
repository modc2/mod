# sn113 — LongShort Ѓ

pivoting to long-short DEX of alpha token

Bittensor subnet **113** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002544 | -0.00% | -11.78% | +31.38% | 6,437 | 1,834 | 452.11 |

## Last 24h flow

50 trades by 31 coldkeys · 16 buys (166.76 τ) / 34 sells (284.45 τ) · net -117.70 τ

## Use

```bash
m subnets.sn113/info        # live identity + market (snapshot if bt is down)
m subnets.sn113/news        # scraped news
m subnets.sn113/trades      # 24h alpha tape
m subnets.sn113/daily       # daily candles
python3 orbit/subnets/sn113/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
