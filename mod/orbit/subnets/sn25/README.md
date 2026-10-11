# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006314 | +0.00% | -1.10% | -5.61% | 38,832 | 12,866 | 1,238 |

## Last 24h flow

133 trades by 91 coldkeys · 27 buys (569.34 τ) / 106 sells (653.87 τ) · net -84.53 τ

## News

- 2026-10-10 · release · [v2026.10.10-1068748650](https://github.com/urfoundation/sn/releases/tag/v2026.10.10-1068748650) — urfoundation/sn
- 2026-10-10 · commit · [Merge plain db migrate docs now that sn is live](https://github.com/urfoundation/sn/commit/56f44577063304dcd81dc82dd1319dd4d3f942b8) — urfoundation/sn
- 2026-10-10 · commit · [Use plain db migrate in the launch steps now that sn is live](https://github.com/urfoundation/sn/commit/2720c5dfa1be1959c4fb9a9f0b006258e99ed056) — urfoundation/sn
- 2026-10-10 · commit · [Merge seeding validator trails from every online provider](https://github.com/urfoundation/sn/commit/7822a4e8955448b5b875204a2b9cef04d2023801) — urfoundation/sn
- 2026-10-10 · commit · [Merge the validator trail probe](https://github.com/urfoundation/sn/commit/94b0ab678e6e9706801e289ddab38b835d079383) — urfoundation/sn
- 2026-10-10 · release · [v2026.10.10-1068595480](https://github.com/urfoundation/sn/releases/tag/v2026.10.10-1068595480) — urfoundation/sn
- 2026-10-10 · release · [v2026.10.10-1068549930](https://github.com/urfoundation/sn/releases/tag/v2026.10.10-1068549930) — urfoundation/sn
- 2026-10-10 · release · [v2026.10.10-1068404160](https://github.com/urfoundation/sn/releases/tag/v2026.10.10-1068404160) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
