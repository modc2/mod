# sn117 — everyframe.studio Ⲁ

Every frame gets craft

Bittensor subnet **117** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/everyframe-studios/everyframe-miner) · [url](https://everyframe.studio/) · [discord](https://discord.com/channels/799672011265015819/1544011415692644444)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002330 | +0.14% | -11.91% | -11.83% | 7,150 | 2,760 | 466.25 |

## Last 24h flow

39 trades by 18 coldkeys · 18 buys (142.84 τ) / 21 sells (322.75 τ) · net -179.92 τ

## News

- 2026-10-15 · commit · [Merge pull request #36 from everyframe-studio/fix/report-provider-fai…](https://github.com/everyframe-studio/everyframe-miner/commit/9addfdba9182aaaaebed2b363f1e4fa6f6cf027f) — everyframe-studios/everyframe-miner
- 2026-10-15 · release · [v0.5.7](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.5.7) — everyframe-studios/everyframe-miner
- 2026-10-15 · commit · [fix: preserve provider failure categories and prepare v0.5.7](https://github.com/everyframe-studio/everyframe-miner/commit/39d431a19d857f20e779d15a58748d682b47f51a) — everyframe-studios/everyframe-miner
- 2026-10-15 · commit · [Report a fixed provider failure category; renew sessions for 12-minut…](https://github.com/everyframe-studio/everyframe-miner/commit/b30e92eac6dee0393808431e0da705820f74c09f) — everyframe-studios/everyframe-miner
- 2026-10-14 · release · [everycli v0.5.6](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.5.6) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [Merge pull request #35 from everyframe-studio/release/v0.5.6-recovery…](https://github.com/everyframe-studio/everyframe-miner/commit/9fe18293c7e83ac56ef7ed7c7dc3a4d8a3d1729d) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [fix: reconcile abandoned deployment preparation and release v0.5.6](https://github.com/everyframe-studio/everyframe-miner/commit/a9c90e6d28fbbbc9d78aae06b276012c12c76f11) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [feat: report Fal, BFL and Runway balances from the keys workers run with](https://github.com/everyframe-studio/everyframe-miner/commit/33d893eb78d591e35a5d855dccf1f60e40e85724) — everyframe-studios/everyframe-miner

## Use

```bash
m subnets.sn117/info        # live identity + market (snapshot if bt is down)
m subnets.sn117/news        # scraped news
m subnets.sn117/trades      # 24h alpha tape
m subnets.sn117/daily       # daily candles
python3 orbit/subnets/sn117/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
