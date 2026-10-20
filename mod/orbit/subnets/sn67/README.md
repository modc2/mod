# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003155 | -0.03% | -0.55% | -5.18% | 5,866 | 2,552 | 40.21 |

## Last 24h flow

101 trades by 21 coldkeys · 2 buys (15.00 τ) / 99 sells (21.09 τ) · net -6.09 τ

## News

- 2026-10-19 · commit · [chore(validator): bump repo-owned validator version to 20261019.post4](https://github.com/harnyx/harnyx/commit/a8b47f5dceb463600db9c72bc81d4f7a02f313df) — harnyx/harnyx
- 2026-10-19 · commit · [Add Drex, Span-02 and Step 5 Preview miner models (#1754)](https://github.com/harnyx/harnyx/commit/090ff971cbb37ef12b607852fd127b2ead28e4c1) — harnyx/harnyx
- 2026-10-19 · commit · [chore(validator): bump repo-owned validator version to 20261019.post3](https://github.com/harnyx/harnyx/commit/e262ada21d233341770bad5121acf4e1dead86cb) — harnyx/harnyx
- 2026-10-19 · commit · [chore(validator): bump repo-owned validator version to 20261019.post2](https://github.com/harnyx/harnyx/commit/5ff066dfcaf8ffa8090f55a9e801437742b5f2b1) — harnyx/harnyx
- 2026-10-19 · commit · [Add Muse Spark Open to miner models (#1746)](https://github.com/harnyx/harnyx/commit/7eb973c15d4ba930e410c53d9d0a5c9c00bbc216) — harnyx/harnyx
- 2026-10-18 · commit · [chore(validator): bump repo-owned validator version to 20261018.post0](https://github.com/harnyx/harnyx/commit/b3666706b3fce8afdc3e4013e584ae09c6cfcc24) — harnyx/harnyx
- 2026-10-18 · commit · [Add DeepSeek Search and Fetch and Flash 1017 routes (#1736)](https://github.com/harnyx/harnyx/commit/920c10af9e5324a6f0934669b33253adbb90e340) — harnyx/harnyx
- 2026-10-16 · commit · [chore(validator): bump repo-owned validator version to 20261016.post1](https://github.com/harnyx/harnyx/commit/1f8c6f385692a25ece53246dfcfc2ed87b0d6938) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
