# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003005 | -0.02% | -0.17% | -1.06% | 15,018 | 6,141 | 7.35 |

## Last 24h flow

39 trades by 33 coldkeys · 2 buys (1.03 τ) / 37 sells (6.01 τ) · net -4.98 τ

## News

- 2026-10-03 · commit · [hf v6: list the 19 Vanta-tradeable Hyperliquid pairs from 2026-10-07 …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/e4d00ea86082007f148903e4325f9237a677e0f7) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-03 · commit · [config: SN89_HF_CUSTOM_BANDS_UNTIL ends the custom-band window withou…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/d7c0ff3ae30dea2570c18c876a94e150af7db40d) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-02 · commit · [attest_referrer_succession: log the extrinsic success flag, not the r…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/9b29ce3e51d555a8d06fce29a79a8b2d55d69854) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-02 · commit · [referrer: attested succession for re-rolled recruiter hotkeys (sn89refs)](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/90686c6720b6fd87650ca502b4e371e6a122fa88) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-02 · commit · [hf: diversity-blocked qualified miners keep the probation dust floor](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/22c2ac3fc0fbad8c9d16b78a6effe3311549f013) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-02 · commit · [hf board: carry horizon_s into the diversity gate input](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/09023276482264f4f64adec819f863ce96142078) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-09-21 · commit · [bands: flip the live board to cryptoalts21-20260921](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/bd25eabe0dd5fd813aa0faf1e7f882576c920c6d) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-09-18 · commit · [bands: list 21 crypto alts on HF (board v5) and LF, effective 2026-09…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/b643025709dfe6b962dac535e9709746138385d1) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
