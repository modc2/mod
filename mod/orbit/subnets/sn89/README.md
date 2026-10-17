# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002976 | +0.00% | -0.21% | -0.49% | 15,152 | 6,120 | 26.71 |

## Last 24h flow

281 trades by 37 coldkeys · 1 buys (10.00 τ) / 280 sells (15.81 τ) · net -5.81 τ

## News

- 2026-10-16 · commit · [Markets V3.5: 1h sigma scale 1.20 -> 1.70 from 2026-10-16 21:00 UTC](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/b55171d8a15420e033c4515f1a5ecc5ba370b1b3) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [limit_watcher: reject mainnet orders from hotkeys not registered on n…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/452dacb5f95c78169e3f8b1088bb21d95323c0e2) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [Docs: LF reveal is 15 minutes, entry is the last anchored tick at or …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/2929742ec7c6becba378baba623881e090ee6c90) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [README: IQ Markets $200 credit is now earned by referral (invite one …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/78a75678f4080f0b08e33b00512aec647bd988b8) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [HF warmup dust: not-yet-eligible HF miners hold DUST_WEIGHT for 8 day…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/62c7f732d62b80a5f90ee3e74c8421447f6799eb) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-14 · commit · [Markets entity dust: MARKETS_VAULT_UID_FLOOR inside the Markets vecto…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/f364e2a2733478238030c85c5ba1c11295de3d40) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [README + overview: 2026-10-14 split banner (Markets 20 / LF 30 / HF 3…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/2aae9c33a70d6771e3334f501100254e2dfb5fb1) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Emission split from 2026-10-14 00:00 UTC: LF 30 / HF 30 / Markets 20 …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/8ddaa4ed0e4b145505107b0981bf61bb73595066) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
