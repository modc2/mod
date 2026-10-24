# sn123 — MANTIS 𑀀

Incentivizing cooperative prediction

Bittensor subnet **123** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/Barbariandev/MANTIS) · [url](https://mantis123.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002308 | +1.43% | +2.12% | +1.53% | 10,635 | 5,643 | 1,152 |

## Last 24h flow

64 trades by 25 coldkeys · 13 buys (605.69 τ) / 51 sells (545.39 τ) · net 60.30 τ

## News

- 2026-10-02 · commit · [Update bucket_forecast.py](https://github.com/Barbariandev/MANTIS/commit/9fad0e6ae251a60322bf1a05029a0e5689e095e8) — Barbariandev/MANTIS
- 2026-09-30 · commit · [Update ledger.py](https://github.com/Barbariandev/MANTIS/commit/75ee146676150902a770fc6208df5a9663a3a18d) — Barbariandev/MANTIS

## Use

```bash
m subnets.sn123/info        # live identity + market (snapshot if bt is down)
m subnets.sn123/news        # scraped news
m subnets.sn123/trades      # 24h alpha tape
m subnets.sn123/daily       # daily candles
python3 orbit/subnets/sn123/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
