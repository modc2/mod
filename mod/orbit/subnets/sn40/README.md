# sn40 — Ralph ן

 The same model, small enough to run on your phone. Compress the frontier models into GGUFs.

Bittensor subnet **40** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/RalphLabsAI/ralph) · [url](https://ralphlabs.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002086 | -0.00% | -1.80% | -8.09% | 2,403 | 1,329 | 16.37 |

## Last 24h flow

7 trades by 6 coldkeys · 2 buys (2.14 τ) / 5 sells (6.64 τ) · net -4.50 τ

## News

- 2026-09-16 · commit · [Merge pull request #17 from RalphLabsAI/fix/round8-readiness](https://github.com/RalphLabsAI/ralph/commit/6500e1bd2bab809fb3e5a3650d0b26e680ee7285) — RalphLabsAI/ralph
- 2026-09-16 · commit · [Name the anchor holder's flag for what it is](https://github.com/RalphLabsAI/ralph/commit/af70ce593516ade125062597434e9c4ef9b5df3d) — RalphLabsAI/ralph

## Use

```bash
m subnets.sn40/info        # live identity + market (snapshot if bt is down)
m subnets.sn40/news        # scraped news
m subnets.sn40/trades      # 24h alpha tape
m subnets.sn40/daily       # daily candles
python3 orbit/subnets/sn40/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
