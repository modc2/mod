# sn26 — Perturb ב

Decentralized adversarial robustness network

Bittensor subnet **26** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/0xsigurd/Perturb) · [url](https://www.perturbai.io/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002273 | -0.13% | -0.44% | -5.20% | 3,848 | 1,633 | 23.67 |

## Last 24h flow

8 trades by 5 coldkeys · 2 buys (10.00 τ) / 6 sells (13.22 τ) · net -3.21 τ

## News

- 2026-09-28 · commit · [Merge pull request #58 from 0xsigurd/dev](https://github.com/0xsigurd/Perturb/commit/d3bb64ee9c554da6c0d005da9172f1b53b8fef8a) — 0xsigurd/Perturb
- 2026-09-28 · commit · [Merge pull request #57 from 0xsigurd/feat/adversarial-training-evalua…](https://github.com/0xsigurd/Perturb/commit/035c0f2ff4de00f8e2f25758db557f5462d0baec) — 0xsigurd/Perturb
- 2026-09-28 · commit · [fix: seed evaluation sampling from the pinned dataset commit so it ca…](https://github.com/0xsigurd/Perturb/commit/10679c5d472a9e630a0d5bb725d2bcd122c69ccc) — 0xsigurd/Perturb
- 2026-09-22 · commit · [Merge pull request #56 from 0xsigurd/dev](https://github.com/0xsigurd/Perturb/commit/3a12b3a87c9a10b0c067fb47e4e5c24ddb98ca9c) — 0xsigurd/Perturb
- 2026-09-22 · commit · [Merge pull request #55 from 0xsigurd/feat/adversarial-training-evalua…](https://github.com/0xsigurd/Perturb/commit/612aa46aa3fb4fb131aa4aabb6a88316d634a6c4) — 0xsigurd/Perturb

## Use

```bash
m subnets.sn26/info        # live identity + market (snapshot if bt is down)
m subnets.sn26/news        # scraped news
m subnets.sn26/trades      # 24h alpha tape
m subnets.sn26/daily       # daily candles
python3 orbit/subnets/sn26/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
