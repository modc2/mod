# sn102 — ConnitoAI ვ

Contributors train specialized expert modules that are aggregated into powerful AI systems, without massive centralized compute.

Bittensor subnet **102** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/Connito-AI/Connito) · [url](https://connito.ai/) · discord `isabella618033`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005479 | -0.00% | +0.13% | -6.20% | 9,667 | 3,399 | 177.48 |

## Last 24h flow

42 trades by 26 coldkeys · 14 buys (88.73 τ) / 28 sells (85.86 τ) · net 2.87 τ

## News

- 2026-10-16 · release · [v0.7.1](https://github.com/Connito-AI/Connito/releases/tag/v0.7.1) — Connito-AI/Connito
- 2026-10-16 · commit · [Merge pull request #298 from Connito-AI/staging_v3](https://github.com/Connito-AI/Connito/commit/6fa4ac3e08daf670ea37289b47c2a9387e7aa2c4) — Connito-AI/Connito
- 2026-10-16 · commit · [Merge pull request #297 from Connito-AI/fix/freeze-chain-read](https://github.com/Connito-AI/Connito/commit/2fab8c41d614c954f0cc918c19b61e7b49c40f11) — Connito-AI/Connito
- 2026-10-15 · commit · [✅ test(validator): after a skipped round, stale weights take the fall…](https://github.com/Connito-AI/Connito/commit/73cb3ef11346f4946b87a8f0f33614d4b6eb19d0) — Connito-AI/Connito
- 2026-10-15 · commit · [🐛 fix(validator): skip the Validate and Merge waits for a skipped round](https://github.com/Connito-AI/Connito/commit/70b187c053f4d9f8ff093cb4ea3027296c879da4) — Connito-AI/Connito
- 2026-10-15 · commit · [✅ test(validator): a skip past Merge reaches ValidatorCommit, and the…](https://github.com/Connito-AI/Connito/commit/d5ad6da6a5f7bd4e3c74f14bed8db281be52dd57) — Connito-AI/Connito
- 2026-10-15 · release · [v0.7.0](https://github.com/Connito-AI/Connito/releases/tag/v0.7.0) — Connito-AI/Connito
- 2026-10-15 · commit · [Merge pull request #295 from Connito-AI/staging_v3](https://github.com/Connito-AI/Connito/commit/aa5e246badee6c50d4f86b8c139febe9e6030036) — Connito-AI/Connito

## Use

```bash
m subnets.sn102/info        # live identity + market (snapshot if bt is down)
m subnets.sn102/news        # scraped news
m subnets.sn102/trades      # 24h alpha tape
m subnets.sn102/daily       # daily candles
python3 orbit/subnets/sn102/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
