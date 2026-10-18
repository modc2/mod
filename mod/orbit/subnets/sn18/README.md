# sn18 — Zeus σ

Pushing weather forecasts beyond state-of-the-art

Bittensor subnet **18** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/Orpheus-AI/Zeus) · [url](https://www.zeussubnet.com/) · discord `wouter_orpheusai`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003710 | +0.00% | +0.18% | +0.66% | 22,211 | 9,800 | 27.40 |

## Last 24h flow

20 trades by 18 coldkeys · 3 buys (18.00 τ) / 17 sells (8.57 τ) · net 9.43 τ

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
