# sn2 — DSperse β

Verifiable and distributed inference on Bittensor

Bittensor subnet **2** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/inference-labs-inc/subnet-2) · [url](https://subnet2.inferencelabs.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002982 | -0.00% | -1.49% | -3.11% | 17,622 | 7,632 | 60.71 |

## Last 24h flow

17 trades by 14 coldkeys · 7 buys (1.57 τ) / 10 sells (58.73 τ) · net -57.17 τ

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
