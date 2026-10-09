# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006603 | +0.23% | -3.59% | -1.12% | 40,501 | 13,144 | 1,290 |

## Last 24h flow

68 trades by 37 coldkeys · 34 buys (518.34 τ) / 34 sells (765.43 τ) · net -247.09 τ

## News

- 2026-10-09 · commit · [Merge the SN25 launch record and momentum terms](https://github.com/urfoundation/sn/commit/0f525b115eea7659ef03fc1ca4efa8076c72301b) — urfoundation/sn
- 2026-10-09 · commit · [Merge shape-only bootstrap custody checks and untraversable approval …](https://github.com/urfoundation/sn/commit/e1500f150584a3b39ba9753b20f7e2eeb50f1147) — urfoundation/sn
- 2026-10-09 · commit · [Document shape-only v5 custody checks and untraversable approval sources](https://github.com/urfoundation/sn/commit/4a8d6f9bc35ea9e52e7cfe547f412a397d9180cb) — urfoundation/sn
- 2026-10-09 · commit · [Read retained production inputs when a signed source is untraversable](https://github.com/urfoundation/sn/commit/6f1997a3adf0bc04593725d74bda52c5089ddce9) — urfoundation/sn
- 2026-10-09 · commit · [Inspect pre-activation bootstrap custody paths by shape only](https://github.com/urfoundation/sn/commit/de00ed187a6e3bd3cfd663ef485f6db74b515713) — urfoundation/sn
- 2026-10-08 · release · [v2026.10.8-1066946420](https://github.com/urfoundation/sn/releases/tag/v2026.10.8-1066946420) — urfoundation/sn
- 2026-10-08 · release · [v2026.10.8-1066912010](https://github.com/urfoundation/sn/releases/tag/v2026.10.8-1066912010) — urfoundation/sn
- 2026-10-08 · commit · [Preserve regression fixes, memory diagnostics, and test harness evidence](https://github.com/urfoundation/sn/commit/6c322bf6165f22b5636c514f8e3a33b494f766d6) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
