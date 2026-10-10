# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006384 | -0.05% | -3.31% | -5.24% | 39,212 | 12,931 | 5,949 |

## Last 24h flow

354 trades by 151 coldkeys · 208 buys (2,871 τ) / 146 sells (3,082 τ) · net -211.09 τ

## News

- 2026-10-10 · release · [v2026.10.9-1067985620](https://github.com/urfoundation/sn/releases/tag/v2026.10.9-1067985620) — urfoundation/sn
- 2026-10-10 · release · [v2026.10.9-1067932340](https://github.com/urfoundation/sn/releases/tag/v2026.10.9-1067932340) — urfoundation/sn
- 2026-10-09 · commit · [Tidy go.mod against the current sibling checkouts](https://github.com/urfoundation/sn/commit/68ab0eee29d4d5ff30e8509d2820008abd6221c2) — urfoundation/sn
- 2026-10-09 · commit · [Review VERSION2 readiness and specify cumulative app claims](https://github.com/urfoundation/sn/commit/21303ed1468671141307fe160e0ae8d2b4dc7a9a) — urfoundation/sn
- 2026-10-09 · commit · [Test that a quarantining run ends with exit status 75](https://github.com/urfoundation/sn/commit/2773f1cbe85ac9c6319ab09c74a83741d1b70538) — urfoundation/sn
- 2026-10-09 · commit · [Sign an auto-register operator in again after a rejected network token](https://github.com/urfoundation/sn/commit/3bbffc82b8cff7e7a46f75b9635ef85b8a7148ed) — urfoundation/sn
- 2026-10-09 · commit · [Set a rejected network token aside under its owner lock](https://github.com/urfoundation/sn/commit/d26f048071646164c0ddb52e7d98eb13a1ad42d7) — urfoundation/sn
- 2026-10-09 · commit · [Add the version 2 pool payment scaling design, its review and the fin…](https://github.com/urfoundation/sn/commit/31dd6fd521a22a38c4468ea7543795c246ea4b27) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
