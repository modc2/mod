# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002991 | -0.00% | -0.07% | +0.42% | 15,189 | 6,134 | 124.20 |

## Last 24h flow

69 trades by 51 coldkeys · 4 buys (60.59 τ) / 65 sells (62.75 τ) · net -2.16 τ

## News

- 2026-10-14 · commit · [Markets entity dust: MARKETS_VAULT_UID_FLOOR inside the Markets vecto…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/f364e2a2733478238030c85c5ba1c11295de3d40) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [README + overview: 2026-10-14 split banner (Markets 20 / LF 30 / HF 3…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/2aae9c33a70d6771e3334f501100254e2dfb5fb1) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Emission split from 2026-10-14 00:00 UTC: LF 30 / HF 30 / Markets 20 …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/8ddaa4ed0e4b145505107b0981bf61bb73595066) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Markets vault: from the payout+vault arms a withdrawal keeps open bet…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/36eca682e13a61e752f8424e0229a6175fe003da) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [README: depth allowances by vault collateral tier](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/0d57a59b2f37c874f63b4abd0580c0e00f2cbd1e) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Markets V3.3 depth by vault collateral tier + V3.4 sigma calibration …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/83ac5c96d37f56612eacc04e8405ad9fe3f3c2ef) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Markets: grade from the record, not the clock (arm 2026-10-14 00:00 U…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/932357c3e34e8d0fbe9ce6143e5c0c7959a87066) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Markets V3.2 depth ends with the vault: 15m/1h markets starting at/af…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/a9797acacf77f92282866c0040ff592c9cd715c6) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
