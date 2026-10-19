# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002955 | — | -0.53% | -0.81% | 15,088 | 6,100 | 18.21 |

## Last 24h flow

68 trades by 52 coldkeys · 5 buys (0.57 τ) / 63 sells (16.81 τ) · net -16.24 τ

## News

- 2026-10-17 · commit · [Markets: gold daily and weekly windows from 2026-10-18 17:00 EDT](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/ff54d9ae6dc4949440ea9cd52227b003fbaa8d07) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-17 · commit · [README: IQ Markets withdrawal minimum is $50](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/f1c3e89ea1ac06d548ecd6779278709fa06edcac) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-16 · commit · [Markets V3.5: 1h sigma scale 1.20 -> 1.70 from 2026-10-16 21:00 UTC](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/b55171d8a15420e033c4515f1a5ecc5ba370b1b3) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [limit_watcher: reject mainnet orders from hotkeys not registered on n…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/452dacb5f95c78169e3f8b1088bb21d95323c0e2) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [Docs: LF reveal is 15 minutes, entry is the last anchored tick at or …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/2929742ec7c6becba378baba623881e090ee6c90) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [README: IQ Markets $200 credit is now earned by referral (invite one …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/78a75678f4080f0b08e33b00512aec647bd988b8) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [HF warmup dust: not-yet-eligible HF miners hold DUST_WEIGHT for 8 day…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/62c7f732d62b80a5f90ee3e74c8421447f6799eb) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-14 · commit · [Markets entity dust: MARKETS_VAULT_UID_FLOOR inside the Markets vecto…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/f364e2a2733478238030c85c5ba1c11295de3d40) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
