# sn22 — Desearch χ

Decentralized search engine

Bittensor subnet **22** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/datura-ai/desearch) · [url](https://desearch.ai) · [discord](https://discord.gg/P44zrJmdFy)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003030 | -0.01% | -0.54% | +2.80% | 18,798 | 7,555 | 75.11 |

## Last 24h flow

141 trades by 21 coldkeys · 5 buys (27.10 τ) / 136 sells (46.81 τ) · net -19.71 τ

## News

- 2026-10-05 · commit · [Merge pull request #383 from Desearch-ai/hotfix/publish-pool](https://github.com/Desearch-ai/subnet-22/commit/7a2062746809aa6ac2728687cf9c3fac38b45a36) — datura-ai/desearch
- 2026-10-05 · commit · [fix(task-api): judge completions when they reach the API, run storage…](https://github.com/Desearch-ai/subnet-22/commit/ab06267d1914f08a1b43d8aef06c227ef22775b5) — datura-ai/desearch
- 2026-10-05 · commit · [Merge pull request #382 from Desearch-ai/hotfix/settle-speed](https://github.com/Desearch-ai/subnet-22/commit/45e848cd138220d8e043fb7a35e227f804e25d3d) — datura-ai/desearch
- 2026-10-05 · commit · [fix(task-api): settle uploads in parallel reading each seed block onc…](https://github.com/Desearch-ai/subnet-22/commit/e12a068b4bb4e01fe2547daf59b013109759ef4f) — datura-ai/desearch
- 2026-10-05 · commit · [Merge pull request #381 from Desearch-ai/feat/crawl-scale](https://github.com/Desearch-ai/subnet-22/commit/237ca4541ac4bf91a9d79f23c43e11b113197997) — datura-ai/desearch
- 2026-10-01 · commit · [Merge pull request #380 from Desearch-ai/develop](https://github.com/Desearch-ai/subnet-22/commit/a88c51d510b9f69186ee221dac0c1ae7be54e29f) — datura-ai/desearch
- 2026-10-01 · commit · [chore: update version to 0.0.232](https://github.com/Desearch-ai/subnet-22/commit/7845cb041b2b647ed78a59c4f7bead01d27de0e4) — datura-ai/desearch
- 2026-10-01 · commit · [fix(validators): take an upload before waiting on its seed, so parall…](https://github.com/Desearch-ai/subnet-22/commit/fbf05aa791c323b9b87e7ef3c73bd140a29ded0f) — datura-ai/desearch

## Use

```bash
m subnets.sn22/info        # live identity + market (snapshot if bt is down)
m subnets.sn22/news        # scraped news
m subnets.sn22/trades      # 24h alpha tape
m subnets.sn22/daily       # daily candles
python3 orbit/subnets/sn22/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
