# sn7 — Allways η

universal transaction layer

Bittensor subnet **7** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/entrius/allways) · [url](https://all-ways.io/) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002206 | -0.00% | -3.11% | -10.13% | 12,090 | 5,292 | 136.02 |

## Last 24h flow

57 trades by 17 coldkeys · 5 buys (25.06 τ) / 52 sells (109.84 τ) · net -84.78 τ

## News

- 2026-10-15 · commit · [Quote the min for alpha pairs from each candidate's floor (#775)](https://github.com/entrius/allways/commit/621cde54d0a50fb3d04ed7576fae4f2e0ce6c847) — entrius/allways
- 2026-10-15 · release · [release-20261015-220818: Quote the min for alpha pairs from each candidate's floor (#775)](https://github.com/entrius/allways/releases/tag/release-20261015-220818) — entrius/allways
- 2026-10-15 · commit · [Serialize shared substrate requests (#773)](https://github.com/entrius/allways/commit/79b5542d9770a0b0eea05b472af24f0c00eadad3) — entrius/allways
- 2026-10-15 · commit · [Quote /rate from held miner states and a lazy market memo (#774)](https://github.com/entrius/allways/commit/584bd538c29b16fb9cff4c7ccb8b214ea0106204) — entrius/allways
- 2026-10-15 · commit · [Gate quotes and crown reconcile on the offer's own backing bit (#772)](https://github.com/entrius/allways/commit/3bcbe2fe47107f2f64355e510a76a224c9a26c60) — entrius/allways
- 2026-10-15 · release · [release-20261015-164605: Gate quotes and crown reconcile on the offer's own backing bit (#772)](https://github.com/entrius/allways/releases/tag/release-20261015-164605) — entrius/allways
- 2026-10-15 · commit · [Add USDC on Avalanche and HyperEVM as launch spokes (#771)](https://github.com/entrius/allways/commit/d33911a9897a2247b82cfe22347ad30db3ece57e) — entrius/allways
- 2026-10-14 · commit · [Report the payer's source address in swap status (#770)](https://github.com/entrius/allways/commit/904249df51fadbdfce39c9bf2c5ec54c1e97d76d) — entrius/allways

## Use

```bash
m subnets.sn7/info        # live identity + market (snapshot if bt is down)
m subnets.sn7/news        # scraped news
m subnets.sn7/trades      # 24h alpha tape
m subnets.sn7/daily       # daily candles
python3 orbit/subnets/sn7/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
