# sn78 — Umi و

Universal motion to meaning

Bittensor subnet **78** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/Umi-BitSign/umi) · [url](https://www.umi.vision) · [discord](https://discord.gg/8pexneWef)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002458 | -3.97% | -12.48% | -32.31% | 3,948 | 1,185 | 391.30 |

## Last 24h flow

133 trades by 66 coldkeys · 40 buys (154.85 τ) / 93 sells (232.39 τ) · net -77.53 τ

## News

- 2026-10-20 · commit · [Merge pull request #321 from Umi-BitSign/retained-checkpoint-header-h…](https://github.com/Umi-BitSign/umi/commit/e797fd71f5eb2cd743784d0a18249f6e1adfe6e8) — Umi-BitSign/umi
- 2026-10-20 · commit · [Restore completed control history from retained checkpoint header hints](https://github.com/Umi-BitSign/umi/commit/fae0118a0f741c73dc5b1dcec78f818e183872b2) — Umi-BitSign/umi
- 2026-10-19 · commit · [Merge pull request #320 from Umi-BitSign/runtime-adoption-authenticat…](https://github.com/Umi-BitSign/umi/commit/525ca54c4b27ce108ae7ee1d4778c4ab64d4a69f) — Umi-BitSign/umi
- 2026-10-19 · commit · [Authorize contained runtime code supersession without replacing signe…](https://github.com/Umi-BitSign/umi/commit/95276f8d4da846fa6cfa8d8fe70f96858e66b0b5) — Umi-BitSign/umi
- 2026-10-19 · commit · [Merge pull request #319 from Umi-BitSign/qualify-runtime481-eligibility](https://github.com/Umi-BitSign/umi/commit/2c127fa3c961536baf6b19ff7c30d769eecafdc0) — Umi-BitSign/umi
- 2026-10-19 · commit · [Reuse canonical retained bytes during checkpoint metadata loading (#318)](https://github.com/Umi-BitSign/umi/commit/ba20e668c1e1aaae87444006087877f08c65aab1) — Umi-BitSign/umi
- 2026-10-19 · commit · [Batch checkpoint source identities within fresh SQL snapshots (#317)](https://github.com/Umi-BitSign/umi/commit/662821998437948f60a8b2149d3ea86eb6e2b431) — Umi-BitSign/umi
- 2026-10-19 · commit · [Merge pull request #316 from Umi-BitSign/fix/canonical-small-document…](https://github.com/Umi-BitSign/umi/commit/fa87e0add020be1ebd04837de75840d6b3c92f18) — Umi-BitSign/umi

## Use

```bash
m subnets.sn78/info        # live identity + market (snapshot if bt is down)
m subnets.sn78/news        # scraped news
m subnets.sn78/trades      # 24h alpha tape
m subnets.sn78/daily       # daily candles
python3 orbit/subnets/sn78/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
