# sn15 — ORO ο

AI commerce agents

Bittensor subnet **15** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/ORO-AI/oro) · [url](https://oroagents.com) · [discord](https://discord.gg/MHqAVWTdka)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021011 | +0.00% | -2.72% | -10.32% | 49,090 | 13,672 | 626.03 |

## Last 24h flow

93 trades by 56 coldkeys · 29 buys (166.14 τ) / 64 sells (372.33 τ) · net -206.19 τ

## News

- 2026-10-15 · release · [v2.5.3](https://github.com/ORO-AI/oro/releases/tag/v2.5.3) — ORO-AI/oro
- 2026-10-15 · commit · [Retry configured Reader decisions like the shopper's chat call (#375)](https://github.com/ORO-AI/oro/commit/8c0921276c0b59fd9e85cb6a0815e362480a3acf) — ORO-AI/oro
- 2026-10-15 · release · [v2.5.2](https://github.com/ORO-AI/oro/releases/tag/v2.5.2) — ORO-AI/oro
- 2026-10-15 · commit · [Treat a miner's OpenRouter credit 402 as an exhausted run key (#374)](https://github.com/ORO-AI/oro/commit/b4612a450f3df1ce8ebfbd54cf118c90357396b0) — ORO-AI/oro
- 2026-10-15 · release · [v2.5.1](https://github.com/ORO-AI/oro/releases/tag/v2.5.1) — ORO-AI/oro
- 2026-10-15 · commit · [Move the validator to oro-env-runtime 3.7.1 (#373)](https://github.com/ORO-AI/oro/commit/a78750d0c21e14ac3f0229dfb2a6dfbb90e682c7) — ORO-AI/oro
- 2026-10-13 · commit · [Load v7 and v8 task packs; move to oro-env-runtime 3.7.0 (runtime con…](https://github.com/ORO-AI/oro/commit/1fc79c04ad4a7758e765515e6992b9da0dc886ac) — ORO-AI/oro
- 2026-10-13 · release · [v2.5.0](https://github.com/ORO-AI/oro/releases/tag/v2.5.0) — ORO-AI/oro

## Use

```bash
m subnets.sn15/info        # live identity + market (snapshot if bt is down)
m subnets.sn15/news        # scraped news
m subnets.sn15/trades      # 24h alpha tape
m subnets.sn15/daily       # daily candles
python3 orbit/subnets/sn15/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
