# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021136 | +0.00% | +0.59% | -8.03% | 49,577 | 13,758 | 365.33 |

## Last 24h flow

82 trades by 55 coldkeys · 53 buys (154.81 τ) / 29 sells (158.02 τ) · net -3.21 τ

## News

- 2026-10-17 · commit · [Bound validator reads of sandbox output files (#378)](https://github.com/ORO-AI/oro/commit/e61cc7f3bae867104e3f937af100922b3f67f288) — ORO-AI/oro
- 2026-10-17 · release · [v2.5.5: Bound validator reads of sandbox output files (#378)](https://github.com/ORO-AI/oro/releases/tag/v2.5.5) — ORO-AI/oro
- 2026-10-17 · release · [v2.5.4](https://github.com/ORO-AI/oro/releases/tag/v2.5.4) — ORO-AI/oro
- 2026-10-17 · commit · [Give a grouped tool call its timeout per action, up to 30s](https://github.com/ORO-AI/oro/commit/2d372b8e4b552dff0b37e03120106f8500c683ca) — ORO-AI/oro
- 2026-10-15 · release · [v2.5.3](https://github.com/ORO-AI/oro/releases/tag/v2.5.3) — ORO-AI/oro
- 2026-10-15 · commit · [Retry configured Reader decisions like the shopper's chat call (#375)](https://github.com/ORO-AI/oro/commit/8c0921276c0b59fd9e85cb6a0815e362480a3acf) — ORO-AI/oro
- 2026-10-15 · release · [v2.5.2](https://github.com/ORO-AI/oro/releases/tag/v2.5.2) — ORO-AI/oro
- 2026-10-15 · commit · [Treat a miner's OpenRouter credit 402 as an exhausted run key (#374)](https://github.com/ORO-AI/oro/commit/b4612a450f3df1ce8ebfbd54cf118c90357396b0) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
