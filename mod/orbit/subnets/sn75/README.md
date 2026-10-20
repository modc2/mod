# sn75 — Hippius م

Blockchain-backed cloud: storage, VMs, and apps with unmatched transparency, trust, and power.

Bittensor subnet **75** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/thenervelab/thebrain) · [url](https://hippius.com/)

Fleet mods for this subnet: `hippius`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.018358 | +0.08% | +1.76% | +7.81% | 107,402 | 34,827 | 3,481 |

## Last 24h flow

357 trades by 129 coldkeys · 207 buys (1,827 τ) / 150 sells (1,579 τ) · net 248.62 τ

## News

- 2026-10-19 · commit · [Merge pull request #73 from thenervelab/fix/hip-01-ranking-registration](https://github.com/thenervelab/thebrain/commit/32b5dae555d7a691d560e17fb0557250e26d5bef) — thenervelab/thebrain
- 2026-10-19 · commit · [fix: resolve code review findings for PR #73](https://github.com/thenervelab/thebrain/commit/3a3372279130be6504a3f6714a87565de685984b) — thenervelab/thebrain
- 2026-10-19 · commit · [Merge branch 'main' into fix/hip-01-ranking-registration](https://github.com/thenervelab/thebrain/commit/151d3aac129567a53b785503e8e9de90f8569d01) — thenervelab/thebrain
- 2026-10-19 · commit · [updated spec version](https://github.com/thenervelab/thebrain/commit/39fbcd49ff60848805a739a4422ca01d544292ef) — thenervelab/thebrain
- 2026-10-19 · commit · [fix(security): restrict ranking writes and bind registration signer t…](https://github.com/thenervelab/thebrain/commit/906464a0586f49bdf1f27b555cd1563d43375c14) — thenervelab/thebrain
- 2026-10-15 · commit · [Merge pull request #70 from thenervelab/chore/runtime-spec-92018](https://github.com/thenervelab/thebrain/commit/2af9ec4c57cd45d5ff34d0c87e540c1d7a78d2d0) — thenervelab/thebrain
- 2026-10-15 · commit · [chore(runtime): bump mainnet spec_version to 92018](https://github.com/thenervelab/thebrain/commit/76a9a1fb4b1a0f07a7950637cb0c1307870f477f) — thenervelab/thebrain
- 2026-10-05 · commit · [Merge pull request #63 from thenervelab/feat/marketplace-buy-credits](https://github.com/thenervelab/thebrain/commit/530ef9997385b54fa450aab4e5835dd3865a18de) — thenervelab/thebrain

## Use

```bash
m subnets.sn75/info        # live identity + market (snapshot if bt is down)
m subnets.sn75/news        # scraped news
m subnets.sn75/trades      # 24h alpha tape
m subnets.sn75/daily       # daily candles
python3 orbit/subnets/sn75/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
