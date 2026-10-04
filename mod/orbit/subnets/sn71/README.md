# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003776 | -0.71% | -1.40% | -5.82% | 22,099 | 4,893 | 447.35 |

## Last 24h flow

81 trades by 46 coldkeys · 43 buys (206.40 τ) / 38 sells (240.16 τ) · net -33.76 τ

## News

- 2026-10-04 · commit · [Merge pull request #207 from leadpoet/codex/arena-execute-host-cooldo…](https://github.com/leadpoet/leadpoet/commit/98e2f6291c48dc01bb5708d776b8f3cf4ef7e536) — leadpoet/leadpoet
- 2026-10-04 · commit · [Cool down repeated zero-call execute host failures](https://github.com/leadpoet/leadpoet/commit/215eb9f90da56b199e45449f9473a120253364b2) — leadpoet/leadpoet
- 2026-10-04 · commit · [Merge pull request #205 from leadpoet/codex/daily-miner-admission](https://github.com/leadpoet/leadpoet/commit/7f2d9f3b738ea1b2bf26bbfb21136baad38f6fc2) — leadpoet/leadpoet
- 2026-10-04 · commit · [Recover only capacity-rejected uploads when lifting admission cap](https://github.com/leadpoet/leadpoet/commit/593f9b9be69a0e752055585e26c8952c7fc2832e) — leadpoet/leadpoet
- 2026-10-04 · commit · [Reopen eligible capacity rejections after Arena cap expansion](https://github.com/leadpoet/leadpoet/commit/3cb272cd1f73b44701a7d383a067722fb28c1b74) — leadpoet/leadpoet
- 2026-10-03 · commit · [Bind release manifest to related article extraction fix](https://github.com/leadpoet/leadpoet/commit/7acec44b18dd64d845157a438b1f12835c5d8136) — leadpoet/leadpoet
- 2026-10-03 · commit · [Exclude related news widgets from article evidence text](https://github.com/leadpoet/leadpoet/commit/a273828ab9a19fdebfadf2818ffaa57197363622) — leadpoet/leadpoet
- 2026-10-03 · commit · [Bind release manifest to observed overview navigation fix](https://github.com/leadpoet/leadpoet/commit/8a4ac01c10af96e8050ef775be0f836bdb383c9c) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
