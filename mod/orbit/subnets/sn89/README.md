# sn89 — InfiniteQuant ᛒ

Proof of Edge - Trading signals

Bittensor subnet **89** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/DeltaCompute24/InfiniteQuant-Subnet) · [url](https://infinitequant.app)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002986 | -0.00% | -0.16% | -0.65% | 15,073 | 6,125 | 5.92 |

## Last 24h flow

48 trades by 41 coldkeys · 4 buys (0.30 τ) / 44 sells (5.10 τ) · net -4.80 τ

## News

- 2026-10-11 · commit · [markets V2d: lock while open. An entity short of its open bets at any…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/18d2e693ef7247ce11830da666c7a1e72790545a) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets V3.1: size 15m/1h markets from the 3 h of sealed ticks before…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/1738b1f941528c3ca4a87945a2b48ad0d8571841) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets V2c: carry retained losses forward per entity (pay only above…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/2f7cab56e27160486a290f807fd61720e1d10bdf) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets: daily and weekly Bitcoin Up/Down windows (5 pm New York; wee…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/798320b7e90bffa4056175a25f40fb56620f23d6) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets V4: correct the oracle cadence in the config comment (measure…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/0ff1cdd100aa20386c620bf6454bd2fd557442d7) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets V4: arm the oracle record for markets starting at or after 20…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/cff6c7ae1ee93268ed6ef236d0f67e11b411264d) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets V4: an oracle fill is the first change at least 3 s after the…](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/52c278fa5b3a54bb3674e31889646e32c3773f81) — DeltaCompute24/InfiniteQuant-Subnet
- 2026-10-10 · commit · [markets V4: oracle price record and settlement path (not armed)](https://github.com/DeltaCompute24/InfiniteQuant-Subnet/commit/264fe7eaed1c4b091081622e1930df027bb3a2a8) — DeltaCompute24/InfiniteQuant-Subnet

## Use

```bash
m subnets.sn89/info        # live identity + market (snapshot if bt is down)
m subnets.sn89/news        # scraped news
m subnets.sn89/trades      # 24h alpha tape
m subnets.sn89/daily       # daily candles
python3 orbit/subnets/sn89/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
