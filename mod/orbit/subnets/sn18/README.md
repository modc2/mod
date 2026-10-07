# sn18 — Zeus σ

Pushing weather forecasts beyond state-of-the-art

Bittensor subnet **18** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/Orpheus-AI/Zeus) · [url](https://www.zeussubnet.com/) · discord `wouter_orpheusai`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003724 | -0.00% | +0.09% | -3.50% | 21,984 | 9,802 | 33.29 |

## Last 24h flow

29 trades by 20 coldkeys · 6 buys (18.93 τ) / 23 sells (13.78 τ) · net 5.15 τ

## News

- 2026-09-07 · release · [Release 2.1.4](https://github.com/Orpheus-AI/Zeus/releases/tag/v2.1.4) — Orpheus-AI/Zeus
- 2026-09-07 · commit · [Stop emissions for non-participating miners (#88)](https://github.com/Orpheus-AI/Zeus/commit/024eb19eeca724aaad135b4492091abf658694ce) — Orpheus-AI/Zeus

## Use

```bash
m subnets.sn18/info        # live identity + market (snapshot if bt is down)
m subnets.sn18/news        # scraped news
m subnets.sn18/trades      # 24h alpha tape
m subnets.sn18/daily       # daily candles
python3 orbit/subnets/sn18/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
