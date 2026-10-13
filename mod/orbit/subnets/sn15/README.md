# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.023116 | +0.03% | +2.42% | -9.23% | 53,181 | 14,150 | 632.94 |

## Last 24h flow

131 trades by 60 coldkeys · 31 buys (341.20 τ) / 100 sells (223.42 τ) · net 117.78 τ

## News

- 2026-10-11 · commit · [Fail the configured Reader's Gemini call over to Gemini 3.7 Flash, th…](https://github.com/ORO-AI/oro/commit/5753a1283dbe38f6b3620e67ee0621583363050c) — ORO-AI/oro
- 2026-10-11 · release · [v2.4.1](https://github.com/ORO-AI/oro/releases/tag/v2.4.1) — ORO-AI/oro
- 2026-10-08 · release · [v2.4.0: Prepare runtime 3.5 validator and practice delivery (#367)](https://github.com/ORO-AI/oro/releases/tag/v2.4.0) — ORO-AI/oro
- 2026-10-08 · commit · [Prepare runtime 3.5 validator and practice delivery (#367)](https://github.com/ORO-AI/oro/commit/db6d80af2f8a749afb502caf5a2542ca5ff381a5) — ORO-AI/oro
- 2026-10-07 · release · [v2.3.0](https://github.com/ORO-AI/oro/releases/tag/v2.3.0) — ORO-AI/oro
- 2026-10-07 · commit · [Bind configured Readers to cancellable miner-funded simulator inferen…](https://github.com/ORO-AI/oro/commit/9484727b3f2674dbca49884987cfc91a2058e2c4) — ORO-AI/oro
- 2026-10-05 · release · [Validator v2.2.0: oro-env-runtime 3.3.0, runtime contract 2 (#362)](https://github.com/ORO-AI/oro/releases/tag/v2.2.0) — ORO-AI/oro
- 2026-10-03 · release · [Validator v2.1.0: runtime contract on claim and delivery load (#361)](https://github.com/ORO-AI/oro/releases/tag/v2.1.0) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
