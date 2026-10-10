# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004843 | -0.33% | -1.42% | +26.47% | 28,555 | 5,541 | 2,461 |

## Last 24h flow

351 trades by 126 coldkeys · 182 buys (1,231 τ) / 169 sells (1,249 τ) · net -17.98 τ

## News

- 2026-10-10 · commit · [Merge pull request #323 from leadpoet/fix/arena-issuer-ticker-oct10](https://github.com/leadpoet/leadpoet/commit/a2e0e67f6c37754a76d2fcf4bfe4ad9219813e76) — leadpoet/leadpoet
- 2026-10-10 · commit · [Prioritize submitted launch and hiring sources for required attributes](https://github.com/leadpoet/leadpoet/commit/e5f2cf32f0d49062ecd1461416663f4422529ab6) — leadpoet/leadpoet
- 2026-10-10 · commit · [Preserve issuer ticker proof across whitespace and quoted aliases](https://github.com/leadpoet/leadpoet/commit/7652cf9f663c9986bd089451cb35ff66aca5143a) — leadpoet/leadpoet
- 2026-10-10 · commit · [Merge pull request #322 from leadpoet/codex/arena-partial-score-deadl…](https://github.com/leadpoet/leadpoet/commit/13924c8af62b9e89ed3c304034ecf6496ac48768) — leadpoet/leadpoet
- 2026-10-10 · commit · [Wait for partial baseline proof deadline without driver error](https://github.com/leadpoet/leadpoet/commit/92bb0234f47c6ec1a7132ae5ad9dbb7701a80a26) — leadpoet/leadpoet
- 2026-10-09 · commit · [Merge pull request #315 from leadpoet/fix/public-current-read-oct09](https://github.com/leadpoet/leadpoet/commit/bb2ff92f880bea1f16e4e8949e8078b89f975f42) — leadpoet/leadpoet
- 2026-10-09 · commit · [fix(arena): narrow public current configuration reads](https://github.com/leadpoet/leadpoet/commit/5c9fe361639d9f3b8645910d97f251ff9a0c26d3) — leadpoet/leadpoet
- 2026-10-09 · commit · [Merge pull request #314 from leadpoet/fix/competition-summary-read-oct09](https://github.com/leadpoet/leadpoet/commit/fb24c74fb6abf55ac4c9250825ae26e78ef22229) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
