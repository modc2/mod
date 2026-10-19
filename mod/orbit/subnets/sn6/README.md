# sn6 — Numinous ζ

Numinous is a forecasting protocol whose goal is to aggregate agents into superhuman LLM forecasters.

Bittensor subnet **6** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/numinouslabs/numinous) · [url](https://numinouslabs.io/) · discord `amedeo_ma`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002225 | — | +0.59% | -3.16% | 13,400 | 7,336 | 108.73 |

## Last 24h flow

25 trades by 17 coldkeys · 8 buys (64.81 τ) / 17 sells (43.28 τ) · net 21.54 τ

## News

- 2026-09-22 · commit · [Merge pull request #55 from numinouslabs/sync-main-2026-09-22-17-30](https://github.com/numinouslabs/numinous/commit/e32cf9dfa0e3f3cb627e262b9f4547e490e08b76) — numinouslabs/numinous
- 2026-09-22 · commit · [Release 2026-09-22-17-30 from main](https://github.com/numinouslabs/numinous/commit/316a0e4c27187f85453c4ed3721581e4e0771e01) — numinouslabs/numinous

## Use

```bash
m subnets.sn6/info        # live identity + market (snapshot if bt is down)
m subnets.sn6/news        # scraped news
m subnets.sn6/trades      # 24h alpha tape
m subnets.sn6/daily       # daily candles
python3 orbit/subnets/sn6/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
