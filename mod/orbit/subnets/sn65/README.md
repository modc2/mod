# sn65 — True Performance Network ص

Distributed AI model compression & optimization engine

Bittensor subnet **65** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/taofu-labs/true-performance-network) · [url](https://www.trueperformancenetwork.com/) · [discord](https://discord.com/invite/GRVZyPYd6G)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002018 | -0.05% | -1.08% | -7.60% | 12,186 | 5,443 | 618.76 |

## Last 24h flow

60 trades by 37 coldkeys · 25 buys (294.60 τ) / 35 sells (323.29 τ) · net -28.69 τ

## News

- 2026-10-05 · commit · [Merge pull request #12 from taofu-labs/testnet](https://github.com/taofu-labs/true-performance-network/commit/7ae25fa7e531e72dd22c48fd4c3a15aabfe7d606) — taofu-labs/true-performance-network
- 2026-10-05 · commit · [Merge pull request #11 from taofu-labs/fix/overreport](https://github.com/taofu-labs/true-performance-network/commit/3a813f82793aa832ca31d049c9d519f70ddbd951) — taofu-labs/true-performance-network
- 2026-10-05 · commit · [fix line + test](https://github.com/taofu-labs/true-performance-network/commit/0547a834a0a9bdec67a47f1770671d5d6e8f7a31) — taofu-labs/true-performance-network
- 2026-10-01 · commit · [update miner docs](https://github.com/taofu-labs/true-performance-network/commit/9d4e3cd3ac86c6a099ba6adec9af3bac687e8a72) — taofu-labs/true-performance-network
- 2026-09-30 · commit · [add proper ram tracking](https://github.com/taofu-labs/true-performance-network/commit/79718e5fa4ae1cdb82de3520c5d02371ec4d6d5e) — taofu-labs/true-performance-network
- 2026-09-17 · commit · [Merge pull request #8 from taofu-labs/testnet](https://github.com/taofu-labs/true-performance-network/commit/ea73f1ecccb6bb804eb986e0e51acb7991ba81d2) — taofu-labs/true-performance-network
- 2026-09-17 · commit · [fix follower loop](https://github.com/taofu-labs/true-performance-network/commit/ff76ee8076ab0b54950bf5eec2d4d68228ba3302) — taofu-labs/true-performance-network
- 2026-09-14 · commit · [add pause option per competition](https://github.com/taofu-labs/true-performance-network/commit/1433f8f4d633fab7a7de5a6a3e1454bb4304de7b) — taofu-labs/true-performance-network

## Use

```bash
m subnets.sn65/info        # live identity + market (snapshot if bt is down)
m subnets.sn65/news        # scraped news
m subnets.sn65/trades      # 24h alpha tape
m subnets.sn65/daily       # daily candles
python3 orbit/subnets/sn65/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
