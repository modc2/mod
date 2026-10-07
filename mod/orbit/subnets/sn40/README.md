# sn40 — Ralph ן

 The same model, small enough to run on your phone. Compress the frontier models into GGUFs.

Bittensor subnet **40** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/RalphLabsAI/ralph) · [url](https://ralphlabs.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002336 | -0.00% | -1.15% | -13.99% | 2,538 | 1,404 | 8.14 |

## Last 24h flow

9 trades by 7 coldkeys · 2 buys (0.02 τ) / 7 sells (7.02 τ) · net -7.01 τ

## News

- 2026-09-16 · commit · [Merge pull request #17 from RalphLabsAI/fix/round8-readiness](https://github.com/RalphLabsAI/ralph/commit/6500e1bd2bab809fb3e5a3650d0b26e680ee7285) — RalphLabsAI/ralph
- 2026-09-16 · commit · [Name the anchor holder's flag for what it is](https://github.com/RalphLabsAI/ralph/commit/af70ce593516ade125062597434e9c4ef9b5df3d) — RalphLabsAI/ralph
- 2026-09-16 · commit · [Write miner commitments through the 11.x call path](https://github.com/RalphLabsAI/ralph/commit/7f7c05d7e8881a2a37fbf265dc8fd08ef27321ac) — RalphLabsAI/ralph
- 2026-09-16 · commit · [The crowns publisher owns one block of the card](https://github.com/RalphLabsAI/ralph/commit/f0a78909685d90dc17cfac6358d1958ff7b53e6f) — RalphLabsAI/ralph
- 2026-09-16 · commit · [Docs: round-7 rules, miner CLI flow, auditor weights](https://github.com/RalphLabsAI/ralph/commit/fdb10a2c647a23d7258b428f74265cac52c1de4d) — RalphLabsAI/ralph

## Use

```bash
m subnets.sn40/info        # live identity + market (snapshot if bt is down)
m subnets.sn40/news        # scraped news
m subnets.sn40/trades      # 24h alpha tape
m subnets.sn40/daily       # daily candles
python3 orbit/subnets/sn40/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
