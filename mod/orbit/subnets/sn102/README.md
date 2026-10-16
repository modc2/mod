# sn102 — ConnitoAI ვ

Contributors train specialized expert modules that are aggregated into powerful AI systems, without massive centralized compute.

Bittensor subnet **102** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/Connito-AI/Connito) · [url](https://connito.ai/) · discord `isabella618033`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005457 | -0.62% | -1.38% | -4.21% | 9,549 | 3,387 | 1,218 |

## Last 24h flow

143 trades by 52 coldkeys · 69 buys (595.99 τ) / 74 sells (618.92 τ) · net -22.93 τ

## News

- 2026-10-15 · release · [v0.7.0](https://github.com/Connito-AI/Connito/releases/tag/v0.7.0) — Connito-AI/Connito
- 2026-10-15 · commit · [Merge pull request #295 from Connito-AI/staging_v3](https://github.com/Connito-AI/Connito/commit/aa5e246badee6c50d4f86b8c139febe9e6030036) — Connito-AI/Connito
- 2026-10-14 · commit · [Merge pull request #294 from Connito-AI/feat/qwen-09-isolation-gate](https://github.com/Connito-AI/Connito/commit/f138b14ae8cfe1ba2d5074c076948b18a83d9978) — Connito-AI/Connito
- 2026-10-14 · commit · [Merge pull request #293 from Connito-AI/feat/qwen-10-docs](https://github.com/Connito-AI/Connito/commit/5ffb4577f8814847e8ec7ab74d05dd5bed8491c2) — Connito-AI/Connito
- 2026-10-14 · commit · [Merge pull request #285 from Connito-AI/feat/qwen-01-loader](https://github.com/Connito-AI/Connito/commit/769661f8998ef6165cfd235a0d27d1879ef255e8) — Connito-AI/Connito
- 2026-10-14 · commit · [📝 docs(miner-faq): base usage varies a little more on c4 than on task…](https://github.com/Connito-AI/Connito/commit/44828dcecbe9b0d87baf090763faae6acfe71c90) — Connito-AI/Connito
- 2026-09-25 · release · [v0.6.4 — the reference miner trains the full model](https://github.com/Connito-AI/Connito/releases/tag/v0.6.4) — Connito-AI/Connito
- 2026-09-25 · commit · [Merge pull request #284 from Connito-AI/staging_v3](https://github.com/Connito-AI/Connito/commit/05eead5d559a4c80b35e5738cd9a6934dd79735c) — Connito-AI/Connito

## Use

```bash
m subnets.sn102/info        # live identity + market (snapshot if bt is down)
m subnets.sn102/news        # scraped news
m subnets.sn102/trades      # 24h alpha tape
m subnets.sn102/daily       # daily candles
python3 orbit/subnets/sn102/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
