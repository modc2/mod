# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005745 | -0.03% | -1.46% | -16.12% | 35,522 | 12,296 | 1,046 |

## Last 24h flow

83 trades by 44 coldkeys · 34 buys (474.42 τ) / 49 sells (567.46 τ) · net -93.03 τ

## News

- 2026-10-15 · commit · [Never let a nested gate fixture remove its parent gate's mainnet exports](https://github.com/urfoundation/sn/commit/819f00c52492f262a0fe5b57d622a98eb79d064e) — urfoundation/sn
- 2026-10-15 · commit · [Merge v2/gate-more-shards into main](https://github.com/urfoundation/sn/commit/6b007ed183e4f5be8f32de2c6036a836bd337dc1) — urfoundation/sn
- 2026-10-15 · commit · [Select the startup verified tests and pin the memo's replay edge](https://github.com/urfoundation/sn/commit/c22d715f1cfcff4c20e8d8b3fb3d63e7fcda3efd) — urfoundation/sn
- 2026-10-15 · commit · [Give the isolation job's two runs of the gate pins ten minutes](https://github.com/urfoundation/sn/commit/b000670d0c22ffd37b0985c2f4f8067f3da1d465) — urfoundation/sn
- 2026-10-15 · commit · [Run ./validator, ./sim-testnet and connect's race runs in hash shards](https://github.com/urfoundation/sn/commit/2ce50cf9054ed90921aa228a5b357663326da16b) — urfoundation/sn
- 2026-10-14 · commit · [Merge v2/mainnet-export-fixes-a into main](https://github.com/urfoundation/sn/commit/5eb6af92e06ad17a116d6ad84e3dcc118f4ed094) — urfoundation/sn
- 2026-10-14 · commit · [Merge v2/request-checkpoint-xattr into main](https://github.com/urfoundation/sn/commit/27e786d9773dfdad1d1b770d08a9bb8f1fa95c0c) — urfoundation/sn
- 2026-10-14 · commit · [Merge v2/whitepaper-accuracy into main](https://github.com/urfoundation/sn/commit/98f64496be129be04c36c0799850106110de4cbe) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
