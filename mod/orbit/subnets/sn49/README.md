# sn49 — Nepher Robotics ר

Pioneering Simulation-First Robotics Development

Bittensor subnet **49** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/nepher-ai/nepher-subnet) · [url](https://www.nepher.ai/) · [discord](https://discord.gg/qZUc3vdjVq)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007498 | — | +10.00% | -5.57% | 23,398 | 4,519 | 360.66 |

## Last 24h flow

79 trades by 41 coldkeys · 52 buys (284.01 τ) / 27 sells (73.96 τ) · net 210.05 τ

## News

- 2026-10-05 · commit · [Merge pull request #11 from nepher-ai/feat/isaacsim-6.1-isaaclab-3.0](https://github.com/nepher-ai/nepher-subnet/commit/dfbd06a5dc1e2fa996c5925a9e7a96c19adfb1ea) — nepher-ai/nepher-subnet
- 2026-09-30 · commit · [Fix issue: IsaacLab 3.0.0 rejects --headless](https://github.com/nepher-ai/nepher-subnet/commit/8d01570718f988296575e2aaa688cdd6cf16c3b0) — nepher-ai/nepher-subnet
- 2026-09-30 · commit · [Upgrade validator sandbox to Isaac Sim 6.1 / Isaac Lab 3.0.0](https://github.com/nepher-ai/nepher-subnet/commit/1e23cf0664621ece7696cf0375e205d911595892) — nepher-ai/nepher-subnet
- 2026-09-30 · release · [v1.0.0](https://github.com/nepher-ai/nepher-subnet/releases/tag/v1.0.0) — nepher-ai/nepher-subnet

## Use

```bash
m subnets.sn49/info        # live identity + market (snapshot if bt is down)
m subnets.sn49/news        # scraped news
m subnets.sn49/trades      # 24h alpha tape
m subnets.sn49/daily       # daily candles
python3 orbit/subnets/sn49/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
