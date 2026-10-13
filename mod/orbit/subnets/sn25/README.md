# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006487 | +0.00% | +1.94% | -6.80% | 40,007 | 13,055 | 1,601 |

## Last 24h flow

76 trades by 37 coldkeys · 38 buys (858.88 τ) / 38 sells (736.11 τ) · net 122.77 τ

## News

- 2026-10-12 · commit · [Merge pointing the MinIO endpoint notes at edge-5](https://github.com/urfoundation/sn/commit/257a6a4affe19b507d05d2e63cc38325d44a9d61) — urfoundation/sn
- 2026-10-12 · commit · [Update the handoff's current MinIO endpoint host for edge-5](https://github.com/urfoundation/sn/commit/63bafa69bedf7acf1fd5e3442a35183fcaecb1d9) — urfoundation/sn
- 2026-10-12 · commit · [Point the live blob canary at edge-5's MinIO](https://github.com/urfoundation/sn/commit/dffedf8dfe1039d36f8bf119053317fcea798d99) — urfoundation/sn
- 2026-10-12 · commit · [Merge the native gap bridge's head EMA census update](https://github.com/urfoundation/sn/commit/24763c8e29a68c2a39cea8e4ded5aae7535e686e) — urfoundation/sn
- 2026-10-12 · commit · [Count the native gap bridge in the head EMA owner field census](https://github.com/urfoundation/sn/commit/fe895756c1883535dcde9be16a14fad97b8a3100) — urfoundation/sn
- 2026-10-12 · commit · [Merge the native gap bridge for missed native epochs](https://github.com/urfoundation/sn/commit/75923e2f1e7943e321b6ae83c10ee6740e849d07) — urfoundation/sn
- 2026-10-12 · commit · [Admit one pinned native gap edge so the validator resumes after misse…](https://github.com/urfoundation/sn/commit/246210928b8c53677fd7433b8a4e522c16f372fa) — urfoundation/sn
- 2026-10-11 · commit · [Merge the retained return bound's provider settings guard](https://github.com/urfoundation/sn/commit/e87c89364334a4bae061fc0665f43c31410a18d8) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
