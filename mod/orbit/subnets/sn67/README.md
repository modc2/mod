# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003467 | -0.15% | -0.37% | -5.71% | 6,127 | 2,662 | 7.76 |

## Last 24h flow

50 trades by 20 coldkeys · 4 buys (0.33 τ) / 46 sells (5.25 τ) · net -4.92 τ

## News

- 2026-10-06 · commit · [chore(validator): bump repo-owned validator version to 20261006.post3](https://github.com/harnyx/harnyx/commit/e41fe8e51a8e8e1584f541a09a2e8625ae61a3b6) — harnyx/harnyx
- 2026-10-06 · commit · [Increase miner task generation Luna concurrency to twenty (#1657)](https://github.com/harnyx/harnyx/commit/d0164c9aaf3fa1926da848388fc939e6c2a2801e) — harnyx/harnyx
- 2026-10-06 · commit · [chore(validator): bump repo-owned validator version to 20261006.post2](https://github.com/harnyx/harnyx/commit/337a03e2b282c7c74d5350124282842e0a475297) — harnyx/harnyx
- 2026-10-06 · commit · [chore(validator): bump repo-owned validator version to 20261006.post1](https://github.com/harnyx/harnyx/commit/a2a324bc6024aaf1af49ea0310485acb7fcdf266) — harnyx/harnyx
- 2026-10-06 · commit · [Recover incomplete generation outputs and closing source connections …](https://github.com/harnyx/harnyx/commit/16383a2d94430441d46066a7a4033e16621d7e77) — harnyx/harnyx
- 2026-10-05 · commit · [chore(validator): bump repo-owned validator version to 20261005.post0](https://github.com/harnyx/harnyx/commit/2bfe5fef2798de53f196494ac074ec5cf3692eb7) — harnyx/harnyx
- 2026-10-05 · commit · [Retry incomplete Flash search-worker responses (#1648)](https://github.com/harnyx/harnyx/commit/7efa628e93a1b774b5a2936b76406896537ba58a) — harnyx/harnyx
- 2026-10-04 · commit · [chore(validator): bump repo-owned validator version to 20261004.post1](https://github.com/harnyx/harnyx/commit/3db2e50fea9c098dc16108e597583a45184861ac) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
