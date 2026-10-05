# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.025972 | +0.68% | +4.58% | +9.18% | 57,861 | 14,566 | 4,762 |

## Last 24h flow

347 trades by 175 coldkeys · 204 buys (2,473 τ) / 143 sells (2,196 τ) · net 276.88 τ

## News

- 2026-10-05 · release · [Validator v2.2.0: oro-env-runtime 3.3.0, runtime contract 2 (#362)](https://github.com/ORO-AI/oro/releases/tag/v2.2.0) — ORO-AI/oro
- 2026-10-03 · release · [Validator v2.1.0: runtime contract on claim and delivery load (#361)](https://github.com/ORO-AI/oro/releases/tag/v2.1.0) — ORO-AI/oro
- 2026-10-02 · commit · [Validator v2.0.41: per-event market notices, preflight replay parity …](https://github.com/ORO-AI/oro/commit/35fc09ddd49f51d3898e6a26f19c49269c60e1cf) — ORO-AI/oro
- 2026-10-02 · commit · [chore: remove unused load_problems from subnet.sandbox (#359)](https://github.com/ORO-AI/oro/commit/f3600d1fe6eef8c5476c491bf44df0736c8557f1) — ORO-AI/oro
- 2026-10-02 · commit · [Capture cached input tokens in private evaluation usage](https://github.com/ORO-AI/oro/commit/535d58559ea7ab817310d06992940f5071771516) — ORO-AI/oro
- 2026-10-01 · commit · [Composed situation tasks: validator, proxy and local testing](https://github.com/ORO-AI/oro/commit/8737b4c6e6989193f58ba0a129d865cbb35bcaca) — ORO-AI/oro
- 2026-10-01 · release · [v2.0.40: Composed situation tasks: validator, proxy and local testing](https://github.com/ORO-AI/oro/releases/tag/v2.0.40) — ORO-AI/oro
- 2026-09-30 · commit · [Pin the JDK used by the search index builder and base image](https://github.com/ORO-AI/oro/commit/9eb8525194ccb320733efa2eeada77026c637540) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
