# sn68 — NOVA ظ

Accelerating drug discovery.

Bittensor subnet **68** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/metanova-labs/nova/) · [url](https://www.metanova-labs.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.018297 | +0.00% | -5.50% | -8.64% | 110,292 | 41,344 | 1,657 |

## Last 24h flow

92 trades by 66 coldkeys · 34 buys (133.29 τ) / 58 sells (1,410 τ) · net -1,276 τ

## News

- 2026-10-13 · commit · [read molecule filter thresholds from filters config in save_data](https://github.com/metanova-labs/nova/commit/15c59f2de81035401031c22755f7cb02013b9947) — metanova-labs/nova
- 2026-10-13 · commit · [Point immunebuilder installation to fixed repo](https://github.com/metanova-labs/nova/commit/c340b912b7140448c47f087a88ff5b13ada528e0) — metanova-labs/nova
- 2026-10-13 · commit · [restore min_heavy_atoms to default Compound value](https://github.com/metanova-labs/nova/commit/e0ace9c6e204cf537ce275291394805d5d2e789a) — metanova-labs/nova
- 2026-10-13 · commit · [Merge pull request #99 from metanova-labs/feat/sanqi-molecule-filters](https://github.com/metanova-labs/nova/commit/610325697266a86a96d1866185da9f16441c8a2e) — metanova-labs/nova
- 2026-10-13 · commit · [update molecule filters for sanqi combinatorial db](https://github.com/metanova-labs/nova/commit/de21e073756d25644e0554ac6e5a8319ed57ab74) — metanova-labs/nova
- 2026-10-13 · commit · [Merge pull request #97 from metanova-labs/feat/sanqi-combinatorial-db](https://github.com/metanova-labs/nova/commit/d98e6f7b9be20e36bcc9e46e52e6f6bea34313bb) — metanova-labs/nova
- 2026-10-13 · commit · [Merge pull request #95 from metanova-labs/fix/subtensor-connection-ha…](https://github.com/metanova-labs/nova/commit/cf4e0a1892f96457e02a2fb190a27ab4ca17f521) — metanova-labs/nova
- 2026-10-08 · commit · [support sanqi combinatorial db](https://github.com/metanova-labs/nova/commit/d237a7d9ccda9f274b525e103755618f71167a40) — metanova-labs/nova

## Use

```bash
m subnets.sn68/info        # live identity + market (snapshot if bt is down)
m subnets.sn68/news        # scraped news
m subnets.sn68/trades      # 24h alpha tape
m subnets.sn68/daily       # daily candles
python3 orbit/subnets/sn68/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
