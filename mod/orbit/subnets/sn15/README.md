# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.023257 | -0.86% | -1.67% | -5.72% | 52,652 | 13,993 | 711.59 |

## Last 24h flow

128 trades by 63 coldkeys · 40 buys (234.07 τ) / 88 sells (407.65 τ) · net -173.59 τ

## News

- 2026-10-08 · release · [v2.4.0: Prepare runtime 3.5 validator and practice delivery (#367)](https://github.com/ORO-AI/oro/releases/tag/v2.4.0) — ORO-AI/oro
- 2026-10-08 · commit · [Prepare runtime 3.5 validator and practice delivery (#367)](https://github.com/ORO-AI/oro/commit/db6d80af2f8a749afb502caf5a2542ca5ff381a5) — ORO-AI/oro
- 2026-10-07 · release · [v2.3.0](https://github.com/ORO-AI/oro/releases/tag/v2.3.0) — ORO-AI/oro
- 2026-10-07 · commit · [Bind configured Readers to cancellable miner-funded simulator inferen…](https://github.com/ORO-AI/oro/commit/9484727b3f2674dbca49884987cfc91a2058e2c4) — ORO-AI/oro
- 2026-10-05 · release · [Validator v2.2.0: oro-env-runtime 3.3.0, runtime contract 2 (#362)](https://github.com/ORO-AI/oro/releases/tag/v2.2.0) — ORO-AI/oro
- 2026-10-03 · release · [Validator v2.1.0: runtime contract on claim and delivery load (#361)](https://github.com/ORO-AI/oro/releases/tag/v2.1.0) — ORO-AI/oro
- 2026-10-02 · commit · [Validator v2.0.41: per-event market notices, preflight replay parity …](https://github.com/ORO-AI/oro/commit/35fc09ddd49f51d3898e6a26f19c49269c60e1cf) — ORO-AI/oro
- 2026-10-02 · commit · [chore: remove unused load_problems from subnet.sandbox (#359)](https://github.com/ORO-AI/oro/commit/f3600d1fe6eef8c5476c491bf44df0736c8557f1) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
