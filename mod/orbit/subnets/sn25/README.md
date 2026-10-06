# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006961 | +0.24% | +1.69% | +4.41% | 42,541 | 13,488 | 1,294 |

## Last 24h flow

86 trades by 58 coldkeys · 39 buys (703.37 τ) / 47 sells (587.72 τ) · net 115.66 τ

## News

- 2026-10-06 · commit · [Archive retained historical qualification planning reviews](https://github.com/urfoundation/sn/commit/2b9b5810c3d0f71eecc7bce0d9398864b0e10bc9) — urfoundation/sn
- 2026-10-06 · commit · [Record focused retained heap response qualification](https://github.com/urfoundation/sn/commit/76dd43acdfeee2c1c902e2a996faf4023772d784) — urfoundation/sn
- 2026-10-06 · commit · [Record completed code and guide delivery](https://github.com/urfoundation/sn/commit/a3ef2d3fe8d627d41c22385dfc94e0fa91b62e88) — urfoundation/sn
- 2026-10-06 · commit · [Record completed code docs and focused delivery checks](https://github.com/urfoundation/sn/commit/334dc521e6034ffe223563b0ae8fa07c0ed2825c) — urfoundation/sn
- 2026-10-06 · commit · [Record current carry and treasury qualification](https://github.com/urfoundation/sn/commit/3a3fc0014b79a232dbedfa935adfbcc96f33d399) — urfoundation/sn
- 2026-10-05 · commit · [Merge transient HTTP 500 retries for mainnet reads](https://github.com/urfoundation/sn/commit/6a1470730f8a8f4364d8e024b60227a28fdbe796) — urfoundation/sn
- 2026-10-05 · commit · [Retry transient HTTP 500 failures in mainnet read observations](https://github.com/urfoundation/sn/commit/ef833695087f542022321b22656356441bbdf122) — urfoundation/sn
- 2026-10-05 · commit · [Merge read-only original allocator observation snapshots](https://github.com/urfoundation/sn/commit/78907037ad79a401d8070bfce2b35f3d32a3a7b8) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
