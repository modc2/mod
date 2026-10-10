# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003382 | +0.00% | -0.34% | -5.09% | 6,049 | 2,633 | 7.46 |

## Last 24h flow

65 trades by 17 coldkeys · 1 buys (0.13 τ) / 64 sells (5.23 τ) · net -5.10 τ

## News

- 2026-10-07 · commit · [Document miner decision queries and staging smoke results (#1673)](https://github.com/harnyx/harnyx/commit/3824b37d90de8fd778b335957d7b91ac2d9ce49f) — harnyx/harnyx
- 2026-10-07 · commit · [chore(validator): bump repo-owned validator version to 20261007.post2](https://github.com/harnyx/harnyx/commit/46058b4447ed5d9781c2962f39f8e3e867e7b2b0) — harnyx/harnyx
- 2026-10-07 · commit · [Add native decision queries for miners (#1662)](https://github.com/harnyx/harnyx/commit/2494b74a06e1436eaa9171d2aa238cb27fada1f1) — harnyx/harnyx
- 2026-10-07 · commit · [chore(validator): bump repo-owned validator version to 20261007.post1](https://github.com/harnyx/harnyx/commit/1044dfa994222db4d7678598ef3a76ae33e447e2) — harnyx/harnyx
- 2026-10-07 · commit · [chore(validator): bump repo-owned validator version to 20261007.post0](https://github.com/harnyx/harnyx/commit/192160b39cafaf13f8a1bba68a46bfc21a914cd9) — harnyx/harnyx
- 2026-10-06 · commit · [chore(validator): bump repo-owned validator version to 20261006.post5](https://github.com/harnyx/harnyx/commit/bd2d9eee2040946b7649411d14f3df4179b06471) — harnyx/harnyx
- 2026-10-06 · commit · [chore(validator): bump repo-owned validator version to 20261006.post3](https://github.com/harnyx/harnyx/commit/e41fe8e51a8e8e1584f541a09a2e8625ae61a3b6) — harnyx/harnyx
- 2026-10-06 · commit · [Increase miner task generation Luna concurrency to twenty (#1657)](https://github.com/harnyx/harnyx/commit/d0164c9aaf3fa1926da848388fc939e6c2a2801e) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
