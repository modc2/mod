# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002979 | -0.00% | -0.22% | -0.67% | 15,062 | 6,119 | 7.19 |

## Last 24h flow

58 trades by 42 coldkeys · 1 buys (0.01 τ) / 57 sells (6.63 τ) · net -6.62 τ

## News

- 2026-10-12 · commit · [Markets V3.2: finite house depth (arm 2026-10-12 05:00 UTC)](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/3f6d260696fecc8f98b0f33398e2e2216b0e356c) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-11 · commit · [Markets V2e: unpaid claims carry into later cycles (arm 2026-10-12 01…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/5693c4beb512e73fc87ba5278e0050827eb8915a) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-11 · commit · [README: USDC withdrawals go out automatically](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/cd691a3e3a2b1afecf40c021f589413f585fa3d0) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-11 · commit · [README: Markets real-money section (USDC/alpha withdrawals on request…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/7ce88739f7222b6c7581ee4bd4732f696515dced) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-11 · commit · [markets V2d: lock while open. An entity short of its open bets at any…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/18d2e693ef7247ce11830da666c7a1e72790545a) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets V3.1: size 15m/1h markets from the 3 h of sealed ticks before…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/1738b1f941528c3ca4a87945a2b48ad0d8571841) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets V2c: carry retained losses forward per entity (pay only above…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/2f7cab56e27160486a290f807fd61720e1d10bdf) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets: daily and weekly Bitcoin Up/Down windows (5 pm New York; wee…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/798320b7e90bffa4056175a25f40fb56620f23d6) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
