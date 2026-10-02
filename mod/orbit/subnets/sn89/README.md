# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003014 | -0.01% | -0.87% | -1.07% | 15,024 | 6,148 | 34.30 |

## Last 24h flow

58 trades by 35 coldkeys · 2 buys (3.47 τ) / 56 sells (30.10 τ) · net -26.63 τ

## News

- 2026-09-21 · commit · [bands: flip the live board to cryptoalts21-20260921](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/bd25eabe0dd5fd813aa0faf1e7f882576c920c6d) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-09-18 · commit · [bands: list 21 crypto alts on HF (board v5) and LF, effective 2026-09…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/b643025709dfe6b962dac535e9709746138385d1) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-09-17 · commit · [limit_watcher: one chain handle per process for LF submits](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/3997ef513d2514f770d2c1fc25fb642bb9c28ca4) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-09-13 · commit · [ops: issue script gains --only and a capped --allow-evict for a full 496](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/070878269a7af76fb57b04f87186dd0bccd23c4e) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-09-10 · commit · [ops: re-register beta keys pruned from testnet 496](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/cc25a72e0cfeceaa131fe8cdef19b1db7e087bb3) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
