# sn78 — Umi و

Universal motion to meaning

Bittensor subnet **78** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/Umi-BitSign/umi) · [url](https://www.umi.vision) · [discord](https://discord.gg/8pexneWef)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004700 | +3.02% | -4.97% | +19.52% | 7,001 | 1,630 | 1,154 |

## Last 24h flow

492 trades by 219 coldkeys · 188 buys (555.69 τ) / 304 sells (588.72 τ) · net -33.03 τ

## News

- 2026-10-04 · commit · [Merge pull request #209 from Umi-BitSign/codex/model-router-concurren…](https://github.com/Umi-BitSign/umi/commit/09005ad34c8d393d5752bef4a75939ad85a35f77) — Umi-BitSign/umi
- 2026-10-04 · commit · [Preserve model router upload capacity](https://github.com/Umi-BitSign/umi/commit/0b74da6b888310ca01ff046275f4b3764376d2a0) — Umi-BitSign/umi
- 2026-10-04 · commit · [Merge pull request #208 from Umi-BitSign/codex/checkpoint-incremental…](https://github.com/Umi-BitSign/umi/commit/74e120891b4432ed4416d99a685d1a550143a873) — Umi-BitSign/umi
- 2026-10-03 · commit · [Bound submission checkpoint replay](https://github.com/Umi-BitSign/umi/commit/46933fa4a460e74792ea8dd8c6539ec54ecfe946) — Umi-BitSign/umi
- 2026-10-03 · commit · [Merge pull request #207 from Umi-BitSign/codex/successor-series-harde…](https://github.com/Umi-BitSign/umi/commit/6c5387282df7f85cc68b88576a9838733f138cdb) — Umi-BitSign/umi
- 2026-10-03 · commit · [Select standing successor handoffs explicitly](https://github.com/Umi-BitSign/umi/commit/1018f702dda1192eca8d3ff3713e974b0d763173) — Umi-BitSign/umi
- 2026-10-03 · commit · [Replay standing predecessor opportunities natively](https://github.com/Umi-BitSign/umi/commit/0680251798bbafd7cb7dc939515a6abefd4ef672) — Umi-BitSign/umi
- 2026-10-03 · commit · [Migrate standing reward hosts across successor series](https://github.com/Umi-BitSign/umi/commit/3c6ba1c70589d0e80fad30f94e8cea13111de542) — Umi-BitSign/umi

## Use

```bash
m subnets.sn78/info        # live identity + market (snapshot if bt is down)
m subnets.sn78/news        # scraped news
m subnets.sn78/trades      # 24h alpha tape
m subnets.sn78/daily       # daily candles
python3 orbit/subnets/sn78/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
