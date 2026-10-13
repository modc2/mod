# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003001 | -0.03% | +0.73% | +0.19% | 15,193 | 6,142 | 154.44 |

## Last 24h flow

58 trades by 46 coldkeys · 10 buys (87.86 τ) / 48 sells (64.82 τ) · net 23.04 τ

## News

- 2026-10-13 · commit · [Markets vault: from the payout+vault arms a withdrawal keeps open bet…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/36eca682e13a61e752f8424e0229a6175fe003da) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [README: depth allowances by vault collateral tier](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/0d57a59b2f37c874f63b4abd0580c0e00f2cbd1e) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Markets V3.3 depth by vault collateral tier + V3.4 sigma calibration …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/83ac5c96d37f56612eacc04e8405ad9fe3f3c2ef) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Markets: grade from the record, not the clock (arm 2026-10-14 00:00 U…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/932357c3e34e8d0fbe9ce6143e5c0c7959a87066) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Markets V3.2 depth ends with the vault: 15m/1h markets starting at/af…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/a9797acacf77f92282866c0040ff592c9cd715c6) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-12 · commit · [Markets V6: payout-based collateral check + emission credit, daily se…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/e96a566cacc7b344b237eaef4299319367abc169) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-12 · commit · [Markets V3.2: finite house depth (arm 2026-10-12 05:00 UTC)](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/3f6d260696fecc8f98b0f33398e2e2216b0e356c) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-11 · commit · [Markets V2e: unpaid claims carry into later cycles (arm 2026-10-12 01…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/5693c4beb512e73fc87ba5278e0050827eb8915a) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
