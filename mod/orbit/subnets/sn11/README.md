# sn11 — TrajectoryRL λ

Agentic RL as a Service, Optimize agent trajectories to make agents cheaper, safer, and more reliable.

Bittensor subnet **11** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/trajectoryRL/trajectoryRL) · [url](https://trajrl.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.007658 | -0.19% | +0.39% | -3.69% | 46,401 | 20,186 | 222.26 |

## Last 24h flow

67 trades by 21 coldkeys · 13 buys (121.15 τ) / 54 sells (90.35 τ) · net 30.80 τ

## News

- 2026-09-21 · release · [v0.7.3](https://github.com/trajectoryRL/trajectoryRL/releases/tag/v0.7.3) — trajectoryRL/trajectoryRL
- 2026-09-21 · commit · [chore(release): bump version to 0.7.3 (SPEC 26: drop 6 low-signal sce…](https://github.com/trajectoryRL/trajectoryRL/commit/f20a623aabd7c150ac8f07bd50c00b3d9d9ed130) — trajectoryRL/trajectoryRL
- 2026-09-21 · commit · [[coding-agent] SPEC 26: drop 6 low-signal/high-cost scenarios (26 -> …](https://github.com/trajectoryRL/trajectoryRL/commit/989c7cc766ef11c3bb904bcbdb3341a13fc10c92) — trajectoryRL/trajectoryRL
- 2026-09-21 · release · [v0.7.2](https://github.com/trajectoryRL/trajectoryRL/releases/tag/v0.7.2) — trajectoryRL/trajectoryRL
- 2026-09-21 · commit · [chore(release): bump version to 0.7.2 (own-container resolution, vali…](https://github.com/trajectoryRL/trajectoryRL/commit/405be66921b854f9403353425b042b238d41cd82) — trajectoryRL/trajectoryRL
- 2026-09-21 · commit · [feat(validator): report health on the heartbeat, and never blame a mi…](https://github.com/trajectoryRL/trajectoryRL/commit/772601dae7825ca62e39562d4712ccf891360bfd) — trajectoryRL/trajectoryRL
- 2026-09-21 · commit · [fix(harness): resolve own container when Watchtower left a stale host…](https://github.com/trajectoryRL/trajectoryRL/commit/14fc57a5deeaf639b9788249ce2ee69c93213d1f) — trajectoryRL/trajectoryRL
- 2026-09-20 · release · [v0.7.1](https://github.com/trajectoryRL/trajectoryRL/releases/tag/v0.7.1) — trajectoryRL/trajectoryRL

## Use

```bash
m subnets.sn11/info        # live identity + market (snapshot if bt is down)
m subnets.sn11/news        # scraped news
m subnets.sn11/trades      # 24h alpha tape
m subnets.sn11/daily       # daily candles
python3 orbit/subnets/sn11/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
