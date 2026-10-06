# sn68 — NOVA ظ

Accelerating drug discovery.

Bittensor subnet **68** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/metanova-labs/nova/) · [url](https://www.metanova-labs.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021624 | +0.02% | +0.20% | +3.77% | 128,536 | 44,715 | 238.63 |

## Last 24h flow

68 trades by 50 coldkeys · 25 buys (52.00 τ) / 43 sells (82.22 τ) · net -30.22 τ

## News

- 2026-09-16 · commit · [Update config.yaml](https://github.com/metanova-labs/nova/commit/623f3456088f651609a1bcb870ab0587b0dc0520) — metanova-labs/nova
- 2026-09-16 · commit · [update submodule to include retry of TNP errors](https://github.com/metanova-labs/nova/commit/e510bf77db2f90d2386945cf72ec2156b45753f5) — metanova-labs/nova
- 2026-09-16 · commit · [Merge pull request #92 from thomasvangurp/fix/retry-tnp-compute-errors](https://github.com/metanova-labs/nova/commit/dc42566c760f3fb9d69aa4207b99ff976796baf7) — metanova-labs/nova
- 2026-09-15 · commit · [Retry TNP profiles that failed to compute, before rejecting the submi…](https://github.com/metanova-labs/nova/commit/161b7d3c566828ad2d1e021fd3f046b5b430f9da) — metanova-labs/nova
- 2026-09-09 · commit · [Merge branch 'main' of https://github.com/metanova-labs/nova](https://github.com/metanova-labs/nova/commit/607b07a61389008cb60a09c424e4a56011ea9014) — metanova-labs/nova

## Use

```bash
m subnets.sn68/info        # live identity + market (snapshot if bt is down)
m subnets.sn68/news        # scraped news
m subnets.sn68/trades      # 24h alpha tape
m subnets.sn68/daily       # daily candles
python3 orbit/subnets/sn68/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
