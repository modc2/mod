# sn12 — Compute Horde μ

Bittensor subnet **12** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/backend-developers-ltd/ComputeHorde/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004853 | -0.00% | +0.76% | +0.97% | 30,966 | 12,756 | 50.48 |

## Last 24h flow

12 trades by 7 coldkeys · 6 buys (47.13 τ) / 6 sells (1.00 τ) · net 46.13 τ

## News

- 2026-09-18 · commit · [fix: evict old neurons in allowance evict_old_data task](https://github.com/backend-developers-ltd/ComputeHorde/commit/d7ba0880c13528fb45c873a6a1bb7e9dc3e29bc5) — backend-developers-ltd/ComputeHorde
- 2026-09-18 · release · [executor-staging-2026-09-18-35367827863-335-1: fix: evict old neurons in allowance evict_old_data task](https://github.com/backend-developers-ltd/ComputeHorde/releases/tag/executor-staging-2026-09-18-35367827863-335-1) — backend-developers-ltd/ComputeHorde
- 2026-09-18 · release · [miner-staging-2026-09-18-35367827890-422-1: fix: evict old neurons in allowance evict_old_data task](https://github.com/backend-developers-ltd/ComputeHorde/releases/tag/miner-staging-2026-09-18-35367827890-422-1) — backend-developers-ltd/ComputeHorde
- 2026-09-18 · release · [validator-preprod-2026-09-18-35368194640-313-1: fix: evict old neurons in allowance evict_old_data task](https://github.com/backend-developers-ltd/ComputeHorde/releases/tag/validator-preprod-2026-09-18-35368194640-313-1) — backend-developers-ltd/ComputeHorde
- 2026-09-18 · release · [validator-staging-2026-09-18-35368036673-626-1: fix: evict old neurons in allowance evict_old_data task](https://github.com/backend-developers-ltd/ComputeHorde/releases/tag/validator-staging-2026-09-18-35368036673-626-1) — backend-developers-ltd/ComputeHorde

## Use

```bash
m subnets.sn12/info        # live identity + market (snapshot if bt is down)
m subnets.sn12/news        # scraped news
m subnets.sn12/trades      # 24h alpha tape
m subnets.sn12/daily       # daily candles
python3 orbit/subnets/sn12/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
