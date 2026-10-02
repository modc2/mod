# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.024394 | -0.22% | +2.80% | +0.23% | 53,802 | 13,999 | 1,159 |

## Last 24h flow

170 trades by 90 coldkeys · 70 buys (637.15 τ) / 100 sells (452.13 τ) · net 185.02 τ

## News

- 2026-10-01 · commit · [Composed situation tasks: validator, proxy and local testing](https://github.com/ORO-AI/oro/commit/8737b4c6e6989193f58ba0a129d865cbb35bcaca) — ORO-AI/oro
- 2026-10-01 · release · [v2.0.40: Composed situation tasks: validator, proxy and local testing](https://github.com/ORO-AI/oro/releases/tag/v2.0.40) — ORO-AI/oro
- 2026-09-30 · commit · [Pin the JDK used by the search index builder and base image](https://github.com/ORO-AI/oro/commit/9eb8525194ccb320733efa2eeada77026c637540) — ORO-AI/oro
- 2026-09-30 · commit · [Translate live Chutes model IDs on OpenRouter runs (#345)](https://github.com/ORO-AI/oro/commit/d1ca50d692abd9397b9d411267cac04573720467) — ORO-AI/oro
- 2026-09-30 · release · [v2.0.39: Translate live Chutes model IDs on OpenRouter runs (#345)](https://github.com/ORO-AI/oro/releases/tag/v2.0.39) — ORO-AI/oro
- 2026-09-29 · commit · [fix(agent): retry a 200 inference response whose body is not JSON (#348)](https://github.com/ORO-AI/oro/commit/a8b890d8ef25f6af8d355f6851b05c7f6cb18d1e) — ORO-AI/oro
- 2026-09-29 · release · [v2.0.38: fix(agent): retry a 200 inference response whose body is not JSON (#348)](https://github.com/ORO-AI/oro/releases/tag/v2.0.38) — ORO-AI/oro
- 2026-09-29 · commit · [fix(proxy): sum inference counters across ProxyClient instances (#347)](https://github.com/ORO-AI/oro/commit/bc9a2d54672b2a3369f7416e61e1e404e2a2820b) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
