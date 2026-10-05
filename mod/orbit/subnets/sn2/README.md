# sn2 — DSperse β

Verifiable and distributed inference on Bittensor

Bittensor subnet **2** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/inference-labs-inc/subnet-2) · [url](https://subnet2.inferencelabs.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003039 | -0.20% | -0.16% | -0.98% | 17,915 | 7,704 | 68.32 |

## Last 24h flow

45 trades by 16 coldkeys · 8 buys (31.01 τ) / 37 sells (36.42 τ) · net -5.41 τ

## News

- 2026-09-08 · release · [14.14.3](https://github.com/inference-labs-inc/subnet-2/releases/tag/14.14.3) — inference-labs-inc/subnet-2
- 2026-09-08 · release · [Testnet (testnet-92c75cee)](https://github.com/inference-labs-inc/subnet-2/releases/tag/testnet-92c75cee) — inference-labs-inc/subnet-2
- 2026-09-08 · commit · [Merge testnet into main for 14.14.3 release](https://github.com/inference-labs-inc/subnet-2/commit/96d03c44729a350df79160e2ea8e3e175d492ce5) — inference-labs-inc/subnet-2
- 2026-09-08 · commit · [Introduce weight commit guard for epochs with zero miner scores (#627)](https://github.com/inference-labs-inc/subnet-2/commit/92c75cee84551e73e7629b7a7306a0ca90f91e6d) — inference-labs-inc/subnet-2

## Use

```bash
m subnets.sn2/info        # live identity + market (snapshot if bt is down)
m subnets.sn2/news        # scraped news
m subnets.sn2/trades      # 24h alpha tape
m subnets.sn2/daily       # daily candles
python3 orbit/subnets/sn2/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
