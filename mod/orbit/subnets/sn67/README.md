# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003280 | +0.00% | -0.52% | -4.52% | 5,984 | 2,598 | 66.71 |

## Last 24h flow

117 trades by 24 coldkeys · 4 buys (29.03 τ) / 113 sells (36.03 τ) · net -7.00 τ

## News

- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post6](https://github.com/harnyx/harnyx/commit/98dd7cfa35694bc6eaab0dbaa81fac80cde20232) — harnyx/harnyx
- 2026-10-14 · commit · [Use Codex HIGH author for miner task construction (#1715)](https://github.com/harnyx/harnyx/commit/051384655bbd8496beb09af2ca230be7cbb87da4) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post5](https://github.com/harnyx/harnyx/commit/e445a1dbe8618eed2cc8b5c972ef9cbdcdca4d81) — harnyx/harnyx
- 2026-10-14 · commit · [Update staging Subtensor image and authority storage (#1721)](https://github.com/harnyx/harnyx/commit/d6fd8cee9f3c86da1ddd19305845839cf10cc3df) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post4](https://github.com/harnyx/harnyx/commit/a1ee407914e2b8bfa2a2844fdc681d5a6a85ff55) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post3](https://github.com/harnyx/harnyx/commit/f129569bf25c0cd52db2b571c2431b7e3b7b9744) — harnyx/harnyx
- 2026-10-14 · commit · [Recover invalid structured outputs in Research API (#1716)](https://github.com/harnyx/harnyx/commit/79bc02dcc7f69ca63b5d862114872978f28250cb) — harnyx/harnyx
- 2026-10-14 · commit · [chore(validator): bump repo-owned validator version to 20261014.post2](https://github.com/harnyx/harnyx/commit/beff2cc02f6e0cdd14bd7d66b9fbe941b36b71b2) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
