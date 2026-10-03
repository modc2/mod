# sn65 — True Performance Network ص

Distributed AI model compression & optimization engine

Bittensor subnet **65** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/taofu-labs/true-performance-network) · [url](https://www.trueperformancenetwork.com/) · [discord](https://discord.com/invite/GRVZyPYd6G)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002019 | -0.00% | -1.12% | -9.07% | 12,151 | 5,445 | 148.30 |

## Last 24h flow

37 trades by 20 coldkeys · 7 buys (58.83 τ) / 30 sells (89.25 τ) · net -30.42 τ

## News

- 2026-09-17 · commit · [Merge pull request #8 from taofu-labs/testnet](https://github.com/taofu-labs/true-performance-network/commit/ea73f1ecccb6bb804eb986e0e51acb7991ba81d2) — taofu-labs/true-performance-network
- 2026-09-17 · commit · [fix follower loop](https://github.com/taofu-labs/true-performance-network/commit/ff76ee8076ab0b54950bf5eec2d4d68228ba3302) — taofu-labs/true-performance-network
- 2026-09-14 · commit · [add pause option per competition](https://github.com/taofu-labs/true-performance-network/commit/1433f8f4d633fab7a7de5a6a3e1454bb4304de7b) — taofu-labs/true-performance-network
- 2026-09-14 · commit · [Precheck command update and widen block range for model commit](https://github.com/taofu-labs/true-performance-network/commit/cd0bd24268d9e8feb39187944e1ed0b9254fa405) — taofu-labs/true-performance-network
- 2026-09-04 · commit · [Merge pull request #7 from taofu-labs/docs/col](https://github.com/taofu-labs/true-performance-network/commit/0d1429d8c2875f7c30bec17eb6cf72007fa4ec1c) — taofu-labs/true-performance-network

## Use

```bash
m subnets.sn65/info        # live identity + market (snapshot if bt is down)
m subnets.sn65/news        # scraped news
m subnets.sn65/trades      # 24h alpha tape
m subnets.sn65/daily       # daily candles
python3 orbit/subnets/sn65/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
