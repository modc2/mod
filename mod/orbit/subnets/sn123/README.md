# sn123 — MANTIS 𑀀

Incentivizing cooperative prediction

Bittensor subnet **123** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/Barbariandev/MANTIS) · [url](https://mantis123.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002268 | -0.00% | -0.22% | +0.02% | 10,353 | 5,594 | 74.21 |

## Last 24h flow

19 trades by 15 coldkeys · 5 buys (34.01 τ) / 14 sells (39.73 τ) · net -5.72 τ

## News

- 2026-10-02 · commit · [Update bucket_forecast.py](https://github.com/Barbariandev/MANTIS/commit/9fad0e6ae251a60322bf1a05029a0e5689e095e8) — Barbariandev/MANTIS
- 2026-09-30 · commit · [Update ledger.py](https://github.com/Barbariandev/MANTIS/commit/75ee146676150902a770fc6208df5a9663a3a18d) — Barbariandev/MANTIS
- 2026-09-18 · commit · [Update config.py](https://github.com/Barbariandev/MANTIS/commit/3d41501adc82ad1d8d6892a0027b22cf8b04d220) — Barbariandev/MANTIS
- 2026-09-18 · commit · [Update flow.py](https://github.com/Barbariandev/MANTIS/commit/8d9ad06335631ff726c1cc83fd951243b8fd9a17) — Barbariandev/MANTIS

## Use

```bash
m subnets.sn123/info        # live identity + market (snapshot if bt is down)
m subnets.sn123/news        # scraped news
m subnets.sn123/trades      # 24h alpha tape
m subnets.sn123/daily       # daily candles
python3 orbit/subnets/sn123/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
