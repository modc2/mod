# sn65 — True Performance Network ص

Distributed AI model compression & optimization engine

Bittensor subnet **65** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/taofu-labs/true-performance-network) · [url](https://www.trueperformancenetwork.com/) · [discord](https://discord.com/invite/GRVZyPYd6G)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002040 | +0.00% | -0.31% | -8.09% | 12,304 | 5,473 | 73.97 |

## Last 24h flow

20 trades by 15 coldkeys · 5 buys (32.69 τ) / 15 sells (40.47 τ) · net -7.79 τ

## News

- 2026-09-17 · commit · [Merge pull request #8 from taofu-labs/testnet](https://github.com/taofu-labs/true-performance-network/commit/ea73f1ecccb6bb804eb986e0e51acb7991ba81d2) — taofu-labs/true-performance-network
- 2026-09-17 · commit · [fix follower loop](https://github.com/taofu-labs/true-performance-network/commit/ff76ee8076ab0b54950bf5eec2d4d68228ba3302) — taofu-labs/true-performance-network
- 2026-09-14 · commit · [add pause option per competition](https://github.com/taofu-labs/true-performance-network/commit/1433f8f4d633fab7a7de5a6a3e1454bb4304de7b) — taofu-labs/true-performance-network
- 2026-09-14 · commit · [Precheck command update and widen block range for model commit](https://github.com/taofu-labs/true-performance-network/commit/cd0bd24268d9e8feb39187944e1ed0b9254fa405) — taofu-labs/true-performance-network

## Use

```bash
m subnets.sn65/info        # live identity + market (snapshot if bt is down)
m subnets.sn65/news        # scraped news
m subnets.sn65/trades      # 24h alpha tape
m subnets.sn65/daily       # daily candles
python3 orbit/subnets/sn65/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
