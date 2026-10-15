# sn117 — everyframe.studio Ⲁ

Every frame gets craft

Bittensor subnet **117** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/everyframe-studios/everyframe-miner) · [url](https://everyframe.studio/) · [discord](https://discord.com/channels/799672011265015819/1544011415692644444)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002645 | -0.00% | +0.74% | +1.04% | 8,106 | 2,940 | 112.25 |

## Last 24h flow

17 trades by 8 coldkeys · 11 buys (61.49 τ) / 6 sells (50.22 τ) · net 11.27 τ

## News

- 2026-10-14 · release · [everycli v0.5.6](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.5.6) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [Merge pull request #35 from everyframe-studio/release/v0.5.6-recovery…](https://github.com/everyframe-studio/everyframe-miner/commit/9fe18293c7e83ac56ef7ed7c7dc3a4d8a3d1729d) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [fix: reconcile abandoned deployment preparation and release v0.5.6](https://github.com/everyframe-studio/everyframe-miner/commit/a9c90e6d28fbbbc9d78aae06b276012c12c76f11) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [feat: report Fal, BFL and Runway balances from the keys workers run with](https://github.com/everyframe-studio/everyframe-miner/commit/33d893eb78d591e35a5d855dccf1f60e40e85724) — everyframe-studios/everyframe-miner
- 2026-10-14 · release · [everycli v0.5.5](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.5.5) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [release: everycli v0.5.5 dashboard and atomic batch offers (#34)](https://github.com/everyframe-studio/everyframe-miner/commit/60b471926f98e88867af11ae3010c9b867f85be2) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [fix: streamline model offers and miner dashboard reporting (#33)](https://github.com/everyframe-studio/everyframe-miner/commit/930701e49f0650e0ce4cbdfe9d085c530c32dbda) — everyframe-studios/everyframe-miner
- 2026-10-14 · release · [everycli v0.5.4](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.5.4) — everyframe-studios/everyframe-miner

## Use

```bash
m subnets.sn117/info        # live identity + market (snapshot if bt is down)
m subnets.sn117/news        # scraped news
m subnets.sn117/trades      # 24h alpha tape
m subnets.sn117/daily       # daily candles
python3 orbit/subnets/sn117/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
