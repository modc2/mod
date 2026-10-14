# sn7 — Allways η

universal transaction layer

Bittensor subnet **7** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/entrius/allways) · [url](https://all-ways.io/) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002301 | -0.00% | -0.69% | -6.34% | 12,580 | 5,404 | 20.01 |

## Last 24h flow

42 trades by 9 coldkeys · 1 buys (0.50 τ) / 41 sells (18.92 τ) · net -18.41 τ

## News

- 2026-10-13 · commit · [Fix the bond relay's boot stall, spread its audit across steps, trim …](https://github.com/entrius/allways/commit/a6c5b752dea8289fdba28ca0e3f912a6ba2aabbd) — entrius/allways
- 2026-10-13 · release · [release-20261013-222136](https://github.com/entrius/allways/releases/tag/release-20261013-222136) — entrius/allways
- 2026-10-13 · commit · [Ban a miner from scoring for 12 rounds when it posts an unusable addr…](https://github.com/entrius/allways/commit/44162fafcea874d07a3068b1f0dae03dadf09240) — entrius/allways
- 2026-10-13 · commit · [Route takers only to quotes whose miner addresses their chains accept…](https://github.com/entrius/allways/commit/d9533e056bfd195b611b7f23473049f2253ce2ec) — entrius/allways
- 2026-10-13 · commit · [Stop paying quotes that name an unusable miner address (#765)](https://github.com/entrius/allways/commit/726a87983a5a4000049fcf7a9a0bde750ec84938) — entrius/allways
- 2026-09-25 · commit · [Fail the subtensor over on rate limits, return it to the primary once…](https://github.com/entrius/allways/commit/9767f921ea3aa86913fcd143ae65bd4390789d05) — entrius/allways
- 2026-09-25 · release · [release-20260926-135859](https://github.com/entrius/allways/releases/tag/release-20260926-135859) — entrius/allways
- 2026-09-25 · commit · [Bump version to 3.5.0 for the subnet alpha release (#762)](https://github.com/entrius/allways/commit/4ac543e1c58440d895649ec7d2928d1393eed3cb) — entrius/allways

## Use

```bash
m subnets.sn7/info        # live identity + market (snapshot if bt is down)
m subnets.sn7/news        # scraped news
m subnets.sn7/trades      # 24h alpha tape
m subnets.sn7/daily       # daily candles
python3 orbit/subnets/sn7/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
