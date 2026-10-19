# sn16 — Zero Hour π

Robot task disruptions and recovery demos for VLA training

Bittensor subnet **16** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/fast-thinker/kenju)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.000928 | — | -16.39% | +0.43% | 920.2324 | 324.2809 | 125.83 |

## Last 24h flow

110 trades by 40 coldkeys · 59 buys (47.75 τ) / 51 sells (77.64 τ) · net -29.89 τ

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
