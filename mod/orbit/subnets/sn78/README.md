# sn78 — Umi و

Universal motion to meaning

Bittensor subnet **78** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/Umi-BitSign/umi) · [url](https://www.umi.vision) · [discord](https://discord.gg/8pexneWef)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002821 | -0.00% | -1.99% | -24.45% | 4,489 | 1,269 | 309.92 |

## Last 24h flow

179 trades by 91 coldkeys · 45 buys (148.60 τ) / 134 sells (160.60 τ) · net -12.00 τ

## News

- 2026-10-18 · commit · [Record exact C5 checkpoint batching backport ancestry](https://github.com/Umi-BitSign/umi/commit/1455f80bc83fd74ff30643b599c7ecebf9ac0c1a) — Umi-BitSign/umi
- 2026-10-18 · commit · [Batch retained history checkpoint metadata reads](https://github.com/Umi-BitSign/umi/commit/10c8e57c93c85b63789a4efba1e3a3b4e3f537ea) — Umi-BitSign/umi
- 2026-10-18 · commit · [Merge pull request #299 from Umi-BitSign/perf/history-checkpoint-batches](https://github.com/Umi-BitSign/umi/commit/2f4962365b56c9724d2cebdb4d7a585c395f3b16) — Umi-BitSign/umi
- 2026-10-17 · commit · [Record exact C5 durable coverage backport release ancestry](https://github.com/Umi-BitSign/umi/commit/a5fb84643382866802f2294dd57ecb99702b5978) — Umi-BitSign/umi
- 2026-10-17 · commit · [Merge pull request #298 from Umi-BitSign/codex/coverage-control-runti…](https://github.com/Umi-BitSign/umi/commit/14bce82b1659db9c826948e1604c05585c0d5195) — Umi-BitSign/umi
- 2026-10-17 · commit · [Recover historical reward coverage gaps from durable native endpoints](https://github.com/Umi-BitSign/umi/commit/3bcecc5f059f9ae1669c9bffa766b62f3dd5e9fb) — Umi-BitSign/umi
- 2026-10-17 · commit · [Reuse owned historical control verification during coverage capture](https://github.com/Umi-BitSign/umi/commit/1576e8b52af595006010484bb2667e6ad9f1aada) — Umi-BitSign/umi
- 2026-10-17 · commit · [Record exact C5 coverage backport release ancestry](https://github.com/Umi-BitSign/umi/commit/8d399c11f40ca34861550476cf194eeb7f15298f) — Umi-BitSign/umi

## Use

```bash
m subnets.sn78/info        # live identity + market (snapshot if bt is down)
m subnets.sn78/news        # scraped news
m subnets.sn78/trades      # 24h alpha tape
m subnets.sn78/daily       # daily candles
python3 orbit/subnets/sn78/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
