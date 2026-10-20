# sn25 — UR א

The Privacy Protocol

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005531 | +0.00% | -4.41% | -14.75% | 34,424 | 12,093 | 909.41 |

## Last 24h flow

64 trades by 38 coldkeys · 27 buys (315.06 τ) / 37 sells (590.81 τ) · net -275.75 τ

## News

- 2026-10-19 · commit · [Merge root monitor regular file regression coverage](https://github.com/urfoundation/sn/commit/b94c2ff28456183fb4ec14fdede348eccb31ecbf) — urfoundation/sn
- 2026-10-19 · commit · [Align root monitor tests with supported regular file sinks](https://github.com/urfoundation/sn/commit/6f920279a2a1d3d27640320f784e504ad7912e2f) — urfoundation/sn
- 2026-10-19 · commit · [Merge reviewed runtime 481 compatibility and rehearsal qualification](https://github.com/urfoundation/sn/commit/a4abe646cb7d952630b21b5937d9ab2a2a65349d) — urfoundation/sn
- 2026-10-19 · commit · [Admit reviewed runtime 481 and qualify settlement rehearsals](https://github.com/urfoundation/sn/commit/22c38068faccfef8b3676238f77110f123fab7a2) — urfoundation/sn
- 2026-10-19 · commit · [competition: document immutable runtime blob capability](https://github.com/urfoundation/sn/commit/c1ab8c19d75bbf556711415df3d45d3d788c54b4) — urfoundation/sn
- 2026-10-19 · release · [v2026.10.19-1076177550](https://github.com/urfoundation/sn/releases/tag/v2026.10.19-1076177550) — urfoundation/sn
- 2026-10-19 · release · [v2026.10.19-1076002880](https://github.com/urfoundation/sn/releases/tag/v2026.10.19-1076002880) — urfoundation/sn
- 2026-10-19 · release · [v2026.10.18-1075928470](https://github.com/urfoundation/sn/releases/tag/v2026.10.18-1075928470) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
