# sn113 — LongShort Ѓ

pivoting to long-short DEX of alpha token

Bittensor subnet **113** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002826 | -0.70% | +13.92% | +46.80% | 7,111 | 1,934 | 980.63 |

## Last 24h flow

152 trades by 51 coldkeys · 96 buys (551.39 τ) / 56 sells (427.45 τ) · net 123.94 τ

## Use

```bash
m subnets.sn113/info        # live identity + market (snapshot if bt is down)
m subnets.sn113/news        # scraped news
m subnets.sn113/trades      # 24h alpha tape
m subnets.sn113/daily       # daily candles
python3 orbit/subnets/sn113/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
