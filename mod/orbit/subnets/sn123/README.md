# sn123 — MANTIS 𑀀

Incentivizing cooperative prediction

Bittensor subnet **123** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/Barbariandev/MANTIS) · [url](https://mantis123.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002266 | +0.00% | -0.08% | -4.12% | 10,166 | 5,591 | 72.17 |

## Last 24h flow

35 trades by 14 coldkeys · 2 buys (35.00 τ) / 33 sells (36.94 τ) · net -1.94 τ

## News

- 2026-10-02 · commit · [Update bucket_forecast.py](https://github.com/Barbariandev/MANTIS/commit/9fad0e6ae251a60322bf1a05029a0e5689e095e8) — Barbariandev/MANTIS
- 2026-09-30 · commit · [Update ledger.py](https://github.com/Barbariandev/MANTIS/commit/75ee146676150902a770fc6208df5a9663a3a18d) — Barbariandev/MANTIS
- 2026-09-18 · commit · [Update config.py](https://github.com/Barbariandev/MANTIS/commit/3d41501adc82ad1d8d6892a0027b22cf8b04d220) — Barbariandev/MANTIS
- 2026-09-18 · commit · [Update flow.py](https://github.com/Barbariandev/MANTIS/commit/8d9ad06335631ff726c1cc83fd951243b8fd9a17) — Barbariandev/MANTIS
- 2026-09-13 · commit · [Update flow_collateral.py](https://github.com/Barbariandev/MANTIS/commit/663c4b657347be7f1dc240198e40453a99c06089) — Barbariandev/MANTIS

## Use

```bash
m subnets.sn123/info        # live identity + market (snapshot if bt is down)
m subnets.sn123/news        # scraped news
m subnets.sn123/trades      # 24h alpha tape
m subnets.sn123/daily       # daily candles
python3 orbit/subnets/sn123/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
