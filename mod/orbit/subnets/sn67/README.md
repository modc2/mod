# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003193 | -0.08% | -0.41% | -5.53% | 5,892 | 2,566 | 10.89 |

## Last 24h flow

72 trades by 21 coldkeys · 5 buys (1.07 τ) / 67 sells (7.31 τ) · net -6.25 τ

## News

- 2026-10-16 · commit · [chore(validator): bump repo-owned validator version to 20261016.post1](https://github.com/harnyx/harnyx/commit/1f8c6f385692a25ece53246dfcfc2ed87b0d6938) — harnyx/harnyx
- 2026-10-16 · commit · [Add Expand fetch provider with separately citable JSON (#1733)](https://github.com/harnyx/harnyx/commit/51b2733a9808fc50cd8d2d19c6dc5c01c66ea809) — harnyx/harnyx
- 2026-10-16 · commit · [chore(validator): bump repo-owned validator version to 20261016.post0](https://github.com/harnyx/harnyx/commit/c0e6aaca2689d688681ec8c9410baa2863c1db37) — harnyx/harnyx
- 2026-10-16 · commit · [Preserve evaluation results and recover validator sandboxes after OOM…](https://github.com/harnyx/harnyx/commit/02ad188da069fb2d3c990d4eb97995e8439f61bb) — harnyx/harnyx
- 2026-10-15 · commit · [chore(validator): bump repo-owned validator version to 20261015.post0](https://github.com/harnyx/harnyx/commit/9c59642ebc1c877610f2b27b9282c391a976aa33) — harnyx/harnyx
- 2026-10-15 · commit · [Retry rate-limited validator weight submissions on the next worker ti…](https://github.com/harnyx/harnyx/commit/99567b3bfa9ede4c71cb567f4cf7ecbf37bb3c24) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post6](https://github.com/harnyx/harnyx/commit/98dd7cfa35694bc6eaab0dbaa81fac80cde20232) — harnyx/harnyx
- 2026-10-14 · commit · [Use Codex HIGH author for miner task construction (#1715)](https://github.com/harnyx/harnyx/commit/051384655bbd8496beb09af2ca230be7cbb87da4) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
