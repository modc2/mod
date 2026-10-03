# sn75 — Hippius م

Blockchain-backed cloud: storage, VMs, and apps with unmatched transparency, trust, and power.

Bittensor subnet **75** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/thenervelab/thebrain) · [url](https://hippius.com/)

Fleet mods for this subnet: `hippius`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.016577 | +0.02% | +2.68% | -3.25% | 94,738 | 32,796 | 3,190 |

## Last 24h flow

210 trades by 98 coldkeys · 110 buys (1,726 τ) / 100 sells (1,376 τ) · net 350.34 τ

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
