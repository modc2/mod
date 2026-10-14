# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003297 | -0.27% | -0.92% | -4.90% | 5,992 | 2,604 | 75.40 |

## Last 24h flow

78 trades by 21 coldkeys · 5 buys (30.47 τ) / 73 sells (42.55 τ) · net -12.08 τ

## News

- 2026-10-13 · commit · [chore(validator): bump repo-owned validator version to 20261013.post1](https://github.com/harnyx/harnyx/commit/5fecb44aaa15b0073668ad5bdb05dc9910380361) — harnyx/harnyx
- 2026-10-13 · commit · [Replace Resend with Postmark for transactional and campaign email (#1…](https://github.com/harnyx/harnyx/commit/78d5beae97d1d347672f846d8d73e0b8d6ef7d9c) — harnyx/harnyx
- 2026-10-13 · commit · [chore(validator): bump repo-owned validator version to 20261013.post0](https://github.com/harnyx/harnyx/commit/8ec7f641682d2ad1a96acd8e35306e8e330d0a54) — harnyx/harnyx
- 2026-10-12 · commit · [chore(validator): bump repo-owned validator version to 20261012.post0](https://github.com/harnyx/harnyx/commit/19de7aeb2c22df973d31d366b011a1fc22547fc1) — harnyx/harnyx
- 2026-10-12 · commit · [Restore DRACO with JEV grading and limit automatic benchmark suites (…](https://github.com/harnyx/harnyx/commit/5383331cdbef85130335d59989d455579b57c33d) — harnyx/harnyx
- 2026-10-07 · commit · [Document miner decision queries and staging smoke results (#1673)](https://github.com/harnyx/harnyx/commit/3824b37d90de8fd778b335957d7b91ac2d9ce49f) — harnyx/harnyx
- 2026-10-07 · commit · [chore(validator): bump repo-owned validator version to 20261007.post2](https://github.com/harnyx/harnyx/commit/46058b4447ed5d9781c2962f39f8e3e867e7b2b0) — harnyx/harnyx
- 2026-10-07 · commit · [Add native decision queries for miners (#1662)](https://github.com/harnyx/harnyx/commit/2494b74a06e1436eaa9171d2aa238cb27fada1f1) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
