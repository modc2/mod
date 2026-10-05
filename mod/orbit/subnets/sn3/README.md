# sn3 — Teutonic γ

Coordinated Learning

Bittensor subnet **3** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/unarbos/teutonic) · [url](https://www.teutonic.ai/) · discord `@unarbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.025705 | -0.16% | -0.41% | -12.94% | 159,484 | 76,068 | 743.04 |

## Last 24h flow

63 trades by 42 coldkeys · 28 buys (196.99 τ) / 35 sells (438.46 τ) · net -241.47 τ

## News

- 2026-09-24 · commit · [Code benchmarks naming change](https://github.com/unarbos/teutonic/commit/5479810a8c8b8635ede24e5be93515ffa9db4c6f) — unarbos/teutonic
- 2026-09-24 · commit · [Add code benchmarks to dashboard](https://github.com/unarbos/teutonic/commit/3c8631ed7ea5a4549e746c9f73749e45c325d47e) — unarbos/teutonic
- 2026-09-22 · commit · [Update benchmark specifications and improve key handling in dashboard](https://github.com/unarbos/teutonic/commit/124c66bc5db5ae6531cf3cf1b5e706fb0e6ca505) — unarbos/teutonic
- 2026-09-21 · commit · [Adjust delta and sample count](https://github.com/unarbos/teutonic/commit/544b99bea10254f44072399263adc3469e064354) — unarbos/teutonic
- 2026-09-21 · commit · [Set loss chart scale from the initial value](https://github.com/unarbos/teutonic/commit/a90d314da0302fe5f70121aaba1f5f1e110b4866) — unarbos/teutonic

## Use

```bash
m subnets.sn3/info        # live identity + market (snapshot if bt is down)
m subnets.sn3/news        # scraped news
m subnets.sn3/trades      # 24h alpha tape
m subnets.sn3/daily       # daily candles
python3 orbit/subnets/sn3/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
