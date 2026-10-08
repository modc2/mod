# sn7 — Allways η

universal transaction layer

Bittensor subnet **7** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/entrius/allways) · [url](https://all-ways.io/) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002458 | -0.00% | +0.04% | -1.50% | 13,334 | 5,584 | 3.90 |

## Last 24h flow

13 trades by 8 coldkeys · 5 buys (2.57 τ) / 8 sells (0.97 τ) · net 1.61 τ

## News

- 2026-09-25 · commit · [Fail the subtensor over on rate limits, return it to the primary once…](https://github.com/entrius/allways/commit/9767f921ea3aa86913fcd143ae65bd4390789d05) — entrius/allways
- 2026-09-25 · release · [release-20260926-135859](https://github.com/entrius/allways/releases/tag/release-20260926-135859) — entrius/allways
- 2026-09-25 · commit · [Bump version to 3.5.0 for the subnet alpha release (#762)](https://github.com/entrius/allways/commit/4ac543e1c58440d895649ec7d2928d1393eed3cb) — entrius/allways
- 2026-09-25 · commit · [Back the subtensor endpoint with env fallback and archive lists and r…](https://github.com/entrius/allways/commit/c4d1360afe457a8fb6eacdbc40ecc07bf752e9fd) — entrius/allways
- 2026-09-25 · commit · [Drop tao↔alpha pairs: subtensor swaps them natively (#761)](https://github.com/entrius/allways/commit/0a1276e92f06320da718006f4024340a83c8c3a3) — entrius/allways
- 2026-09-25 · commit · [Say an alpha leg makes a valid pair in the CLI pair refusal (#759)](https://github.com/entrius/allways/commit/253db2a931ddb0f8b3c55840cfcc0755104acf66) — entrius/allways
- 2026-09-11 · release · [release-20260911-013831](https://github.com/entrius/allways/releases/tag/release-20260911-013831) — entrius/allways

## Use

```bash
m subnets.sn7/info        # live identity + market (snapshot if bt is down)
m subnets.sn7/news        # scraped news
m subnets.sn7/trades      # 24h alpha tape
m subnets.sn7/daily       # daily candles
python3 orbit/subnets/sn7/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
