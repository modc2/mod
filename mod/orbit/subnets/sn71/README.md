# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004886 | -1.77% | -8.90% | -0.54% | 29,021 | 5,566 | 4,745 |

## Last 24h flow

422 trades by 149 coldkeys · 197 buys (2,240 τ) / 225 sells (2,513 τ) · net -272.65 τ

## News

- 2026-10-16 · commit · [Merge pull request #476 from leadpoet/fix/presign-opening-client-oct16](https://github.com/leadpoet/leadpoet/commit/368ff0fb8d4a0d35ae42c34f5d8f934bcd9776f0) — leadpoet/leadpoet
- 2026-10-16 · commit · [Cover both halves of the presign opening retry window](https://github.com/leadpoet/leadpoet/commit/1b2b019e77183c8953b2e20a3915a1cb8f1de620) — leadpoet/leadpoet
- 2026-10-16 · commit · [Merge pull request #474 from leadpoet/op/V7jOJiWPpT/presign-cutoff](https://github.com/leadpoet/leadpoet/commit/33970bc121f0c2f7926e0a6b5e4f7db56efef826) — leadpoet/leadpoet
- 2026-10-16 · commit · [Merge pull request #475 from leadpoet/audit/oct16-cycle-independent](https://github.com/leadpoet/leadpoet/commit/6d021b34afbc610a6ef2ca512e9fa0fa86d91065) — leadpoet/leadpoet
- 2026-10-16 · commit · [Document continuous OnePatch coverage and unresolved blockers](https://github.com/leadpoet/leadpoet/commit/af24d01a77fa57e583d96b12c9f52f5a4c50be26) — leadpoet/leadpoet
- 2026-10-15 · commit · [Merge pull request #473 from leadpoet/fix/arena-progression-lock-orde…](https://github.com/leadpoet/leadpoet/commit/30139d6b99d91d523569fe87915695f03e5f1faa) — leadpoet/leadpoet
- 2026-10-15 · commit · [Fix Arena progression lock order and close legacy scoring RPC access](https://github.com/leadpoet/leadpoet/commit/534be4f669cdcbaf12ae425803479d15614fb294) — leadpoet/leadpoet
- 2026-10-15 · commit · [Merge pull request #472 from leadpoet/fix/structured-hq-manifest-oct15](https://github.com/leadpoet/leadpoet/commit/77db393b0bf899c888edd7d3a495aeb0f5f78820) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
