# sn125 — Refinery 𑀂

Incentivizing the improvement of the algorithms behind a pretraining run

Bittensor subnet **125** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/Barbariandev/refinery) · [url](https://refinery125.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002751 | +0.00% | -0.27% | -4.53% | 12,424 | 5,446 | 7.48 |

## Last 24h flow

5 trades by 4 coldkeys · 0 buys (0.00 τ) / 5 sells (7.20 τ) · net -7.20 τ

## News

- 2026-09-17 · commit · [Add files via upload](https://github.com/Barbariandev/Refinery/commit/a6a9cc202ccd36d5564da0b0dd2cd555447d1991) — Barbariandev/refinery

## Use

```bash
m subnets.sn125/info        # live identity + market (snapshot if bt is down)
m subnets.sn125/news        # scraped news
m subnets.sn125/trades      # 24h alpha tape
m subnets.sn125/daily       # daily candles
python3 orbit/subnets/sn125/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
