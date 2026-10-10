# sn65 — True Performance Network ص

Distributed AI model compression & optimization engine

Bittensor subnet **65** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/taofu-labs/true-performance-network) · [url](https://www.trueperformancenetwork.com/) · [discord](https://discord.com/invite/GRVZyPYd6G)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002168 | +0.00% | +0.49% | +7.36% | 13,155 | 5,642 | 213.85 |

## Last 24h flow

33 trades by 27 coldkeys · 14 buys (113.83 τ) / 19 sells (99.75 τ) · net 14.08 τ

## News

- 2026-10-08 · commit · [Merge pull request #13 from taofu-labs/testnet](https://github.com/taofu-labs/true-performance-network/commit/2994eb0831685d1400e23834a0f50c04805fbd73) — taofu-labs/true-performance-network
- 2026-10-08 · commit · [fix fluid bench epoch settings](https://github.com/taofu-labs/true-performance-network/commit/1ccd32f30c19bf34f2b3c863769caed10916ce17) — taofu-labs/true-performance-network
- 2026-10-05 · commit · [Merge pull request #12 from taofu-labs/testnet](https://github.com/taofu-labs/true-performance-network/commit/7ae25fa7e531e72dd22c48fd4c3a15aabfe7d606) — taofu-labs/true-performance-network
- 2026-10-05 · commit · [Merge pull request #11 from taofu-labs/fix/overreport](https://github.com/taofu-labs/true-performance-network/commit/3a813f82793aa832ca31d049c9d519f70ddbd951) — taofu-labs/true-performance-network
- 2026-10-05 · commit · [fix line + test](https://github.com/taofu-labs/true-performance-network/commit/0547a834a0a9bdec67a47f1770671d5d6e8f7a31) — taofu-labs/true-performance-network
- 2026-10-01 · commit · [update miner docs](https://github.com/taofu-labs/true-performance-network/commit/9d4e3cd3ac86c6a099ba6adec9af3bac687e8a72) — taofu-labs/true-performance-network
- 2026-09-30 · commit · [add proper ram tracking](https://github.com/taofu-labs/true-performance-network/commit/79718e5fa4ae1cdb82de3520c5d02371ec4d6d5e) — taofu-labs/true-performance-network
- 2026-09-17 · commit · [Merge pull request #8 from taofu-labs/testnet](https://github.com/taofu-labs/true-performance-network/commit/ea73f1ecccb6bb804eb986e0e51acb7991ba81d2) — taofu-labs/true-performance-network

## Use

```bash
m subnets.sn65/info        # live identity + market (snapshot if bt is down)
m subnets.sn65/news        # scraped news
m subnets.sn65/trades      # 24h alpha tape
m subnets.sn65/daily       # daily candles
python3 orbit/subnets/sn65/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
