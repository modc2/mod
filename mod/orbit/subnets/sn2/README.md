# sn2 — DaniLabs β

Distributed Accelerated Neural intelligence

Bittensor subnet **2** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/DaniLabsAI/subnet) · [url](https://danilabs.ai/) · discord `opendansor`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003839 | — | +5.43% | +29.85% | 23,017 | 8,660 | 2,662 |

## Last 24h flow

234 trades by 83 coldkeys · 110 buys (1,444 τ) / 124 sells (1,217 τ) · net 227.43 τ

## Use

```bash
m subnets.sn2/info        # live identity + market (snapshot if bt is down)
m subnets.sn2/news        # scraped news
m subnets.sn2/trades      # 24h alpha tape
m subnets.sn2/daily       # daily candles
python3 orbit/subnets/sn2/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
