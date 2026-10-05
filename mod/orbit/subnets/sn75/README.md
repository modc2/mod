# sn75 — Hippius م

Blockchain-backed cloud: storage, VMs, and apps with unmatched transparency, trust, and power.

Bittensor subnet **75** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/thenervelab/thebrain) · [url](https://hippius.com/)

Fleet mods for this subnet: `hippius`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.016049 | +0.03% | -1.19% | -6.31% | 91,987 | 32,304 | 2,443 |

## Last 24h flow

189 trades by 89 coldkeys · 87 buys (1,072 τ) / 102 sells (1,307 τ) · net -235.12 τ

## News

- 2026-09-25 · commit · [Merge pull request #60 from thenervelab/feat/marketplace-compute-usage](https://github.com/thenervelab/thebrain/commit/6c42aa9da10027cfdf89527e805390000761905b) — thenervelab/thebrain
- 2026-09-24 · commit · [fix(marketplace): price compute billing proof size at FRAME's unbound…](https://github.com/thenervelab/thebrain/commit/dc8f2b5bc6baebb89f71bf5b9bba31ff5cc61176) — thenervelab/thebrain
- 2026-09-24 · commit · [feat(marketplace): charge hourly compute usage once per account and p…](https://github.com/thenervelab/thebrain/commit/cc94609226a34ddcb4298a672bb2e59ad7753bf7) — thenervelab/thebrain
- 2026-09-09 · commit · [Merge pull request #59 from thenervelab/feat/s3-hourly-price](https://github.com/thenervelab/thebrain/commit/3187525a9317ff70f7157c930124e19858dfe235) — thenervelab/thebrain
- 2026-09-09 · commit · [feat(marketplace): price S3 storage per hour separately from Drive](https://github.com/thenervelab/thebrain/commit/888c6424ff961230a778b734d52d168380b0ff0a) — thenervelab/thebrain

## Use

```bash
m subnets.sn75/info        # live identity + market (snapshot if bt is down)
m subnets.sn75/news        # scraped news
m subnets.sn75/trades      # 24h alpha tape
m subnets.sn75/daily       # daily candles
python3 orbit/subnets/sn75/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
