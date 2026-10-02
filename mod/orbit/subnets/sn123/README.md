# sn123 — MANTIS 𑀀

Incentivizing cooperative prediction

Bittensor subnet **123** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/Barbariandev/MANTIS) · [url](https://mantis123.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002266 | -0.01% | -0.42% | -4.60% | 10,158 | 5,592 | 11.85 |

## Last 24h flow

47 trades by 14 coldkeys · 0 buys (0.00 τ) / 47 sells (11.50 τ) · net -11.50 τ

## News

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
