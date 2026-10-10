# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002990 | +0.10% | +0.41% | -0.67% | 15,075 | 6,130 | 142.64 |

## Last 24h flow

55 trades by 32 coldkeys · 12 buys (77.34 τ) / 43 sells (64.96 τ) · net 12.38 τ

## News

- 2026-10-10 · commit · [markets V2b: lost stakes stay with the entity; emission on net entity…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/4c8c495cabc46c32b888845a0c16e1b91f0ae79e) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-09 · commit · [tests: pin V1 Markets tests to the pre-V3/pre-V2 era (clock-derived s…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/1f80ca20d160027edcc2a0dd51e0f623dba4698c) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-09 · commit · [markets V2 collateral: mainnet arms 2026-10-10 01:00 UTC (Whit: start…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/461d6130d1cf49d5812cccba5c7a3274a87c6283) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-09 · commit · [tests: pin the pre-V3 running-window refusal to a pre-V3 stamp (flipp…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/dceed355b498778e137791cffb8748889a968aae) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-09 · commit · [markets: entity dust collateral minimum 120 -> 6,100 alpha (~$5,000) …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/cee9b30b0cd75d2b9df667150d04c4d913fccf9f) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-09 · commit · [README: Markets V3 date 2026-10-09 15:00 UTC](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/ae82ccde5cf399fb4a09f11b3e3e41627a2dfb19) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-09 · commit · [markets V3: mainnet arms 2026-10-09 15:00 UTC (Whit: switch as soon a…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/5d04815a8b12715eb0383584cc36c033cbb7d432) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-09 · commit · [markets V3: bets during the window, priced off the live tick (mainnet…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/3216dc1c204858342e35091bb6c1c5f57525985e) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
