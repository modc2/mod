# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003178 | — | -0.33% | -5.31% | 5,885 | 2,561 | 53.84 |

## Last 24h flow

92 trades by 24 coldkeys · 7 buys (23.22 τ) / 85 sells (28.29 τ) · net -5.07 τ

## News

- 2026-10-18 · commit · [chore(validator): bump repo-owned validator version to 20261018.post0](https://github.com/harnyx/harnyx/commit/b3666706b3fce8afdc3e4013e584ae09c6cfcc24) — harnyx/harnyx
- 2026-10-18 · commit · [Add DeepSeek Search and Fetch and Flash 1017 routes (#1736)](https://github.com/harnyx/harnyx/commit/920c10af9e5324a6f0934669b33253adbb90e340) — harnyx/harnyx
- 2026-10-16 · commit · [chore(validator): bump repo-owned validator version to 20261016.post1](https://github.com/harnyx/harnyx/commit/1f8c6f385692a25ece53246dfcfc2ed87b0d6938) — harnyx/harnyx
- 2026-10-16 · commit · [Add Expand fetch provider with separately citable JSON (#1733)](https://github.com/harnyx/harnyx/commit/51b2733a9808fc50cd8d2d19c6dc5c01c66ea809) — harnyx/harnyx
- 2026-10-16 · commit · [chore(validator): bump repo-owned validator version to 20261016.post0](https://github.com/harnyx/harnyx/commit/c0e6aaca2689d688681ec8c9410baa2863c1db37) — harnyx/harnyx
- 2026-10-16 · commit · [Preserve evaluation results and recover validator sandboxes after OOM…](https://github.com/harnyx/harnyx/commit/02ad188da069fb2d3c990d4eb97995e8439f61bb) — harnyx/harnyx
- 2026-10-15 · commit · [chore(validator): bump repo-owned validator version to 20261015.post0](https://github.com/harnyx/harnyx/commit/9c59642ebc1c877610f2b27b9282c391a976aa33) — harnyx/harnyx
- 2026-10-15 · commit · [Retry rate-limited validator weight submissions on the next worker ti…](https://github.com/harnyx/harnyx/commit/99567b3bfa9ede4c71cb567f4cf7ecbf37bb3c24) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
