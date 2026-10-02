# sn6 — Numinous ζ

Numinous is a forecasting protocol whose goal is to aggregate agents into superhuman LLM forecasters.

Bittensor subnet **6** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/numinouslabs/numinous) · [url](https://numinouslabs.io/) · discord `amedeo_ma`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002294 | +0.01% | -0.46% | -2.29% | 13,544 | 7,444 | 145.71 |

## Last 24h flow

35 trades by 21 coldkeys · 5 buys (57.24 τ) / 30 sells (69.45 τ) · net -12.20 τ

## News

- 2026-09-22 · commit · [Merge pull request #55 from numinouslabs/sync-main-2026-09-22-17-30](https://github.com/numinouslabs/numinous/commit/e32cf9dfa0e3f3cb627e262b9f4547e490e08b76) — numinouslabs/numinous
- 2026-09-22 · commit · [Release 2026-09-22-17-30 from main](https://github.com/numinouslabs/numinous/commit/316a0e4c27187f85453c4ed3721581e4e0771e01) — numinouslabs/numinous
- 2026-09-18 · commit · [Merge pull request #54 from numinouslabs/docs-reasoning-trajectories-…](https://github.com/numinouslabs/numinous/commit/4f6e55bce3b01db0af8a25395672dcd80c513a7f) — numinouslabs/numinous
- 2026-09-18 · commit · [Reference the WorldReasoner paper in the reasoning scoring doc](https://github.com/numinouslabs/numinous/commit/b0b0761ef022fde6ca9144ff675a7dffd89f2a83) — numinouslabs/numinous
- 2026-09-18 · commit · [Document reasoning scoring against hindsight ledgers](https://github.com/numinouslabs/numinous/commit/2e8dd0fee10aab54c3e5ab02ce1725eb46366808) — numinouslabs/numinous

## Use

```bash
m subnets.sn6/info        # live identity + market (snapshot if bt is down)
m subnets.sn6/news        # scraped news
m subnets.sn6/trades      # 24h alpha tape
m subnets.sn6/daily       # daily candles
python3 orbit/subnets/sn6/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
