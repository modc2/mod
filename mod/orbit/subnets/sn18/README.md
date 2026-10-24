# sn18 — Zeus σ

Pushing weather forecasts beyond state-of-the-art

Bittensor subnet **18** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/Orpheus-AI/Zeus) · [url](https://www.zeussubnet.com/) · discord `wouter_orpheusai`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003724 | -0.00% | -0.90% | -0.50% | 22,345 | 9,820 | 378.08 |

## Last 24h flow

48 trades by 26 coldkeys · 18 buys (164.95 τ) / 30 sells (209.77 τ) · net -44.82 τ

## News

- 2026-10-15 · commit · [change reward function to win rate (#92)](https://github.com/Orpheus-AI/Zeus/commit/76a79ddee422ad613693987fb9f693d46bae9ab8) — Orpheus-AI/Zeus

## Use

```bash
m subnets.sn18/info        # live identity + market (snapshot if bt is down)
m subnets.sn18/news        # scraped news
m subnets.sn18/trades      # 24h alpha tape
m subnets.sn18/daily       # daily candles
python3 orbit/subnets/sn18/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
