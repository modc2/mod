# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.024519 | -0.02% | -0.61% | +0.51% | 54,181 | 14,043 | 601.56 |

## Last 24h flow

157 trades by 79 coldkeys · 37 buys (255.53 τ) / 120 sells (294.16 τ) · net -38.63 τ

## News

- 2026-10-02 · commit · [Validator v2.0.41: per-event market notices, preflight replay parity …](https://github.com/ORO-AI/oro/commit/35fc09ddd49f51d3898e6a26f19c49269c60e1cf) — ORO-AI/oro
- 2026-10-02 · commit · [chore: remove unused load_problems from subnet.sandbox (#359)](https://github.com/ORO-AI/oro/commit/f3600d1fe6eef8c5476c491bf44df0736c8557f1) — ORO-AI/oro
- 2026-10-02 · commit · [Capture cached input tokens in private evaluation usage](https://github.com/ORO-AI/oro/commit/535d58559ea7ab817310d06992940f5071771516) — ORO-AI/oro
- 2026-10-01 · commit · [Composed situation tasks: validator, proxy and local testing](https://github.com/ORO-AI/oro/commit/8737b4c6e6989193f58ba0a129d865cbb35bcaca) — ORO-AI/oro
- 2026-10-01 · release · [v2.0.40: Composed situation tasks: validator, proxy and local testing](https://github.com/ORO-AI/oro/releases/tag/v2.0.40) — ORO-AI/oro
- 2026-09-30 · commit · [Pin the JDK used by the search index builder and base image](https://github.com/ORO-AI/oro/commit/9eb8525194ccb320733efa2eeada77026c637540) — ORO-AI/oro
- 2026-09-30 · commit · [Translate live Chutes model IDs on OpenRouter runs (#345)](https://github.com/ORO-AI/oro/commit/d1ca50d692abd9397b9d411267cac04573720467) — ORO-AI/oro
- 2026-09-30 · release · [v2.0.39: Translate live Chutes model IDs on OpenRouter runs (#345)](https://github.com/ORO-AI/oro/releases/tag/v2.0.39) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
