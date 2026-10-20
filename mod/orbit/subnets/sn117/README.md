# sn117 — everyframe.studio Ⲁ

Every frame gets craft

Bittensor subnet **117** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/everyframe-studios/everyframe-miner) · [url](https://everyframe.studio/) · [discord](https://discord.com/channels/799672011265015819/1544011415692644444)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002180 | +0.00% | -1.66% | -18.71% | 6,725 | 2,669 | 22.75 |

## Last 24h flow

7 trades by 7 coldkeys · 1 buys (0.11 τ) / 6 sells (21.09 τ) · net -20.98 τ

## News

- 2026-10-19 · release · [everycli v0.5.8](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.5.8) — everyframe-studios/everyframe-miner
- 2026-10-19 · commit · [Merge pull request #37 from everyframe-studio/fix/provider-catalog-av…](https://github.com/everyframe-studio/everyframe-miner/commit/b1c20c77f4bbdbab8574d5faaa5d44dfd58b7f7b) — everyframe-studios/everyframe-miner
- 2026-10-19 · commit · [release: prepare everycli v0.5.8 provider availability fixes](https://github.com/everyframe-studio/everyframe-miner/commit/9ffd78aed252dab03d3a736a54e0960224a52ae6) — everyframe-studios/everyframe-miner
- 2026-10-19 · commit · [fix: classify catalog refusals as safe pre-submit rejections](https://github.com/everyframe-studio/everyframe-miner/commit/ea6910d79af8e556ac7cbd041e93495989953551) — everyframe-studios/everyframe-miner
- 2026-10-19 · commit · [fix: validate provider model availability and clarify catalog IDs](https://github.com/everyframe-studio/everyframe-miner/commit/8a2ee9f55349334481b35297caae3063aae27333) — everyframe-studios/everyframe-miner
- 2026-10-15 · commit · [Merge pull request #36 from everyframe-studio/fix/report-provider-fai…](https://github.com/everyframe-studio/everyframe-miner/commit/9addfdba9182aaaaebed2b363f1e4fa6f6cf027f) — everyframe-studios/everyframe-miner
- 2026-10-15 · release · [v0.5.7](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.5.7) — everyframe-studios/everyframe-miner
- 2026-10-15 · commit · [fix: preserve provider failure categories and prepare v0.5.7](https://github.com/everyframe-studio/everyframe-miner/commit/39d431a19d857f20e779d15a58748d682b47f51a) — everyframe-studios/everyframe-miner

## Use

```bash
m subnets.sn117/info        # live identity + market (snapshot if bt is down)
m subnets.sn117/news        # scraped news
m subnets.sn117/trades      # 24h alpha tape
m subnets.sn117/daily       # daily candles
python3 orbit/subnets/sn117/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
