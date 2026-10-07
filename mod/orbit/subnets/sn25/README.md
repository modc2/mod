# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006849 | -0.00% | -1.60% | +1.42% | 41,909 | 13,380 | 1,246 |

## Last 24h flow

73 trades by 39 coldkeys · 32 buys (568.92 τ) / 41 sells (675.84 τ) · net -106.92 τ

## News

- 2026-10-07 · commit · [Register the reserve recipients close to launch](https://github.com/urfoundation/sn/commit/10a054225b93d37e540ebfc20f181c9f2a3d473a) — urfoundation/sn
- 2026-10-07 · commit · [Launch SN25 without an owner trim](https://github.com/urfoundation/sn/commit/c03ea752ef00656d312de82aeb9e11a9cfd2a1e2) — urfoundation/sn
- 2026-10-07 · release · [v2026.10.6-1065407930](https://github.com/urfoundation/sn/releases/tag/v2026.10.6-1065407930) — urfoundation/sn
- 2026-10-07 · commit · [Describe the treasury setup without a coldkey swap](https://github.com/urfoundation/sn/commit/cd443a350458301d7d98d1317cd976fb0df49683) — urfoundation/sn
- 2026-10-07 · commit · [Merge origin/main into feat/owner-signing-multisig](https://github.com/urfoundation/sn/commit/e9b3ca6aea37042d62f9f12fe1f4140df3ede126) — urfoundation/sn
- 2026-10-07 · commit · [Sign the SN25 owner trim through the owner's native multisig](https://github.com/urfoundation/sn/commit/c638a86d6350389752197efb3a48f6a2151c176b) — urfoundation/sn
- 2026-10-07 · release · [v2026.10.6-1065359600](https://github.com/urfoundation/sn/releases/tag/v2026.10.6-1065359600) — urfoundation/sn
- 2026-10-07 · release · [v2026.10.6-1065321000](https://github.com/urfoundation/sn/releases/tag/v2026.10.6-1065321000) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
