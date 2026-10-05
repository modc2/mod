# sn11 — TrajectoryRL λ

Agentic RL as a Service, Optimize agent trajectories to make agents cheaper, safer, and more reliable.

Bittensor subnet **11** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/trajectoryRL/trajectoryRL) · [url](https://trajrl.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007600 | +0.00% | -0.61% | -1.98% | 46,192 | 20,130 | 230.93 |

## Last 24h flow

31 trades by 18 coldkeys · 6 buys (75.60 τ) / 25 sells (143.89 τ) · net -68.29 τ

## News

- 2026-10-04 · commit · [[coding-agent] meter: record and log the Engy request id for every ca…](https://github.com/trajectoryRL/trajectoryRL/commit/e1399fd984569380413dd938fa971b25022a7eac) — trajectoryRL/trajectoryRL
- 2026-10-03 · release · [v0.7.4](https://github.com/trajectoryRL/trajectoryRL/releases/tag/v0.7.4) — trajectoryRL/trajectoryRL
- 2026-10-03 · commit · [[coding-agent] chore(release): bump version to 0.7.4 (SPEC 27: $0.30 …](https://github.com/trajectoryRL/trajectoryRL/commit/e6eb944eca3297b8d80101f173a226fdb4999437) — trajectoryRL/trajectoryRL
- 2026-10-03 · commit · [[coding-agent] make the episode cap a spec-specific setting (SpecConf…](https://github.com/trajectoryRL/trajectoryRL/commit/c2f32fb838b1c0f552de28ac459378d534884f7b) — trajectoryRL/trajectoryRL
- 2026-10-03 · commit · [[coding-agent] SPEC 27: lower per-episode safety cap from $0.60 to $0…](https://github.com/trajectoryRL/trajectoryRL/commit/a598d725059d3b6a737623d03807de3c33060724) — trajectoryRL/trajectoryRL
- 2026-10-03 · commit · [[coding-agent] validator: weight-only by default, eval behind EVAL_EN…](https://github.com/trajectoryRL/trajectoryRL/commit/2f32a51b8e3ba0a77bbde2298a0de86fa00883dc) — trajectoryRL/trajectoryRL
- 2026-10-03 · commit · [Reduce per-episode safety cap from $1.00 to $0.60 (#334)](https://github.com/trajectoryRL/trajectoryRL/commit/ccc54047bfa595b0319c3633ac0af7a00818d4e8) — trajectoryRL/trajectoryRL
- 2026-09-21 · release · [v0.7.3](https://github.com/trajectoryRL/trajectoryRL/releases/tag/v0.7.3) — trajectoryRL/trajectoryRL

## Use

```bash
m subnets.sn11/info        # live identity + market (snapshot if bt is down)
m subnets.sn11/news        # scraped news
m subnets.sn11/trades      # 24h alpha tape
m subnets.sn11/daily       # daily candles
python3 orbit/subnets/sn11/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
