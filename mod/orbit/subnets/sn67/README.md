# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003224 | -0.00% | -1.71% | -5.00% | 5,905 | 2,577 | 43.26 |

## Last 24h flow

114 trades by 25 coldkeys · 8 buys (9.20 τ) / 106 sells (26.51 τ) · net -17.31 τ

## News

- 2026-10-15 · commit · [chore(validator): bump repo-owned validator version to 20261015.post0](https://github.com/harnyx/harnyx/commit/9c59642ebc1c877610f2b27b9282c391a976aa33) — harnyx/harnyx
- 2026-10-15 · commit · [Retry rate-limited validator weight submissions on the next worker ti…](https://github.com/harnyx/harnyx/commit/99567b3bfa9ede4c71cb567f4cf7ecbf37bb3c24) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post6](https://github.com/harnyx/harnyx/commit/98dd7cfa35694bc6eaab0dbaa81fac80cde20232) — harnyx/harnyx
- 2026-10-14 · commit · [Use Codex HIGH author for miner task construction (#1715)](https://github.com/harnyx/harnyx/commit/051384655bbd8496beb09af2ca230be7cbb87da4) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post5](https://github.com/harnyx/harnyx/commit/e445a1dbe8618eed2cc8b5c972ef9cbdcdca4d81) — harnyx/harnyx
- 2026-10-14 · commit · [Update staging Subtensor image and authority storage (#1721)](https://github.com/harnyx/harnyx/commit/d6fd8cee9f3c86da1ddd19305845839cf10cc3df) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post4](https://github.com/harnyx/harnyx/commit/a1ee407914e2b8bfa2a2844fdc681d5a6a85ff55) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post3](https://github.com/harnyx/harnyx/commit/f129569bf25c0cd52db2b571c2431b7e3b7b9744) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
