# sn103 — Consted Ա

Const : Centralised Entity deciding emission of subnets based on emotion, not by codes

Bittensor subnet **103** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.000927 | -0.00% | -9.01% | +12.10% | 616.1907 | 292.4422 | 186.41 |

## Last 24h flow

104 trades by 35 coldkeys · 60 buys (86.15 τ) / 44 sells (99.45 τ) · net -13.30 τ

## Use

```bash
m subnets.sn103/info        # live identity + market (snapshot if bt is down)
m subnets.sn103/news        # scraped news
m subnets.sn103/trades      # 24h alpha tape
m subnets.sn103/daily       # daily candles
python3 orbit/subnets/sn103/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
