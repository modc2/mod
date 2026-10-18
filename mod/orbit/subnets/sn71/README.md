# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004441 | -0.71% | -5.39% | -8.23% | 26,438 | 5,306 | 1,535 |

## Last 24h flow

157 trades by 81 coldkeys · 69 buys (693.28 τ) / 88 sells (834.13 τ) · net -140.85 τ

## News

- 2026-10-18 · commit · [Prune superseded Deepline score billing heads before V3 candidate wor…](https://github.com/leadpoet/leadpoet/commit/2ae20ac465ffb4467a56ea88f9372082998cc4f9) — leadpoet/leadpoet
- 2026-10-18 · commit · [Capture runsc child fatal logs through bounded stderr (#527)](https://github.com/leadpoet/leadpoet/commit/c6e6fd314bb53f2471db185e5e3cd84e011ce498) — leadpoet/leadpoet
- 2026-10-18 · commit · [Preserve bounded Deepline execute responses above 1 MiB (#526)](https://github.com/leadpoet/leadpoet/commit/c5235af0f3e380b1953bf80725c477913aea34c8) — leadpoet/leadpoet
- 2026-10-17 · commit · [Expose billing lookup and training-capture failures in Arena telemetr…](https://github.com/leadpoet/leadpoet/commit/5cf0defe7df798a8b411778c438864b2fd45d7a9) — leadpoet/leadpoet
- 2026-10-17 · commit · [fix(arena): require worker response to fill training export gap (#524)](https://github.com/leadpoet/leadpoet/commit/f7096f9b1e224a935de42fbbd8991b830f6c118b) — leadpoet/leadpoet
- 2026-10-17 · commit · [Update README.md](https://github.com/leadpoet/leadpoet/commit/9e01c8d5479fbd8ffdd91a05bb121c17c51c9a44) — leadpoet/leadpoet
- 2026-10-17 · commit · [fix(arena): bound training exports separately from requests (#522)](https://github.com/leadpoet/leadpoet/commit/acc4771235cf9076cc72fd8d796c8a9709d8c175) — leadpoet/leadpoet
- 2026-10-17 · commit · [fix(arena): allow bounded receipt reads for submission costs (#521)](https://github.com/leadpoet/leadpoet/commit/f1dd3d4ea64636490c5e9033600163fd1e95a415) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
