# sn16 — kenju run next week π

Robot task disruptions and recovery demos for VLA training

Bittensor subnet **16** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/fast-thinker/kenju)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.001002 | +0.00% | -3.39% | +17.07% | 979.6372 | 336.9316 | 239.75 |

## Last 24h flow

177 trades by 43 coldkeys · 115 buys (116.97 τ) / 62 sells (121.82 τ) · net -4.85 τ

## News

- 2026-10-15 · commit · [first commit](https://github.com/fast-thinker/kenju/commit/055c22a2a5b4d8e03fa2e07949b7bf52f096a5de) — fast-thinker/kenju

## Use

```bash
m subnets.sn16/info        # live identity + market (snapshot if bt is down)
m subnets.sn16/news        # scraped news
m subnets.sn16/trades      # 24h alpha tape
m subnets.sn16/daily       # daily candles
python3 orbit/subnets/sn16/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
