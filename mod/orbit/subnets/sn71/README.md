# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005052 | +0.86% | +3.68% | +36.49% | 29,894 | 5,659 | 1,498 |

## Last 24h flow

225 trades by 91 coldkeys · 128 buys (800.01 τ) / 97 sells (700.81 τ) · net 99.20 τ

## News

- 2026-10-13 · commit · [Merge pull request #407 from leadpoet/fix/oct12-structured-investigat…](https://github.com/leadpoet/leadpoet/commit/40391ea53cbfd7ad152546b7933ca068aca15515) — leadpoet/leadpoet
- 2026-10-13 · commit · [Bind identity review manifest to committed source](https://github.com/leadpoet/leadpoet/commit/8e26e1f5cc87ff9198ea9274dfa4bedde3282feb) — leadpoet/leadpoet
- 2026-10-13 · commit · [Carry independently bound structured identity into company review](https://github.com/leadpoet/leadpoet/commit/512420227f70370634dc1dfa426bb803d4fbd87a) — leadpoet/leadpoet
- 2026-10-13 · commit · [Merge pull request #406 from leadpoet/fix/oct12-diagnostic-page-fallback](https://github.com/leadpoet/leadpoet/commit/667bbf6e533089f154271924cd6cf98b5b6a4f01) — leadpoet/leadpoet
- 2026-10-13 · commit · [Recover diagnostic page bodies through existing exact-source fallback](https://github.com/leadpoet/leadpoet/commit/5b1c4739ccf67e19eda99f6617d16d5d99abf80d) — leadpoet/leadpoet
- 2026-10-12 · commit · [Merge pull request #400: keep resumed duplicate contact verdicts cons…](https://github.com/leadpoet/leadpoet/commit/3ede183d8fe51e1c98b265930c3139d8941574fb) — leadpoet/leadpoet
- 2026-10-12 · commit · [Merge remote-tracking branch 'origin/main' into fix/oct12-retained-du…](https://github.com/leadpoet/leadpoet/commit/8950676c9422805829e16cf321ae31af6ce1e7a9) — leadpoet/leadpoet
- 2026-10-12 · commit · [Merge pull request #399 from leadpoet/fix/oct12-durable-completion](https://github.com/leadpoet/leadpoet/commit/ca1093a7302c4f0ccd7d8780594c07204b7581af) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
