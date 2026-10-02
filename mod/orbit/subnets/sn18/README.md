# sn18 — Zeus σ

Pushing weather forecasts beyond state-of-the-art

Bittensor subnet **18** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/Orpheus-AI/Zeus) · [url](https://www.zeussubnet.com/) · discord `wouter_orpheusai`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004018 | -0.29% | -0.24% | +9.00% | 23,579 | 10,174 | 286.24 |

## Last 24h flow

137 trades by 77 coldkeys · 87 buys (136.97 τ) / 50 sells (148.64 τ) · net -11.67 τ

## News

- 2026-09-07 · release · [Release 2.1.4](https://github.com/Orpheus-AI/Zeus/releases/tag/v2.1.4) — Orpheus-AI/Zeus
- 2026-09-07 · commit · [Stop emissions for non-participating miners (#88)](https://github.com/Orpheus-AI/Zeus/commit/024eb19eeca724aaad135b4492091abf658694ce) — Orpheus-AI/Zeus
- 2026-09-03 · commit · [HOTFIX : Punish negative SSRD only for challenges with start_timestam…](https://github.com/Orpheus-AI/Zeus/commit/f9e505478aa6f078f6e5ade4ff4a77ab60294c31) — Orpheus-AI/Zeus

## Use

```bash
m subnets.sn18/info        # live identity + market (snapshot if bt is down)
m subnets.sn18/news        # scraped news
m subnets.sn18/trades      # 24h alpha tape
m subnets.sn18/daily       # daily candles
python3 orbit/subnets/sn18/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
