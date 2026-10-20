# sn2 — DaniLabs β

Distributed Accelerated Neural intelligence

Bittensor subnet **2** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/DaniLabsAI/subnet) · [url](https://danilabs.ai/) · discord `opendansor`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003337 | -0.24% | -13.00% | +12.91% | 20,033 | 8,074 | 2,212 |

## Last 24h flow

190 trades by 93 coldkeys · 80 buys (815.30 τ) / 110 sells (1,396 τ) · net -580.22 τ

## Use

```bash
m subnets.sn2/info        # live identity + market (snapshot if bt is down)
m subnets.sn2/news        # scraped news
m subnets.sn2/trades      # 24h alpha tape
m subnets.sn2/daily       # daily candles
python3 orbit/subnets/sn2/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
