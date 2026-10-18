# sn16 — Zero Hour π

Robot task disruptions and recovery demos for VLA training

Bittensor subnet **16** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/fast-thinker/kenju)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.001110 | -0.00% | +9.33% | +22.24% | 1,093 | 354.6292 | 295.86 |

## Last 24h flow

78 trades by 43 coldkeys · 38 buys (155.70 τ) / 40 sells (133.02 τ) · net 22.68 τ

## News

- 2026-10-17 · commit · [skeleton](https://github.com/fast-thinker/kenju/commit/92ce8a31e08860c98791f48cebde8c189a86e284) — fast-thinker/kenju
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
