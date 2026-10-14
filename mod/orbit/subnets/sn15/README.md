# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.022288 | -0.01% | -3.58% | -8.90% | 51,477 | 13,943 | 1,698 |

## Last 24h flow

150 trades by 68 coldkeys · 37 buys (660.95 τ) / 113 sells (945.45 τ) · net -284.50 τ

## News

- 2026-10-13 · commit · [Load v7 and v8 task packs; move to oro-env-runtime 3.7.0 (runtime con…](https://github.com/ORO-AI/oro/commit/1fc79c04ad4a7758e765515e6992b9da0dc886ac) — ORO-AI/oro
- 2026-10-13 · release · [v2.5.0](https://github.com/ORO-AI/oro/releases/tag/v2.5.0) — ORO-AI/oro
- 2026-10-11 · commit · [Fail the configured Reader's Gemini call over to Gemini 3.7 Flash, th…](https://github.com/ORO-AI/oro/commit/5753a1283dbe38f6b3620e67ee0621583363050c) — ORO-AI/oro
- 2026-10-11 · release · [v2.4.1](https://github.com/ORO-AI/oro/releases/tag/v2.4.1) — ORO-AI/oro
- 2026-10-08 · release · [v2.4.0: Prepare runtime 3.5 validator and practice delivery (#367)](https://github.com/ORO-AI/oro/releases/tag/v2.4.0) — ORO-AI/oro
- 2026-10-08 · commit · [Prepare runtime 3.5 validator and practice delivery (#367)](https://github.com/ORO-AI/oro/commit/db6d80af2f8a749afb502caf5a2542ca5ff381a5) — ORO-AI/oro
- 2026-10-07 · release · [v2.3.0](https://github.com/ORO-AI/oro/releases/tag/v2.3.0) — ORO-AI/oro
- 2026-10-07 · commit · [Bind configured Readers to cancellable miner-funded simulator inferen…](https://github.com/ORO-AI/oro/commit/9484727b3f2674dbca49884987cfc91a2058e2c4) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
