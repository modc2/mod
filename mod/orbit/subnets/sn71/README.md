# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004839 | +0.23% | -0.09% | +28.15% | 28,564 | 5,539 | 1,317 |

## Last 24h flow

196 trades by 78 coldkeys · 122 buys (657.33 τ) / 74 sells (658.76 τ) · net -1.43 τ

## News

- 2026-10-11 · commit · [Merge pull request #371 from leadpoet/codex/oct11-native-settlement-b…](https://github.com/leadpoet/leadpoet/commit/5e71476e2a3d3ea4d67110f11637150a5386ef39) — leadpoet/leadpoet
- 2026-10-11 · commit · [fix(arena): bind native Deepline settlement outage recovery](https://github.com/leadpoet/leadpoet/commit/11c9aa2b303d26f7fc008daed45cfedc7a8dc810) — leadpoet/leadpoet
- 2026-10-11 · commit · [Merge pull request #370 from leadpoet/codex/oct11-broker-persistence-…](https://github.com/leadpoet/leadpoet/commit/65f000891f9a0590315280230ea1b85451a4229f) — leadpoet/leadpoet
- 2026-10-11 · commit · [Report undurable transport accounting without false uncertainty](https://github.com/leadpoet/leadpoet/commit/586b1c42f4413c599044fe38591c86439029ed46) — leadpoet/leadpoet
- 2026-10-11 · commit · [Retain exact provider accounting through transient database outages](https://github.com/leadpoet/leadpoet/commit/514894cc37b0566fb0098db2334ed6bd23518384) — leadpoet/leadpoet
- 2026-10-10 · commit · [Merge pull request #364 from leadpoet/fix/arena-publication-round-wit…](https://github.com/leadpoet/leadpoet/commit/9e50de908fbe1cfad657b2f18101972b1060c2bf) — leadpoet/leadpoet
- 2026-10-10 · commit · [Bind incomplete publication judge witnesses to the current round](https://github.com/leadpoet/leadpoet/commit/d778997334d3b6d1d951b26b5a41a833127034de) — leadpoet/leadpoet
- 2026-10-10 · commit · [Merge pull request #363 from leadpoet/fix/arena-oct10-score-claim-cutoff](https://github.com/leadpoet/leadpoet/commit/2727be91bd2b96bfa21efa3106261a416ae49cae) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
