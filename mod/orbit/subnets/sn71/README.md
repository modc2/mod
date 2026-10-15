# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005364 | -1.28% | -1.02% | +29.37% | 31,819 | 5,831 | 2,291 |

## Last 24h flow

271 trades by 115 coldkeys · 132 buys (1,131 τ) / 139 sells (1,159 τ) · net -28.08 τ

## News

- 2026-10-15 · commit · [Merge pull request #444 from leadpoet/fix/arena-host-quarantine-paid-…](https://github.com/leadpoet/leadpoet/commit/a1588888aaf6f54aeb617d1a95597f41b2eb3610) — leadpoet/leadpoet
- 2026-10-15 · commit · [Count verified paid host interruptions in round claim quarantine](https://github.com/leadpoet/leadpoet/commit/087b3a1cf571e0c682ec841783c703871ea10742) — leadpoet/leadpoet
- 2026-10-15 · commit · [Merge pull request #443 from leadpoet/codex/arena-continuity-ingress-…](https://github.com/leadpoet/leadpoet/commit/b50f9e9e13d4dcca3ad86e08af3da76a496851d1) — leadpoet/leadpoet
- 2026-10-15 · commit · [Retain the paid review drain regression in source control](https://github.com/leadpoet/leadpoet/commit/9bfd25a6f1f0c8d98626f80b3cce2470e059bf1c) — leadpoet/leadpoet
- 2026-10-15 · commit · [Keep Arena ingress available during canonical gateway restarts](https://github.com/leadpoet/leadpoet/commit/d2d5f66fb3a83cb2f4d71d652f36099d541c3b45) — leadpoet/leadpoet
- 2026-10-14 · commit · [Merge pull request #440 from leadpoet/codex/arena-denial-telemetry-20…](https://github.com/leadpoet/leadpoet/commit/7070fa3b11554a087198e59eab3285ff599007d3) — leadpoet/leadpoet
- 2026-10-14 · commit · [Keep existing Arena refusal reasons visible in telemetry](https://github.com/leadpoet/leadpoet/commit/e538df5ef9544aef5b39b126cc268ea45b1a50cf) — leadpoet/leadpoet
- 2026-10-14 · commit · [Merge pull request #439 from leadpoet/codex/arena-shutdown-lifecycle-…](https://github.com/leadpoet/leadpoet/commit/40503e5218891af2a2e85a1e87aa57398fbbadcd) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
