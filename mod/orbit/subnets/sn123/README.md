# sn123 — MANTIS 𑀀

Incentivizing cooperative prediction

Bittensor subnet **123** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/Barbariandev/MANTIS) · [url](https://mantis123.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002259 | +0.00% | +0.10% | -1.07% | 10,376 | 5,583 | 7.69 |

## Last 24h flow

14 trades by 8 coldkeys · 1 buys (5.00 τ) / 13 sells (1.76 τ) · net 3.24 τ

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
