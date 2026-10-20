# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.020720 | +0.22% | -0.20% | -10.37% | 48,983 | 13,711 | 602.12 |

## Last 24h flow

76 trades by 45 coldkeys · 21 buys (243.67 τ) / 55 sells (297.86 τ) · net -54.20 τ

## News

- 2026-10-19 · commit · [fix: use validator lockfile for local tests and CI](https://github.com/ORO-AI/oro/commit/22d40b2aa86012a931e5dc1539bcf28581ae4d09) — ORO-AI/oro
- 2026-10-18 · commit · [chore: remove dead weight-overlay code from backend_client (#376)](https://github.com/ORO-AI/oro/commit/ee953c18c8d57d8fe69c38d10f657457f3b161f1) — ORO-AI/oro
- 2026-10-18 · commit · [docs: update stale pack digest and runtime in miner-guide sample run …](https://github.com/ORO-AI/oro/commit/4ecc0cb737a17884a2a9c3f4de436b06ed0d0848) — ORO-AI/oro
- 2026-10-18 · commit · [fix(validator): send sandbox_metadata when retrying a completion (#352)](https://github.com/ORO-AI/oro/commit/34ec328dcdfb3631235b20708cd66041b625ebae) — ORO-AI/oro
- 2026-10-18 · commit · [fix(validator): treat a dropped backend connection as transient (#351)](https://github.com/ORO-AI/oro/commit/b071bb9fe4b24621392ea6814dfcdaaec6995502) — ORO-AI/oro
- 2026-10-18 · commit · [fix(validator): 43 of 45 sealed pack rejection reasons were logged wi…](https://github.com/ORO-AI/oro/commit/273392c54f03c69cab08a9a986eb958805002539) — ORO-AI/oro
- 2026-10-17 · release · [v2.5.6: Bound per-evaluation sandbox disk use](https://github.com/ORO-AI/oro/releases/tag/v2.5.6) — ORO-AI/oro
- 2026-10-17 · commit · [Bound validator reads of sandbox output files (#378)](https://github.com/ORO-AI/oro/commit/e61cc7f3bae867104e3f937af100922b3f67f288) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
