# sn7 — Allways η

universal transaction layer

Bittensor subnet **7** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/entrius/allways) · [url](https://all-ways.io/) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002277 | -0.01% | -1.04% | -7.36% | 12,463 | 5,376 | 61.94 |

## Last 24h flow

55 trades by 14 coldkeys · 5 buys (16.71 τ) / 50 sells (44.96 τ) · net -28.25 τ

## News

- 2026-10-15 · commit · [Add USDC on Avalanche and HyperEVM as launch spokes (#771)](https://github.com/entrius/allways/commit/d33911a9897a2247b82cfe22347ad30db3ece57e) — entrius/allways
- 2026-10-14 · commit · [Report the payer's source address in swap status (#770)](https://github.com/entrius/allways/commit/904249df51fadbdfce39c9bf2c5ec54c1e97d76d) — entrius/allways
- 2026-10-13 · commit · [Fix the bond relay's boot stall, spread its audit across steps, trim …](https://github.com/entrius/allways/commit/a6c5b752dea8289fdba28ca0e3f912a6ba2aabbd) — entrius/allways
- 2026-10-13 · release · [release-20261013-222136](https://github.com/entrius/allways/releases/tag/release-20261013-222136) — entrius/allways
- 2026-10-13 · commit · [Ban a miner from scoring for 12 rounds when it posts an unusable addr…](https://github.com/entrius/allways/commit/44162fafcea874d07a3068b1f0dae03dadf09240) — entrius/allways
- 2026-10-13 · commit · [Route takers only to quotes whose miner addresses their chains accept…](https://github.com/entrius/allways/commit/d9533e056bfd195b611b7f23473049f2253ce2ec) — entrius/allways
- 2026-10-13 · commit · [Stop paying quotes that name an unusable miner address (#765)](https://github.com/entrius/allways/commit/726a87983a5a4000049fcf7a9a0bde750ec84938) — entrius/allways
- 2026-09-25 · commit · [Fail the subtensor over on rate limits, return it to the primary once…](https://github.com/entrius/allways/commit/9767f921ea3aa86913fcd143ae65bd4390789d05) — entrius/allways

## Use

```bash
m subnets.sn7/info        # live identity + market (snapshot if bt is down)
m subnets.sn7/news        # scraped news
m subnets.sn7/trades      # 24h alpha tape
m subnets.sn7/daily       # daily candles
python3 orbit/subnets/sn7/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
