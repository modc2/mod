# sn101 — Tag101 ე

Tag101 is a Bittensor subnet for decentralized social post tagging

Bittensor subnet **101** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/tag101-ai/tag101) · [url](http://tag101.ai) · discord `Tag101`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003688 | -0.00% | -0.08% | -1.30% | 13,200 | 3,572 | 1.51 |

## Last 24h flow

9 trades by 8 coldkeys · 4 buys (0.01 τ) / 5 sells (0.92 τ) · net -0.91 τ

## Use

```bash
m subnets.sn101/info        # live identity + market (snapshot if bt is down)
m subnets.sn101/news        # scraped news
m subnets.sn101/trades      # 24h alpha tape
m subnets.sn101/daily       # daily candles
python3 orbit/subnets/sn101/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
