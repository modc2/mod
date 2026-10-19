# sn111 — Claims Ё

Turning scientific literature into a structured claim-evidence graph

Bittensor subnet **111** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/DeSciClaims/Claims) · [discord](https://discord.com/channels/799672011265015819/1515007366016401599)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004054 | — | -0.21% | -6.47% | 20,362 | 5,441 | 6.65 |

## Last 24h flow

50 trades by 29 coldkeys · 3 buys (0.53 τ) / 47 sells (5.91 τ) · net -5.38 τ

## News

- 2026-10-15 · commit · [fix(consensus): log finalization status for voided rounds](https://github.com/DeSciClaims/Claims/commit/41573a43589f96ce27746e24426c3f025134ecae) — DeSciClaims/Claims
- 2026-10-13 · commit · [Docs: update agent_v1 vs section_context_v1 schema](https://github.com/DeSciClaims/Claims/commit/84d9f5f1ac7ca480c90ff529ae114d2f3902b02b) — DeSciClaims/Claims
- 2026-10-13 · commit · [Scripts: add eligibility benchmark replay](https://github.com/DeSciClaims/Claims/commit/729d321a81ca57bb04fc32257e64c0d29c01de2e) — DeSciClaims/Claims
- 2026-10-08 · commit · [feat(validator): route verified operator miner allocation](https://github.com/DeSciClaims/Claims/commit/650898a510048c5a092e2f576e4fe67dbf73d5b1) — DeSciClaims/Claims
- 2026-10-06 · commit · [feat(validator): default miner burn to 90 percent](https://github.com/DeSciClaims/Claims/commit/6b4a427c58b524c826b6dfc32e82f3092cfe2d25) — DeSciClaims/Claims
- 2026-10-06 · commit · [fix(validator): scope split audit repairs to expected draft units](https://github.com/DeSciClaims/Claims/commit/366d4797f405677635935675c6b562aea529d341) — DeSciClaims/Claims
- 2026-10-06 · commit · [feat(validator): support configurable owner miner burn](https://github.com/DeSciClaims/Claims/commit/2ab7dda3311929e687484345190c4d0a6c37cdf5) — DeSciClaims/Claims
- 2026-10-02 · release · [v1.0.1](https://github.com/DeSciClaims/Claims/releases/tag/v1.0.1) — DeSciClaims/Claims

## Use

```bash
m subnets.sn111/info        # live identity + market (snapshot if bt is down)
m subnets.sn111/news        # scraped news
m subnets.sn111/trades      # 24h alpha tape
m subnets.sn111/daily       # daily candles
python3 orbit/subnets/sn111/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
