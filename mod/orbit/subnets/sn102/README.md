# sn102 — ConnitoAI ვ

Contributors train specialized expert modules that are aggregated into powerful AI systems, without massive centralized compute.

Bittensor subnet **102** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/Connito-AI/Connito) · [url](https://connito.ai/) · discord `isabella618033`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005660 | -0.32% | -1.96% | -6.34% | 9,439 | 3,419 | 265.42 |

## Last 24h flow

43 trades by 20 coldkeys · 19 buys (114.20 τ) / 24 sells (144.10 τ) · net -29.91 τ

## News

- 2026-09-25 · release · [v0.6.4 — the reference miner trains the full model](https://github.com/Connito-AI/Connito/releases/tag/v0.6.4) — Connito-AI/Connito
- 2026-09-25 · commit · [Merge pull request #284 from Connito-AI/staging_v3](https://github.com/Connito-AI/Connito/commit/05eead5d559a4c80b35e5738cd9a6934dd79735c) — Connito-AI/Connito
- 2026-09-25 · commit · [Merge pull request #283 from Connito-AI/feat/miner-full-topology](https://github.com/Connito-AI/Connito/commit/3ee1edec781caa41253a16e16d5e8d0fa825dd0f) — Connito-AI/Connito
- 2026-09-24 · commit · [✨ feat(miner): train the full model the validator scores](https://github.com/Connito-AI/Connito/commit/2b69b446ab3833eef8897ee15e934f2fdc35b448) — Connito-AI/Connito
- 2026-09-21 · release · [v0.6.3](https://github.com/Connito-AI/Connito/releases/tag/v0.6.3) — Connito-AI/Connito
- 2026-09-21 · commit · [Merge pull request #281 from Connito-AI/staging_v3](https://github.com/Connito-AI/Connito/commit/d732f0bb841c0c7b9a510f7dd99f97bdb162053b) — Connito-AI/Connito
- 2026-09-21 · commit · [Merge pull request #280 from Connito-AI/feat/parquet-row-group-seek](https://github.com/Connito-AI/Connito/commit/c713a73931fd0b3cb780c5c88d51d37cf0618d41) — Connito-AI/Connito
- 2026-09-17 · release · [v0.6.2](https://github.com/Connito-AI/Connito/releases/tag/v0.6.2) — Connito-AI/Connito

## Use

```bash
m subnets.sn102/info        # live identity + market (snapshot if bt is down)
m subnets.sn102/news        # scraped news
m subnets.sn102/trades      # 24h alpha tape
m subnets.sn102/daily       # daily candles
python3 orbit/subnets/sn102/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
