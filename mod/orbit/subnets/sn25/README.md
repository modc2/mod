# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006364 | +0.00% | +0.80% | -7.03% | 39,195 | 12,924 | 389.59 |

## Last 24h flow

50 trades by 34 coldkeys · 25 buys (216.73 τ) / 25 sells (168.31 τ) · net 48.42 τ

## News

- 2026-10-12 · commit · [Merge the native gap bridge's head EMA census update](https://github.com/urfoundation/sn/commit/24763c8e29a68c2a39cea8e4ded5aae7535e686e) — urfoundation/sn
- 2026-10-12 · commit · [Count the native gap bridge in the head EMA owner field census](https://github.com/urfoundation/sn/commit/fe895756c1883535dcde9be16a14fad97b8a3100) — urfoundation/sn
- 2026-10-12 · commit · [Merge the native gap bridge for missed native epochs](https://github.com/urfoundation/sn/commit/75923e2f1e7943e321b6ae83c10ee6740e849d07) — urfoundation/sn
- 2026-10-12 · commit · [Admit one pinned native gap edge so the validator resumes after misse…](https://github.com/urfoundation/sn/commit/246210928b8c53677fd7433b8a4e522c16f372fa) — urfoundation/sn
- 2026-10-11 · commit · [Merge the retained return bound's provider settings guard](https://github.com/urfoundation/sn/commit/e87c89364334a4bae061fc0665f43c31410a18d8) — urfoundation/sn
- 2026-10-11 · release · [v2026.10.10-1068958850](https://github.com/urfoundation/sn/releases/tag/v2026.10.10-1068958850) — urfoundation/sn
- 2026-10-11 · commit · [Test that every launched provider keeps the retained ACK bound](https://github.com/urfoundation/sn/commit/f4d94f3c5d4bf853ec0cd30883bc56c35d14ef7b) — urfoundation/sn
- 2026-10-10 · release · [v2026.10.10-1068748650](https://github.com/urfoundation/sn/releases/tag/v2026.10.10-1068748650) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
