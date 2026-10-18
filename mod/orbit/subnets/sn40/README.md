# sn40 — Ralph ן

 The same model, small enough to run on your phone. Compress the frontier models into GGUFs.

Bittensor subnet **40** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/RalphLabsAI/ralph) · [url](https://ralphlabs.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002006 | -0.00% | -2.03% | -10.43% | 2,341 | 1,304 | 13.46 |

## Last 24h flow

5 trades by 3 coldkeys · 0 buys (0.00 τ) / 5 sells (12.47 τ) · net -12.47 τ

## Use

```bash
m subnets.sn40/info        # live identity + market (snapshot if bt is down)
m subnets.sn40/news        # scraped news
m subnets.sn40/trades      # 24h alpha tape
m subnets.sn40/daily       # daily candles
python3 orbit/subnets/sn40/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
