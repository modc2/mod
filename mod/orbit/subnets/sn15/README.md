# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.023652 | +0.17% | -3.33% | -1.12% | 53,331 | 14,060 | 1,539 |

## Last 24h flow

168 trades by 88 coldkeys · 56 buys (582.66 τ) / 112 sells (882.74 τ) · net -300.09 τ

## News

- 2026-10-07 · release · [v2.3.0](https://github.com/ORO-AI/oro/releases/tag/v2.3.0) — ORO-AI/oro
- 2026-10-07 · commit · [Bind configured Readers to cancellable miner-funded simulator inferen…](https://github.com/ORO-AI/oro/commit/9484727b3f2674dbca49884987cfc91a2058e2c4) — ORO-AI/oro
- 2026-10-05 · release · [Validator v2.2.0: oro-env-runtime 3.3.0, runtime contract 2 (#362)](https://github.com/ORO-AI/oro/releases/tag/v2.2.0) — ORO-AI/oro
- 2026-10-03 · release · [Validator v2.1.0: runtime contract on claim and delivery load (#361)](https://github.com/ORO-AI/oro/releases/tag/v2.1.0) — ORO-AI/oro
- 2026-10-02 · commit · [Validator v2.0.41: per-event market notices, preflight replay parity …](https://github.com/ORO-AI/oro/commit/35fc09ddd49f51d3898e6a26f19c49269c60e1cf) — ORO-AI/oro
- 2026-10-02 · commit · [chore: remove unused load_problems from subnet.sandbox (#359)](https://github.com/ORO-AI/oro/commit/f3600d1fe6eef8c5476c491bf44df0736c8557f1) — ORO-AI/oro
- 2026-10-02 · commit · [Capture cached input tokens in private evaluation usage](https://github.com/ORO-AI/oro/commit/535d58559ea7ab817310d06992940f5071771516) — ORO-AI/oro
- 2026-10-01 · commit · [Composed situation tasks: validator, proxy and local testing](https://github.com/ORO-AI/oro/commit/8737b4c6e6989193f58ba0a129d865cbb35bcaca) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
