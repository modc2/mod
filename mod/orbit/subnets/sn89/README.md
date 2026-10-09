# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002978 | -0.00% | -0.02% | -2.00% | 14,993 | 6,117 | 1,143 |

## Last 24h flow

72 trades by 55 coldkeys · 9 buys (571.20 τ) / 63 sells (571.17 τ) · net 0.03 τ

## News

- 2026-10-09 · commit · [markets V2: entity collateral + P&L-basis emission per weight cycle (…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/404c9967e011acb97bc9fd15f5b725ac51f4962c) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-09 · commit · [README: IQ Markets for players (web app, /markets, no miner needed; r…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/51c896b4f2674f85c60bfa95536bf4629931080c) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-08 · commit · [markets: mainnet cutover 2026-10-09 00:00Z — Markets takes Closers' 0…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/9f7ea9b7c2f12a13661146e5c2ee34c49509919e) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-08 · commit · [markets: on-chain Up/Down prediction markets (testnet-armed, mainnet …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/0e2a71ee03c437c957f7bca323a34138686bdb1e) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-08 · commit · [markets: bets only before the window — open one window ahead, close b…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/79600f8e05735c4979391d9d4440d81ed05bc90b) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-07 · commit · [README: list the hluniverse-20261007 pairs on the LF and HF tables](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/3de69fbea98f72f89b7abeb4015878562d53590b) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-07 · commit · [bands: flip signals-bands.json to hluniverse-20261007 (18 Vanta-trade…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/1e2011da2573d9ffe5b09e3576cc5c5af3857085) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-03 · commit · [hf v6: list the 19 Vanta-tradeable Hyperliquid pairs from 2026-10-07 …](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/e4d00ea86082007f148903e4325f9237a677e0f7) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
