# sn97 — Ingot ა

Open models, cast small.

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/DendriteLabs/ingot) · [url](https://theingot.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.012800 | +0.01% | -1.14% | -6.32% | 26,168 | 10,860 | 1,772 |

## Last 24h flow

117 trades by 46 coldkeys · 52 buys (855.25 τ) / 65 sells (894.12 τ) · net -38.87 τ

## News

- 2026-10-19 · commit · [feat(model_validation): reject models with NaN or infinite weights](https://github.com/DendriteLabs/ingot/commit/8c22cdacb16b57abae07eebb352ca090f6ebd83f) — DendriteLabs/ingot
- 2026-10-19 · commit · [fix(web): lineage rows share the chart's height when there are few kings](https://github.com/DendriteLabs/ingot/commit/a7a3681846c0df8fc1e5fe07a79533254d9ac79b) — DendriteLabs/ingot
- 2026-10-19 · commit · [feat(web): kings lineage beside the staircase; three benchmarks in on…](https://github.com/DendriteLabs/ingot/commit/e719abc456373c40ecef10379bb3e393ddad122b) — DendriteLabs/ingot
- 2026-10-19 · commit · [fix(web): stop iOS Safari enlarging the fails table's text](https://github.com/DendriteLabs/ingot/commit/ba3e9dcf1236c83a0d5d414fb436ac86cd7a72ee) — DendriteLabs/ingot
- 2026-10-19 · commit · [feat(web): better charts](https://github.com/DendriteLabs/ingot/commit/62732716aea52e69fa536f9b8ff6a93325e0a5d1) — DendriteLabs/ingot
- 2026-10-19 · commit · [feat(model_dedup): only near-identical copies are duplicates (corr 0.…](https://github.com/DendriteLabs/ingot/commit/83097373f8a5a2b4209b5c66ff1a69988c1ae914) — DendriteLabs/ingot
- 2026-10-19 · commit · [fix(web): push the HTML as no-store and stamp scripts, styles and imp…](https://github.com/DendriteLabs/ingot/commit/1fba61df978c3c6d05954e7285146a09bc62beb5) — DendriteLabs/ingot
- 2026-10-19 · commit · [feat: win margins as a share of the king's score (KL 1.5%, distance 0…](https://github.com/DendriteLabs/ingot/commit/67a4a2413322ce355c849253dcc36670a0170abf) — DendriteLabs/ingot

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
