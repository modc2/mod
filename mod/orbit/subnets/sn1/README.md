# sn1 — Apex α

The general intelligence platform

Bittensor subnet **1** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/macrocosm-os/apex) · [url](https://apex.macrocosmos.ai) · [discord](https://discord.gg/bvBDat3Gy)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006571 | -0.00% | -0.20% | -0.82% | 39,927 | 23,042 | 188.31 |

## Last 24h flow

252 trades by 28 coldkeys · 179 buys (82.77 τ) / 73 sells (104.40 τ) · net -21.62 τ

## News

- 2026-10-02 · release · [v4.4.12](https://github.com/macrocosm-os/apex/releases/tag/v4.4.12) — macrocosm-os/apex
- 2026-10-02 · commit · [Release v4.4.12 (#973)](https://github.com/macrocosm-os/apex/commit/da4a308ea7e097a1e357937e5a322a54bba9cc41) — macrocosm-os/apex
- 2026-09-29 · release · [v4.4.11](https://github.com/macrocosm-os/apex/releases/tag/v4.4.11) — macrocosm-os/apex
- 2026-09-29 · commit · [Release v4.4.11 (#972)](https://github.com/macrocosm-os/apex/commit/96d5978b823cf15f2b418d49b68da21431723197) — macrocosm-os/apex
- 2026-09-25 · release · [v4.4.10](https://github.com/macrocosm-os/apex/releases/tag/v4.4.10) — macrocosm-os/apex
- 2026-09-25 · commit · [Release v4.4.10 (#971)](https://github.com/macrocosm-os/apex/commit/dbb3831c13a6d21762256522bcbf2cab9d9b1e5f) — macrocosm-os/apex
- 2026-09-24 · release · [v4.4.9](https://github.com/macrocosm-os/apex/releases/tag/v4.4.9) — macrocosm-os/apex
- 2026-09-24 · commit · [Release v4.4.9 (#970)](https://github.com/macrocosm-os/apex/commit/b19daed573d2ba4194f2a67d6651d07acda91b0a) — macrocosm-os/apex

## Use

```bash
m subnets.sn1/info        # live identity + market (snapshot if bt is down)
m subnets.sn1/news        # scraped news
m subnets.sn1/trades      # 24h alpha tape
m subnets.sn1/daily       # daily candles
python3 orbit/subnets/sn1/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
