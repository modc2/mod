# sn3 — Teutonic γ

Coordinated Learning

Bittensor subnet **3** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/unarbos/teutonic) · [url](https://www.teutonic.ai/) · discord `@unarbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.025499 | -0.35% | -7.51% | -14.42% | 157,673 | 75,697 | 10,382 |

## Last 24h flow

291 trades by 134 coldkeys · 162 buys (3,554 τ) / 129 sells (6,686 τ) · net -3,132 τ

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
