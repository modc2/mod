# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002982 | -0.00% | -0.31% | +0.13% | 15,163 | 6,125 | 255.50 |

## Last 24h flow

92 trades by 65 coldkeys · 11 buys (122.16 τ) / 81 sells (127.21 τ) · net -5.04 τ

## News

- 2026-10-15 · commit · [limit_watcher: reject mainnet orders from hotkeys not registered on n…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/452dacb5f95c78169e3f8b1088bb21d95323c0e2) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [Docs: LF reveal is 15 minutes, entry is the last anchored tick at or …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/2929742ec7c6becba378baba623881e090ee6c90) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [README: IQ Markets $200 credit is now earned by referral (invite one …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/78a75678f4080f0b08e33b00512aec647bd988b8) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-15 · commit · [HF warmup dust: not-yet-eligible HF miners hold DUST_WEIGHT for 8 day…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/62c7f732d62b80a5f90ee3e74c8421447f6799eb) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-14 · commit · [Markets entity dust: MARKETS_VAULT_UID_FLOOR inside the Markets vecto…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/f364e2a2733478238030c85c5ba1c11295de3d40) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [README + overview: 2026-10-14 split banner (Markets 20 / LF 30 / HF 3…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/2aae9c33a70d6771e3334f501100254e2dfb5fb1) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Emission split from 2026-10-14 00:00 UTC: LF 30 / HF 30 / Markets 20 …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/8ddaa4ed0e4b145505107b0981bf61bb73595066) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-13 · commit · [Markets vault: from the payout+vault arms a withdrawal keeps open bet…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/36eca682e13a61e752f8424e0229a6175fe003da) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
