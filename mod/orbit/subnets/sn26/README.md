# sn26 — Perturb ב

Decentralized adversarial robustness network

Bittensor subnet **26** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/0xsigurd/Perturb) · [url](https://www.perturbai.io/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002248 | +0.00% | -1.08% | -8.51% | 3,823 | 1,624 | 9.74 |

## Last 24h flow

9 trades by 7 coldkeys · 1 buys (0.43 τ) / 8 sells (7.17 τ) · net -6.74 τ

## News

- 2026-10-04 · commit · [Merge pull request #60 from 0xsigurd/dev](https://github.com/0xsigurd/Perturb/commit/0dad95434f28357ec518eb45ec690bc6b5f8ef7e) — 0xsigurd/Perturb
- 2026-10-04 · commit · [Merge pull request #59 from 0xsigurd/feat/adversarial-training-evalua…](https://github.com/0xsigurd/Perturb/commit/2d25075a6aa4025f11115f627fc50d20055fd574) — 0xsigurd/Perturb
- 2026-10-04 · commit · [feat: rank scanning miners by stake-weighted consensus rank instead o…](https://github.com/0xsigurd/Perturb/commit/1c87c921850307d6b09e40c70225ac1eb24bd790) — 0xsigurd/Perturb
- 2026-10-04 · commit · [feat: weight model evaluation 3:7 toward adversarial accuracy](https://github.com/0xsigurd/Perturb/commit/d111420e60a10b90bee3c39ee80123fe9630aad2) — 0xsigurd/Perturb
- 2026-09-28 · commit · [Merge pull request #58 from 0xsigurd/dev](https://github.com/0xsigurd/Perturb/commit/d3bb64ee9c554da6c0d005da9172f1b53b8fef8a) — 0xsigurd/Perturb
- 2026-09-28 · commit · [Merge pull request #57 from 0xsigurd/feat/adversarial-training-evalua…](https://github.com/0xsigurd/Perturb/commit/035c0f2ff4de00f8e2f25758db557f5462d0baec) — 0xsigurd/Perturb
- 2026-09-28 · commit · [fix: seed evaluation sampling from the pinned dataset commit so it ca…](https://github.com/0xsigurd/Perturb/commit/10679c5d472a9e630a0d5bb725d2bcd122c69ccc) — 0xsigurd/Perturb
- 2026-09-22 · commit · [Merge pull request #56 from 0xsigurd/dev](https://github.com/0xsigurd/Perturb/commit/3a12b3a87c9a10b0c067fb47e4e5c24ddb98ca9c) — 0xsigurd/Perturb

## Use

```bash
m subnets.sn26/info        # live identity + market (snapshot if bt is down)
m subnets.sn26/news        # scraped news
m subnets.sn26/trades      # 24h alpha tape
m subnets.sn26/daily       # daily candles
python3 orbit/subnets/sn26/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
