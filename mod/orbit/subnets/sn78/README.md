# sn78 — Umi و

Universal motion to meaning

Bittensor subnet **78** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/Umi-BitSign/umi) · [url](https://www.umi.vision) · [discord](https://discord.gg/8pexneWef)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003823 | -0.71% | -9.40% | +13.54% | 5,778 | 1,473 | 678.37 |

## Last 24h flow

417 trades by 184 coldkeys · 121 buys (302.00 τ) / 296 sells (374.72 τ) · net -72.72 τ

## News

- 2026-10-06 · commit · [Reduce intake seal contention and isolate upgraded miner imports (#230)](https://github.com/Umi-BitSign/umi/commit/e7ef2c16b9abe7487f2926de294a1ed57d8fa5f5) — Umi-BitSign/umi
- 2026-10-06 · commit · [Preserve manual miner state during upgrades and bound supervisor catc…](https://github.com/Umi-BitSign/umi/commit/4953ccf9c8d20bbab2d1174a2b5f3a51932d8dc5) — Umi-BitSign/umi
- 2026-10-06 · commit · [Recover paid service claims after shared finalized-head advances (#228)](https://github.com/Umi-BitSign/umi/commit/44d9e600b3a5a80857d263361cff680d4f234bdc) — Umi-BitSign/umi
- 2026-10-06 · commit · [Preserve service admission across local contention and expose safe mi…](https://github.com/Umi-BitSign/umi/commit/d473b1c25640cc4c36dc865ffa5a9722acd7ade4) — Umi-BitSign/umi
- 2026-10-06 · commit · [Merge pull request #226 from Umi-BitSign/codex/c5-rpc-cache](https://github.com/Umi-BitSign/umi/commit/75181fda550bf8630f73f4c8c83df0806840ce12) — Umi-BitSign/umi
- 2026-10-06 · commit · [Merge pull request #224 from Umi-BitSign/codex/c5-service-retention-r…](https://github.com/Umi-BitSign/umi/commit/a406db4ff3167bf61eb82617c8a62a07458ea485) — Umi-BitSign/umi
- 2026-10-06 · commit · [Match miner scoring runtime pins and diagnose admission holds](https://github.com/Umi-BitSign/umi/commit/84a1cda1c6422e376231e1d2301e405d7b4ac229) — Umi-BitSign/umi
- 2026-10-06 · commit · [Allow bounded concurrent private cohort history replies](https://github.com/Umi-BitSign/umi/commit/8c2a959ff0a2a4267b4a575bda34c47d7879e0da) — Umi-BitSign/umi

## Use

```bash
m subnets.sn78/info        # live identity + market (snapshot if bt is down)
m subnets.sn78/news        # scraped news
m subnets.sn78/trades      # 24h alpha tape
m subnets.sn78/daily       # daily candles
python3 orbit/subnets/sn78/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
